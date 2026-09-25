from __future__ import annotations

import base64
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import FakeCrawler, FakeHttp, HttpHandler, make_result

from crawl4tools.engine import fetcher as fetcher_module
from crawl4tools.engine.errors import (
    BlockedFetchError,
    BrowserNotInstalledError,
    ConnectionRefusedFetchError,
    FetchError,
    FetchTimeoutError,
    HttpStatusError,
    NonHtmlContentError,
    ProxyFetchError,
    TlsFetchError,
)
from crawl4tools.engine.fetcher import Fetcher, build_run_config
from crawl4tools.engine.models import (
    ContentKind,
    FailureKind,
    FetchOptions,
    FetchOutcome,
    Note,
    OutputFormat,
)

URL = "https://example.com/page"
SECRET = "s3cr3t-pa55"
PROXY = f"http://user:{SECRET}@proxy.example:8080"


def make_fetcher(
    options: FetchOptions | None = None,
    *,
    crawler: FakeCrawler | None = None,
    http: FakeHttp | None = None,
) -> tuple[Fetcher, FakeCrawler, FakeHttp]:
    crawler = crawler if crawler is not None else FakeCrawler()
    http = http if http is not None else FakeHttp()
    fetcher = Fetcher(
        options if options is not None else FetchOptions(),
        crawler_factory=crawler.factory,
        http_client_factory=http,
    )
    return fetcher, crawler, http


async def fetch_one(
    options: FetchOptions | None = None,
    *,
    crawler: FakeCrawler | None = None,
    http: FakeHttp | None = None,
    url: str = URL,
) -> tuple[FetchOutcome, FakeCrawler, FakeHttp]:
    fetcher, crawler, http = make_fetcher(options, crawler=crawler, http=http)
    async with fetcher:
        outcome = await fetcher.fetch(url)
    return outcome, crawler, http


def fail(message: str) -> Any:
    return make_result(success=False, error_message=message, status_code=None, html="")


def proxy_aware(with_proxy: Any, without_proxy: Any) -> Callable[[str, Any], Any]:
    def handler(url: str, config: Any) -> Any:
        return with_proxy if config.proxy_config is not None else without_proxy

    return handler


def note_texts(outcome: FetchOutcome) -> list[str]:
    return [str(note) for note in outcome.notes]


def assert_no_secret(outcome: FetchOutcome) -> None:
    texts = [*note_texts(outcome), str(outcome.error) if outcome.error else ""]
    for text in texts:
        assert SECRET not in text
        assert "user:" not in text


def response(content_type: str | None, content: bytes, status: int = 200) -> httpx.Response:
    headers = {"content-type": content_type} if content_type else {}
    return httpx.Response(status, headers=headers, content=content)


# --- browser path: formats -------------------------------------------------


async def test_markdown_success() -> None:
    crawler = FakeCrawler(
        make_result(response_headers={"Content-Type": "text/html; charset=utf-8"})
    )
    outcome, crawler, http = await fetch_one(crawler=crawler)
    assert outcome.ok
    assert outcome.error is None
    assert outcome.text == "# Hello"
    assert outcome.data is None
    assert outcome.suggested_extension == ".md"
    assert outcome.content_kind is ContentKind.HTML
    assert outcome.content_type == "text/html; charset=utf-8"
    assert outcome.final_url == URL
    assert outcome.status_code == 200
    assert outcome.notes == []
    assert len(crawler.calls) == 1
    assert crawler.calls[0][1].proxy_config is None
    assert http.proxies == [None]
    assert crawler.entered == 1
    assert crawler.exited == 1


async def test_citations() -> None:
    outcome, _, _ = await fetch_one(FetchOptions(citations=True))
    assert outcome.text == "# Hello [1]\n\n## References\n[1]: x"


async def test_redirected_url_is_final_url() -> None:
    crawler = FakeCrawler(make_result(redirected_url="https://example.com/moved"))
    outcome, _, _ = await fetch_one(crawler=crawler)
    assert outcome.final_url == "https://example.com/moved"


async def test_missing_response_headers() -> None:
    outcome, _, _ = await fetch_one(crawler=FakeCrawler(make_result(response_headers=None)))
    assert outcome.ok
    assert outcome.content_type is None


async def test_title_from_metadata() -> None:
    outcome, _, _ = await fetch_one()
    assert outcome.title == "Example Domain"


async def test_title_is_stripped() -> None:
    crawler = FakeCrawler(make_result(metadata={"title": "  Hello World  "}))
    outcome, _, _ = await fetch_one(crawler=crawler)
    assert outcome.title == "Hello World"


@pytest.mark.parametrize(
    "metadata",
    [{}, {"title": None}, {"title": 123}, {"title": "   "}],
    ids=["no_title_key", "none_title", "non_str_title", "whitespace_only_title"],
)
async def test_title_missing_or_invalid_is_none(metadata: dict[str, Any]) -> None:
    crawler = FakeCrawler(make_result(metadata=metadata))
    outcome, _, _ = await fetch_one(crawler=crawler)
    assert outcome.title is None


async def test_title_is_none_when_metadata_attribute_is_absent() -> None:
    result = make_result()
    del result.metadata
    outcome, _, _ = await fetch_one(crawler=FakeCrawler(result))
    assert outcome.title is None


@pytest.mark.parametrize(
    ("fmt", "overrides", "text", "data", "ext"),
    [
        (OutputFormat.HTML, {}, "<html><body><h1>Hello</h1></body></html>", None, ".html"),
        (OutputFormat.PDF, {"pdf": b"%PDF-bytes"}, None, b"%PDF-bytes", ".pdf"),
        (
            OutputFormat.SCREENSHOT,
            {"screenshot": base64.b64encode(b"\x89PNG-data").decode()},
            None,
            b"\x89PNG-data",
            ".png",
        ),
        (OutputFormat.MHTML, {"mhtml": "MIME-Version: 1.0"}, "MIME-Version: 1.0", None, ".mhtml"),
        (
            OutputFormat.RAW,
            {"html": "<html>é</html>"},
            None,
            "<html>é</html>".encode(),
            ".html",
        ),
    ],
)
async def test_formats(
    fmt: OutputFormat,
    overrides: dict[str, Any],
    text: str | None,
    data: bytes | None,
    ext: str,
) -> None:
    crawler = FakeCrawler(make_result(**overrides))
    outcome, crawler, _ = await fetch_one(FetchOptions(format=fmt), crawler=crawler)
    assert outcome.ok, outcome.error
    assert outcome.text == text
    assert outcome.data == data
    assert outcome.suggested_extension == ext
    assert outcome.content_kind is ContentKind.HTML
    config = crawler.calls[0][1]
    assert config.pdf is (fmt is OutputFormat.PDF)
    assert config.screenshot is (fmt is OutputFormat.SCREENSHOT)
    assert config.capture_mhtml is (fmt is OutputFormat.MHTML)


@pytest.mark.parametrize(
    ("fmt", "message", "ext"),
    [
        (OutputFormat.PDF, "the browser did not produce a PDF", ".pdf"),
        (OutputFormat.SCREENSHOT, "the browser did not produce a screenshot", ".png"),
        (OutputFormat.MHTML, "the browser did not produce MHTML", ".mhtml"),
    ],
)
async def test_missing_artifacts(fmt: OutputFormat, message: str, ext: str) -> None:
    outcome, _, _ = await fetch_one(FetchOptions(format=fmt))
    assert not outcome.ok
    assert outcome.error is not None
    assert message in str(outcome.error)
    assert outcome.error.kind is FailureKind.OTHER
    assert outcome.suggested_extension == ext


# --- failures and proxy fallback ---------------------------------------------


async def test_http_404_is_failure_without_fallback() -> None:
    crawler = FakeCrawler(make_result(status_code=404))
    outcome, crawler, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert not outcome.ok
    assert isinstance(outcome.error, HttpStatusError)
    assert outcome.status_code == 404
    assert outcome.suggested_extension == ".md"
    assert len(crawler.calls) == 1
    assert outcome.notes == []


async def test_proxy_failure_falls_back_to_direct() -> None:
    crawler = FakeCrawler(
        proxy_aware(fail("net::ERR_PROXY_CONNECTION_FAILED at " + URL), make_result())
    )
    outcome, crawler, http = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert outcome.ok
    assert outcome.text == "# Hello"
    assert len(crawler.calls) == 2
    assert crawler.calls[0][1].proxy_config is not None
    assert crawler.calls[1][1].proxy_config is None
    assert http.proxies == [PROXY, None]
    assert len(outcome.notes) == 1
    note = note_texts(outcome)[0]
    assert note.startswith("proxy failed (")
    assert note.endswith("); retried with a direct connection")
    assert "http://***@proxy.example:8080" in note
    assert_no_secret(outcome)


async def test_proxy_fallback_note_quotes_the_error_object() -> None:
    crawler = FakeCrawler(
        proxy_aware(fail("net::ERR_PROXY_CONNECTION_FAILED at " + URL), make_result())
    )
    outcome, _, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert outcome.ok
    [note] = outcome.notes
    assert note.template == "proxy failed ({error}); retried with a direct connection"
    error = note.params["error"]
    assert isinstance(error, ProxyFetchError)
    assert error.proxy == "http://***@proxy.example:8080"


async def test_fallback_disabled_reports_proxy_failure() -> None:
    crawler = FakeCrawler(fail("net::ERR_PROXY_CONNECTION_FAILED"))
    outcome, crawler, _ = await fetch_one(
        FetchOptions(proxy=PROXY, fallback=False), crawler=crawler
    )
    assert not outcome.ok
    assert isinstance(outcome.error, ProxyFetchError)
    assert len(crawler.calls) == 1
    assert outcome.notes == []
    assert_no_secret(outcome)


async def test_fallback_retries_only_once() -> None:
    crawler = FakeCrawler(fail("net::ERR_PROXY_CONNECTION_FAILED"))
    outcome, crawler, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert not outcome.ok
    assert len(crawler.calls) == 2
    assert len(outcome.notes) == 1
    assert note_texts(outcome)[0].startswith("proxy failed (")
    assert_no_secret(outcome)


async def test_http_502_through_proxy_falls_back() -> None:
    crawler = FakeCrawler(proxy_aware(make_result(status_code=502), make_result()))
    outcome, crawler, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert outcome.ok
    assert outcome.status_code == 200
    assert len(crawler.calls) == 2
    assert "HTTP 502" in note_texts(outcome)[0]
    assert_no_secret(outcome)


async def test_timeout_through_proxy_falls_back() -> None:
    crawler = FakeCrawler(proxy_aware(fail("Timeout 60000ms exceeded."), make_result()))
    outcome, _, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert outcome.ok
    assert "timed out" in note_texts(outcome)[0]
    assert_no_secret(outcome)


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        ("net::ERR_TIMED_OUT at https://example.com/page", FailureKind.TIMEOUT),
        ("Page.goto: Timeout 5000ms exceeded.", FailureKind.TIMEOUT),
        ("net::ERR_CONNECTION_REFUSED at https://example.com/page", FailureKind.CONNECTION_REFUSED),
        ("net::ERR_NAME_NOT_RESOLVED at https://example.com/page", FailureKind.NAME_RESOLUTION),
        ("something unexpected", FailureKind.OTHER),
        (None, FailureKind.OTHER),
    ],
)
async def test_failure_classification(message: str | None, kind: FailureKind) -> None:
    crawler = FakeCrawler(fail(message) if message else make_result(success=False, html=""))
    outcome, crawler, _ = await fetch_one(FetchOptions(timeout_s=5), crawler=crawler)
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.kind is kind
    assert len(crawler.calls) == 1


async def test_timeout_error_uses_configured_timeout() -> None:
    crawler = FakeCrawler(fail("net::ERR_TIMED_OUT"))
    outcome, _, _ = await fetch_one(FetchOptions(timeout_s=7.5), crawler=crawler)
    assert str(outcome.error).startswith("timed out after 7.5s")


async def test_crawler_start_failure_reported_for_every_url() -> None:
    crawler = FakeCrawler(
        start_error=RuntimeError(
            "BrowserType.launch: Executable doesn't exist at /x/chrome\n"
            "Please run the following command: playwright install"
        )
    )
    fetcher, crawler, _ = make_fetcher(crawler=crawler)
    urls = [f"https://example.com/{i}" for i in range(3)]
    async with fetcher:
        outcomes = await fetcher.fetch_many(urls, concurrency=3)
    assert [o.url for o in outcomes] == urls
    for outcome in outcomes:
        assert not outcome.ok
        assert isinstance(outcome.error, BrowserNotInstalledError)
        assert outcome.error.kind is FailureKind.BROWSER_NOT_INSTALLED
    assert crawler.factory_calls == 1
    assert crawler.entered == 1
    assert crawler.exited == 0
    assert crawler.calls == []


async def test_crawler_start_failure_other() -> None:
    crawler = FakeCrawler(start_error=RuntimeError("weird launch failure"))
    outcome, crawler, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.kind is FailureKind.OTHER
    # A browser that cannot start is not retried without the proxy.
    assert crawler.entered == 1
    assert outcome.notes == []
    assert_no_secret(outcome)


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (RuntimeError("net::ERR_CONNECTION_REFUSED at x"), FailureKind.CONNECTION_REFUSED),
        (RuntimeError("boom"), FailureKind.OTHER),
        (ValueError(""), FailureKind.OTHER),
    ],
)
async def test_arun_exception_is_classified(exc: Exception, kind: FailureKind) -> None:
    outcome, _, _ = await fetch_one(crawler=FakeCrawler(exc))
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.kind is kind


async def test_arun_exception_through_proxy_falls_back() -> None:
    crawler = FakeCrawler(
        proxy_aware(RuntimeError("net::ERR_PROXY_CONNECTION_FAILED"), make_result())
    )
    outcome, crawler, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert outcome.ok
    assert len(crawler.calls) == 2
    assert_no_secret(outcome)


# --- non-HTML resources (probe path) ---------------------------------------------


def pdf_http(data: bytes, content_type: str = "application/pdf") -> FakeHttp:
    return FakeHttp(lambda request: response(content_type, data))


async def test_pdf_to_markdown_without_browser(sample_pdf: bytes) -> None:
    outcome, crawler, _ = await fetch_one(http=pdf_http(sample_pdf))
    assert outcome.ok, outcome.error
    assert outcome.text is not None and "Hello crawl4tools" in outcome.text
    assert outcome.data is None
    assert outcome.content_kind is ContentKind.PDF
    assert outcome.suggested_extension == ".md"
    assert outcome.content_type == "application/pdf"
    assert outcome.status_code == 200
    assert outcome.notes == []
    assert outcome.title is None
    assert crawler.factory_calls == 0
    assert not crawler.started
    assert crawler.exited == 0


async def test_empty_pdf_text_is_saved_with_note(
    sample_pdf: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def empty(data: bytes) -> str:
        return ""

    monkeypatch.setattr(fetcher_module, "pdf_to_markdown", empty)
    outcome, _, _ = await fetch_one(http=pdf_http(sample_pdf))
    assert outcome.ok
    assert outcome.text == ""
    assert outcome.suggested_extension == ".md"
    assert note_texts(outcome) == ["no extractable text in PDF; saved an empty document"]


async def test_corrupt_pdf_falls_back_to_raw() -> None:
    outcome, crawler, _ = await fetch_one(http=pdf_http(b"not really a pdf"))
    assert outcome.ok
    assert outcome.content_kind is ContentKind.PDF
    assert outcome.data == b"not really a pdf"
    assert outcome.text is None
    assert outcome.suggested_extension == ".pdf"
    assert len(outcome.notes) == 1
    assert note_texts(outcome)[0].startswith("could not convert PDF to Markdown (")
    assert note_texts(outcome)[0].endswith("); saved the original file")
    assert not crawler.started


@pytest.mark.parametrize(
    ("fmt", "notes"),
    [
        (OutputFormat.RAW, []),
        (OutputFormat.PDF, []),
        (OutputFormat.HTML, ["PDF saved as-is (format 'html' does not apply)"]),
        (OutputFormat.SCREENSHOT, ["PDF saved as-is (format 'screenshot' does not apply)"]),
    ],
)
async def test_pdf_other_formats_saved_as_is(
    sample_pdf: bytes, fmt: OutputFormat, notes: list[str]
) -> None:
    outcome, crawler, _ = await fetch_one(FetchOptions(format=fmt), http=pdf_http(sample_pdf))
    assert outcome.ok
    assert outcome.data == sample_pdf
    assert outcome.content_kind is ContentKind.PDF
    assert outcome.suggested_extension == ".pdf"
    assert note_texts(outcome) == notes
    assert not crawler.started


async def test_pdf_content_type_with_params(sample_pdf: bytes) -> None:
    outcome, _, _ = await fetch_one(http=pdf_http(sample_pdf, "Application/PDF; qs=0.1"))
    assert outcome.content_kind is ContentKind.PDF
    assert outcome.text is not None and "Hello crawl4tools" in outcome.text


async def test_image_is_saved_as_binary() -> None:
    outcome, crawler, _ = await fetch_one(http=pdf_http(b"\xff\xd8JPEG", "image/jpeg"))
    assert outcome.ok
    assert outcome.content_kind is ContentKind.BINARY
    assert outcome.data == b"\xff\xd8JPEG"
    assert outcome.suggested_extension == ".jpg"
    assert outcome.content_type == "image/jpeg"
    assert note_texts(outcome) == ["not a web page (image/jpeg); saved the original file"]
    assert outcome.notes == [
        Note(
            "not a web page ({media_type}); saved the original file",
            {"media_type": "image/jpeg"},
        )
    ]
    assert not crawler.started


async def test_probe_4xx_defers_to_browser() -> None:
    http = FakeHttp(lambda request: response("application/json", b"{}", status=403))
    outcome, crawler, _ = await fetch_one(http=http)
    assert outcome.ok
    assert outcome.content_kind is ContentKind.HTML
    assert len(crawler.calls) == 1


async def test_probe_network_error_defers_to_browser() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    outcome, crawler, _ = await fetch_one(http=FakeHttp(handler))
    assert outcome.ok
    assert len(crawler.calls) == 1


async def test_socks_proxy_skips_probe() -> None:
    socks = "socks5://proxy.example:1080"
    outcome, crawler, http = await fetch_one(FetchOptions(proxy=socks))
    assert outcome.ok
    assert http.client_calls == []
    assert crawler.calls[0][1].proxy_config is not None


def sequenced(*responses: httpx.Response) -> FakeHttp:
    """HTTP mock returning *responses* in order (the last one repeats)."""
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return FakeHttp(handler)


async def test_err_aborted_is_downloaded_via_http() -> None:
    url = "https://example.com/file.zip"
    # No content type: the first probe treats it as a possible page, the browser aborts.
    http = FakeHttp(lambda request: response(None, b"PK\x03\x04"))
    crawler = FakeCrawler(fail("net::ERR_ABORTED at " + url))
    outcome, crawler, http = await fetch_one(crawler=crawler, http=http, url=url)
    assert outcome.ok, outcome.error
    assert outcome.content_kind is ContentKind.BINARY
    assert outcome.data == b"PK\x03\x04"
    assert outcome.suggested_extension == ".zip"
    assert len(http.requests) == 2
    assert len(crawler.calls) == 1


async def test_download_without_content_type_is_noted_as_unknown_type() -> None:
    url = "https://example.com/file.zip"
    http = FakeHttp(lambda request: response(None, b"PK\x03\x04"))
    crawler = FakeCrawler(fail("net::ERR_ABORTED at " + url))
    outcome, _, _ = await fetch_one(crawler=crawler, http=http, url=url)
    assert outcome.ok, outcome.error
    assert outcome.notes == [Note("not a web page (unknown type); saved the original file")]
    assert note_texts(outcome) == ["not a web page (unknown type); saved the original file"]


async def test_err_aborted_download_failure() -> None:
    http = sequenced(response("text/html", b""), response(None, b"", status=500))
    crawler = FakeCrawler(fail("Download is starting"))
    outcome, _, _ = await fetch_one(crawler=crawler, http=http)
    assert not outcome.ok
    assert isinstance(outcome.error, NonHtmlContentError)


async def test_browser_non_html_without_html_is_downloaded(sample_pdf: bytes) -> None:
    http = sequenced(
        response("text/html", b"", status=503),
        response("application/octet-stream", b"\x00\x01"),
    )
    crawler = FakeCrawler(
        make_result(html="  ", response_headers={"Content-Type": "application/octet-stream"})
    )
    outcome, _, http = await fetch_one(crawler=crawler, http=http)
    assert outcome.ok, outcome.error
    assert outcome.content_kind is ContentKind.BINARY
    assert outcome.data == b"\x00\x01"
    assert len(http.requests) == 2


async def test_browser_non_html_download_failure() -> None:
    http = sequenced(response("text/html", b"", status=503))
    crawler = FakeCrawler(
        make_result(html="", response_headers={"content-type": "application/octet-stream"})
    )
    outcome, _, _ = await fetch_one(crawler=crawler, http=http)
    assert not outcome.ok
    assert "browser returned no HTML" in str(outcome.error)


async def test_browser_non_html_with_html_is_kept() -> None:
    crawler = FakeCrawler(make_result(response_headers={"content-type": "text/plain"}))
    outcome, _, http = await fetch_one(crawler=crawler)
    assert outcome.ok
    assert outcome.text == "# Hello"
    assert len(http.requests) == 1


# --- fetch_many -----------------------------------------------------------------


async def test_fetch_many_order_concurrency_and_isolation() -> None:
    urls = [f"https://example.com/{i}" for i in range(7)]

    def handler(url: str, config: Any) -> Any:
        if url.endswith("/3"):
            return fail("net::ERR_NAME_NOT_RESOLVED")
        if url.endswith("/5"):
            return RuntimeError("crash")
        return make_result(url=url)

    crawler = FakeCrawler(handler, delay=0.01)
    fetcher, crawler, _ = make_fetcher(crawler=crawler)
    async with fetcher:
        outcomes = await fetcher.fetch_many(urls, concurrency=2)
    assert [o.url for o in outcomes] == urls
    assert [o.ok for o in outcomes] == [True, True, True, False, True, False, True]
    assert crawler.max_in_flight == 2
    assert crawler.factory_calls == 1
    assert crawler.entered == 1
    assert crawler.exited == 1


async def test_fetch_many_empty() -> None:
    fetcher, crawler, _ = make_fetcher()
    async with fetcher:
        assert await fetcher.fetch_many([]) == []
    assert not crawler.started


@pytest.mark.parametrize("concurrency", [0, -1])
async def test_fetch_many_rejects_bad_concurrency(concurrency: int) -> None:
    fetcher, _, _ = make_fetcher()
    with pytest.raises(ValueError):
        await fetcher.fetch_many([URL], concurrency=concurrency)


# --- construction and run config ----------------------------------------------


def test_invalid_proxy_raises() -> None:
    with pytest.raises(ValueError):
        make_fetcher(FetchOptions(proxy="ftp://proxy.example:21"))


async def test_proxy_is_normalized() -> None:
    _, crawler, http = await fetch_one(FetchOptions(proxy="proxy.example:8080"))
    assert http.proxies == ["http://proxy.example:8080"]
    assert crawler.calls[0][1].proxy_config.server == "http://proxy.example:8080"


def test_build_run_config() -> None:
    from crawl4ai import CacheMode

    options = FetchOptions(
        format=OutputFormat.MARKDOWN,
        timeout_s=12.5,
        ignore_links=True,
        ignore_images=True,
        verbose=True,
    )
    config = build_run_config(options, "http://proxy.example:8080")
    assert config.cache_mode == CacheMode.BYPASS
    assert config.page_timeout == 12500
    assert config.verbose is True
    assert config.proxy_config.server == "http://proxy.example:8080"
    assert config.pdf is False
    assert config.screenshot is False
    assert config.capture_mhtml is False
    generator_options = config.markdown_generator.options
    assert generator_options["ignore_links"] is True
    assert generator_options["ignore_images"] is True
    assert generator_options["body_width"] == 0

    direct = build_run_config(FetchOptions(), None)
    assert direct.proxy_config is None
    assert direct.markdown_generator.options["ignore_links"] is False


def test_crawl4ai_is_not_imported_at_module_load() -> None:
    code = (
        "import sys, crawl4tools.engine.fetcher, crawl4tools.engine.pdf, "
        "crawl4tools.engine.probe, crawl4tools.engine; "
        "print('crawl4ai' in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == "False"


# --- --fit (content-filtered Markdown) ------------------------------------------


def test_build_run_config_adds_pruning_filter_only_with_fit() -> None:
    from crawl4ai import PruningContentFilterLXML

    with_fit = build_run_config(FetchOptions(fit=True), None)
    assert isinstance(with_fit.markdown_generator.content_filter, PruningContentFilterLXML)
    without_fit = build_run_config(FetchOptions(), None)
    assert without_fit.markdown_generator.content_filter is None


async def test_fit_uses_filtered_markdown() -> None:
    outcome, _, _ = await fetch_one(FetchOptions(fit=True))
    assert outcome.ok
    assert outcome.text == "# Hello (fit)"
    assert outcome.notes == []


async def test_fit_falls_back_to_full_page_when_filter_keeps_nothing() -> None:
    crawler = FakeCrawler(make_result(fit_markdown="  \n"))
    outcome, _, _ = await fetch_one(FetchOptions(fit=True), crawler=crawler)
    assert outcome.ok
    assert outcome.text == "# Hello"
    assert any("content filter kept nothing" in note for note in note_texts(outcome))


async def test_fit_falls_back_to_full_page_when_filter_fails() -> None:
    crawler = FakeCrawler(make_result(fit_markdown="Error generating fit markdown: boom"))
    outcome, _, _ = await fetch_one(FetchOptions(fit=True), crawler=crawler)
    assert outcome.ok
    assert outcome.text == "# Hello"
    assert any("content filter failed" in note and "boom" in note for note in note_texts(outcome))


async def test_fit_with_citations_converts_links_of_filtered_markdown() -> None:
    crawler = FakeCrawler(
        make_result(fit_markdown="Read [the docs](https://docs.example/guide) now.")
    )
    outcome, _, _ = await fetch_one(FetchOptions(fit=True, citations=True), crawler=crawler)
    assert outcome.ok
    assert outcome.text is not None
    body, _, references = outcome.text.partition("\n\n")
    assert "(https://docs.example/guide)" not in body
    assert "https://docs.example/guide" in references


async def test_fit_does_not_apply_to_other_formats() -> None:
    crawler = FakeCrawler(make_result(html="<html><body>full</body></html>"))
    outcome, _, _ = await fetch_one(
        FetchOptions(fit=True, format=OutputFormat.HTML), crawler=crawler
    )
    assert outcome.text == "<html><body>full</body></html>"


# --- per-call options -------------------------------------------------------------


async def test_per_call_options_override_constructor_options() -> None:
    from crawl4ai import PruningContentFilterLXML

    fetcher, crawler, http = make_fetcher(FetchOptions(format=OutputFormat.MARKDOWN))
    async with fetcher:
        first = await fetcher.fetch(
            URL, options=FetchOptions(format=OutputFormat.MARKDOWN, fit=False, timeout_s=60)
        )
        second = await fetcher.fetch(
            URL,
            options=FetchOptions(
                format=OutputFormat.HTML, fit=True, timeout_s=5, ignore_links=True
            ),
        )
    assert first.ok
    assert first.text == "# Hello"
    assert first.suggested_extension == ".md"
    assert second.ok
    assert second.text == "<html><body><h1>Hello</h1></body></html>"
    assert second.suggested_extension == ".html"

    first_config, second_config = (config for _, config in crawler.calls)
    assert first_config.page_timeout == 60000
    assert first_config.markdown_generator.content_filter is None
    assert first_config.markdown_generator.options["ignore_links"] is False
    assert second_config.page_timeout == 5000
    assert isinstance(second_config.markdown_generator.content_filter, PruningContentFilterLXML)
    assert second_config.markdown_generator.options["ignore_links"] is True
    assert [timeout for _, timeout in http.client_calls] == [60, 5]
    # The browser is started once and shared across calls with different options.
    assert crawler.factory_calls == 1
    assert crawler.entered == 1
    assert crawler.exited == 1
    # Per-call options do not replace the constructor options.
    assert fetcher.options == FetchOptions(format=OutputFormat.MARKDOWN)


async def test_per_call_markdown_options_select_text() -> None:
    fetcher, _, _ = make_fetcher()
    async with fetcher:
        fit = await fetcher.fetch(URL, options=FetchOptions(fit=True))
        cited = await fetcher.fetch(URL, options=FetchOptions(citations=True))
        plain = await fetcher.fetch(URL)
    assert fit.text == "# Hello (fit)"
    assert cited.text == "# Hello [1]\n\n## References\n[1]: x"
    assert plain.text == "# Hello"


async def test_per_call_format_applies_to_failures() -> None:
    crawler = FakeCrawler(fail("net::ERR_NAME_NOT_RESOLVED"))
    fetcher, _, _ = make_fetcher(crawler=crawler)
    async with fetcher:
        outcome = await fetcher.fetch(URL, options=FetchOptions(format=OutputFormat.HTML))
    assert not outcome.ok
    assert outcome.suggested_extension == ".html"


async def test_per_call_timeout_is_reported_in_timeout_error() -> None:
    crawler = FakeCrawler(fail("Timeout 5000ms exceeded."))
    fetcher, _, _ = make_fetcher(FetchOptions(timeout_s=60), crawler=crawler)
    async with fetcher:
        outcome = await fetcher.fetch(URL, options=FetchOptions(timeout_s=5))
    assert outcome.error is not None
    assert outcome.error.kind is FailureKind.TIMEOUT
    assert "5" in str(outcome.error)
    assert "60" not in str(outcome.error)


async def test_per_call_pdf_resource_follows_per_call_format(sample_pdf: bytes) -> None:
    http = FakeHttp(lambda request: response("application/pdf", sample_pdf))
    fetcher, crawler, _ = make_fetcher(FetchOptions(format=OutputFormat.MARKDOWN), http=http)
    async with fetcher:
        outcome = await fetcher.fetch(URL, options=FetchOptions(format=OutputFormat.RAW))
    assert outcome.ok
    assert outcome.content_kind is ContentKind.PDF
    assert outcome.data == sample_pdf
    assert outcome.text is None
    assert outcome.suggested_extension == ".pdf"
    assert not crawler.started


async def test_fetch_many_applies_per_call_options_to_every_url() -> None:
    urls = [f"https://example.com/{i}" for i in range(4)]
    fetcher, crawler, _ = make_fetcher()
    async with fetcher:
        outcomes = await fetcher.fetch_many(
            urls, concurrency=2, options=FetchOptions(format=OutputFormat.HTML, timeout_s=7)
        )
    assert [o.url for o in outcomes] == urls
    assert all(o.ok for o in outcomes)
    assert all(o.suggested_extension == ".html" for o in outcomes)
    assert all(o.text == "<html><body><h1>Hello</h1></body></html>" for o in outcomes)
    assert all(config.page_timeout == 7000 for _, config in crawler.calls)
    assert crawler.factory_calls == 1


async def test_omitted_options_use_constructor_options() -> None:
    fetcher, crawler, http = make_fetcher(
        FetchOptions(format=OutputFormat.HTML, timeout_s=12, proxy="proxy.example:8080")
    )
    async with fetcher:
        single = await fetcher.fetch(URL)
        many = await fetcher.fetch_many([URL])
    for outcome in (single, *many):
        assert outcome.ok
        assert outcome.suggested_extension == ".html"
    assert all(config.page_timeout == 12000 for _, config in crawler.calls)
    assert http.proxies == ["http://proxy.example:8080", "http://proxy.example:8080"]


async def test_per_call_proxy_is_normalized_and_falls_back() -> None:
    crawler = FakeCrawler(
        proxy_aware(fail("net::ERR_PROXY_CONNECTION_FAILED at " + URL), make_result())
    )
    fetcher, crawler, http = make_fetcher(crawler=crawler)
    async with fetcher:
        direct = await fetcher.fetch(URL)
        proxied = await fetcher.fetch(URL, options=FetchOptions(proxy="proxy.example:8080"))
    assert direct.ok
    assert direct.notes == []
    assert proxied.ok
    assert note_texts(proxied)[0].startswith("proxy failed (")
    assert http.proxies == [None, "http://proxy.example:8080", None]
    assert crawler.calls[1][1].proxy_config.server == "http://proxy.example:8080"
    assert crawler.calls[2][1].proxy_config is None


async def test_per_call_proxy_respects_per_call_fallback() -> None:
    crawler = FakeCrawler(fail("net::ERR_PROXY_CONNECTION_FAILED at " + URL))
    fetcher, crawler, _ = make_fetcher(crawler=crawler)
    async with fetcher:
        outcome = await fetcher.fetch(URL, options=FetchOptions(proxy=PROXY, fallback=False))
    assert not outcome.ok
    assert isinstance(outcome.error, ProxyFetchError)
    assert len(crawler.calls) == 1
    assert_no_secret(outcome)


async def test_per_call_options_without_proxy_do_not_use_constructor_proxy() -> None:
    fetcher, crawler, http = make_fetcher(FetchOptions(proxy=PROXY))
    async with fetcher:
        outcome = await fetcher.fetch(URL, options=FetchOptions())
    assert outcome.ok
    assert http.proxies == [None]
    assert crawler.calls[0][1].proxy_config is None


async def test_invalid_per_call_proxy_raises_before_fetching() -> None:
    fetcher, crawler, http = make_fetcher()
    bad = FetchOptions(proxy="ftp://proxy.example:21")
    async with fetcher:
        with pytest.raises(ValueError):
            await fetcher.fetch(URL, options=bad)
        with pytest.raises(ValueError):
            await fetcher.fetch_many([URL, URL], options=bad)
    assert crawler.factory_calls == 0
    assert crawler.calls == []
    assert http.client_calls == []


# --- proxy interference (TLS interception) -------------------------------------------

PROXY_NOTE = "proxy failed ({error}); retried with a direct connection"
REDACTED_PROXY = "http://***@proxy.example:8080"
SQUID_TITLE = "ERROR: The requested URL could not be retrieved"
TLS_FAILURE = (
    "Unexpected error in _crawl_web at line 700 in _crawl_web (async_webcrawler.py):\n"
    "Error: Failed on navigating ACS-GOTO:\n"
    f"Page.goto: net::ERR_SSL_PROTOCOL_ERROR at {URL}\n"
)
CHALLENGE_PAGE = "<html><head><title>Just a moment...</title></head><body></body></html>"


def squid_page(code: str) -> str:
    """Return an error page shaped like Squid's stock templates."""
    return (
        f"<html><head><title>{SQUID_TITLE}</title></head>"
        f"<body id={code}><h1>ERROR</h1></body></html>"
    )


def anti_bot(status_code: int) -> str:
    """Return crawl4ai's error text for a page its own anti-bot check rejected."""
    return f"Blocked by anti-bot protection: HTTP {status_code} with HTML content (180 bytes)"


def squid_by_header(*, success: bool) -> Any:
    # crawl4ai marks a 503 HTML page as failed but keeps its headers and HTML (C33).
    return make_result(
        success=success,
        error_message=None if success else anti_bot(503),
        status_code=503,
        html=squid_page("ERR_SECURE_CONNECT_FAIL"),
        response_headers={
            "content-type": "text/html",
            "x-squid-error": "ERR_SECURE_CONNECT_FAIL 0",
        },
    )


def squid_by_title() -> Any:
    return make_result(
        status_code=503,
        html=squid_page("ERR_SECURE_CONNECT_FAIL"),
        response_headers={"content-type": "text/html"},
    )


def challenge(*, success: bool) -> Any:
    return make_result(
        success=success,
        error_message=None if success else anti_bot(403),
        status_code=403,
        html=CHALLENGE_PAGE,
        response_headers={"content-type": "text/html", "cf-mitigated": "challenge"},
    )


TLS_ERROR = f"TLS error: net::ERR_SSL_PROTOCOL_ERROR: {URL}"
PROXY_ERROR = f"proxy connection failed ({REDACTED_PROXY}): {URL}"
BLOCKED_ERROR = f"blocked by a bot challenge (HTTP 403): {URL}"

# (result through the proxy, error it is reported as, that error in English)
INTERCEPTED = [
    pytest.param(fail(TLS_FAILURE), TlsFetchError, TLS_ERROR, id="s1-tls"),
    pytest.param(squid_by_header(success=False), ProxyFetchError, PROXY_ERROR, id="s2-header"),
    pytest.param(
        squid_by_header(success=True), ProxyFetchError, PROXY_ERROR, id="s2-header-success"
    ),
    pytest.param(squid_by_title(), ProxyFetchError, PROXY_ERROR, id="s2-title"),
    pytest.param(challenge(success=True), BlockedFetchError, BLOCKED_ERROR, id="s3-challenge"),
    pytest.param(
        challenge(success=False), BlockedFetchError, BLOCKED_ERROR, id="s3-challenge-anti-bot"
    ),
]


@pytest.mark.parametrize(("intercepted", "error_type", "english"), INTERCEPTED)
async def test_interception_through_proxy_falls_back_to_direct(
    intercepted: Any, error_type: type[FetchError], english: str
) -> None:
    crawler = FakeCrawler(proxy_aware(intercepted, make_result()))
    outcome, crawler, http = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert outcome.ok, outcome.error
    assert outcome.text == "# Hello"
    assert len(crawler.calls) == 2
    assert crawler.calls[0][1].proxy_config is not None
    assert crawler.calls[1][1].proxy_config is None
    assert http.proxies == [PROXY, None]
    [note] = outcome.notes
    assert note.template == PROXY_NOTE
    assert type(note.params["error"]) is error_type
    assert str(note) == f"proxy failed ({english}); retried with a direct connection"
    assert_no_secret(outcome)


@pytest.mark.parametrize(("intercepted", "error_type", "english"), INTERCEPTED)
async def test_interception_with_fallback_disabled_is_reported(
    intercepted: Any, error_type: type[FetchError], english: str
) -> None:
    crawler = FakeCrawler(intercepted)
    outcome, crawler, _ = await fetch_one(
        FetchOptions(proxy=PROXY, fallback=False), crawler=crawler
    )
    assert not outcome.ok
    assert type(outcome.error) is error_type
    assert str(outcome.error) == english
    assert len(crawler.calls) == 1
    assert outcome.notes == []
    assert_no_secret(outcome)


@pytest.mark.parametrize(("intercepted", "error_type", "english"), INTERCEPTED)
async def test_interception_on_both_attempts_retries_only_once(
    intercepted: Any, error_type: type[FetchError], english: str
) -> None:
    outcome, crawler, _ = await fetch_one(
        FetchOptions(proxy=PROXY), crawler=FakeCrawler(intercepted)
    )
    assert not outcome.ok
    assert len(crawler.calls) == 2
    [note] = outcome.notes
    assert type(note.params["error"]) is error_type


@pytest.mark.parametrize(
    ("result", "error_type", "english"),
    [
        pytest.param(fail(TLS_FAILURE), TlsFetchError, TLS_ERROR, id="s1-tls"),
        # Without a proxy there is none to name: the generic error, still a failure.
        pytest.param(
            squid_by_header(success=False),
            FetchError,
            f"fetch failed: ERR_SECURE_CONNECT_FAIL 0: {URL}",
            id="s2-header",
        ),
        pytest.param(
            squid_by_title(),
            FetchError,
            f"fetch failed: ERR_SECURE_CONNECT_FAIL: {URL}",
            id="s2-title",
        ),
        # Without a proxy a challenge is the site's own answer: the usual status failure.
        pytest.param(
            challenge(success=True), HttpStatusError, f"HTTP 403 Forbidden: {URL}", id="s3"
        ),
        pytest.param(
            challenge(success=False),
            FetchError,
            f"fetch failed: {anti_bot(403)}: {URL}",
            id="s3-anti-bot",
        ),
    ],
)
async def test_interception_without_proxy_is_not_retried(
    result: Any, error_type: type[FetchError], english: str
) -> None:
    outcome, crawler, http = await fetch_one(crawler=FakeCrawler(result))
    assert not outcome.ok
    assert type(outcome.error) is error_type
    assert str(outcome.error) == english
    assert len(crawler.calls) == 1
    assert http.proxies == [None]
    assert outcome.notes == []


POLICY_DENIALS = [
    "ERR_ACCESS_DENIED",
    "ERR_CACHE_ACCESS_DENIED",
    "ERR_FORWARDING_DENIED",
    "ERR_CACHE_MGR_ACCESS_DENIED",
]


@pytest.mark.parametrize("code", POLICY_DENIALS)
async def test_squid_policy_denial_is_not_bypassed(code: str) -> None:
    denied = make_result(
        status_code=403,
        html=squid_page(code),
        response_headers={"content-type": "text/html", "X-Squid-Error": f"{code} 0"},
    )
    crawler = FakeCrawler(proxy_aware(denied, make_result()))
    outcome, crawler, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert not outcome.ok
    assert isinstance(outcome.error, HttpStatusError)
    assert outcome.status_code == 403
    assert len(crawler.calls) == 1
    assert outcome.notes == []


async def test_squid_policy_denial_rejected_by_crawl4ai_is_not_bypassed() -> None:
    denied = make_result(
        success=False,
        error_message=anti_bot(403),
        status_code=403,
        html=squid_page("ERR_ACCESS_DENIED"),
        response_headers={"content-type": "text/html", "x-squid-error": "ERR_ACCESS_DENIED 0"},
    )
    crawler = FakeCrawler(proxy_aware(denied, make_result()))
    outcome, crawler, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert not outcome.ok
    assert outcome.error is not None
    assert outcome.error.kind is FailureKind.OTHER
    assert len(crawler.calls) == 1
    assert outcome.notes == []


async def test_squid_title_on_a_successful_page_is_kept() -> None:
    page = make_result(status_code=200, html=squid_page("ERR_SECURE_CONNECT_FAIL"))
    outcome, crawler, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=FakeCrawler(page))
    assert outcome.ok
    assert len(crawler.calls) == 1
    assert outcome.notes == []


# --- proxy interference on the HTTP download (C34) ---------------------------------------

FILE_URL = "https://example.com/file.zip"
TLS_TEXT = (
    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
    "self-signed certificate in certificate chain (_ssl.c:1000)"
)


class ProxyAwareHttp(FakeHttp):
    """A ``FakeHttp`` that answers requests through a proxy with a separate handler."""

    def __init__(self, with_proxy: HttpHandler, without_proxy: HttpHandler) -> None:
        super().__init__(without_proxy)
        self.with_proxy = with_proxy

    def __call__(self, proxy: str | None, timeout_s: float) -> httpx.AsyncClient:
        self.client_calls.append((proxy, timeout_s))
        handler = self.with_proxy if proxy is not None else self.handler

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return handler(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True)


def raising(exc: Exception) -> HttpHandler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def zip_file(request: httpx.Request) -> httpx.Response:
    return response("application/zip", b"PK\x03\x04")


def squid_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        503,
        headers={"content-type": "text/html", "X-Squid-Error": "ERR_SECURE_CONNECT_FAIL 0"},
        text=squid_page("ERR_SECURE_CONNECT_FAIL"),
    )


def challenge_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        403, headers={"content-type": "text/html", "CF-Mitigated": "challenge"}, text="x"
    )


# (what the HTTP client meets through the proxy, error the download is reported as)
DOWNLOAD_FAILURES = [
    pytest.param(raising(httpx.ConnectError(TLS_TEXT)), TlsFetchError, id="tls"),
    pytest.param(raising(httpx.ProxyError("proxy said no")), ProxyFetchError, id="proxy"),
    pytest.param(raising(httpx.ConnectTimeout("timed out")), FetchTimeoutError, id="timeout"),
    pytest.param(
        raising(httpx.ConnectError("[Errno 61] Connection refused")),
        ConnectionRefusedFetchError,
        id="connection-refused",
    ),
    pytest.param(squid_response, ProxyFetchError, id="squid-error-page"),
    pytest.param(challenge_response, BlockedFetchError, id="bot-challenge"),
]


@pytest.mark.parametrize(("through_proxy", "error_type"), DOWNLOAD_FAILURES)
async def test_download_failure_through_proxy_falls_back_to_direct(
    through_proxy: HttpHandler, error_type: type[FetchError]
) -> None:
    http = ProxyAwareHttp(through_proxy, zip_file)
    crawler = FakeCrawler(proxy_aware(fail("net::ERR_ABORTED at " + FILE_URL), make_result()))
    outcome, crawler, _ = await fetch_one(
        FetchOptions(proxy=PROXY), crawler=crawler, http=http, url=FILE_URL
    )
    assert outcome.ok, outcome.error
    assert outcome.content_kind is ContentKind.BINARY
    assert outcome.data == b"PK\x03\x04"
    # Probe and download through the proxy, then the direct probe finds the file.
    assert http.proxies == [PROXY, PROXY, None]
    assert len(crawler.calls) == 1
    proxy_note, resource_note = outcome.notes
    assert proxy_note.template == PROXY_NOTE
    assert type(proxy_note.params["error"]) is error_type
    assert str(resource_note) == "not a web page (application/zip); saved the original file"
    assert_no_secret(outcome)


@pytest.mark.parametrize(("through_proxy", "error_type"), DOWNLOAD_FAILURES)
async def test_download_failure_with_fallback_disabled_keeps_its_kind(
    through_proxy: HttpHandler, error_type: type[FetchError]
) -> None:
    http = ProxyAwareHttp(through_proxy, zip_file)
    crawler = FakeCrawler(fail("net::ERR_ABORTED at " + FILE_URL))
    outcome, crawler, _ = await fetch_one(
        FetchOptions(proxy=PROXY, fallback=False), crawler=crawler, http=http, url=FILE_URL
    )
    assert not outcome.ok
    assert type(outcome.error) is error_type
    assert len(crawler.calls) == 1
    assert outcome.notes == []
    assert_no_secret(outcome)


async def test_download_tls_failure_of_a_non_html_page_keeps_its_kind() -> None:
    # The browser got a non-HTML response without HTML; the HTTP download then fails.
    http = ProxyAwareHttp(raising(httpx.ConnectError(TLS_TEXT)), zip_file)
    crawler = FakeCrawler(
        make_result(html="", response_headers={"content-type": "application/zip"})
    )
    outcome, _, _ = await fetch_one(
        FetchOptions(proxy=PROXY, fallback=False), crawler=crawler, http=http
    )
    assert not outcome.ok
    assert isinstance(outcome.error, TlsFetchError)
    assert str(outcome.error) == f"TLS error: {TLS_TEXT}: {URL}"


async def test_download_other_failure_is_still_non_html_content() -> None:
    http = ProxyAwareHttp(raising(httpx.RemoteProtocolError("bad")), zip_file)
    crawler = FakeCrawler(fail("net::ERR_ABORTED at " + FILE_URL))
    outcome, crawler, _ = await fetch_one(
        FetchOptions(proxy=PROXY), crawler=crawler, http=http, url=FILE_URL
    )
    assert not outcome.ok
    assert isinstance(outcome.error, NonHtmlContentError)
    assert len(crawler.calls) == 1
    assert outcome.notes == []


async def test_download_tls_failure_without_proxy_is_not_retried() -> None:
    http = FakeHttp(raising(httpx.ConnectError(TLS_TEXT)))
    crawler = FakeCrawler(fail("net::ERR_ABORTED at " + FILE_URL))
    outcome, crawler, http = await fetch_one(crawler=crawler, http=http, url=FILE_URL)
    assert not outcome.ok
    assert isinstance(outcome.error, TlsFetchError)
    assert http.proxies == [None, None]
    assert outcome.notes == []


async def test_download_squid_error_page_without_proxy_is_a_generic_failure() -> None:
    crawler = FakeCrawler(fail("net::ERR_ABORTED at " + FILE_URL))
    outcome, _, _ = await fetch_one(crawler=crawler, http=FakeHttp(squid_response), url=FILE_URL)
    assert not outcome.ok
    assert type(outcome.error) is FetchError
    assert str(outcome.error) == f"fetch failed: ERR_SECURE_CONNECT_FAIL 0: {FILE_URL}"
    assert outcome.status_code == 503


async def test_download_challenge_without_proxy_is_non_html_content() -> None:
    crawler = FakeCrawler(fail("net::ERR_ABORTED at " + FILE_URL))
    outcome, _, _ = await fetch_one(
        crawler=crawler, http=FakeHttp(challenge_response), url=FILE_URL
    )
    assert not outcome.ok
    assert isinstance(outcome.error, NonHtmlContentError)
