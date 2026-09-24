"""Exception hierarchy for fetch failures.

Every error carries a :class:`~crawl4tools.engine.models.FailureKind` so
callers can decide on retries/fallback without parsing message text, and a
user-facing ``str()`` message that the CLI can print directly (prefixed
with "error: " by the caller).
"""

from __future__ import annotations

from http import HTTPStatus

from crawl4tools.engine.models import FailureKind
from crawl4tools.engine.proxy import redact_proxy


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
        if self.detail:
            first_line = self.detail.splitlines()[0]
            return f"fetch failed: {first_line}: {self.url}"
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
            "browser is not installed. Run 'crawl4ai-setup' "
            "(or 'python -m playwright install chromium') and retry."
        )


class NonHtmlContentError(FetchError):
    """The URL points at content that is not a fetchable web page."""

    kind = FailureKind.NON_HTML

    def __str__(self) -> str:
        return f"content is not a web page and could not be downloaded: {self.url}"
