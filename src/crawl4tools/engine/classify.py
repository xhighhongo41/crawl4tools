"""Classification of raw crawl4ai/Playwright error text into FailureKind.

crawl4ai/Playwright surface most failures as plain error strings rather
than distinct exception types, so classification is pattern-based. Patterns
are kept in module-level tuples, grouped by kind, so new patterns can be
added without touching the matching logic.
"""

from __future__ import annotations

import re

from crawl4tools.engine.errors import (
    BrowserNotInstalledError,
    ConnectionRefusedFetchError,
    FetchError,
    FetchTimeoutError,
    HttpStatusError,
    NameResolutionError,
    NonHtmlContentError,
    ProxyFetchError,
)
from crawl4tools.engine.models import FailureKind

# Checked first: a missing browser install should be surfaced distinctly,
# even if its message also happens to mention a timeout or similar.
_BROWSER_NOT_INSTALLED_PATTERNS = (
    "executable doesn't exist",
    "playwright install",
    "browsertype.launch: executable",
)

_PROXY_PATTERNS = (
    "err_proxy_",
    "err_tunnel_connection_failed",
    "err_socks_connection_failed",
)

_NAME_RESOLUTION_PATTERNS = (
    "err_name_not_resolved",
    "err_name_resolution_failed",
)

_CONNECTION_REFUSED_PATTERNS = ("err_connection_refused",)

_TIMEOUT_PATTERNS = (
    "err_timed_out",
    "err_connection_timed_out",
    "timed out",
)
_TIMEOUT_REGEX = re.compile(r"timeout \d+ms exceeded")

_NON_HTML_PATTERNS = (
    "err_aborted",
    "download is starting",
)


def _matches_any(lowered_message: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in lowered_message for pattern in patterns)


def classify_error_message(message: str | None) -> FailureKind:
    """Classify a raw error message into a :class:`FailureKind`.

    Matching is case-insensitive and checks kinds in a fixed priority
    order, so a message matching multiple patterns resolves to the
    earliest-checked kind. Returns ``FailureKind.OTHER`` for ``None``,
    empty, or unrecognized messages.
    """
    if not message:
        return FailureKind.OTHER

    lowered = message.lower()

    if _matches_any(lowered, _BROWSER_NOT_INSTALLED_PATTERNS):
        return FailureKind.BROWSER_NOT_INSTALLED
    if _matches_any(lowered, _PROXY_PATTERNS):
        return FailureKind.PROXY
    if _matches_any(lowered, _NAME_RESOLUTION_PATTERNS):
        return FailureKind.NAME_RESOLUTION
    if _matches_any(lowered, _CONNECTION_REFUSED_PATTERNS):
        return FailureKind.CONNECTION_REFUSED
    if _matches_any(lowered, _TIMEOUT_PATTERNS) or _TIMEOUT_REGEX.search(lowered):
        return FailureKind.TIMEOUT
    if _matches_any(lowered, _NON_HTML_PATTERNS):
        return FailureKind.NON_HTML
    return FailureKind.OTHER


def build_error(
    url: str,
    kind: FailureKind,
    *,
    detail: str | None = None,
    status_code: int | None = None,
    timeout_s: float = 60.0,
    proxy: str | None = None,
) -> FetchError:
    """Build the concrete :class:`FetchError` subclass for *kind*.

    Raises:
        ValueError: if ``kind`` is ``HTTP_STATUS`` and ``status_code`` is
            not provided.
    """
    if kind is FailureKind.HTTP_STATUS:
        if status_code is None:
            raise ValueError("status_code is required to build an HTTP_STATUS error")
        return HttpStatusError(url, status_code)
    if kind is FailureKind.NAME_RESOLUTION:
        return NameResolutionError(url, detail)
    if kind is FailureKind.CONNECTION_REFUSED:
        return ConnectionRefusedFetchError(url, detail)
    if kind is FailureKind.TIMEOUT:
        return FetchTimeoutError(url, timeout_s, detail)
    if kind is FailureKind.PROXY:
        if proxy is None:
            # No proxy to name: fall back to the generic error rather than
            # constructing a ProxyFetchError with nothing to redact/report.
            return FetchError(url, detail)
        return ProxyFetchError(url, proxy, detail)
    if kind is FailureKind.BROWSER_NOT_INSTALLED:
        return BrowserNotInstalledError(url, detail)
    if kind is FailureKind.NON_HTML:
        return NonHtmlContentError(url, detail)
    return FetchError(url, detail)


def error_for_status(url: str, status_code: int | None) -> HttpStatusError | None:
    """Return an :class:`HttpStatusError` for a 4xx/5xx status, else None.

    crawl4ai treats 4xx/5xx HTTP responses as a successful fetch (it did
    get a response), so this check is ours to make on top of it.
    """
    if status_code is None or status_code < 400:
        return None
    return HttpStatusError(url, status_code)
