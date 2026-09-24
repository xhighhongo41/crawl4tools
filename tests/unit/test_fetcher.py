from __future__ import annotations

import base64
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from conftest import FakeCrawler, FakeHttp, make_result

from crawl4tools.engine import fetcher as fetcher_module
from crawl4tools.engine.errors import (
    BrowserNotInstalledError,
    HttpStatusError,
    NonHtmlContentError,
    ProxyFetchError,
)
from crawl4tools.engine.fetcher import Fetcher, build_run_config
from crawl4tools.engine.models import (
    ContentKind,
    FailureKind,
    FetchOptions,
    FetchOutcome,
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


def assert_no_secret(outcome: FetchOutcome) -> None:
    texts = [*outcome.notes, str(outcome.error) if outcome.error else ""]
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
    note = outcome.notes[0]
    assert note.startswith("proxy failed (")
    assert note.endswith("); retried with a direct connection")
    assert "http://***@proxy.example:8080" in note
    assert_no_secret(outcome)


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
    assert outcome.notes[0].startswith("proxy failed (")
    assert_no_secret(outcome)


async def test_http_502_through_proxy_falls_back() -> None:
    crawler = FakeCrawler(proxy_aware(make_result(status_code=502), make_result()))
    outcome, crawler, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert outcome.ok
    assert outcome.status_code == 200
    assert len(crawler.calls) == 2
    assert "HTTP 502" in outcome.notes[0]
    assert_no_secret(outcome)


async def test_timeout_through_proxy_falls_back() -> None:
    crawler = FakeCrawler(proxy_aware(fail("Timeout 60000ms exceeded."), make_result()))
    outcome, _, _ = await fetch_one(FetchOptions(proxy=PROXY), crawler=crawler)
    assert outcome.ok
    assert "timed out" in outcome.notes[0]
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
    assert outcome.notes == ["no extractable text in PDF; saved an empty document"]


async def test_corrupt_pdf_falls_back_to_raw() -> None:
    outcome, crawler, _ = await fetch_one(http=pdf_http(b"not really a pdf"))
    assert outcome.ok
    assert outcome.content_kind is ContentKind.PDF
    assert outcome.data == b"not really a pdf"
    assert outcome.text is None
    assert outcome.suggested_extension == ".pdf"
    assert len(outcome.notes) == 1
    assert outcome.notes[0].startswith("could not convert PDF to Markdown (")
    assert outcome.notes[0].endswith("); saved the original file")
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
    assert outcome.notes == notes
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
    assert outcome.notes == ["not a web page (image/jpeg); saved the original file"]
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
