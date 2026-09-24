"""Shared test doubles: a fake crawler, crawl-result builder, and httpx mocks.

Nothing here starts a real browser or touches the network.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Any

import httpx
import pytest

from crawl4tools.engine.models import FetchOptions

FIXTURES = Path(__file__).parent / "fixtures"

# Variables that choose the message language of the three commands.
_LANGUAGE_ENV_VARS = ("CRAWL4CLI_LANG", "CRAWL4MCP_LANG", "CRAWL4SERVER_LANG")

_DEFAULT_HTML = "<html><body><h1>Hello</h1></body></html>"


def make_result(**overrides: Any) -> SimpleNamespace:
    """Return a fake crawl4ai ``CrawlResult`` with every field defaulted."""
    markdown = SimpleNamespace(
        raw_markdown=overrides.pop("raw_markdown", "# Hello"),
        markdown_with_citations=overrides.pop("markdown_with_citations", "# Hello [1]"),
        references_markdown=overrides.pop("references_markdown", "## References\n[1]: x"),
        fit_markdown=overrides.pop("fit_markdown", "# Hello (fit)"),
    )
    fields: dict[str, Any] = {
        "success": True,
        "error_message": None,
        "status_code": 200,
        "url": "https://example.com/",
        "redirected_url": None,
        "html": _DEFAULT_HTML,
        "markdown": markdown,
        "metadata": {"title": "Example Domain"},
        "response_headers": {"content-type": "text/html"},
        "pdf": None,
        "screenshot": None,
        "mhtml": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


# A handler returns a result or an exception (which ``arun`` then raises).
ArunHandler = Callable[[str, Any], Any]


class FakeCrawler:
    """A stand-in for ``AsyncWebCrawler`` with a programmable ``arun``.

    ``handler`` may be a callable ``(url, config) -> result``, a mapping from
    URL to result, or a single result returned for every URL. A handler
    value that is an exception instance is raised from ``arun``.
    """

    def __init__(
        self,
        handler: ArunHandler | Mapping[str, Any] | Any = None,
        *,
        start_error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        self.handler = handler
        self.start_error = start_error
        self.delay = delay
        self.calls: list[tuple[str, Any]] = []
        self.factory_calls = 0
        self.entered = 0
        self.exited = 0
        self.in_flight = 0
        self.max_in_flight = 0

    def factory(self, options: FetchOptions) -> FakeCrawler:
        """Crawler factory compatible with ``Fetcher(crawler_factory=...)``."""
        self.factory_calls += 1
        return self

    @property
    def started(self) -> bool:
        """Return True if the crawler was ever entered."""
        return self.entered > 0

    async def __aenter__(self) -> FakeCrawler:
        self.entered += 1
        if self.start_error is not None:
            raise self.start_error
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.exited += 1

    def _resolve(self, url: str, config: Any) -> Any:
        if self.handler is None:
            return make_result(url=url)
        if callable(self.handler):
            return self.handler(url, config)
        if isinstance(self.handler, Mapping):
            return self.handler[url]
        return self.handler

    async def arun(self, url: str, config: Any) -> Any:
        """Record the call and return (or raise) the programmed result."""
        self.calls.append((url, config))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            else:
                await asyncio.sleep(0)
            result = self._resolve(url, config)
        finally:
            self.in_flight -= 1
        if isinstance(result, BaseException):
            raise result
        return result


HttpHandler = Callable[[httpx.Request], httpx.Response]


def html_response(request: httpx.Request) -> httpx.Response:
    """Default HTTP handler: every URL is a small HTML page."""
    return httpx.Response(200, headers={"content-type": "text/html"}, text=_DEFAULT_HTML)


class FakeHttp:
    """An ``HttpClientFactory`` backed by ``httpx.MockTransport``.

    Records the ``(proxy, timeout_s)`` arguments of every client creation and
    every request that reaches the transport.
    """

    def __init__(self, handler: HttpHandler = html_response) -> None:
        self.handler = handler
        self.client_calls: list[tuple[str | None, float]] = []
        self.requests: list[httpx.Request] = []

    @property
    def proxies(self) -> list[str | None]:
        """Return the proxy argument of every client creation, in order."""
        return [proxy for proxy, _ in self.client_calls]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def __call__(self, proxy: str | None, timeout_s: float) -> httpx.AsyncClient:
        self.client_calls.append((proxy, timeout_s))
        return httpx.AsyncClient(transport=httpx.MockTransport(self._handle), follow_redirects=True)


@pytest.fixture(autouse=True)
def english_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every test see English messages, whatever the machine's locale is.

    The command-specific language variables are removed and ``LANGUAGE`` is
    set to English; the CLI consults it before ``LC_ALL`` / ``LC_MESSAGES`` /
    ``LANG``. A test that wants Japanese passes ``--lang ja`` or sets a
    variable itself. Subprocesses started by integration tests inherit this.
    """
    for name in _LANGUAGE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGUAGE", "en")


@pytest.fixture
def sample_pdf() -> bytes:
    """Return the bytes of the committed one-page sample PDF."""
    return (FIXTURES / "sample.pdf").read_bytes()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip integration tests (real browser and network) unless explicitly requested."""
    if os.environ.get("CRAWL4TOOLS_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set CRAWL4TOOLS_INTEGRATION=1 to run integration tests")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
