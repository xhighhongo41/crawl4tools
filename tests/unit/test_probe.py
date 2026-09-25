from __future__ import annotations

import httpx
import pytest
from conftest import FakeHttp

from crawl4tools.engine.models import FailureKind
from crawl4tools.engine.probe import ProbeResult, default_http_client, probe

URL = "https://example.com/doc"


async def test_default_http_client_ignores_environment_and_follows_redirects() -> None:
    client = default_http_client(None, 12.5)
    try:
        assert client.trust_env is False
        assert client.follow_redirects is True
        assert client.timeout.read == 12.5
        assert "Chrome" in client.headers["User-Agent"]
    finally:
        await client.aclose()


async def test_default_http_client_ignores_proxy_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.invalid:9999")
    monkeypatch.setenv("HTTP_PROXY", "http://env-proxy.invalid:9999")
    client = default_http_client(None, 5.0)
    try:
        assert client.trust_env is False
        assert not client._mounts
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("content_type", "media_type", "is_html"),
    [
        (None, None, True),
        ("text/html; charset=utf-8", "text/html", True),
        ("Application/XHTML+XML", "application/xhtml+xml", True),
        ("application/pdf", "application/pdf", False),
        ("image/jpeg", "image/jpeg", False),
        ("", None, True),
    ],
)
def test_media_type_and_is_html(
    content_type: str | None, media_type: str | None, is_html: bool
) -> None:
    result = ProbeResult(status_code=200, content_type=content_type)
    assert result.media_type == media_type
    assert result.is_html is is_html


@pytest.mark.parametrize(
    ("result", "ok"),
    [
        (ProbeResult(status_code=200), True),
        (ProbeResult(status_code=204), True),
        (ProbeResult(status_code=301), False),
        (ProbeResult(status_code=404), False),
        (ProbeResult(status_code=None), False),
        (ProbeResult(skipped=True), False),
        (ProbeResult(status_code=200, error_kind=FailureKind.OTHER), False),
    ],
)
def test_ok(result: ProbeResult, ok: bool) -> None:
    assert result.ok is ok


async def test_html_body_is_not_read() -> None:
    http = FakeHttp()
    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=http)
    assert result.ok
    assert result.is_html
    assert result.body is None
    assert result.status_code == 200
    assert result.final_url == URL
    assert http.client_calls == [(None, 3.0)]


async def test_non_html_body_is_read(sample_pdf: bytes) -> None:
    http = FakeHttp(
        lambda request: httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=sample_pdf
        )
    )
    result = await probe(URL, proxy="http://proxy.example:8080", timeout_s=3.0, client_factory=http)
    assert result.ok
    assert result.media_type == "application/pdf"
    assert result.body == sample_pdf
    assert http.proxies == ["http://proxy.example:8080"]


async def test_error_status_non_html_body_not_read_unless_requested() -> None:
    http = FakeHttp(
        lambda request: httpx.Response(404, headers={"content-type": "text/plain"}, text="nope")
    )
    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=http)
    assert result.status_code == 404
    assert not result.ok
    assert result.body is None

    forced = await probe(URL, proxy=None, timeout_s=3.0, client_factory=http, read_body=True)
    assert forced.body == b"nope"


async def test_read_body_forces_html_read() -> None:
    http = FakeHttp()
    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=http, read_body=True)
    assert result.body is not None and b"Hello" in result.body


async def test_redirect_records_final_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/doc":
            return httpx.Response(302, headers={"location": "https://example.com/final"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"PNG")

    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=FakeHttp(handler))
    assert result.final_url == "https://example.com/final"
    assert result.body == b"PNG"


async def test_socks_proxy_is_skipped_without_network() -> None:
    http = FakeHttp()
    result = await probe(
        URL, proxy="socks5://proxy.example:1080", timeout_s=3.0, client_factory=http
    )
    assert result.skipped
    assert not result.ok
    assert http.client_calls == []


def _raiser(exc: Exception) -> FakeHttp:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return FakeHttp(handler)


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (httpx.ProxyError("proxy said no"), FailureKind.PROXY),
        (httpx.ReadTimeout("read timed out"), FailureKind.TIMEOUT),
        (httpx.ConnectTimeout("connect timed out"), FailureKind.TIMEOUT),
        (
            httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known"),
            FailureKind.NAME_RESOLUTION,
        ),
        (httpx.ConnectError("[Errno -2] Name or service not known"), FailureKind.NAME_RESOLUTION),
        (httpx.ConnectError("getaddrinfo failed"), FailureKind.NAME_RESOLUTION),
        (httpx.ConnectError("[Errno 61] Connection refused"), FailureKind.CONNECTION_REFUSED),
        (httpx.ConnectError("something else"), FailureKind.OTHER),
        (
            httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                "self-signed certificate in certificate chain (_ssl.c:1000)"
            ),
            FailureKind.TLS,
        ),
        (
            httpx.ConnectError("[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:1000)"),
            FailureKind.TLS,
        ),
        (
            httpx.ReadError("[SSL: SSLV3_ALERT_BAD_RECORD_MAC] sslv3 alert bad record mac"),
            FailureKind.TLS,
        ),
        (
            httpx.ProxyError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
            FailureKind.PROXY,
        ),
        (
            httpx.ConnectTimeout("_ssl.c:989: The handshake operation timed out"),
            FailureKind.TIMEOUT,
        ),
        (httpx.RemoteProtocolError("bad\nsecond line"), FailureKind.OTHER),
    ],
)
async def test_exceptions_are_mapped_not_raised(exc: Exception, kind: FailureKind) -> None:
    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=_raiser(exc))
    assert result.error_kind is kind
    assert result.error_detail == str(exc).splitlines()[0]
    assert not result.ok
    assert result.status_code is None


@pytest.mark.parametrize(
    ("exc", "proxy_status"),
    [
        (httpx.ProxyError("403 Forbidden"), 403),
        (httpx.ProxyError("502 Bad Gateway"), 502),
        (httpx.ProxyError("connection refused"), None),
        (httpx.ConnectError("[Errno 61] Connection refused"), None),
    ],
)
async def test_proxy_status_from_proxy_error(exc: Exception, proxy_status: int | None) -> None:
    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=_raiser(exc))
    assert result.proxy_status == proxy_status


async def test_proxy_status_defaults_to_none() -> None:
    assert ProbeResult().proxy_status is None
    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=FakeHttp())
    assert result.proxy_status is None


async def test_headers_are_kept_with_lowercase_names() -> None:
    http = FakeHttp(
        lambda request: httpx.Response(
            503,
            headers={"Content-Type": "text/html", "X-Squid-Error": "ERR_SECURE_CONNECT_FAIL 0"},
            text="<html></html>",
        )
    )
    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=http)
    assert result.headers["x-squid-error"] == "ERR_SECURE_CONNECT_FAIL 0"
    assert result.headers["content-type"] == "text/html"
    assert all(name == name.lower() for name in result.headers)


async def test_headers_of_the_final_response_are_kept() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/doc":
            return httpx.Response(
                302, headers={"location": "https://example.com/final", "x-hop": "first"}
            )
        return httpx.Response(200, headers={"CF-Mitigated": "challenge"}, text="x")

    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=FakeHttp(handler))
    assert result.headers["cf-mitigated"] == "challenge"
    assert "x-hop" not in result.headers


async def test_repeated_headers_are_joined() -> None:
    http = FakeHttp(
        lambda request: httpx.Response(200, headers=[("X-Test", "a"), ("x-test", "b")], text="x")
    )
    result = await probe(URL, proxy=None, timeout_s=3.0, client_factory=http)
    assert result.headers["x-test"] == "a, b"


async def test_headers_are_empty_without_a_response() -> None:
    result = await probe(
        URL, proxy=None, timeout_s=3.0, client_factory=_raiser(httpx.ConnectError("x"))
    )
    assert result.headers == {}
    assert ProbeResult().headers == {}
    assert ProbeResult(skipped=True).headers == {}
