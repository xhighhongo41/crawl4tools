"""Turn engine :class:`FetchOutcome` objects into MCP-tool-facing results.

Pure formatting/validation helpers shared by every crawl4mcp tool: URL list
checking, download-path resolution, human-readable content blocks, and the
structured metadata/records returned alongside them. No network or MCP
server code lives here.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING, cast

from mcp.types import ImageContent, TextContent

from crawl4tools.cli.report import error_line
from crawl4tools.engine.models import ContentKind, FetchOutcome
from crawl4tools.engine.naming import dedupe_urls, validate_url

if TYPE_CHECKING:
    from collections.abc import Sequence


def check_urls(urls: Sequence[str], max_urls: int) -> tuple[list[str], list[str]]:
    """Validate, dedupe, and cap a list of URLs from a tool call.

    Returns:
        A ``(unique, duplicates)`` pair, both in first-seen order.

    Raises:
        ValueError: if *urls* is empty, contains one or more invalid URLs
            (all of them are named in the message), or has more unique
            URLs than *max_urls*.
    """
    if not urls:
        raise ValueError("at least one URL is required")

    validated: list[str] = []
    invalid: list[str] = []
    for url in urls:
        try:
            validated.append(validate_url(url))
        except ValueError:
            invalid.append(url)
    if invalid:
        raise ValueError(f"invalid URL(s): {', '.join(invalid)}")

    unique, duplicates = dedupe_urls(validated)
    if len(unique) > max_urls:
        raise ValueError(f"too many URLs: {len(unique)} given, at most {max_urls} per call")
    return unique, duplicates


def resolve_directory(root: Path, directory: str | None) -> Path:
    """Resolve *directory* (relative to *root*) for a download call.

    *root* is the configured download root. *directory* is an optional,
    caller-supplied subdirectory; it must stay inside *root*. Symlinks that
    would otherwise escape *root* are rejected because ``Path.resolve()``
    follows them before the containment check runs. This function never
    creates directories on disk.

    Raises:
        ValueError: if *directory* is absolute, or resolves outside *root*.
    """
    resolved_root = root.resolve()
    if not directory:
        return resolved_root

    candidate = Path(directory)
    if candidate.is_absolute():
        raise ValueError(f"directory must be a relative path: {directory}")

    resolved = (root / directory).resolve()
    if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
        raise ValueError(f"directory must stay inside the download root: {directory}")
    return resolved


def _header_lines(outcome: FetchOutcome, url: str, *, multiple: bool) -> list[str]:
    """Build the ``<!-- ... -->`` comment lines prefixed to a rendered block."""
    lines: list[str] = []
    if multiple:
        status = outcome.status_code if outcome.status_code is not None else "-"
        lines.append(f"<!-- crawl4tools: url={url} status={status} -->")
    lines.extend(f"<!-- note: {note} -->" for note in outcome.notes)
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
    outcome: FetchOutcome, url: str, *, multiple: bool
) -> list[TextContent | ImageContent]:
    """Render one URL's outcome as human-readable MCP content blocks.

    *multiple* controls whether a ``url=... status=...`` header comment is
    added, which is only useful when several URLs are rendered together.
    """
    header_lines = _header_lines(outcome, url, multiple=multiple)

    if not outcome.ok:
        return [TextContent(type="text", text=_with_header(header_lines, error_line(outcome)))]

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
    content_type_display = outcome.content_type or "unknown type"
    message = (
        f"binary content ({content_type_display}, {data_len} bytes) was not included; "
        f"use the download tool to save it: {url}"
    )
    return [TextContent(type="text", text=_with_header(header_lines, message))]


def page_meta(outcome: FetchOutcome, url: str) -> dict[str, object]:
    """Return the structured metadata for one URL's outcome."""
    return {
        "url": url,
        "final_url": outcome.final_url,
        "ok": outcome.ok,
        "status_code": outcome.status_code,
        "content_kind": str(outcome.content_kind),
        "content_type": outcome.content_type,
        "chars": len(outcome.text) if outcome.text is not None else None,
        "bytes": len(outcome.data) if outcome.data is not None else None,
        "error": str(outcome.error) if outcome.error is not None else None,
        "notes": list(outcome.notes),
    }


def download_record(
    outcome: FetchOutcome, url: str, path: Path | None, error: str | None = None
) -> dict[str, object]:
    """Return the structured record for one URL's download attempt.

    *path* is the path the payload was written to, or None if it was not
    written. *error* overrides ``outcome.error`` when the failure happened
    while writing the file (rather than while fetching).
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
        resolved_error = str(outcome.error)
    return {
        "url": url,
        "ok": ok,
        "path": str(path) if path is not None else None,
        "bytes": payload_bytes,
        "content_kind": str(outcome.content_kind),
        "content_type": outcome.content_type,
        "status_code": outcome.status_code,
        "error": resolved_error,
        "notes": list(outcome.notes),
    }


def download_lines(record: dict[str, object]) -> list[str]:
    """Render a download record from :func:`download_record` as report lines."""
    url = record["url"]
    notes = cast("list[str]", record["notes"])
    lines = [f"note: {url}: {note}" for note in notes]
    if record["ok"]:
        lines.append(f"saved: {url} -> {record['path']} ({record['bytes']} bytes)")
    else:
        error = record["error"] or f"fetch failed: {url}"
        lines.append(f"error: {error}")
    return lines
