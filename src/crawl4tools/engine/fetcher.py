"""The per-URL fetch pipeline: HTTP probe, browser crawl, judgement, fallback.

:class:`Fetcher` turns URLs into :class:`~crawl4tools.engine.models.FetchOutcome`
objects and never raises for fetch failures. Non-HTML resources (PDFs,
images, downloads) are detected with a cheap HTTP probe and fetched without
the browser; everything else goes through crawl4ai, whose browser is only
started when a URL actually needs it.

crawl4ai is imported lazily inside functions so that importing this module
(and therefore starting the CLI) stays fast.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from types import TracebackType
from typing import Any, Protocol

from crawl4tools.engine.classify import build_error, classify_error_message, error_for_status
from crawl4tools.engine.errors import FetchError, NonHtmlContentError
from crawl4tools.engine.models import (
    ContentKind,
    FailureKind,
    FetchOptions,
    FetchOutcome,
    OutputFormat,
)
from crawl4tools.engine.naming import extension_for
from crawl4tools.engine.pdf import PdfConversionError, pdf_to_markdown
from crawl4tools.engine.probe import (
    HttpClientFactory,
    ProbeResult,
    default_http_client,
    probe,
)
from crawl4tools.engine.proxy import normalize_proxy, should_fallback

_PDF_MEDIA_TYPE = "application/pdf"
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})


class CrawlerLike(Protocol):
    """The subset of ``AsyncWebCrawler`` the fetcher relies on."""

    async def arun(self, url: str, config: Any) -> Any:
        """Crawl *url* with the given ``CrawlerRunConfig``."""
        ...


CrawlerFactory = Callable[[FetchOptions], AbstractAsyncContextManager[CrawlerLike]]
"""Create a (not yet started) crawler context manager for the given options."""


def default_crawler_factory(options: FetchOptions) -> AbstractAsyncContextManager[CrawlerLike]:
    """Return a headless crawl4ai ``AsyncWebCrawler`` that logs to stderr only."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig
    from crawl4ai.async_logger import AsyncLogger

    crawler: AbstractAsyncContextManager[CrawlerLike] = AsyncWebCrawler(
        config=BrowserConfig(headless=True, verbose=options.verbose),
        logger=AsyncLogger(verbose=options.verbose),
    )
    return crawler


def build_run_config(options: FetchOptions, proxy: str | None) -> Any:
    """Build the crawl4ai ``CrawlerRunConfig`` for one browser attempt."""
    from crawl4ai import CacheMode, CrawlerRunConfig, DefaultMarkdownGenerator

    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=int(options.timeout_s * 1000),
        verbose=options.verbose,
        proxy_config=proxy,
        pdf=options.format is OutputFormat.PDF,
        screenshot=options.format is OutputFormat.SCREENSHOT,
        capture_mhtml=options.format is OutputFormat.MHTML,
        markdown_generator=DefaultMarkdownGenerator(
            options={
                "ignore_links": options.ignore_links,
                "ignore_images": options.ignore_images,
                "body_width": 0,
            }
        ),
    )


def _describe(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


def _header(headers: Mapping[str, Any] | None, name: str) -> str | None:
    """Return header *name* from *headers*, matching the key case-insensitively."""
    if not headers:
        return None
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return None if value is None else str(value)
    return None


def _media_type(content_type: str | None) -> str | None:
    if not content_type:
        return None
    return content_type.split(";", 1)[0].strip().lower() or None


class _StartFailure(Exception):
    """Internal marker: the crawler could not be started (see ``Fetcher``)."""


class Fetcher:
    """Fetch URLs according to :class:`FetchOptions`.

    Use as an async context manager so the browser (if it was started) is
    closed afterwards. The browser is started lazily on the first URL that
    needs it; if starting fails, that failure is remembered and reported
    for every later URL that needs the browser instead of retrying.
    """

    def __init__(
        self,
        options: FetchOptions,
        *,
        crawler_factory: CrawlerFactory = default_crawler_factory,
        http_client_factory: HttpClientFactory = default_http_client,
    ) -> None:
        """Create a fetcher.

        Raises:
            ValueError: if ``options.proxy`` is not a valid proxy URL.
        """
        if options.proxy is not None:
            options = replace(options, proxy=normalize_proxy(options.proxy))
        self._options = options
        self._crawler_factory = crawler_factory
        self._http_client_factory = http_client_factory
        self._lock = asyncio.Lock()
        self._crawler_cm: AbstractAsyncContextManager[CrawlerLike] | None = None
        self._crawler: CrawlerLike | None = None
        self._start_error: str | None = None

    @property
    def options(self) -> FetchOptions:
        """Return the effective options (with the proxy normalized)."""
        return self._options

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the browser if it was started. Safe to call more than once."""
        async with self._lock:
            crawler_cm = self._crawler_cm
            self._crawler_cm = None
            self._crawler = None
        if crawler_cm is not None:
            await crawler_cm.__aexit__(None, None, None)

    async def _get_crawler(self) -> CrawlerLike:
        """Return the running crawler, starting it on first use.

        Raises:
            _StartFailure: if the crawler could not be started (now or before).
        """
        async with self._lock:
            if self._crawler is not None:
                return self._crawler
            if self._start_error is None:
                try:
                    crawler_cm = self._crawler_factory(self._options)
                    crawler = await crawler_cm.__aenter__()
                except Exception as exc:
                    self._start_error = _describe(exc)
                else:
                    self._crawler_cm = crawler_cm
                    self._crawler = crawler
                    return crawler
            raise _StartFailure(self._start_error)

    async def fetch_many(self, urls: Sequence[str], concurrency: int = 3) -> list[FetchOutcome]:
        """Fetch *urls* with at most *concurrency* in flight, preserving order.

        Raises:
            ValueError: if *concurrency* is less than 1.
        """
        if concurrency < 1:
            raise ValueError(f"concurrency must be at least 1, got {concurrency}")
        semaphore = asyncio.Semaphore(concurrency)

        async def run(url: str) -> FetchOutcome:
            async with semaphore:
                return await self.fetch(url)

        return list(await asyncio.gather(*(run(url) for url in urls)))

    async def fetch(self, url: str) -> FetchOutcome:
        """Fetch a single URL. Failures are reported in the outcome, not raised."""
        try:
            return await self._fetch(url)
        except Exception as exc:
            # Last-resort safety net: a bug in one URL must not abort a batch.
            return self._failure(url, FetchError(url, _describe(exc)))

    async def _fetch(self, url: str) -> FetchOutcome:
        options = self._options
        first, retriable = await self._attempt(url, options.proxy)
        error = first.error
        if first.ok or error is None or not retriable:
            return first
        status_code = first.status_code if error.kind is FailureKind.HTTP_STATUS else None
        if not should_fallback(
            options=options, kind=error.kind, status_code=status_code, already_retried=False
        ):
            return first
        second, _ = await self._attempt(url, None)
        # Errors redact proxy credentials themselves, so str() is safe here.
        second.notes.insert(0, f"proxy failed ({error}); retried with a direct connection")
        return second

    async def _attempt(self, url: str, proxy: str | None) -> tuple[FetchOutcome, bool]:
        """Run one probe + browser attempt for *url* through *proxy*.

        Returns the outcome and whether a failure may be retried without the
        proxy (False when the browser itself could not be started).
        """
        options = self._options
        probed = await probe(
            url,
            proxy=proxy,
            timeout_s=options.timeout_s,
            client_factory=self._http_client_factory,
        )
        if probed.ok and not probed.is_html and probed.body is not None:
            return await self._resource_outcome(url, probed), True

        try:
            crawler = await self._get_crawler()
        except _StartFailure as exc:
            detail = _describe(exc)
            start_error = build_error(url, classify_error_message(detail), detail=detail)
            # Retrying without the proxy cannot fix a browser that won't start.
            return self._failure(url, start_error), False

        try:
            result = await crawler.arun(url, config=build_run_config(options, proxy))
        except Exception as exc:
            detail = _describe(exc)
            return self._failure(url, self._classified_error(url, detail, proxy)), True

        return await self._judge(url, proxy, result), True

    def _classified_error(self, url: str, detail: str | None, proxy: str | None) -> FetchError:
        return build_error(
            url,
            classify_error_message(detail),
            detail=detail,
            timeout_s=self._options.timeout_s,
            proxy=proxy,
        )

    async def _download(self, url: str, proxy: str | None) -> ProbeResult | None:
        """Download *url* over plain HTTP; return the probe only if it succeeded."""
        downloaded = await probe(
            url,
            proxy=proxy,
            timeout_s=self._options.timeout_s,
            client_factory=self._http_client_factory,
            read_body=True,
        )
        if downloaded.ok and downloaded.body is not None:
            return downloaded
        return None

    async def _judge(self, url: str, proxy: str | None, result: Any) -> FetchOutcome:
        """Turn a crawl4ai result into an outcome (possibly via an HTTP download)."""
        status_code: int | None = getattr(result, "status_code", None)
        if not getattr(result, "success", False):
            detail: str | None = getattr(result, "error_message", None)
            kind = classify_error_message(detail)
            if kind is FailureKind.NON_HTML:
                downloaded = await self._download(url, proxy)
                if downloaded is not None:
                    return await self._resource_outcome(url, downloaded)
                return self._failure(url, NonHtmlContentError(url, detail), status_code)
            return self._failure(url, self._classified_error(url, detail, proxy), status_code)

        status_error = error_for_status(url, status_code)
        if status_error is not None:
            return self._failure(url, status_error, status_code)

        content_type = _header(getattr(result, "response_headers", None), "content-type")
        media_type = _media_type(content_type)
        html: str | None = getattr(result, "html", None)
        if (
            media_type is not None
            and media_type not in _HTML_MEDIA_TYPES
            and not (html or "").strip()
        ):
            downloaded = await self._download(url, proxy)
            if downloaded is not None:
                return await self._resource_outcome(url, downloaded)
            return self._failure(url, FetchError(url, "browser returned no HTML"), status_code)

        return self._page_outcome(url, result, status_code, content_type)

    def _page_outcome(
        self, url: str, result: Any, status_code: int | None, content_type: str | None
    ) -> FetchOutcome:
        """Assemble a successful outcome for an HTML page in the requested format."""
        fmt = self._options.format
        outcome = FetchOutcome(
            url=url,
            ok=True,
            final_url=getattr(result, "redirected_url", None) or url,
            status_code=status_code,
            content_kind=ContentKind.HTML,
            content_type=content_type,
            suggested_extension=extension_for(fmt),
        )
        html: str = getattr(result, "html", None) or ""
        if fmt is OutputFormat.MARKDOWN:
            markdown = result.markdown
            if self._options.citations:
                outcome.text = (
                    f"{markdown.markdown_with_citations}\n\n{markdown.references_markdown}"
                )
            else:
                outcome.text = markdown.raw_markdown
        elif fmt is OutputFormat.HTML:
            outcome.text = html
        elif fmt is OutputFormat.MHTML:
            if result.mhtml is None:
                return self._failure(
                    url, FetchError(url, "the browser did not produce MHTML"), status_code
                )
            outcome.text = result.mhtml
        elif fmt is OutputFormat.PDF:
            if result.pdf is None:
                return self._failure(
                    url, FetchError(url, "the browser did not produce a PDF"), status_code
                )
            outcome.data = bytes(result.pdf)
        elif fmt is OutputFormat.SCREENSHOT:
            if result.screenshot is None:
                return self._failure(
                    url, FetchError(url, "the browser did not produce a screenshot"), status_code
                )
            outcome.data = base64.b64decode(result.screenshot)
        else:  # OutputFormat.RAW for an HTML page: the page source as-is.
            outcome.data = html.encode("utf-8")
            outcome.suggested_extension = ".html"
        return outcome

    async def _resource_outcome(self, url: str, probed: ProbeResult) -> FetchOutcome:
        """Assemble an outcome for a non-HTML resource downloaded over HTTP."""
        fmt = self._options.format
        body = probed.body if probed.body is not None else b""
        media_type = probed.media_type
        outcome = FetchOutcome(
            url=url,
            ok=True,
            final_url=probed.final_url or url,
            status_code=probed.status_code,
            content_type=probed.content_type,
        )
        if media_type == _PDF_MEDIA_TYPE:
            outcome.content_kind = ContentKind.PDF
            if fmt is OutputFormat.MARKDOWN:
                try:
                    markdown = await pdf_to_markdown(body)
                except PdfConversionError as exc:
                    outcome.data = body
                    outcome.suggested_extension = ".pdf"
                    outcome.notes.append(
                        f"could not convert PDF to Markdown ({exc}); saved the original file"
                    )
                    return outcome
                outcome.text = markdown
                outcome.suggested_extension = ".md"
                if not markdown:
                    outcome.notes.append("no extractable text in PDF; saved an empty document")
                return outcome
            outcome.data = body
            outcome.suggested_extension = ".pdf"
            if fmt not in (OutputFormat.RAW, OutputFormat.PDF):
                outcome.notes.append(f"PDF saved as-is (format '{fmt}' does not apply)")
            return outcome

        outcome.content_kind = ContentKind.BINARY
        outcome.data = body
        outcome.suggested_extension = extension_for(
            OutputFormat.RAW, content_type=probed.content_type, url=probed.final_url or url
        )
        outcome.notes.append(
            f"not a web page ({media_type or 'unknown type'}); saved the original file"
        )
        return outcome

    def _failure(self, url: str, error: FetchError, status_code: int | None = None) -> FetchOutcome:
        return FetchOutcome(
            url=url,
            ok=False,
            status_code=status_code,
            error=error,
            suggested_extension=extension_for(self._options.format),
        )
