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

URL = "https://example.com/page"


def test_base_fetch_error_kind_is_other() -> None:
    assert FetchError.kind is FailureKind.OTHER


def test_base_fetch_error_message_with_detail() -> None:
    error = FetchError(URL, "boom\nmore context")
    assert str(error) == f"fetch failed: boom: {URL}"


def test_base_fetch_error_message_without_detail() -> None:
    assert str(FetchError(URL)) == f"fetch failed: {URL}"
    assert str(FetchError(URL, "")) == f"fetch failed: {URL}"


def test_http_status_error_known_code_uses_standard_phrase() -> None:
    error = HttpStatusError(URL, 404)
    assert error.kind is FailureKind.HTTP_STATUS
    assert error.status_code == 404
    assert error.reason == "Not Found"
    assert str(error) == f"HTTP 404 Not Found: {URL}"


def test_http_status_error_unknown_code_omits_reason() -> None:
    error = HttpStatusError(URL, 599)
    assert error.reason is None
    assert str(error) == f"HTTP 599: {URL}"


def test_http_status_error_explicit_reason_overrides_default() -> None:
    error = HttpStatusError(URL, 404, reason="Custom Reason")
    assert error.reason == "Custom Reason"
    assert str(error) == f"HTTP 404 Custom Reason: {URL}"


def test_name_resolution_error() -> None:
    error = NameResolutionError(URL)
    assert error.kind is FailureKind.NAME_RESOLUTION
    assert str(error) == f"could not resolve host: {URL}"


def test_connection_refused_error() -> None:
    error = ConnectionRefusedFetchError(URL)
    assert error.kind is FailureKind.CONNECTION_REFUSED
    assert str(error) == f"connection refused: {URL}"


def test_fetch_timeout_error_formats_seconds_with_g() -> None:
    error = FetchTimeoutError(URL, 60.0)
    assert error.kind is FailureKind.TIMEOUT
    assert str(error) == f"timed out after 60s: {URL}"


def test_fetch_timeout_error_keeps_fractional_seconds() -> None:
    error = FetchTimeoutError(URL, 2.5)
    assert str(error) == f"timed out after 2.5s: {URL}"


def test_proxy_fetch_error_redacts_credentials() -> None:
    error = ProxyFetchError(URL, "http://user:s3cr3t@proxy.example:8080")
    assert error.kind is FailureKind.PROXY
    message = str(error)
    assert "s3cr3t" not in message
    assert "user" not in message
    assert message == f"proxy connection failed (http://***@proxy.example:8080): {URL}"


def test_proxy_fetch_error_never_leaks_credentials_via_repr() -> None:
    error = ProxyFetchError(URL, "http://user:s3cr3t@proxy.example:8080")
    assert "s3cr3t" not in repr(error)
    assert "s3cr3t" not in str(error.args)
    assert error.proxy == "http://***@proxy.example:8080"


def test_browser_not_installed_error_has_no_url() -> None:
    error = BrowserNotInstalledError(URL)
    assert error.kind is FailureKind.BROWSER_NOT_INSTALLED
    message = str(error)
    assert URL not in message
    assert "crawl4ai-setup" in message
    assert "playwright install chromium" in message


def test_non_html_content_error() -> None:
    error = NonHtmlContentError(URL)
    assert error.kind is FailureKind.NON_HTML
    assert str(error) == f"content is not a web page and could not be downloaded: {URL}"


def test_all_errors_are_fetch_errors() -> None:
    for cls in (
        HttpStatusError,
        NameResolutionError,
        ConnectionRefusedFetchError,
        FetchTimeoutError,
        ProxyFetchError,
        BrowserNotInstalledError,
        NonHtmlContentError,
    ):
        assert issubclass(cls, FetchError)
