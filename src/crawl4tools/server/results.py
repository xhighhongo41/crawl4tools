"""Turn engine :class:`FetchOutcome` objects into MCP-tool-facing results.

Pure formatting/validation helpers shared by every crawl4mcp tool: URL list
checking, download-path resolution, human-readable content blocks, and the
structured metadata/records returned alongside them. No network or MCP
server code lives here.

The formatting functions take the :data:`~crawl4tools.i18n.Translator` of
the server's language. Only the messages are translated: the ``note:`` /
``saved:`` / ``file:`` / ``error:`` prefixes, the ``<!-- crawl4tools: ... -->`` header
and the keys of the structured data stay in English.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, cast

from mcp.types import ImageContent, TextContent

from crawl4tools.cli.report import error_line
from crawl4tools.engine.models import ContentKind, FetchOutcome
from crawl4tools.engine.naming import dedupe_urls, validate_url
from crawl4tools.i18n import N_, LocalizedError, Translator

if TYPE_CHECKING:
    from collections.abc import Sequence


class UrlsError(LocalizedError, ValueError):
    """The URL list of a tool call is empty, has invalid URLs, or is too long.

    Also a ``ValueError``, so existing ``except ValueError`` clauses keep
    catching it; ``str()`` is the English message.
    """


class DirectoryError(LocalizedError, ValueError):
    """The download subdirectory of a tool call is absolute or escapes the root.

    Also a ``ValueError``, so existing ``except ValueError`` clauses keep
    catching it; ``str()`` is the English message.
    """


def check_urls(urls: Sequence[str], max_urls: int) -> tuple[list[str], list[str]]:
    """Validate, dedupe, and cap a list of URLs from a tool call.

    Returns:
        A ``(unique, duplicates)`` pair, both in first-seen order.

    Raises:
        UrlsError: (a ``ValueError``) if *urls* is empty, contains one or
            more invalid URLs (all of them are named in the message), or
            has more unique URLs than *max_urls*.
    """
    if not urls:
        raise UrlsError(N_("at least one URL is required"))

    validated: list[str] = []
    invalid: list[str] = []
    for url in urls:
        try:
            validated.append(validate_url(url))
        except ValueError:
            invalid.append(url)
    if invalid:
        raise UrlsError(N_("invalid URL(s): {urls}"), urls=", ".join(invalid))

    unique, duplicates = dedupe_urls(validated)
    if len(unique) > max_urls:
        raise UrlsError(
            N_("too many URLs: {count} given, at most {limit} per call"),
            count=len(unique),
            limit=max_urls,
        )
    return unique, duplicates


def resolve_directory(root: Path, directory: str | None) -> Path:
    """Resolve *directory* (relative to *root*) for a download call.

    *root* is the configured download root. *directory* is an optional,
    caller-supplied subdirectory; it must stay inside *root*. Symlinks that
    would otherwise escape *root* are rejected because ``Path.resolve()``
    follows them before the containment check runs. This function never
    creates directories on disk.

    Raises:
        DirectoryError: (a ``ValueError``) if *directory* is absolute, or
            resolves outside *root*.
    """
    resolved_root = root.resolve()
    if not directory:
        return resolved_root

    candidate = Path(directory)
    if candidate.is_absolute():
        raise DirectoryError(
            N_("directory must be a relative path: {directory}"), directory=directory
        )

    resolved = (root / directory).resolve()
    if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
        raise DirectoryError(
            N_("directory must stay inside the download root: {directory}"), directory=directory
        )
    return resolved


def _header_lines(outcome: FetchOutcome, url: str, t: Translator, *, multiple: bool) -> list[str]:
    """Build the ``<!-- ... -->`` comment lines prefixed to a rendered block.

    The notes are translated by *t*; the ``crawl4tools:`` line is not, since
    clients may parse it.
    """
    lines: list[str] = []
    if multiple:
        status = outcome.status_code if outcome.status_code is not None else "-"
        lines.append(f"<!-- crawl4tools: url={url} status={status} -->")
    lines.extend(f"<!-- note: {note.render(t)} -->" for note in outcome.notes)
    return lines


def _with_header(header_lines: list[str], body: str) -> str:
    """Prefix *body* with *header_lines*, joined by newlines, when present."""
    if not header_lines:
        return body
    return "\n".join(header_lines) + "\n" + body


def _image_mime_type(outcome: FetchOutcome) -> str | None:
    """Return the mime type to use for *outcome* as an image, or None."""
    if outcome.content_kind is ContentKind.HTML and outcome.data is not None:
        # A rendered page whose payload is bytes is a screenshot.
        return "image/png"
    if outcome.content_kind is ContentKind.BINARY and outcome.content_type:
        media_type = outcome.content_type.split(";", 1)[0].strip().lower()
        if media_type.startswith("image/"):
            return media_type
    return None


def page_blocks(
    outcome: FetchOutcome, url: str, t: Translator, *, multiple: bool
) -> list[TextContent | ImageContent]:
    """Render one URL's outcome as human-readable MCP content blocks.

    *multiple* controls whether a ``url=... status=...`` header comment is
    added, which is only useful when several URLs are rendered together.
    Notes, errors and the binary-content message are translated by *t*.
    """
    header_lines = _header_lines(outcome, url, t, multiple=multiple)

    if not outcome.ok:
        return [TextContent(type="text", text=_with_header(header_lines, error_line(outcome, t)))]

    if outcome.text is not None:
        return [TextContent(type="text", text=_with_header(header_lines, outcome.text))]

    mime_type = _image_mime_type(outcome)
    if mime_type is not None:
        blocks: list[TextContent | ImageContent] = []
        if header_lines:
            blocks.append(TextContent(type="text", text="\n".join(header_lines)))
        data = outcome.data if outcome.data is not None else b""
        encoded = base64.b64encode(data).decode("ascii")
        blocks.append(ImageContent(type="image", data=encoded, mime_type=mime_type))
        return blocks

    data_len = len(outcome.data) if outcome.data is not None else 0
    if outcome.content_type:
        message = t.gettext(
            "binary content ({content_type}, {size} bytes) was not included; "
            "use the download tool to save it: {url}"
        ).format(content_type=outcome.content_type, size=data_len, url=url)
    else:
        message = t.gettext(
            "binary content (unknown type, {size} bytes) was not included; "
            "use the download tool to save it: {url}"
        ).format(size=data_len, url=url)
    return [TextContent(type="text", text=_with_header(header_lines, message))]


def page_meta(outcome: FetchOutcome, url: str, t: Translator) -> dict[str, object]:
    """Return the structured metadata for one URL's outcome.

    ``text`` duplicates the body :func:`page_blocks` puts in the text
    content block (without the header/note lines), so that MCP clients that
    only look at ``structuredContent`` still get the page's text; it is None
    for failures and for outcomes without a text payload (images,
    screenshots, other binary content). The ``error`` and ``notes`` values
    are translated by *t*; the keys are not.
    """
    return {
        "url": url,
        "final_url": outcome.final_url,
        "title": outcome.title,
        "ok": outcome.ok,
        "status_code": outcome.status_code,
        "content_kind": str(outcome.content_kind),
        "content_type": outcome.content_type,
        "text": outcome.text if outcome.ok and outcome.text is not None else None,
        "chars": len(outcome.text) if outcome.text is not None else None,
        "bytes": len(outcome.data) if outcome.data is not None else None,
        "error": outcome.error.render(t) if outcome.error is not None else None,
        "notes": [note.render(t) for note in outcome.notes],
    }


def download_record(
    outcome: FetchOutcome,
    url: str,
    path: Path | None,
    t: Translator,
    error: str | None = None,
    *,
    file_url: str | None = None,
) -> dict[str, object]:
    """Return the structured record for one URL's download attempt.

    *path* is the path the payload was written to, or None if it was not
    written. *error* overrides ``outcome.error`` when the failure happened
    while writing the file (rather than while fetching); it is a message
    the caller has already translated. ``outcome.error`` and the notes are
    translated by *t*. *file_url* is the URL to fetch the saved file from
    over HTTP; it is kept only when the file was saved.
    """
    ok = outcome.ok and path is not None and error is None
    payload_bytes: int | None = None
    if ok:
        if outcome.data is not None:
            payload_bytes = len(outcome.data)
        elif outcome.text is not None:
            payload_bytes = len(outcome.text.encode("utf-8"))
    resolved_error = error
    if resolved_error is None and outcome.error is not None:
        resolved_error = outcome.error.render(t)
    return {
        "url": url,
        "ok": ok,
        "path": str(path) if path is not None else None,
        "bytes": payload_bytes,
        "content_kind": str(outcome.content_kind),
        "content_type": outcome.content_type,
        "status_code": outcome.status_code,
        "error": resolved_error,
        "notes": [note.render(t) for note in outcome.notes],
        "file_url": file_url if ok else None,
    }


def download_lines(record: dict[str, object], t: Translator) -> list[str]:
    """Render a download record from :func:`download_record` as report lines.

    The notes and the error come from *record*, already translated; the
    ``saved:`` details and the fallback error are translated by *t*. A
    ``file:`` line with the record's ``file_url``, if any, follows the
    ``saved:`` line. The ``note:`` / ``saved:`` / ``file:`` / ``error:``
    prefixes stay in English.
    """
    url = record["url"]
    notes = cast("list[str]", record["notes"])
    lines = [f"note: {url}: {note}" for note in notes]
    if record["ok"]:
        saved = t.gettext("{url} -> {path} ({size} bytes)").format(
            url=url, path=record["path"], size=record["bytes"]
        )
        lines.append(f"saved: {saved}")
        file_url = record.get("file_url")
        if file_url:
            lines.append(f"file: {file_url}")
    else:
        error = record["error"] or t.gettext("fetch failed: {url}").format(url=url)
        lines.append(f"error: {error}")
    return lines
