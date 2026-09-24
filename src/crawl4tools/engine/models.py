"""Data models shared across the fetch engine.

These are plain data containers (enums and dataclasses) with no I/O of
their own. Keeping them free of behavior makes them safe to import from
every other engine module without risking circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only needed for the type checker: importing errors at runtime would
    # create a cycle, since errors.py imports FailureKind from this module.
    from crawl4tools.engine.errors import FetchError


class OutputFormat(StrEnum):
    """The output format requested for a fetch."""

    MARKDOWN = "markdown"
    HTML = "html"
    PDF = "pdf"
    SCREENSHOT = "screenshot"
    MHTML = "mhtml"
    RAW = "raw"

    @property
    def is_text(self) -> bool:
        """Return True for formats whose payload is text, not binary."""
        return self in (OutputFormat.MARKDOWN, OutputFormat.HTML, OutputFormat.MHTML)


class ContentKind(StrEnum):
    """The kind of content actually retrieved for a fetch."""

    HTML = "html"
    PDF = "pdf"
    BINARY = "binary"


class FailureKind(StrEnum):
    """A coarse classification of why a fetch failed."""

    HTTP_STATUS = "http_status"
    NAME_RESOLUTION = "name_resolution"
    CONNECTION_REFUSED = "connection_refused"
    TIMEOUT = "timeout"
    PROXY = "proxy"
    BROWSER_NOT_INSTALLED = "browser_not_installed"
    NON_HTML = "non_html"
    OTHER = "other"


@dataclass(frozen=True)
class FetchOptions:
    """User-configurable options that control how a URL is fetched."""

    format: OutputFormat = OutputFormat.MARKDOWN
    proxy: str | None = None
    fallback: bool = True
    timeout_s: float = 60.0
    citations: bool = False
    ignore_links: bool = False
    ignore_images: bool = False
    fit: bool = False
    verbose: bool = False


@dataclass
class FetchOutcome:
    """The result of attempting to fetch a single URL."""

    url: str
    ok: bool
    final_url: str | None = None
    status_code: int | None = None
    content_kind: ContentKind = ContentKind.HTML
    content_type: str | None = None
    text: str | None = None
    data: bytes | None = None
    suggested_extension: str = ".md"
    error: FetchError | None = None
    notes: list[str] = field(default_factory=list)
    # The page's <title>, when the fetch produced one (HTML pages only).
    title: str | None = None
