"""URL validation and output filename derivation.

Turns a URL (plus the requested output format/content type) into a safe,
deterministic filename, and hands out collision-free paths within a
single run via :class:`NameAllocator`.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from crawl4tools.engine.models import OutputFormat

if TYPE_CHECKING:
    from collections.abc import Iterable

# Filesystem-hostile characters (Windows reserved characters plus the
# POSIX path separator) and C0 control characters.
_DANGEROUS_CHARS_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
_REPEATED_UNDERSCORE_RE = re.compile(r"_+")

_MAX_STEM_BYTES = 200

# Overrides for media types where mimetypes.guess_extension would pick a
# less common (but technically valid) extension, or none at all,
# depending on the platform's registered MIME types.
_RAW_MEDIA_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "text/html": ".html",
    "text/plain": ".txt",
    "application/pdf": ".pdf",
}

_FORMAT_EXTENSIONS = {
    OutputFormat.MARKDOWN: ".md",
    OutputFormat.HTML: ".html",
    OutputFormat.PDF: ".pdf",
    OutputFormat.SCREENSHOT: ".png",
    OutputFormat.MHTML: ".mhtml",
}


def validate_url(url: str) -> str:
    """Validate that *url* is an absolute http(s) URL.

    Returns:
        The stripped URL.

    Raises:
        ValueError: if the URL has no http/https scheme, or no host.
    """
    stripped = url.strip()
    parts = urlsplit(stripped)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {url}")
    return stripped


def dedupe_urls(urls: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split *urls* into first-seen unique URLs and dropped duplicates.

    Both lists preserve the order in which the URLs were encountered.
    """
    seen: set[str] = set()
    unique: list[str] = []
    duplicates: list[str] = []
    for url in urls:
        if url in seen:
            duplicates.append(url)
        else:
            seen.add(url)
            unique.append(url)
    return unique, duplicates


def extension_for(
    fmt: OutputFormat,
    *,
    content_type: str | None = None,
    url: str | None = None,
) -> str:
    """Return the filename extension (with leading dot) for *fmt*.

    For ``OutputFormat.RAW``, the extension is inferred from
    *content_type* when available, else from *url*'s path suffix, else
    falls back to ``.bin``.
    """
    fixed = _FORMAT_EXTENSIONS.get(fmt)
    if fixed is not None:
        return fixed
    return _extension_for_raw(content_type, url)


def _extension_for_raw(content_type: str | None, url: str | None) -> str:
    if content_type:
        media_type = content_type.split(";", 1)[0].strip().lower()
        override = _RAW_MEDIA_TYPE_EXTENSIONS.get(media_type)
        if override:
            return override
        guessed = mimetypes.guess_extension(media_type)
        if guessed:
            return guessed
    if url:
        suffix = PurePosixPath(urlsplit(url).path).suffix
        candidate = suffix[1:]
        if 1 <= len(candidate) <= 8 and candidate.isalnum():
            return suffix
    return ".bin"


def _sanitize(text: str) -> str:
    replaced = _DANGEROUS_CHARS_RE.sub("_", text)
    return _REPEATED_UNDERSCORE_RE.sub("_", replaced)


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """Truncate *text* to at most *max_bytes* UTF-8 bytes without splitting a char."""
    data = text.encode("utf-8")[:max_bytes]
    while data:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            data = data[:-1]
    return ""


def filename_for(url: str, extension: str) -> str:
    """Derive a safe, deterministic filename for *url*.

    The filename is built from the host (plus an explicit port), the
    percent-decoded path segments, and the query string, joined with
    underscores. Filesystem-hostile characters are replaced with
    underscores. If the resulting stem is too long, it is truncated and
    suffixed with a short hash of *url* so names stay both safe and
    reasonably unique.
    """
    parts = urlsplit(url)

    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}_{parts.port}"

    segments = [unquote(segment) for segment in parts.path.split("/") if segment]
    if segments and extension and segments[-1].endswith(extension):
        segments[-1] = segments[-1][: -len(extension)]

    components = [host, *segments]
    if parts.query:
        components.append(parts.query)

    stem = _sanitize("_".join(component for component in components if component))

    if len(stem.encode("utf-8")) > _MAX_STEM_BYTES:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
        stem = f"{_truncate_utf8(stem, _MAX_STEM_BYTES)}-{digest}"

    return f"{stem}{extension}"


class NameAllocator:
    """Hands out collision-free filenames within a directory for one run.

    Collisions are tracked only in memory (not on disk): repeated
    allocations of the same filename get a "-2", "-3", ... suffix
    inserted before the extension.
    """

    def __init__(self, directory: Path) -> None:
        """Initialize the allocator to hand out paths under *directory*."""
        self._directory = directory
        self._allocated: set[str] = set()

    def allocate(self, filename: str) -> Path:
        """Return a unique path under this allocator's directory for *filename*."""
        name = filename
        candidate = Path(filename)
        number = 1
        while name in self._allocated:
            number += 1
            name = f"{candidate.stem}-{number}{candidate.suffix}"
        self._allocated.add(name)
        return self._directory / name
