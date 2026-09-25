import pytest

from crawl4tools.engine.classify import (
    build_error,
    classify_error_message,
    error_for_status,
)
from crawl4tools.engine.errors import (
    BlockedFetchError,
    BrowserNotInstalledError,
    ConnectionRefusedFetchError,
    FetchError,
    FetchTimeoutError,
    HttpStatusError,
    NameResolutionError,
    NonHtmlContentError,
    ProxyFetchError,
    TlsFetchError,
)
from crawl4tools.engine.models import FailureKind

URL = "https://example.com/page"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Executable doesn't exist at /path/to/chromium", FailureKind.BROWSER_NOT_INSTALLED),
        (
            "Please run the following command to download new browsers:\nplaywright install",
            FailureKind.BROWSER_NOT_INSTALLED,
        ),
        ("BrowserType.launch: Executable doesn't exist", FailureKind.BROWSER_NOT_INSTALLED),
        ("net::ERR_PROXY_CONNECTION_FAILED at https://x", FailureKind.PROXY),
        ("net::ERR_TUNNEL_CONNECTION_FAILED", FailureKind.PROXY),
        ("net::ERR_SOCKS_CONNECTION_FAILED", FailureKind.PROXY),
        ("net::ERR_NAME_NOT_RESOLVED", FailureKind.NAME_RESOLUTION),
        ("net::ERR_NAME_RESOLUTION_FAILED", FailureKind.NAME_RESOLUTION),
        ("net::ERR_CONNECTION_REFUSED", FailureKind.CONNECTION_REFUSED),
        ("net::ERR_TIMED_OUT", FailureKind.TIMEOUT),
        ("net::ERR_CONNECTION_TIMED_OUT", FailureKind.TIMEOUT),
        ("Timeout 30000ms exceeded.", FailureKind.TIMEOUT),
        ("the operation timed out", FailureKind.TIMEOUT),
        ("net::ERR_ABORTED", FailureKind.NON_HTML),
        ("Download is starting", FailureKind.NON_HTML),
        ("some completely unrecognized error", FailureKind.OTHER),
        (None, FailureKind.OTHER),
        ("", FailureKind.OTHER),
    ],
    ids=[
        "browser-executable-missing",
        "browser-playwright-install",
        "browser-launch-executable",
        "proxy-connection-failed",
        "proxy-tunnel-failed",
        "proxy-socks-failed",
        "name-not-resolved",
        "name-resolution-failed",
        "connection-refused",
        "timed-out-code",
        "connection-timed-out-code",
        "timeout-ms-exceeded-regex",
        "generic-timed-out-text",
        "aborted",
        "download-starting",
        "unrecognized",
        "none",
        "empty",
    ],
)
def test_classify_error_message(message: str | None, expected: FailureKind) -> None:
    assert classify_error_message(message) is expected


def test_classify_order_proxy_before_timeout() -> None:
    # Both a proxy pattern and a timeout pattern appear; PROXY must win
    # because it is checked earlier in the priority order.
    message = "net::ERR_PROXY_CONNECTION_FAILED, the request timed out"
    assert classify_error_message(message) is FailureKind.PROXY


def test_classify_order_browser_before_proxy() -> None:
    message = "playwright install required, then net::ERR_PROXY_CONNECTION_FAILED"
    assert classify_error_message(message) is FailureKind.BROWSER_NOT_INSTALLED


def test_classify_is_case_insensitive() -> None:
    assert classify_error_message("NET::ERR_NAME_NOT_RESOLVED") is FailureKind.NAME_RESOLUTION


@pytest.mark.parametrize(
    "message",
    [
        "Unexpected error in _crawl_web at line 700\nError: Failed on navigating ACS-GOTO:\n"
        "Page.goto: net::ERR_SSL_PROTOCOL_ERROR at https://example.com/page",
        "net::ERR_CERT_AUTHORITY_INVALID at https://x",
        "net::ERR_BAD_SSL_CLIENT_AUTH_CERT",
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed (_ssl.c:1000)",
    ],
    ids=["browser-ssl-wrapped", "browser-cert", "browser-client-cert", "httpx-verify"],
)
def test_classify_tls_messages(message: str) -> None:
    assert classify_error_message(message) is FailureKind.TLS


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("net::ERR_PROXY_CERTIFICATE_INVALID at https://x", FailureKind.PROXY),
        (
            "net::ERR_TUNNEL_CONNECTION_FAILED after [SSL: CERTIFICATE_VERIFY_FAILED]",
            FailureKind.PROXY,
        ),
        ("net::ERR_SSL_PROTOCOL_ERROR, the request timed out", FailureKind.TLS),
        ("net::ERR_SSL_PROTOCOL_ERROR after Timeout 30000ms exceeded.", FailureKind.TLS),
        ("net::ERR_SSL_PROTOCOL_ERROR then net::ERR_NAME_NOT_RESOLVED", FailureKind.TLS),
        ("net::ERR_SSL_PROTOCOL_ERROR then net::ERR_CONNECTION_REFUSED", FailureKind.TLS),
        ("net::ERR_ABORTED after net::ERR_CERT_DATE_INVALID", FailureKind.TLS),
        ("playwright install, then net::ERR_SSL_PROTOCOL_ERROR", FailureKind.BROWSER_NOT_INSTALLED),
    ],
    ids=[
        "proxy-certificate-stays-proxy",
        "proxy-before-tls",
        "tls-before-timed-out",
        "tls-before-timeout-regex",
        "tls-before-name-resolution",
        "tls-before-connection-refused",
        "tls-before-non-html",
        "browser-before-tls",
    ],
)
def test_classify_order_around_tls(message: str, expected: FailureKind) -> None:
    assert classify_error_message(message) is expected


@pytest.mark.parametrize(
    ("kind", "kwargs", "expected_type"),
    [
        (FailureKind.NAME_RESOLUTION, {}, NameResolutionError),
        (FailureKind.CONNECTION_REFUSED, {}, ConnectionRefusedFetchError),
        (FailureKind.TIMEOUT, {"timeout_s": 30.0}, FetchTimeoutError),
        (FailureKind.PROXY, {"proxy": "http://proxy.example:8080"}, ProxyFetchError),
        (FailureKind.TLS, {}, TlsFetchError),
        (FailureKind.BLOCKED, {"status_code": 403}, BlockedFetchError),
        (FailureKind.BROWSER_NOT_INSTALLED, {}, BrowserNotInstalledError),
        (FailureKind.NON_HTML, {}, NonHtmlContentError),
        (FailureKind.OTHER, {}, FetchError),
    ],
    ids=[
        "name-resolution",
        "connection-refused",
        "timeout",
        "proxy",
        "tls",
        "blocked",
        "browser-not-installed",
        "non-html",
        "other",
    ],
)
def test_build_error_maps_kind_to_type(
    kind: FailureKind, kwargs: dict[str, object], expected_type: type[FetchError]
) -> None:
    error = build_error(URL, kind, **kwargs)  # type: ignore[arg-type]
    assert type(error) is expected_type
    assert error.kind is expected_type.kind


def test_build_error_http_status_requires_status_code() -> None:
    with pytest.raises(ValueError, match="status_code"):
        build_error(URL, FailureKind.HTTP_STATUS)


def test_build_error_http_status_builds_http_status_error() -> None:
    error = build_error(URL, FailureKind.HTTP_STATUS, status_code=404)
    assert isinstance(error, HttpStatusError)
    assert error.status_code == 404


def test_build_error_tls_keeps_the_detail() -> None:
    error = build_error(URL, FailureKind.TLS, detail="net::ERR_SSL_PROTOCOL_ERROR at x")
    assert isinstance(error, TlsFetchError)
    assert error.detail == "net::ERR_SSL_PROTOCOL_ERROR at x"


def test_build_error_tls_does_not_need_a_proxy() -> None:
    error = build_error(URL, FailureKind.TLS, proxy="http://proxy.example:8080")
    assert type(error) is TlsFetchError


def test_build_error_blocked_requires_status_code() -> None:
    with pytest.raises(ValueError, match="status_code"):
        build_error(URL, FailureKind.BLOCKED)


def test_build_error_blocked_keeps_the_status_code() -> None:
    error = build_error(URL, FailureKind.BLOCKED, status_code=403)
    assert isinstance(error, BlockedFetchError)
    assert error.status_code == 403
    assert error.url == URL


def test_build_error_proxy_without_proxy_falls_back_to_base_error() -> None:
    error = build_error(URL, FailureKind.PROXY, detail="proxy failed")
    assert type(error) is FetchError
    assert error.detail == "proxy failed"


@pytest.mark.parametrize(
    ("status_code", "expect_error"),
    [
        (None, False),
        (200, False),
        (301, False),
        (399, False),
        (400, True),
        (404, True),
        (500, True),
    ],
)
def test_error_for_status(status_code: int | None, expect_error: bool) -> None:
    error = error_for_status(URL, status_code)
    if expect_error:
        assert isinstance(error, HttpStatusError)
        assert error.status_code == status_code
    else:
        assert error is None
