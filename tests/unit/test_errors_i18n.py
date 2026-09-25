"""Tests for the structured, translatable messages of the engine.

Fetch errors, notes and the URL/proxy validation errors keep their English
``str()`` and can also be rendered with any translator.
"""

from __future__ import annotations

import dataclasses
import gettext
from pathlib import Path

import pytest
from babel.messages.pofile import read_po

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
    ProxyRefusedError,
    TlsFetchError,
)
from crawl4tools.engine.models import Note
from crawl4tools.engine.naming import InvalidUrlError, validate_url
from crawl4tools.engine.proxy import ProxyUrlError, normalize_proxy
from crawl4tools.i18n import DOMAIN, ENGLISH, Localized, get_translator

URL = "https://example.com/page"
SECRET = "s3cr3t"
PROXY = f"http://user:{SECRET}@proxy.example:8080"
REDACTED_PROXY = "http://***@proxy.example:8080"
BROWSER_MESSAGE = (
    "browser is not installed. Run 'playwright install chromium' (or 'crawl4ai-setup') and retry."
)
PROXY_NOTE = "proxy failed ({error}); retried with a direct connection"

JA_PO = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "crawl4tools"
    / "locale"
    / "ja"
    / "LC_MESSAGES"
    / f"{DOMAIN}.po"
)


class FakeTranslator(gettext.NullTranslations):
    """A translator backed by a dict; unknown messages stay as they are."""

    def __init__(self, messages: dict[str, str]) -> None:
        super().__init__()
        self._messages = messages

    def gettext(self, message: str) -> str:
        return self._messages.get(message, message)


def tagged(template: str) -> FakeTranslator:
    """Return a translator that marks ``template`` (and only it) as translated."""
    return FakeTranslator({template: f"[xx] {template}"})


def catalog_ids() -> set[str]:
    with JA_PO.open("rb") as f:
        return {str(message.id) for message in read_po(f) if message.id}


# --- fetch errors ------------------------------------------------------------------

FETCH_ERROR_CASES = [
    pytest.param(
        FetchError(URL),
        "fetch failed: {url}",
        {"url": URL},
        f"fetch failed: {URL}",
        id="fetch-error-without-detail",
    ),
    pytest.param(
        FetchError(URL, ""),
        "fetch failed: {url}",
        {"url": URL},
        f"fetch failed: {URL}",
        id="fetch-error-empty-detail",
    ),
    pytest.param(
        FetchError(URL, "  \n\t\n"),
        "fetch failed: {url}",
        {"url": URL},
        f"fetch failed: {URL}",
        id="fetch-error-blank-detail",
    ),
    pytest.param(
        FetchError(URL, "boom\nmore context"),
        "fetch failed: {summary}: {url}",
        {"summary": "boom", "url": URL},
        f"fetch failed: boom: {URL}",
        id="fetch-error-with-summary",
    ),
    pytest.param(
        FetchError(URL, "Page.goto: net::ERR_UNSAFE_PORT at x"),
        "fetch failed: {summary}: {url}",
        {"summary": "net::ERR_UNSAFE_PORT", "url": URL},
        f"fetch failed: net::ERR_UNSAFE_PORT: {URL}",
        id="fetch-error-net-error-summary",
    ),
    pytest.param(
        HttpStatusError(URL, 404),
        "HTTP {status_code} {reason}: {url}",
        {"status_code": 404, "reason": "Not Found", "url": URL},
        f"HTTP 404 Not Found: {URL}",
        id="http-status-with-reason",
    ),
    pytest.param(
        HttpStatusError(URL, 599),
        "HTTP {status_code}: {url}",
        {"status_code": 599, "url": URL},
        f"HTTP 599: {URL}",
        id="http-status-unknown-code",
    ),
    pytest.param(
        HttpStatusError(URL, 404, reason=""),
        "HTTP {status_code}: {url}",
        {"status_code": 404, "url": URL},
        f"HTTP 404: {URL}",
        id="http-status-empty-reason",
    ),
    pytest.param(
        NameResolutionError(URL),
        "could not resolve host: {url}",
        {"url": URL},
        f"could not resolve host: {URL}",
        id="name-resolution",
    ),
    pytest.param(
        ConnectionRefusedFetchError(URL),
        "connection refused: {url}",
        {"url": URL},
        f"connection refused: {URL}",
        id="connection-refused",
    ),
    pytest.param(
        FetchTimeoutError(URL, 60.0),
        "timed out after {timeout_s:g}s: {url}",
        {"timeout_s": 60.0, "url": URL},
        f"timed out after 60s: {URL}",
        id="timeout-whole-seconds",
    ),
    pytest.param(
        FetchTimeoutError(URL, 2.5),
        "timed out after {timeout_s:g}s: {url}",
        {"timeout_s": 2.5, "url": URL},
        f"timed out after 2.5s: {URL}",
        id="timeout-fractional-seconds",
    ),
    pytest.param(
        ProxyFetchError(URL, PROXY),
        "proxy connection failed ({proxy}): {url}",
        {"proxy": REDACTED_PROXY, "url": URL},
        f"proxy connection failed ({REDACTED_PROXY}): {URL}",
        id="proxy",
    ),
    pytest.param(
        ProxyRefusedError(URL, PROXY, 403),
        "proxy refused the connection ({proxy}, HTTP {status}): {url}",
        {"proxy": REDACTED_PROXY, "status": 403, "url": URL},
        f"proxy refused the connection ({REDACTED_PROXY}, HTTP 403): {URL}",
        id="proxy-refused",
    ),
    pytest.param(
        TlsFetchError(URL),
        "TLS error: {url}",
        {"url": URL},
        f"TLS error: {URL}",
        id="tls-without-detail",
    ),
    pytest.param(
        TlsFetchError(URL, "Error: Failed on navigating\nnet::ERR_SSL_PROTOCOL_ERROR at x"),
        "TLS error: {summary}: {url}",
        {"summary": "net::ERR_SSL_PROTOCOL_ERROR", "url": URL},
        f"TLS error: net::ERR_SSL_PROTOCOL_ERROR: {URL}",
        id="tls-with-net-error-summary",
    ),
    pytest.param(
        TlsFetchError(URL, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"),
        "TLS error: {summary}: {url}",
        {"summary": "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", "url": URL},
        f"TLS error: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: {URL}",
        id="tls-with-httpx-summary",
    ),
    pytest.param(
        BlockedFetchError(URL, 403),
        "blocked by a bot challenge (HTTP {status_code}): {url}",
        {"status_code": 403, "url": URL},
        f"blocked by a bot challenge (HTTP 403): {URL}",
        id="blocked",
    ),
    pytest.param(
        BrowserNotInstalledError(URL),
        BROWSER_MESSAGE,
        {},
        BROWSER_MESSAGE,
        id="browser-not-installed",
    ),
    pytest.param(
        NonHtmlContentError(URL),
        "content is not a web page and could not be downloaded: {url}",
        {"url": URL},
        f"content is not a web page and could not be downloaded: {URL}",
        id="non-html",
    ),
]


@pytest.mark.parametrize(("error", "template", "params", "english"), FETCH_ERROR_CASES)
def test_fetch_error_template(
    error: FetchError, template: str, params: dict[str, object], english: str
) -> None:
    assert error.template == template


@pytest.mark.parametrize(("error", "template", "params", "english"), FETCH_ERROR_CASES)
def test_fetch_error_params(
    error: FetchError, template: str, params: dict[str, object], english: str
) -> None:
    assert error.params == params


@pytest.mark.parametrize(("error", "template", "params", "english"), FETCH_ERROR_CASES)
def test_fetch_error_str_is_the_english_rendering(
    error: FetchError, template: str, params: dict[str, object], english: str
) -> None:
    assert error.render(ENGLISH) == english
    assert str(error) == english


@pytest.mark.parametrize(("error", "template", "params", "english"), FETCH_ERROR_CASES)
def test_fetch_error_renders_the_translated_template(
    error: FetchError, template: str, params: dict[str, object], english: str
) -> None:
    assert error.render(tagged(template)) == f"[xx] {english}"


@pytest.mark.parametrize(("error", "template", "params", "english"), FETCH_ERROR_CASES)
def test_fetch_error_is_localized(
    error: FetchError, template: str, params: dict[str, object], english: str
) -> None:
    assert isinstance(error, Localized)


@pytest.mark.parametrize(("error", "template", "params", "english"), FETCH_ERROR_CASES)
def test_fetch_error_template_is_in_the_catalog(
    error: FetchError, template: str, params: dict[str, object], english: str
) -> None:
    # A template built at run time would never be extracted, so it could not be translated.
    assert error.template in catalog_ids()


def test_fetch_error_external_text_is_not_translated() -> None:
    error = HttpStatusError(URL, 404)
    t = FakeTranslator({"Not Found": "見つかりません", "boom": "どかん"})
    assert error.render(t) == f"HTTP 404 Not Found: {URL}"
    assert FetchError(URL, "boom").render(t) == f"fetch failed: boom: {URL}"


def test_fetch_error_values_are_not_parsed_as_template_fields() -> None:
    url = "https://example.com/{url}?q={0}"
    assert str(NameResolutionError(url)) == f"could not resolve host: {url}"
    assert str(FetchError(URL, "odd {detail}")) == f"fetch failed: odd {{detail}}: {URL}"


def test_proxy_fetch_error_params_never_hold_credentials() -> None:
    error = ProxyFetchError(URL, PROXY)
    assert SECRET not in repr(error.params)
    assert SECRET not in error.render(tagged(error.template))


def test_fetch_error_args_are_unchanged() -> None:
    assert HttpStatusError(URL, 404).args == (URL, "Not Found")
    assert FetchTimeoutError(URL, 5.0, "slow").args == (URL, "slow")


# --- notes -----------------------------------------------------------------------------


def test_note_without_params_renders_its_template() -> None:
    note = Note("content filter kept nothing; saved the full page")
    assert note.params == {}
    assert str(note) == "content filter kept nothing; saved the full page"


def test_note_str_is_the_english_rendering() -> None:
    note = Note("PDF saved as-is (format '{format}' does not apply)", {"format": "html"})
    assert note.render(ENGLISH) == "PDF saved as-is (format 'html' does not apply)"
    assert str(note) == "PDF saved as-is (format 'html' does not apply)"


def test_note_in_an_f_string_is_the_english_rendering() -> None:
    note = Note("content filter failed ({reason}); saved the full page", {"reason": "boom"})
    assert f"note: {note}" == "note: content filter failed (boom); saved the full page"


def test_note_renders_the_translated_template() -> None:
    template = "not a web page ({media_type}); saved the original file"
    note = Note(template, {"media_type": "image/jpeg"})
    t = FakeTranslator({template: "Web ページではありません({media_type})"})
    assert note.render(t) == "Web ページではありません(image/jpeg)"


def test_note_values_are_not_parsed_as_template_fields() -> None:
    note = Note("content filter failed ({reason}); saved the full page", {"reason": "{x}"})
    assert str(note) == "content filter failed ({x}); saved the full page"


def test_note_renders_a_localized_param_in_the_same_language() -> None:
    error = NameResolutionError("https://a.invalid/")
    note = Note(PROXY_NOTE, {"error": error})
    t = FakeTranslator(
        {
            PROXY_NOTE: "プロキシ経由で失敗しました({error})",
            "could not resolve host: {url}": "ホスト名を解決できません: {url}",
        }
    )
    assert str(note) == (
        "proxy failed (could not resolve host: https://a.invalid/); "
        "retried with a direct connection"
    )
    assert (
        note.render(t) == "プロキシ経由で失敗しました(ホスト名を解決できません: https://a.invalid/)"
    )


def test_notes_with_the_same_template_and_params_are_equal() -> None:
    assert Note("x {a}", {"a": 1}) == Note("x {a}", {"a": 1})
    assert Note("x {a}", {"a": 1}) != Note("x {a}", {"a": 2})
    assert Note("x") == Note("x", {})
    assert Note("x") != Note("y")


def test_note_is_frozen() -> None:
    note = Note("x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        note.template = "y"  # type: ignore[misc]


def test_note_is_localized() -> None:
    assert isinstance(Note("x"), Localized)


# --- URL and proxy validation errors ---------------------------------------------------

URL_TEMPLATE = "not an http(s) URL: {url}"


@pytest.mark.parametrize(
    "url",
    ["ftp://x", "not-a-url", "https://", " mailto:a@b "],
    ids=["ftp", "no-scheme", "no-host", "padded"],
)
def test_invalid_url_error(url: str) -> None:
    with pytest.raises(InvalidUrlError) as info:
        validate_url(url)
    error = info.value
    assert isinstance(error, ValueError)
    assert str(error) == f"not an http(s) URL: {url}"
    assert error.template == URL_TEMPLATE
    assert error.params == {"url": url}
    assert error.render(tagged(URL_TEMPLATE)) == f"[xx] not an http(s) URL: {url}"


def test_invalid_url_error_is_caught_as_value_error() -> None:
    with pytest.raises(ValueError, match=r"not an http\(s\) URL: ftp://x"):
        validate_url("ftp://x")


PROXY_CASES = [
    pytest.param(
        "ftp://proxy.example:8080",
        "unsupported proxy scheme: {proxy}",
        "ftp://proxy.example:8080",
        id="unsupported-scheme",
    ),
    pytest.param(
        "ftp://user:s3cr3t@proxy.example:8080",
        "unsupported proxy scheme: {proxy}",
        "ftp://***@proxy.example:8080",
        id="unsupported-scheme-with-credentials",
    ),
    pytest.param(
        "http://:8080",
        "proxy URL is missing a host: {proxy}",
        "http://:8080",
        id="missing-host",
    ),
    pytest.param(
        "http://proxy.example:not-a-port",
        "proxy URL has an invalid port: {proxy}",
        "http://proxy.example:not-a-port",
        id="invalid-port",
    ),
    pytest.param(
        "http://user:s3cr3t@proxy.example:99999999",
        "proxy URL has an invalid port: {proxy}",
        "http://***@proxy.example:99999999",
        id="out-of-range-port-with-credentials",
    ),
    pytest.param(
        "http://proxy.example",
        "proxy URL is missing a port: {proxy}",
        "http://proxy.example",
        id="missing-port",
    ),
    pytest.param(
        "  proxy.example  ",
        "proxy URL is missing a port: {proxy}",
        "http://proxy.example",
        id="missing-port-without-scheme",
    ),
]


@pytest.mark.parametrize(("value", "template", "shown"), PROXY_CASES)
def test_proxy_url_error(value: str, template: str, shown: str) -> None:
    with pytest.raises(ProxyUrlError) as info:
        normalize_proxy(value)
    error = info.value
    english = template.format(proxy=shown)
    assert isinstance(error, ValueError)
    assert str(error) == english
    assert error.template == template
    assert error.params == {"proxy": shown}
    assert error.render(tagged(template)) == f"[xx] {english}"
    assert SECRET not in repr(error)


def test_proxy_url_error_keeps_the_port_error_as_its_cause() -> None:
    with pytest.raises(ProxyUrlError) as info:
        normalize_proxy("http://proxy.example:not-a-port")
    assert isinstance(info.value.__cause__, ValueError)


# --- the Japanese catalog ---------------------------------------------------------------


def test_japanese_name_resolution_error() -> None:
    error = NameResolutionError("https://a.invalid/")
    assert error.render(get_translator("ja")) == "ホスト名を解決できません: https://a.invalid/"


def test_japanese_timeout_error_keeps_the_format_spec() -> None:
    error = FetchTimeoutError(URL, 2.5)
    assert error.render(get_translator("ja")) == f"2.5 秒でタイムアウトしました: {URL}"


def test_japanese_proxy_note_renders_the_quoted_error_in_japanese() -> None:
    note = Note(PROXY_NOTE, {"error": FetchTimeoutError(URL, 60.0)})
    assert note.render(get_translator("ja")) == (
        f"プロキシ経由で失敗しました(60 秒でタイムアウトしました: {URL})。直接接続で再試行しました"
    )


def test_japanese_invalid_url_error() -> None:
    with pytest.raises(InvalidUrlError) as info:
        validate_url("ftp://x")
    assert info.value.render(get_translator("ja")) == "http(s) の URL ではありません: ftp://x"


def test_japanese_proxy_refused_error() -> None:
    error = ProxyRefusedError(URL, PROXY, 403)
    assert error.render(get_translator("ja")) == (
        f"プロキシに接続を拒否されました({REDACTED_PROXY}、HTTP 403): {URL}"
    )


def test_japanese_tls_error_with_a_summary() -> None:
    error = TlsFetchError(URL, "Page.goto: net::ERR_SSL_PROTOCOL_ERROR at x")
    assert error.render(get_translator("ja")) == (
        f"TLS エラーが発生しました: net::ERR_SSL_PROTOCOL_ERROR: {URL}"
    )


def test_japanese_tls_error_without_a_summary() -> None:
    assert TlsFetchError(URL).render(get_translator("ja")) == f"TLS エラーが発生しました: {URL}"


def test_japanese_blocked_error() -> None:
    error = BlockedFetchError(URL, 403)
    assert error.render(get_translator("ja")) == (
        f"ボット対策のチャレンジで拒否されました(HTTP 403): {URL}"
    )


def test_japanese_proxy_note_renders_a_quoted_tls_error_in_japanese() -> None:
    note = Note(PROXY_NOTE, {"error": TlsFetchError(URL, "net::ERR_SSL_PROTOCOL_ERROR")})
    assert note.render(get_translator("ja")) == (
        "プロキシ経由で失敗しました(TLS エラーが発生しました: net::ERR_SSL_PROTOCOL_ERROR: "
        f"{URL})。直接接続で再試行しました"
    )


def test_tls_and_blocked_errors_never_name_the_proxy() -> None:
    for error in (TlsFetchError(URL, "net::ERR_SSL_PROTOCOL_ERROR"), BlockedFetchError(URL, 403)):
        assert "proxy" not in error.params
        assert "proxy" not in str(error)
