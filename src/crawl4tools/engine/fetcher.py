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
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from types import TracebackType
from typing import Any, Protocol

from crawl4tools.engine.classify import build_error, classify_error_message, error_for_status
from crawl4tools.engine.errors import FetchError, HttpStatusError, NonHtmlContentError
from crawl4tools.engine.interception import InterferenceSign, detect_interference
from crawl4tools.engine.models import (
    ContentKind,
    FailureKind,
    FetchOptions,
    FetchOutcome,
    Note,
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
from crawl4tools.i18n import N_

logger = logging.getLogger(__name__)

_PDF_MEDIA_TYPE = "application/pdf"
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})

# HTTP download failures on the way to the server (through the proxy, if any)
# rather than at the URL itself. They keep their kind, so they can trigger the
# direct-connection fallback; any other failed download means the content
# could not be used.
_DOWNLOAD_PATH_FAILURES = frozenset(
    {
        FailureKind.TLS,
        FailureKind.PROXY,
        FailureKind.TIMEOUT,
        FailureKind.CONNECTION_REFUSED,
    }
)

# How much of a downloaded body is decoded to look for a proxy's error page:
# its title comes first, and the body may be a large file.
_ERROR_PAGE_SCAN_BYTES = 64 * 1024

# crawl4ai 0.9.4's own anti-bot check (antibot_detector.is_blocked) marks some
# failed results this way, e.g. "Blocked by anti-bot protection: HTTP 503
# with HTML content (180 bytes)". See _judge, which turns these back into the
# HTTP status they report whenever one is available.
_ANTI_BOT_PREFIX = "Blocked by anti-bot protection"


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
    from crawl4ai import (
        CacheMode,
        CrawlerRunConfig,
        DefaultMarkdownGenerator,
        PruningContentFilterLXML,
    )

    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=int(options.timeout_s * 1000),
        verbose=options.verbose,
        proxy_config=proxy,
        pdf=options.format is OutputFormat.PDF,
        screenshot=options.format is OutputFormat.SCREENSHOT,
        capture_mhtml=options.format is OutputFormat.MHTML,
        markdown_generator=DefaultMarkdownGenerator(
            content_filter=PruningContentFilterLXML() if options.fit else None,
            options={
                "ignore_links": options.ignore_links,
                "ignore_images": options.ignore_images,
                "body_width": 0,
            },
        ),
    )


# crawl4ai reports a failing content filter by returning this text as the content.
_FIT_ERROR_PREFIX = "Error generating fit markdown:"


def _citations(markdown: str, base_url: str) -> str:
    """Turn inline links of *markdown* into numbered references, crawl4ai style."""
    from crawl4ai import DefaultMarkdownGenerator

    body, references = DefaultMarkdownGenerator().convert_links_to_citations(markdown, base_url)
    return f"{body}\n\n{references}"


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


def _normalized(options: FetchOptions) -> FetchOptions:
    """Return *options* with its proxy normalized.

    Raises:
        ValueError: if ``options.proxy`` is not a valid proxy URL.
    """
    if options.proxy is None:
        return options
    return replace(options, proxy=normalize_proxy(options.proxy))


class _StartFailure(Exception):
    """Internal marker: the crawler could not be started (see ``Fetcher``)."""


class Fetcher:
    """Fetch URLs according to :class:`FetchOptions`.

    Use as an async context manager so the browser (if it was started) is
    closed afterwards. The browser is started lazily on the first URL that
    needs it; if starting fails, that failure is remembered and reported
    for every later URL that needs the browser instead of retrying.

    After redirects, a page is judged by its final response. crawl4ai
    reports the final status (``redirected_status_code``) but only the
    first response's headers, so the final response's headers are kept by
    an ``after_goto`` hook registered on the crawler when it starts; when
    the hook has none for the URL (or its status differs), the first
    response's headers are used.
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
        self._options = _normalized(options)
        self._crawler_factory = crawler_factory
        self._http_client_factory = http_client_factory
        self._lock = asyncio.Lock()
        self._crawler_cm: AbstractAsyncContextManager[CrawlerLike] | None = None
        self._crawler: CrawlerLike | None = None
        self._start_error: str | None = None
        # Final response (status, headers) of each URL, as seen by the after_goto
        # hook; taken out when the URL's crawl result is judged.
        self._final_responses: dict[str, tuple[int | None, dict[str, str]]] = {}

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
                    self._register_hooks(crawler)
                    return crawler
            raise _StartFailure(self._start_error)

    def _register_hooks(self, crawler: CrawlerLike) -> None:
        """Register :meth:`_after_goto` on *crawler*'s strategy, if it takes hooks."""
        strategy = getattr(crawler, "crawler_strategy", None)
        set_hook = getattr(strategy, "set_hook", None)
        if not callable(set_hook):
            return
        try:
            set_hook("after_goto", self._after_goto)
        except Exception:
            # Without the hook, pages are judged by the first response's headers.
            logger.debug("could not register the after_goto hook", exc_info=True)

    async def _after_goto(
        self, page: Any, *, url: str | None = None, response: Any = None, **_kwargs: Any
    ) -> Any:
        """crawl4ai ``after_goto`` hook: keep the final response's status and headers.

        *response* is what ``page.goto()`` returned, i.e. the last response of
        the redirect chain. Returns *page*, as crawl4ai's hooks do.
        """
        if response is not None and url:
            try:
                status: int | None = getattr(response, "status", None)
                self._final_responses[url] = (status, dict(response.headers))
            except Exception:
                logger.debug("could not keep the final response of %s", url, exc_info=True)
        return page

    def _take_final_response(self, url: str) -> tuple[int | None, dict[str, str]] | None:
        """Remove and return the final response the hook kept for *url*, if any.

        The browser may report the URL with or without a trailing slash, so
        that variant is looked up too.
        """
        final = self._final_responses.pop(url, None)
        if final is not None:
            return final
        variant = url[:-1] if url.endswith("/") else f"{url}/"
        return self._final_responses.pop(variant, None)

    async def fetch_many(
        self,
        urls: Sequence[str],
        concurrency: int = 3,
        *,
        options: FetchOptions | None = None,
    ) -> list[FetchOutcome]:
        """Fetch *urls* with at most *concurrency* in flight, preserving order.

        *options* apply to every URL of this call; when omitted, the options
        given to the constructor are used. The browser is shared either way.

        Raises:
            ValueError: if *concurrency* is less than 1 or ``options.proxy``
                is not a valid proxy URL.
        """
        if concurrency < 1:
            raise ValueError(f"concurrency must be at least 1, got {concurrency}")
        effective = self._options if options is None else _normalized(options)
        semaphore = asyncio.Semaphore(concurrency)

        async def run(url: str) -> FetchOutcome:
            async with semaphore:
                return await self.fetch(url, options=effective)

        return list(await asyncio.gather(*(run(url) for url in urls)))

    async def fetch(self, url: str, *, options: FetchOptions | None = None) -> FetchOutcome:
        """Fetch a single URL. Failures are reported in the outcome, not raised.

        *options* apply to this call only; when omitted, the options given to
        the constructor are used. The browser is shared either way.

        Raises:
            ValueError: if ``options.proxy`` is not a valid proxy URL (checked
                before anything is fetched).
        """
        effective = self._options if options is None else _normalized(options)
        try:
            return await self._fetch(url, effective)
        except Exception as exc:
            # Last-resort safety net: a bug in one URL must not abort a batch.
            return self._failure(url, FetchError(url, _describe(exc)), effective)

    async def _fetch(self, url: str, options: FetchOptions) -> FetchOutcome:
        first, retriable = await self._attempt(url, options.proxy, options)
        error = first.error
        if first.ok or error is None or not retriable:
            return first
        status_code = first.status_code if error.kind is FailureKind.HTTP_STATUS else None
        if not should_fallback(
            options=options, kind=error.kind, status_code=status_code, already_retried=False
        ):
            return first
        second, _ = await self._attempt(url, None, options)
        # Errors redact proxy credentials themselves, so quoting the error is safe.
        # It is kept as an object so it is rendered in the note's language.
        second.notes.insert(
            0,
            Note(N_("proxy failed ({error}); retried with a direct connection"), {"error": error}),
        )
        return second

    async def _attempt(
        self, url: str, proxy: str | None, options: FetchOptions
    ) -> tuple[FetchOutcome, bool]:
        """Run one probe + browser attempt for *url* through *proxy*.

        Returns the outcome and whether a failure may be retried without the
        proxy (False when the browser itself could not be started).
        """
        probed = await probe(
            url,
            proxy=proxy,
            timeout_s=options.timeout_s,
            client_factory=self._http_client_factory,
        )
        if probed.ok and not probed.is_html and probed.body is not None:
            return await self._resource_outcome(url, probed, options), True

        try:
            crawler = await self._get_crawler()
        except _StartFailure as exc:
            detail = _describe(exc)
            start_error = build_error(url, classify_error_message(detail), detail=detail)
            # Retrying without the proxy cannot fix a browser that won't start.
            return self._failure(url, start_error, options), False

        try:
            result = await crawler.arun(url, config=build_run_config(options, proxy))
        except Exception as exc:
            # The hook may have seen a response before the crawl failed; drop it.
            self._take_final_response(url)
            detail = _describe(exc)
            error = self._classified_error(url, detail, proxy, options)
            return self._failure(url, error, options), True

        return await self._judge(url, proxy, result, options), True

    def _classified_error(
        self, url: str, detail: str | None, proxy: str | None, options: FetchOptions
    ) -> FetchError:
        return build_error(
            url,
            classify_error_message(detail),
            detail=detail,
            timeout_s=options.timeout_s,
            proxy=proxy,
        )

    def _interference_error(
        self,
        url: str,
        proxy: str | None,
        headers: Mapping[str, Any] | None,
        html: str | None,
        status_code: int | None,
    ) -> FetchError | None:
        """Return the failure a response broken by an intercepting proxy is reported as.

        A proxy's own error page is always a failure: a proxy failure when a
        proxy was used, a generic one otherwise. A bot challenge only counts
        through a proxy; without one it is the site's own answer, judged by
        its status like any other response.
        """
        found = detect_interference(headers, html, status_code)
        if found is None:
            return None
        if found.sign is InterferenceSign.PROXY_ERROR_PAGE:
            return build_error(url, FailureKind.PROXY, detail=found.detail, proxy=proxy)
        # The challenge error names the status; a response without one is incomplete.
        if proxy is None or status_code is None:
            return None
        return build_error(url, FailureKind.BLOCKED, status_code=status_code)

    async def _download(self, url: str, proxy: str | None, options: FetchOptions) -> ProbeResult:
        """Download *url* over plain HTTP, reading the body whatever the status."""
        return await probe(
            url,
            proxy=proxy,
            timeout_s=options.timeout_s,
            client_factory=self._http_client_factory,
            read_body=True,
        )

    def _download_error(
        self, url: str, proxy: str | None, downloaded: ProbeResult, options: FetchOptions
    ) -> FetchError | None:
        """Return the failure a download that failed on the way is reported as, if any."""
        kind = downloaded.error_kind
        if kind is not None and kind in _DOWNLOAD_PATH_FAILURES:
            return build_error(
                url,
                kind,
                detail=downloaded.error_detail,
                timeout_s=options.timeout_s,
                proxy=proxy,
            )
        html = None
        if downloaded.body is not None and downloaded.is_html:
            html = downloaded.body[:_ERROR_PAGE_SCAN_BYTES].decode("utf-8", errors="replace")
        return self._interference_error(
            url, proxy, downloaded.headers, html, downloaded.status_code
        )

    async def _downloaded_outcome(
        self,
        url: str,
        proxy: str | None,
        options: FetchOptions,
        unusable: FetchError,
        status_code: int | None,
    ) -> FetchOutcome:
        """Fetch *url* over plain HTTP because the browser could not use it.

        A download that failed on the way (or that the proxy answered
        itself) is reported as that failure, so it can be retried without
        the proxy. Any other failed download is reported as *unusable*,
        with the browser's *status_code*.
        """
        downloaded = await self._download(url, proxy, options)
        error = self._download_error(url, proxy, downloaded, options)
        if error is not None:
            return self._failure(url, error, options, downloaded.status_code)
        if downloaded.ok and downloaded.body is not None:
            return await self._resource_outcome(url, downloaded, options)
        return self._failure(url, unusable, options, status_code)

    async def _judge(
        self, url: str, proxy: str | None, result: Any, options: FetchOptions
    ) -> FetchOutcome:
        """Turn a crawl4ai result into an outcome (possibly via an HTTP download).

        After redirects the final response is judged: its status is
        crawl4ai's ``redirected_status_code`` (the first response's
        ``status_code`` when absent), and its headers are those the
        ``after_goto`` hook kept for *url* with that same status. Otherwise
        the first response's headers (``response_headers``) are used.

        A failure whose message is crawl4ai's own anti-bot verdict (see
        ``_ANTI_BOT_PREFIX``) is reported as the HTTP status it names,
        provided one was actually returned, so it is treated exactly like
        the same status without that verdict (in particular, it can trigger
        the direct-connection fallback for 503 like any other HTTP_STATUS
        failure). Without a usable status it stays a generic failure.
        """
        final = self._take_final_response(url)
        first_status: int | None = getattr(result, "status_code", None)
        final_status: int | None = getattr(result, "redirected_status_code", None)
        status_code = final_status if final_status is not None else first_status
        headers: Mapping[str, Any] | None
        if final is not None and final[0] == status_code:
            headers = final[1]
        else:
            headers = getattr(result, "response_headers", None)
        html: str | None = getattr(result, "html", None)
        # Checked before success: crawl4ai's own anti-bot check marks a proxy's
        # error page or a challenge (a 403/503 HTML page) as failed, but keeps
        # its headers and HTML. A failure without a response has neither.
        interference = self._interference_error(url, proxy, headers, html, status_code)
        if interference is not None:
            return self._failure(url, interference, options, status_code)

        if not getattr(result, "success", False):
            detail: str | None = getattr(result, "error_message", None)
            if (
                detail is not None
                and detail.startswith(_ANTI_BOT_PREFIX)
                and status_code is not None
                and status_code >= 400
            ):
                return self._failure(url, HttpStatusError(url, status_code), options, status_code)
            kind = classify_error_message(detail)
            if kind is FailureKind.NON_HTML:
                return await self._downloaded_outcome(
                    url, proxy, options, NonHtmlContentError(url, detail), status_code
                )
            error = self._classified_error(url, detail, proxy, options)
            return self._failure(url, error, options, status_code)

        status_error = error_for_status(url, status_code)
        if status_error is not None:
            return self._failure(url, status_error, options, status_code)

        content_type = _header(headers, "content-type")
        media_type = _media_type(content_type)
        if (
            media_type is not None
            and media_type not in _HTML_MEDIA_TYPES
            and not (html or "").strip()
        ):
            return await self._downloaded_outcome(
                url, proxy, options, FetchError(url, "browser returned no HTML"), status_code
            )

        return self._page_outcome(url, result, status_code, content_type, options)

    def _page_outcome(
        self,
        url: str,
        result: Any,
        status_code: int | None,
        content_type: str | None,
        options: FetchOptions,
    ) -> FetchOutcome:
        """Assemble a successful outcome for an HTML page in the requested format."""
        fmt = options.format
        outcome = FetchOutcome(
            url=url,
            ok=True,
            final_url=getattr(result, "redirected_url", None) or url,
            status_code=status_code,
            content_kind=ContentKind.HTML,
            content_type=content_type,
            suggested_extension=extension_for(fmt),
            title=self._page_title(result),
        )
        html: str = getattr(result, "html", None) or ""
        if fmt is OutputFormat.MARKDOWN:
            outcome.text = self._markdown_text(result.markdown, outcome, options)
        elif fmt is OutputFormat.HTML:
            outcome.text = html
        elif fmt is OutputFormat.MHTML:
            if result.mhtml is None:
                return self._failure(
                    url, FetchError(url, "the browser did not produce MHTML"), options, status_code
                )
            outcome.text = result.mhtml
        elif fmt is OutputFormat.PDF:
            if result.pdf is None:
                return self._failure(
                    url, FetchError(url, "the browser did not produce a PDF"), options, status_code
                )
            outcome.data = bytes(result.pdf)
        elif fmt is OutputFormat.SCREENSHOT:
            if result.screenshot is None:
                error = FetchError(url, "the browser did not produce a screenshot")
                return self._failure(url, error, options, status_code)
            outcome.data = base64.b64decode(result.screenshot)
        else:  # OutputFormat.RAW for an HTML page: the page source as-is.
            outcome.data = html.encode("utf-8")
            outcome.suggested_extension = ".html"
        return outcome

    def _page_title(self, result: Any) -> str | None:
        """Extract the page ``<title>`` from a crawl4ai result, if present."""
        metadata = getattr(result, "metadata", None) or {}
        title = metadata.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        return None

    def _markdown_text(self, markdown: Any, outcome: FetchOutcome, options: FetchOptions) -> str:
        """Pick the full or content-filtered Markdown, with citations if requested."""
        if options.fit:
            fit: str = getattr(markdown, "fit_markdown", None) or ""
            if fit.startswith(_FIT_ERROR_PREFIX):
                reason = fit[len(_FIT_ERROR_PREFIX) :].strip()
                outcome.notes.append(
                    Note(
                        N_("content filter failed ({reason}); saved the full page"),
                        {"reason": reason},
                    )
                )
            elif not fit.strip():
                outcome.notes.append(Note(N_("content filter kept nothing; saved the full page")))
            elif options.citations:
                return _citations(fit, outcome.final_url or outcome.url)
            else:
                return fit
        if options.citations:
            return f"{markdown.markdown_with_citations}\n\n{markdown.references_markdown}"
        text: str = markdown.raw_markdown
        return text

    async def _resource_outcome(
        self, url: str, probed: ProbeResult, options: FetchOptions
    ) -> FetchOutcome:
        """Assemble an outcome for a non-HTML resource downloaded over HTTP."""
        fmt = options.format
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
                    note = Note(
                        N_("could not convert PDF to Markdown ({error}); saved the original file"),
                        {"error": str(exc)},
                    )
                    outcome.notes.append(note)
                    return outcome
                outcome.text = markdown
                outcome.suggested_extension = ".md"
                if not markdown:
                    outcome.notes.append(
                        Note(N_("no extractable text in PDF; saved an empty document"))
                    )
                return outcome
            outcome.data = body
            outcome.suggested_extension = ".pdf"
            if fmt not in (OutputFormat.RAW, OutputFormat.PDF):
                outcome.notes.append(
                    Note(
                        N_("PDF saved as-is (format '{format}' does not apply)"),
                        {"format": fmt.value},
                    )
                )
            return outcome

        outcome.content_kind = ContentKind.BINARY
        outcome.data = body
        outcome.suggested_extension = extension_for(
            OutputFormat.RAW, content_type=probed.content_type, url=probed.final_url or url
        )
        if media_type:
            note = Note(
                N_("not a web page ({media_type}); saved the original file"),
                {"media_type": media_type},
            )
        else:
            note = Note(N_("not a web page (unknown type); saved the original file"))
        outcome.notes.append(note)
        return outcome

    def _failure(
        self,
        url: str,
        error: FetchError,
        options: FetchOptions,
        status_code: int | None = None,
    ) -> FetchOutcome:
        return FetchOutcome(
            url=url,
            ok=False,
            status_code=status_code,
            error=error,
            suggested_extension=extension_for(options.format),
        )
