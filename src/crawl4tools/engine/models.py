"""Data models shared across the fetch engine.

These are plain data containers (enums and dataclasses) with no I/O of
their own. Apart from rendering a :class:`Note`, they have no behavior and
import nothing from the engine (only :mod:`crawl4tools.i18n`, which never
imports the engine), so every other engine module can import them without
risking circular imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from crawl4tools.i18n import ENGLISH, Translator, format_message

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


@dataclass(frozen=True)
class Note:
    """A note about a fetch that succeeded with a caveat.

    ``template`` is an English ``str.format`` template marked with
    :func:`~crawl4tools.i18n.N_`; ``params`` fill its ``{name}`` fields. A
    parameter may itself be localized (a fetch error), and is then rendered
    in the same language as the note. ``str(note)`` is the English message.
    """

    template: str
    params: Mapping[str, object] = field(default_factory=dict)

    def render(self, t: Translator) -> str:
        """Return the note with the template translated by ``t``."""
        return format_message(t, self.template, self.params)

    def __str__(self) -> str:
        return self.render(ENGLISH)


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
    notes: list[Note] = field(default_factory=list)
    # The page's <title>, when the fetch produced one (HTML pages only).
    title: str | None = None
