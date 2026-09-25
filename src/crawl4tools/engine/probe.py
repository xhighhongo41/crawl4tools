"""Lightweight HTTP probe used to detect non-HTML resources before the browser.

A plain HTTP GET (via httpx) is far cheaper than a browser navigation, and
lets PDFs, images and other downloads be fetched without starting Chromium
at all. The probe never raises: every failure is reported through
:class:`ProbeResult` so the caller can fall back to the browser.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from crawl4tools.engine.interception import is_tls_message
from crawl4tools.engine.models import FailureKind
from crawl4tools.engine.proxy import is_socks

HttpClientFactory = Callable[[str | None, float], httpx.AsyncClient]
"""Create an httpx client from ``(proxy URL or None, timeout in seconds)``."""

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})

_NAME_RESOLUTION_PATTERNS = (
    "name or service not known",
    "nodename nor servname",
    "getaddrinfo",
    "name resolution",
    "no address associated",
)


def default_http_client(proxy: str | None, timeout_s: float) -> httpx.AsyncClient:
    """Return the default httpx client used for probing and downloads.

    ``trust_env`` is disabled so HTTP_PROXY/HTTPS_PROXY environment
    variables are never picked up: the proxy is only what the user passed.
    """
    return httpx.AsyncClient(
        proxy=proxy,
        timeout=timeout_s,
        follow_redirects=True,
        trust_env=False,
        headers={"User-Agent": USER_AGENT},
    )


@dataclass
class ProbeResult:
    """What a single HTTP probe found out about a URL."""

    status_code: int | None = None
    content_type: str | None = None
    final_url: str | None = None
    body: bytes | None = None
    error_kind: FailureKind | None = None
    error_detail: str | None = None
    skipped: bool = False
    # The response headers, with lowercase names (empty when there was no response).
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def media_type(self) -> str | None:
        """Return the lowercased media type without parameters, or None."""
        if not self.content_type:
            return None
        media_type = self.content_type.split(";", 1)[0].strip().lower()
        return media_type or None

    @property
    def is_html(self) -> bool:
        """Return True if the response is (or may be) an HTML page."""
        media_type = self.media_type
        return media_type is None or media_type in _HTML_MEDIA_TYPES

    @property
    def ok(self) -> bool:
        """Return True for a completed, non-skipped probe with a 2xx status."""
        return (
            self.error_kind is None
            and not self.skipped
            and self.status_code is not None
            and 200 <= self.status_code < 300
        )


def _first_line(exc: BaseException) -> str:
    text = str(exc)
    lines = text.splitlines()
    return lines[0] if lines else type(exc).__name__


def _classify_exception(exc: Exception) -> FailureKind:
    if isinstance(exc, httpx.ProxyError):
        return FailureKind.PROXY
    if isinstance(exc, httpx.TimeoutException):
        return FailureKind.TIMEOUT
    lowered = str(exc).lower()
    # httpx reports a failed TLS handshake as a ConnectError, and a TLS failure
    # later in the exchange as another transport error; only the text tells.
    if is_tls_message(lowered):
        return FailureKind.TLS
    if isinstance(exc, httpx.ConnectError):
        if any(pattern in lowered for pattern in _NAME_RESOLUTION_PATTERNS):
            return FailureKind.NAME_RESOLUTION
        if "refused" in lowered:
            return FailureKind.CONNECTION_REFUSED
    return FailureKind.OTHER


async def probe(
    url: str,
    *,
    proxy: str | None,
    timeout_s: float,
    client_factory: HttpClientFactory = default_http_client,
    read_body: bool = False,
) -> ProbeResult:
    """Issue a GET for *url* and report status, content type and (maybe) body.

    The body is read only when *read_body* is set, or for a 2xx response
    that is not HTML (the caller will save it as-is). SOCKS proxies are not
    supported by the plain httpx install, so they skip the probe entirely.
    """
    if proxy is not None and is_socks(proxy):
        return ProbeResult(skipped=True)

    try:
        async with (
            client_factory(proxy, timeout_s) as client,
            client.stream("GET", url) as response,
        ):
            result = ProbeResult(
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                final_url=str(response.url),
                headers={name.lower(): value for name, value in response.headers.items()},
            )
            if read_body or (response.is_success and not result.is_html):
                result.body = await response.aread()
            return result
    except (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError) as exc:
        return ProbeResult(error_kind=_classify_exception(exc), error_detail=_first_line(exc))
