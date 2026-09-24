"""The crawl4mcp MCP server: the ``fetch`` and ``download`` tools.

:func:`build_server` wires the shared fetch engine into an
:class:`~mcp.server.mcpserver.MCPServer`. One :class:`Fetcher` (and so at
most one browser) lives for the whole life of the server, and a single
semaphore caps the number of URLs fetched at once across every
concurrent tool call.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ContentBlock, TextContent, ToolAnnotations
from pydantic import Field

from crawl4tools import __version__
from crawl4tools.cli.output import payload_bytes
from crawl4tools.engine.fetcher import Fetcher
from crawl4tools.engine.models import FetchOptions, FetchOutcome, OutputFormat
from crawl4tools.engine.naming import NameAllocator, filename_for
from crawl4tools.server.results import (
    check_urls,
    download_lines,
    download_record,
    page_blocks,
    page_meta,
    resolve_directory,
)
from crawl4tools.server.settings import ServerSettings

logger = logging.getLogger(__name__)

#: Builds the server's :class:`Fetcher` from the server-wide fetch options.
FetcherFactory = Callable[[FetchOptions], Fetcher]

#: Formats the ``fetch`` tool can return directly to the client.
FetchFormat = Literal["markdown", "html", "screenshot"]
#: Formats the ``download`` tool can save to files.
DownloadFormat = Literal["markdown", "html", "pdf", "screenshot", "mhtml", "raw"]

#: A per-call timeout above this multiple of the server default earns a note.
_LONG_TIMEOUT_FACTOR = 10

_URLS_DESCRIPTION = (
    "URLs to fetch (http or https), at most the server's per-call limit (see the "
    "server instructions); duplicates are fetched once."
)
_FIT_DESCRIPTION = (
    "Keep only the main content (drops menus, footers, and the like); falls back to "
    "the full page when nothing is left. Markdown only."
)
_CITATIONS_DESCRIPTION = "Turn links into numbered references listed at the end. Markdown only."
_IGNORE_LINKS_DESCRIPTION = "Drop links from the Markdown output. Markdown only."
_IGNORE_IMAGES_DESCRIPTION = "Drop images from the Markdown output. Markdown only."
_TIMEOUT_DESCRIPTION = "Page load timeout per URL in seconds; defaults to the server setting."
_FETCH_FORMAT_DESCRIPTION = (
    "Output format: 'markdown' (default), 'html' (rendered page HTML), or "
    "'screenshot' (full-page PNG image)."
)
_DOWNLOAD_FORMAT_DESCRIPTION = (
    "File format: 'markdown' (default), 'html', 'pdf' (page printed to PDF), "
    "'screenshot' (PNG), 'mhtml' (single-file web archive), or 'raw' (the original "
    "response bytes, e.g. a PDF or image as served)."
)
_DIRECTORY_DESCRIPTION = (
    "Subdirectory of the server's download directory to save into; must stay inside "
    "it. Defaults to the download directory itself."
)


@dataclass
class ServerState:
    """Objects shared by every tool call for the life of the server."""

    fetcher: Fetcher
    semaphore: asyncio.Semaphore
    settings: ServerSettings


def _instructions(settings: ServerSettings) -> str:
    """Return the server instructions shown to MCP clients."""
    return (
        "Web fetching tools built on crawl4ai. "
        "`fetch` returns page content directly: Markdown by default, or HTML, or a PNG "
        "screenshot. "
        "`download` saves files in any format (including PDF, MHTML, and the raw "
        "source) into a directory on the server and returns their paths. "
        "PDFs are transcribed to Markdown. Duplicate URLs are fetched once. "
        f"At most {settings.max_urls} URLs per call."
    )


def _state(ctx: Context[ServerState, Any]) -> ServerState:
    """Return the :class:`ServerState` yielded by the server lifespan."""
    return ctx.request_context.lifespan_context


def _prepare(
    settings: ServerSettings,
    urls: Sequence[str],
    *,
    format: OutputFormat,
    fit: bool,
    citations: bool,
    ignore_links: bool,
    ignore_images: bool,
    timeout_s: float | None,
) -> tuple[list[str], list[str], FetchOptions]:
    """Validate a call's arguments before anything is fetched.

    Returns:
        ``(unique, duplicates, options)`` for the call.

    Raises:
        ToolError: if the URLs or the timeout are invalid.
    """
    try:
        unique, duplicates = check_urls(urls, settings.max_urls)
        options = settings.fetch_options(
            format=format,
            fit=fit,
            citations=citations,
            ignore_links=ignore_links,
            ignore_images=ignore_images,
            timeout_s=timeout_s,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return unique, duplicates, options


def _note_lines(
    settings: ServerSettings, duplicates: Sequence[str], timeout_s: float | None
) -> list[str]:
    """Return the ``note: ...`` lines about duplicates and a long timeout."""
    lines = [f"note: duplicate URL ignored: {url}" for url in duplicates]
    if timeout_s is not None and timeout_s > _LONG_TIMEOUT_FACTOR * settings.timeout_s:
        lines.append(
            f"note: timeout_s={timeout_s:g} is more than {_LONG_TIMEOUT_FACTOR} times the "
            f"server default ({settings.timeout_s:g} s); slow pages may hold the call open "
            "for a long time"
        )
    return lines


async def _fetch_all(
    state: ServerState,
    ctx: Context[ServerState, Any],
    urls: Sequence[str],
    options: FetchOptions,
) -> list[FetchOutcome]:
    """Fetch *urls* concurrently under the server-wide semaphore, in input order.

    Progress is reported after each URL completes; a failure to report
    progress is logged and never fails the fetch.
    """
    total = len(urls)
    done = 0

    async def run(url: str) -> FetchOutcome:
        nonlocal done
        async with state.semaphore:
            outcome = await state.fetcher.fetch(url, options=options)
        done += 1
        try:
            await ctx.report_progress(done, total, url)
        except Exception:
            logger.debug("could not report progress for %s", url, exc_info=True)
        return outcome

    return list(await asyncio.gather(*(run(url) for url in urls)))


def build_server(
    settings: ServerSettings, *, fetcher_factory: FetcherFactory = Fetcher
) -> MCPServer[ServerState]:
    """Build the crawl4mcp server with its ``fetch`` and ``download`` tools.

    *fetcher_factory* creates the single :class:`Fetcher` used for the life
    of the server (tests pass one that injects fake crawlers).
    """

    @asynccontextmanager
    async def lifespan(_server: MCPServer[ServerState]) -> AsyncIterator[ServerState]:
        async with fetcher_factory(settings.fetch_options()) as fetcher:
            yield ServerState(fetcher, asyncio.Semaphore(settings.concurrency), settings)

    server: MCPServer[ServerState] = MCPServer(
        name="crawl4tools",
        instructions=_instructions(settings),
        version=__version__,
        lifespan=lifespan,
    )

    @server.tool(
        name="fetch",
        title="Fetch web pages",
        description=(
            "Fetch one or more web pages and return their content directly: Markdown "
            "(default), HTML, or a PNG screenshot. PDFs are transcribed to Markdown and "
            "images are returned as images. When several URLs are given, each result "
            "starts with a '<!-- crawl4tools: url=... status=... -->' header. Failed "
            "URLs are reported as 'error: ...' lines; the call fails only if every URL "
            "fails. Use `download` to save other formats to files."
        ),
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
    )
    async def fetch(
        ctx: Context[ServerState, Any],
        urls: Annotated[list[str], Field(description=_URLS_DESCRIPTION)],
        format: Annotated[FetchFormat, Field(description=_FETCH_FORMAT_DESCRIPTION)] = "markdown",
        fit: Annotated[bool, Field(description=_FIT_DESCRIPTION)] = False,
        citations: Annotated[bool, Field(description=_CITATIONS_DESCRIPTION)] = False,
        ignore_links: Annotated[bool, Field(description=_IGNORE_LINKS_DESCRIPTION)] = False,
        ignore_images: Annotated[bool, Field(description=_IGNORE_IMAGES_DESCRIPTION)] = False,
        timeout_s: Annotated[float | None, Field(description=_TIMEOUT_DESCRIPTION)] = None,
    ) -> CallToolResult:
        unique, duplicates, options = _prepare(
            settings,
            urls,
            format=OutputFormat(format),
            fit=fit,
            citations=citations,
            ignore_links=ignore_links,
            ignore_images=ignore_images,
            timeout_s=timeout_s,
        )
        notes = _note_lines(settings, duplicates, timeout_s)
        outcomes = await _fetch_all(_state(ctx), ctx, unique, options)

        content: list[ContentBlock] = []
        if notes:
            content.append(TextContent(type="text", text="\n".join(notes)))
        multiple = len(unique) > 1
        for url, outcome in zip(unique, outcomes, strict=True):
            content.extend(page_blocks(outcome, url, multiple=multiple))
        return CallToolResult(
            content=content,
            structured_content={
                "pages": [page_meta(o, u) for u, o in zip(unique, outcomes, strict=True)],
                "duplicates": duplicates,
            },
            is_error=not any(outcome.ok for outcome in outcomes),
        )

    @server.tool(
        name="download",
        title="Download web pages to files",
        description=(
            "Fetch one or more URLs and save each result as a file in a directory on "
            "the server, returning the saved paths. Supports Markdown (default), HTML, "
            "PDF, PNG screenshot, MHTML, and the raw source. PDFs are transcribed to "
            "Markdown unless format is 'raw'; non-web content (images, archives, ...) is "
            "saved as served. Existing files are overwritten. The call fails only if no "
            "file was saved."
        ),
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def download(
        ctx: Context[ServerState, Any],
        urls: Annotated[list[str], Field(description=_URLS_DESCRIPTION)],
        format: Annotated[
            DownloadFormat, Field(description=_DOWNLOAD_FORMAT_DESCRIPTION)
        ] = "markdown",
        directory: Annotated[str | None, Field(description=_DIRECTORY_DESCRIPTION)] = None,
        fit: Annotated[bool, Field(description=_FIT_DESCRIPTION)] = False,
        citations: Annotated[bool, Field(description=_CITATIONS_DESCRIPTION)] = False,
        ignore_links: Annotated[bool, Field(description=_IGNORE_LINKS_DESCRIPTION)] = False,
        ignore_images: Annotated[bool, Field(description=_IGNORE_IMAGES_DESCRIPTION)] = False,
        timeout_s: Annotated[float | None, Field(description=_TIMEOUT_DESCRIPTION)] = None,
    ) -> CallToolResult:
        unique, duplicates, options = _prepare(
            settings,
            urls,
            format=OutputFormat(format),
            fit=fit,
            citations=citations,
            ignore_links=ignore_links,
            ignore_images=ignore_images,
            timeout_s=timeout_s,
        )
        try:
            target_dir = resolve_directory(settings.download_root, directory)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        notes = _note_lines(settings, duplicates, timeout_s)
        outcomes = await _fetch_all(_state(ctx), ctx, unique, options)

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ToolError(f"could not create {target_dir}: {exc.strerror}") from exc

        allocator = NameAllocator(target_dir)
        records: list[dict[str, object]] = []
        for url, outcome in zip(unique, outcomes, strict=True):
            if not outcome.ok:
                records.append(download_record(outcome, url, None))
                continue
            path = allocator.allocate(filename_for(url, outcome.suggested_extension))
            try:
                path.write_bytes(payload_bytes(outcome))
            except OSError as exc:
                error = f"could not write {path}: {exc.strerror}"
                records.append(download_record(outcome, url, None, error=error))
            else:
                records.append(download_record(outcome, url, path))

        saved = sum(1 for record in records if record["ok"])
        lines = list(notes)
        for record in records:
            lines.extend(download_lines(record))
        lines.append(f"done: {saved} saved, {len(records) - saved} failed")
        return CallToolResult(
            content=[TextContent(type="text", text="\n".join(lines))],
            structured_content={
                "files": records,
                "duplicates": duplicates,
                "directory": str(target_dir),
            },
            is_error=saved == 0,
        )

    return server
