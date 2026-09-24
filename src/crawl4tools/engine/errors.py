"""Exception hierarchy for fetch failures.

Every error carries a :class:`~crawl4tools.engine.models.FailureKind` so
callers can decide on retries/fallback without parsing message text, and a
structured user-facing message: an English ``template`` (marked with
:func:`~crawl4tools.i18n.N_`) and the ``params`` that fill it. ``render(t)``
gives the message in the language of ``t``; ``str()`` gives it in English
(for logs), and the CLI prints it prefixed with "error: ". Text that comes
from outside (a crawl4ai error summary, an HTTP reason phrase) is a
parameter and stays untranslated.
"""

from __future__ import annotations

import re
from http import HTTPStatus

from crawl4tools.engine.models import FailureKind
from crawl4tools.engine.proxy import redact_proxy
from crawl4tools.i18n import ENGLISH, N_, Translator, format_message

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

    Subclasses override ``kind`` and the ``template`` / ``params``
    properties to provide a specific classification and a specific
    user-facing message; ``render`` and ``str()`` are shared.
    """

    kind: FailureKind = FailureKind.OTHER

    def __init__(self, url: str, detail: str | None = None) -> None:
        self.url = url
        self.detail = detail
        super().__init__(url, detail)

    def _summary(self) -> str:
        """Return the one-line summary of ``detail``, or "" when there is none."""
        return summarize_detail(self.detail) if self.detail else ""

    @property
    def template(self) -> str:
        """The English ``str.format`` template of the message."""
        if self._summary():
            return N_("fetch failed: {summary}: {url}")
        return N_("fetch failed: {url}")

    @property
    def params(self) -> dict[str, object]:
        """The values for the fields of :attr:`template`."""
        summary = self._summary()
        if summary:
            return {"summary": summary, "url": self.url}
        return {"url": self.url}

    def render(self, t: Translator) -> str:
        """Return the message with the template translated by ``t``."""
        return format_message(t, self.template, self.params)

    def __str__(self) -> str:
        return self.render(ENGLISH)


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

    @property
    def template(self) -> str:
        """The English ``str.format`` template of the message."""
        if self.reason:
            return N_("HTTP {status_code} {reason}: {url}")
        return N_("HTTP {status_code}: {url}")

    @property
    def params(self) -> dict[str, object]:
        """The values for the fields of :attr:`template`."""
        if self.reason:
            return {"status_code": self.status_code, "reason": self.reason, "url": self.url}
        return {"status_code": self.status_code, "url": self.url}


class NameResolutionError(FetchError):
    """The host name could not be resolved."""

    kind = FailureKind.NAME_RESOLUTION

    @property
    def template(self) -> str:
        """The English ``str.format`` template of the message."""
        return N_("could not resolve host: {url}")

    @property
    def params(self) -> dict[str, object]:
        """The values for the fields of :attr:`template`."""
        return {"url": self.url}


class ConnectionRefusedFetchError(FetchError):
    """The remote host actively refused the connection."""

    kind = FailureKind.CONNECTION_REFUSED

    @property
    def template(self) -> str:
        """The English ``str.format`` template of the message."""
        return N_("connection refused: {url}")

    @property
    def params(self) -> dict[str, object]:
        """The values for the fields of :attr:`template`."""
        return {"url": self.url}


class FetchTimeoutError(FetchError):
    """The fetch did not complete within the configured timeout."""

    kind = FailureKind.TIMEOUT

    def __init__(self, url: str, timeout_s: float, detail: str | None = None) -> None:
        self.timeout_s = timeout_s
        super().__init__(url, detail=detail)

    @property
    def template(self) -> str:
        """The English ``str.format`` template of the message."""
        return N_("timed out after {timeout_s:g}s: {url}")

    @property
    def params(self) -> dict[str, object]:
        """The values for the fields of :attr:`template`."""
        return {"timeout_s": self.timeout_s, "url": self.url}


class ProxyFetchError(FetchError):
    """The configured proxy could not be used to reach the URL.

    Only the redacted form of the proxy is ever stored, so credentials
    cannot leak through ``str()``, ``render()``, logging, or any other
    representation.
    """

    kind = FailureKind.PROXY

    def __init__(self, url: str, proxy: str, detail: str | None = None) -> None:
        self.proxy = redact_proxy(proxy)
        super().__init__(url, detail=detail)

    @property
    def template(self) -> str:
        """The English ``str.format`` template of the message."""
        return N_("proxy connection failed ({proxy}): {url}")

    @property
    def params(self) -> dict[str, object]:
        """The values for the fields of :attr:`template`."""
        return {"proxy": self.proxy, "url": self.url}


class BrowserNotInstalledError(FetchError):
    """The underlying browser engine is not installed."""

    kind = FailureKind.BROWSER_NOT_INSTALLED

    @property
    def template(self) -> str:
        """The English ``str.format`` template of the message."""
        return N_(
            "browser is not installed. Run 'playwright install chromium' "
            "(or 'crawl4ai-setup') and retry."
        )

    @property
    def params(self) -> dict[str, object]:
        """The values for the fields of :attr:`template` (it has none)."""
        return {}


class NonHtmlContentError(FetchError):
    """The URL points at content that is not a fetchable web page."""

    kind = FailureKind.NON_HTML

    @property
    def template(self) -> str:
        """The English ``str.format`` template of the message."""
        return N_("content is not a web page and could not be downloaded: {url}")

    @property
    def params(self) -> dict[str, object]:
        """The values for the fields of :attr:`template`."""
        return {"url": self.url}
