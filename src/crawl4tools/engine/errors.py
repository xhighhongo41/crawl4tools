"""Exception hierarchy for fetch failures.

Every error carries a :class:`~crawl4tools.engine.models.FailureKind` so
callers can decide on retries/fallback without parsing message text, and a
user-facing ``str()`` message that the CLI can print directly (prefixed
with "error: " by the caller).
"""

from __future__ import annotations

import re
from http import HTTPStatus

from crawl4tools.engine.models import FailureKind
from crawl4tools.engine.proxy import redact_proxy

_NET_ERROR_RE = re.compile(r"net::ERR_[A-Z0-9_]+")
# Wrapper lines crawl4ai puts in front of the actual Playwright error.
_WRAPPER_PREFIXES = ("unexpected error in ", "error: failed on navigating")


def summarize_detail(detail: str) -> str:
    """Pick the most informative single line from a raw crawl4ai error text.

    crawl4ai wraps Playwright errors in several lines of its own (source
    location, code context), so the first line is rarely useful. Prefer a
    Chromium ``net::ERR_*`` code, then the first line that is not one of
    crawl4ai's wrapper lines, then the first line.
    """
    match = _NET_ERROR_RE.search(detail)
    if match:
        return match.group(0)
    lines = [line.strip() for line in detail.splitlines() if line.strip()]
    for line in lines:
        if not line.lower().startswith(_WRAPPER_PREFIXES):
            return line
    return lines[0] if lines else ""


class FetchError(Exception):
    """Base class for all fetch failures.

    Subclasses override ``kind`` and ``__str__`` to provide a specific
    classification and a specific user-facing message.
    """

    kind: FailureKind = FailureKind.OTHER

    def __init__(self, url: str, detail: str | None = None) -> None:
        self.url = url
        self.detail = detail
        super().__init__(url, detail)

    def __str__(self) -> str:
        summary = summarize_detail(self.detail) if self.detail else ""
        if summary:
            return f"fetch failed: {summary}: {self.url}"
        return f"fetch failed: {self.url}"


class HttpStatusError(FetchError):
    """The server responded with a 4xx/5xx HTTP status code."""

    kind = FailureKind.HTTP_STATUS

    def __init__(self, url: str, status_code: int, reason: str | None = None) -> None:
        self.status_code = status_code
        if reason is None:
            try:
                reason = HTTPStatus(status_code).phrase
            except ValueError:
                # Unknown/non-standard status code: omit the reason phrase.
                reason = None
        self.reason = reason
        super().__init__(url, detail=reason)

    def __str__(self) -> str:
        if self.reason:
            return f"HTTP {self.status_code} {self.reason}: {self.url}"
        return f"HTTP {self.status_code}: {self.url}"


class NameResolutionError(FetchError):
    """The host name could not be resolved."""

    kind = FailureKind.NAME_RESOLUTION

    def __str__(self) -> str:
        return f"could not resolve host: {self.url}"


class ConnectionRefusedFetchError(FetchError):
    """The remote host actively refused the connection."""

    kind = FailureKind.CONNECTION_REFUSED

    def __str__(self) -> str:
        return f"connection refused: {self.url}"


class FetchTimeoutError(FetchError):
    """The fetch did not complete within the configured timeout."""

    kind = FailureKind.TIMEOUT

    def __init__(self, url: str, timeout_s: float, detail: str | None = None) -> None:
        self.timeout_s = timeout_s
        super().__init__(url, detail=detail)

    def __str__(self) -> str:
        return f"timed out after {self.timeout_s:g}s: {self.url}"


class ProxyFetchError(FetchError):
    """The configured proxy could not be used to reach the URL.

    Only the redacted form of the proxy is ever stored, so credentials
    cannot leak through ``str()``, logging, or any other representation.
    """

    kind = FailureKind.PROXY

    def __init__(self, url: str, proxy: str, detail: str | None = None) -> None:
        self.proxy = redact_proxy(proxy)
        super().__init__(url, detail=detail)

    def __str__(self) -> str:
        return f"proxy connection failed ({self.proxy}): {self.url}"


class BrowserNotInstalledError(FetchError):
    """The underlying browser engine is not installed."""

    kind = FailureKind.BROWSER_NOT_INSTALLED

    def __str__(self) -> str:
        return (
            "browser is not installed. Run 'playwright install chromium' "
            "(or 'crawl4ai-setup') and retry."
        )


class NonHtmlContentError(FetchError):
    """The URL points at content that is not a fetchable web page."""

    kind = FailureKind.NON_HTML

    def __str__(self) -> str:
        return f"content is not a web page and could not be downloaded: {self.url}"
