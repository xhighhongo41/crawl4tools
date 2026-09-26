"""The crawl4mcp MCP server: the ``fetch`` and ``download`` tools.

:func:`build_server` wires the shared fetch engine into an
:class:`~mcp.server.mcpserver.MCPServer`. One :class:`Fetcher` (and so at
most one browser) lives for the whole life of the server, and a single
semaphore caps the number of URLs fetched at once across every
concurrent tool call.

:func:`open_state` opens that shared state on its own so that several apps
in one process (e.g. crawl4server's MCP and web loader apps) can share one
fetcher and one semaphore: pass it to :func:`build_server` as ``state``
and call :func:`fetch_all` with it directly.

The files saved by ``download`` are registered in the state's
:class:`~crawl4tools.server.files.FileRegistry`, and the server's HTTP app
serves them at ``/files/{token}``: over HTTP, each saved file comes with a
``file_url`` the client can fetch it from, and the server deletes the file
once it has been fetched whole (unless ``settings.keep_downloads``).

Every text the server shows its clients (the instructions, the tool titles
and descriptions, the parameter descriptions, notes and errors) is in the
language of ``settings.lang``; the ``note:`` / ``saved:`` / ``file:`` /
``error:`` / ``done:`` prefixes, the ``<!-- crawl4tools: ... -->`` header
and the keys of the structured data stay in English. Logs are always in
English.

``fetch``'s ``structured_content["pages"][i]["text"]`` duplicates the body
of the matching ``content`` block, since some MCP clients drop the
``content`` text blocks when ``structuredContent`` is present.
"""

# No ``from __future__ import annotations`` in this module: the parameter
# descriptions of the tools are built from the server's translator inside
# build_server, and the MCP SDK evaluates string annotations against the
# module globals only. Evaluated when the tool functions are defined, the
# annotations can refer to those local values.

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
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
from crawl4tools.i18n import Translator, render_exception
from crawl4tools.server.files import FILES_PATH, FileRegistry, file_url_base, files_route
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

#: Called after each URL of :func:`fetch_all` completes, with
#: ``(url, done, total)``.
ProgressCallback = Callable[[str, int, int], Awaitable[None]]

#: Formats the ``fetch`` tool can return directly to the client.
FetchFormat = Literal["markdown", "html", "screenshot"]
#: Formats the ``download`` tool can save to files.
DownloadFormat = Literal["markdown", "html", "pdf", "screenshot", "mhtml", "raw"]

#: A per-call timeout above this multiple of the server default earns a note.
_LONG_TIMEOUT_FACTOR = 10


@dataclass
class ServerState:
    """Objects shared by every tool call for the life of the server."""

    fetcher: Fetcher
    semaphore: asyncio.Semaphore
    settings: ServerSettings
    files: FileRegistry


@dataclass(frozen=True)
class _ParameterDescriptions:
    """The descriptions of the tool parameters, in one language."""

    urls: str
    fit: str
    citations: str
    ignore_links: str
    ignore_images: str
    timeout_s: str
    fetch_format: str
    download_format: str
    directory: str


def _parameter_descriptions(t: Translator) -> _ParameterDescriptions:
    """Return the descriptions of the tool parameters translated by *t*."""
    return _ParameterDescriptions(
        urls=t.gettext(
            "URLs to fetch (http or https), at most the server's per-call limit (see the "
            "server instructions); duplicates are fetched once."
        ),
        fit=t.gettext(
            "Keep only the main content (drops menus, footers, and the like); falls back to "
            "the full page when nothing is left. Markdown only."
        ),
        citations=t.gettext(
            "Turn links into numbered references listed at the end. Markdown only."
        ),
        ignore_links=t.gettext("Drop links from the Markdown output. Markdown only."),
        ignore_images=t.gettext("Drop images from the Markdown output. Markdown only."),
        timeout_s=t.gettext(
            "Page load timeout per URL in seconds; defaults to the server setting."
        ),
        fetch_format=t.gettext(
            "Output format: 'markdown' (default), 'html' (rendered page HTML), or "
            "'screenshot' (full-page PNG image)."
        ),
        download_format=t.gettext(
            "File format: 'markdown' (default), 'html', 'pdf' (page printed to PDF), "
            "'screenshot' (PNG), 'mhtml' (single-file web archive), or 'raw' (the original "
            "response bytes, e.g. a PDF or image as served)."
        ),
        directory=t.gettext(
            "Subdirectory of the server's download directory to save into; must stay inside "
            "it. Defaults to the download directory itself."
        ),
    )


def _instructions(settings: ServerSettings, t: Translator) -> str:
    """Return the server instructions shown to MCP clients, translated by *t*."""
    return t.gettext(
        "Web fetching tools built on crawl4ai. "
        "`fetch` returns page content directly: Markdown by default, or HTML, or a PNG "
        "screenshot. "
        "`download` saves files in any format (including PDF, MHTML, and the raw "
        "source) into a directory on the server and returns their paths. "
        "Over HTTP, `download` also returns a `file_url` per file to fetch it from the "
        "server (e.g. with curl). "
        "The server deletes its copy of a file once it has been fetched from its "
        "`file_url` (unless the server was started with `--keep-downloads`). "
        "PDFs are transcribed to Markdown. Duplicate URLs are fetched once. "
        "At most {max_urls} URLs per call."
    ).format(max_urls=settings.max_urls)


def _state(ctx: Context[ServerState, Any]) -> ServerState:
    """Return the :class:`ServerState` yielded by the server lifespan."""
    return ctx.request_context.lifespan_context


def _prepare(
    settings: ServerSettings,
    urls: Sequence[str],
    t: Translator,
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
        ToolError: if the URLs or the timeout are invalid; the message is
            translated by *t*.
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
        raise ToolError(render_exception(exc, t)) from exc
    return unique, duplicates, options


def _note_lines(
    settings: ServerSettings, duplicates: Sequence[str], timeout_s: float | None, t: Translator
) -> list[str]:
    """Return the ``note: ...`` lines about duplicates and a long timeout.

    The messages are translated by *t*; the ``note:`` prefix is not.
    """
    duplicate = t.gettext("duplicate URL ignored: {url}")
    lines = [f"note: {duplicate.format(url=url)}" for url in duplicates]
    if timeout_s is not None and timeout_s > _LONG_TIMEOUT_FACTOR * settings.timeout_s:
        message = t.gettext(
            "timeout_s={timeout_s:g} is more than {factor} times the server default "
            "({default:g} s); slow pages may hold the call open for a long time"
        ).format(timeout_s=timeout_s, factor=_LONG_TIMEOUT_FACTOR, default=settings.timeout_s)
        lines.append(f"note: {message}")
    return lines


@asynccontextmanager
async def open_state(
    settings: ServerSettings,
    fetcher_factory: FetcherFactory = Fetcher,
    *,
    files: FileRegistry | None = None,
) -> AsyncIterator[ServerState]:
    """Open the fetcher and semaphore shared by every fetch for *settings*.

    The :class:`Fetcher` built by *fetcher_factory* from the server-wide
    fetch options is closed when the context exits. The semaphore allows
    ``settings.concurrency`` URLs in flight at once. The saved files are
    registered in *files*, or in a new :class:`FileRegistry` of
    ``settings.download_root`` when it is None.
    """
    registry = files if files is not None else FileRegistry(settings.download_root)
    async with fetcher_factory(settings.fetch_options()) as fetcher:
        yield ServerState(fetcher, asyncio.Semaphore(settings.concurrency), settings, registry)


async def fetch_all(
    state: ServerState,
    urls: Sequence[str],
    options: FetchOptions,
    on_done: ProgressCallback | None = None,
) -> list[FetchOutcome]:
    """Fetch *urls* concurrently under the shared semaphore, in input order.

    If *on_done* is given it is awaited after each URL completes with
    ``(url, done, total)``, where ``done`` counts the URLs finished so far.
    """
    total = len(urls)
    done = 0

    async def run(url: str) -> FetchOutcome:
        nonlocal done
        async with state.semaphore:
            outcome = await state.fetcher.fetch(url, options=options)
        done += 1
        if on_done is not None:
            await on_done(url, done, total)
        return outcome

    return list(await asyncio.gather(*(run(url) for url in urls)))


async def _fetch_all(
    state: ServerState,
    ctx: Context[ServerState, Any],
    urls: Sequence[str],
    options: FetchOptions,
) -> list[FetchOutcome]:
    """Run :func:`fetch_all` for a tool call, reporting progress to the client.

    A failure to report progress is logged and never fails the fetch.
    """

    async def report(url: str, done: int, total: int) -> None:
        try:
            await ctx.report_progress(done, total, url)
        except Exception:
            logger.debug("could not report progress for %s", url, exc_info=True)

    return await fetch_all(state, urls, options, report)


def build_server(
    settings: ServerSettings,
    *,
    fetcher_factory: FetcherFactory = Fetcher,
    state: ServerState | None = None,
) -> MCPServer[ServerState]:
    """Build the crawl4mcp server with its ``fetch`` and ``download`` tools.

    Without *state*, the server opens its own state with :func:`open_state`
    for the life of each lifespan: *fetcher_factory* creates the single
    :class:`Fetcher` used (tests pass one that injects fake crawlers).

    With *state*, the server uses that shared state as is and neither
    creates nor closes a fetcher; *fetcher_factory* is then ignored and the
    caller owns the state's lifetime.

    The server's HTTP app serves the saved files at ``GET /files/{token}``
    from the :class:`FileRegistry` of *state*, or, without *state*, from
    one registry created here and shared by every lifespan (an HTTP server
    runs one per session). A file fetched whole from there is deleted from
    the server, unless ``settings.keep_downloads``.

    The texts sent to the clients are translated by ``settings.translator``
    once, when the server is built.

    The uvicorn server that ``run(transport="streamable-http")`` starts logs
    at INFO, with its access log, only when ``settings.log_level`` is
    ``debug``, and at WARNING otherwise.
    """
    t = settings.translator
    descriptions = _parameter_descriptions(t)
    registry = state.files if state is not None else FileRegistry(settings.download_root)

    @asynccontextmanager
    async def lifespan(_server: MCPServer[ServerState]) -> AsyncIterator[ServerState]:
        if state is not None:
            yield state
            return
        async with open_state(settings, fetcher_factory, files=registry) as opened:
            yield opened

    server: MCPServer[ServerState] = MCPServer(
        name="crawl4tools",
        instructions=_instructions(settings, t),
        version=__version__,
        lifespan=lifespan,
        log_level="INFO" if settings.log_level == "debug" else "WARNING",
    )
    server.custom_route(FILES_PATH, methods=["GET"])(
        files_route(registry, t, keep=settings.keep_downloads)
    )

    @server.tool(
        name="fetch",
        title=t.gettext("Fetch web pages"),
        description=t.gettext(
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
        urls: Annotated[list[str], Field(description=descriptions.urls)],
        format: Annotated[FetchFormat, Field(description=descriptions.fetch_format)] = "markdown",
        fit: Annotated[bool, Field(description=descriptions.fit)] = False,
        citations: Annotated[bool, Field(description=descriptions.citations)] = False,
        ignore_links: Annotated[bool, Field(description=descriptions.ignore_links)] = False,
        ignore_images: Annotated[bool, Field(description=descriptions.ignore_images)] = False,
        timeout_s: Annotated[float | None, Field(description=descriptions.timeout_s)] = None,
    ) -> CallToolResult:
        unique, duplicates, options = _prepare(
            settings,
            urls,
            t,
            format=OutputFormat(format),
            fit=fit,
            citations=citations,
            ignore_links=ignore_links,
            ignore_images=ignore_images,
            timeout_s=timeout_s,
        )
        notes = _note_lines(settings, duplicates, timeout_s, t)
        outcomes = await _fetch_all(_state(ctx), ctx, unique, options)

        content: list[ContentBlock] = []
        if notes:
            content.append(TextContent(type="text", text="\n".join(notes)))
        multiple = len(unique) > 1
        for url, outcome in zip(unique, outcomes, strict=True):
            content.extend(page_blocks(outcome, url, t, multiple=multiple))
        return CallToolResult(
            content=content,
            structured_content={
                "pages": [page_meta(o, u, t) for u, o in zip(unique, outcomes, strict=True)],
                "duplicates": duplicates,
            },
            is_error=not any(outcome.ok for outcome in outcomes),
        )

    @server.tool(
        name="download",
        title=t.gettext("Download web pages to files"),
        description=t.gettext(
            "Fetch one or more URLs and save each result as a file in a directory on "
            "the server, returning the saved paths. Supports Markdown (default), HTML, "
            "PDF, PNG screenshot, MHTML, and the raw source. PDFs are transcribed to "
            "Markdown unless format is 'raw'; non-web content (images, archives, ...) is "
            "saved as served. Existing files are overwritten. The call fails only if no "
            "file was saved. When the server is reached over HTTP, each saved file also has a "
            "`file_url`; fetch it (for example `curl -o <name> <file_url>`) to save the file "
            "on your own machine without passing its content through the conversation. Over "
            "stdio the server runs on your machine, so the returned paths are local. When the "
            "server is used over HTTP, it deletes its own copy of a file once a client has "
            "fetched it from its `file_url` (unless the server was started with "
            "`--keep-downloads`); over stdio the files stay where the returned paths say."
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
        urls: Annotated[list[str], Field(description=descriptions.urls)],
        format: Annotated[
            DownloadFormat, Field(description=descriptions.download_format)
        ] = "markdown",
        directory: Annotated[str | None, Field(description=descriptions.directory)] = None,
        fit: Annotated[bool, Field(description=descriptions.fit)] = False,
        citations: Annotated[bool, Field(description=descriptions.citations)] = False,
        ignore_links: Annotated[bool, Field(description=descriptions.ignore_links)] = False,
        ignore_images: Annotated[bool, Field(description=descriptions.ignore_images)] = False,
        timeout_s: Annotated[float | None, Field(description=descriptions.timeout_s)] = None,
    ) -> CallToolResult:
        unique, duplicates, options = _prepare(
            settings,
            urls,
            t,
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
            raise ToolError(render_exception(exc, t)) from exc
        notes = _note_lines(settings, duplicates, timeout_s, t)
        server_state = _state(ctx)
        outcomes = await _fetch_all(server_state, ctx, unique, options)
        # Over stdio there is no HTTP request, hence no base URL and no file_url.
        base = file_url_base(getattr(ctx.request_context, "request", None))

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            message = t.gettext("could not create {path}: {reason}").format(
                path=target_dir, reason=exc.strerror
            )
            raise ToolError(message) from exc

        allocator = NameAllocator(target_dir)
        records: list[dict[str, object]] = []
        for url, outcome in zip(unique, outcomes, strict=True):
            if not outcome.ok:
                records.append(download_record(outcome, url, None, t))
                continue
            path = allocator.allocate(filename_for(url, outcome.suggested_extension))
            try:
                path.write_bytes(payload_bytes(outcome))
            except OSError as exc:
                error = t.gettext("could not write {path}: {reason}").format(
                    path=path, reason=exc.strerror
                )
                records.append(download_record(outcome, url, None, t, error=error))
            else:
                token = server_state.files.register(path)
                file_url = f"{base}/files/{token}" if base else None
                records.append(download_record(outcome, url, path, t, file_url=file_url))

        saved = sum(1 for record in records if record["ok"])
        lines = list(notes)
        for record in records:
            lines.extend(download_lines(record, t))
        summary = t.gettext("{saved} saved, {failed} failed").format(
            saved=saved, failed=len(records) - saved
        )
        lines.append(f"done: {summary}")
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
