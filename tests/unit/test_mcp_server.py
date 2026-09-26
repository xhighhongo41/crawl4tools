from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import httpx
import httpx2
import pytest
from conftest import FakeCrawler, FakeHttp, make_result
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent

from crawl4tools.engine.fetcher import Fetcher
from crawl4tools.engine.models import FetchOptions
from crawl4tools.engine.naming import filename_for
from crawl4tools.server import ServerSettings, build_server, fetch_all, open_state
from crawl4tools.server.files import FileRegistry
from crawl4tools.server.mcp_server import ServerState

URL = "https://example.com/page"
URL2 = "https://example.com/other"
PNG = b"\x89PNG\r\n\x1a\nfake-png-bytes"

# --- helpers ------------------------------------------------------------------


def make_server(
    tmp_path: Path,
    *,
    crawler: FakeCrawler | None = None,
    http: FakeHttp | None = None,
    **settings: Any,
) -> tuple[MCPServer[Any], FakeCrawler, FakeHttp]:
    """Build a server whose fetcher uses fake crawler/HTTP doubles."""
    fake = crawler if crawler is not None else FakeCrawler()
    fake_http = http if http is not None else FakeHttp()

    def factory(options: FetchOptions) -> Fetcher:
        return Fetcher(options, crawler_factory=fake.factory, http_client_factory=fake_http)

    server = build_server(
        ServerSettings(download_root=tmp_path, **settings), fetcher_factory=factory
    )
    return server, fake, fake_http


async def call(server: MCPServer[Any], name: str, args: dict[str, Any]) -> CallToolResult:
    """Call one tool on *server* through an in-memory client."""
    async with Client(server) as client:
        return await client.call_tool(name, args)


def texts(result: CallToolResult) -> list[str]:
    """Return the text of every TextContent block of *result*."""
    return [block.text for block in result.content if isinstance(block, TextContent)]


def structured(result: CallToolResult) -> dict[str, Any]:
    """Return the structured content of *result*, asserting it is present."""
    data = result.structured_content
    assert isinstance(data, dict)
    return data


def without_header(text: str) -> str:
    """Strip the leading ``<!-- ... -->`` header/note lines off *text*."""
    lines = text.split("\n")
    body_start = 0
    while body_start < len(lines) and lines[body_start].startswith("<!--"):
        body_start += 1
    return "\n".join(lines[body_start:])


def typed_http(data: bytes, content_type: str) -> FakeHttp:
    """Return a FakeHttp answering every URL with *data* of *content_type*."""
    return FakeHttp(
        lambda request: httpx.Response(200, headers={"content-type": content_type}, content=data)
    )


def fail(message: str) -> Any:
    return make_result(success=False, error_message=message, status_code=None, html="")


# --- list_tools -----------------------------------------------------------------

COMMON_PARAMS = {"urls", "format", "fit", "citations", "ignore_links", "ignore_images", "timeout_s"}


async def test_list_tools_schema(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"fetch", "download"}

    expected_params = {
        "fetch": COMMON_PARAMS,
        "download": COMMON_PARAMS | {"directory"},
    }
    expected_formats = {
        "fetch": ["markdown", "html", "screenshot"],
        "download": ["markdown", "html", "pdf", "screenshot", "mhtml", "raw"],
    }
    for name, tool in tools.items():
        schema = tool.input_schema
        properties = schema["properties"]
        assert set(properties) == expected_params[name]
        assert "ctx" not in properties
        assert schema["required"] == ["urls"]
        assert properties["urls"]["type"] == "array"
        assert properties["urls"]["items"]["type"] == "string"
        assert properties["format"]["enum"] == expected_formats[name]
        assert properties["format"]["default"] == "markdown"
        for param, spec in properties.items():
            assert spec.get("description"), f"{name}.{param} has no description"
        assert tool.title
        assert tool.description

    fetch_hints = tools["fetch"].annotations
    assert fetch_hints is not None
    assert fetch_hints.read_only_hint is True
    assert fetch_hints.open_world_hint is True

    download_hints = tools["download"].annotations
    assert download_hints is not None
    assert download_hints.read_only_hint is False
    assert download_hints.destructive_hint is False
    assert download_hints.idempotent_hint is True
    assert download_hints.open_world_hint is True


async def test_no_proxy_parameter(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path)
    async with Client(server) as client:
        for tool in (await client.list_tools()).tools:
            assert "proxy" not in tool.input_schema["properties"]


async def test_server_info_and_instructions(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path, max_urls=7)
    assert server.name == "crawl4tools"
    assert server.instructions is not None
    assert "7" in server.instructions
    assert "download" in server.instructions


# --- fetch --------------------------------------------------------------------


async def test_fetch_single_markdown(tmp_path: Path) -> None:
    server, crawler, _ = make_server(tmp_path)
    result = await call(server, "fetch", {"urls": [URL]})
    assert not result.is_error
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    assert result.content[0].text == "# Hello"
    page = structured(result)["pages"][0]
    assert page["ok"] is True
    assert page["url"] == URL
    assert page["chars"] == len("# Hello")
    assert page["text"] == without_header(result.content[0].text)
    assert structured(result)["duplicates"] == []
    assert crawler.factory_calls == 1


async def test_fetch_multiple_urls_in_order(tmp_path: Path) -> None:
    crawler = FakeCrawler(
        {URL: make_result(raw_markdown="first"), URL2: make_result(raw_markdown="second")}
    )
    server, _, _ = make_server(tmp_path, crawler=crawler)
    result = await call(server, "fetch", {"urls": [URL, URL2]})
    assert not result.is_error
    blocks = texts(result)
    assert len(blocks) == 2
    assert blocks[0].startswith(f"<!-- crawl4tools: url={URL} status=200 -->")
    assert blocks[0].endswith("first")
    assert blocks[1].startswith(f"<!-- crawl4tools: url={URL2} status=200 -->")
    assert blocks[1].endswith("second")
    pages = structured(result)["pages"]
    assert [page["url"] for page in pages] == [URL, URL2]
    assert [page["text"] for page in pages] == [without_header(block) for block in blocks]


async def test_fetch_html(tmp_path: Path) -> None:
    server, crawler, _ = make_server(tmp_path)
    result = await call(server, "fetch", {"urls": [URL], "format": "html"})
    assert not result.is_error
    assert texts(result) == ["<html><body><h1>Hello</h1></body></html>"]
    assert crawler.calls[0][1].screenshot is False


async def test_fetch_screenshot(tmp_path: Path) -> None:
    crawler = FakeCrawler(make_result(screenshot=base64.b64encode(PNG).decode("ascii")))
    server, _, _ = make_server(tmp_path, crawler=crawler)
    result = await call(server, "fetch", {"urls": [URL], "format": "screenshot"})
    assert not result.is_error
    assert len(result.content) == 1
    image = result.content[0]
    assert isinstance(image, ImageContent)
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data) == PNG
    assert crawler.calls[0][1].screenshot is True
    assert structured(result)["pages"][0]["text"] is None


async def test_fetch_image_url_returns_image(tmp_path: Path) -> None:
    server, crawler, _ = make_server(tmp_path, http=typed_http(PNG, "image/png"))
    result = await call(server, "fetch", {"urls": [URL]})
    assert not result.is_error
    images = [block for block in result.content if isinstance(block, ImageContent)]
    assert len(images) == 1
    assert images[0].mime_type == "image/png"
    assert base64.b64decode(images[0].data) == PNG
    assert not crawler.started


async def test_fetch_pdf_is_transcribed(tmp_path: Path, sample_pdf: bytes) -> None:
    server, crawler, _ = make_server(tmp_path, http=typed_http(sample_pdf, "application/pdf"))
    result = await call(server, "fetch", {"urls": [URL]})
    assert not result.is_error
    assert "Hello crawl4tools" in texts(result)[0]
    assert structured(result)["pages"][0]["content_kind"] == "pdf"
    assert not crawler.started


async def test_fetch_partial_failure_is_not_error(tmp_path: Path) -> None:
    crawler = FakeCrawler({URL: make_result(status_code=404), URL2: make_result()})
    server, _, _ = make_server(tmp_path, crawler=crawler)
    result = await call(server, "fetch", {"urls": [URL, URL2]})
    assert not result.is_error
    assert "error: HTTP 404" in texts(result)[0]
    pages = structured(result)["pages"]
    assert [page["ok"] for page in pages] == [False, True]


async def test_fetch_all_failed_is_error(tmp_path: Path) -> None:
    crawler = FakeCrawler(make_result(status_code=404))
    server, _, _ = make_server(tmp_path, crawler=crawler)
    result = await call(server, "fetch", {"urls": [URL, URL2]})
    assert result.is_error
    assert all("error: HTTP 404" in text for text in texts(result))
    assert [page["ok"] for page in structured(result)["pages"]] == [False, False]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"urls": ["not-a-url"]}, "invalid URL"),
        ({"urls": []}, "at least one URL"),
        ({"urls": [URL, URL2, "https://example.com/3"]}, "too many URLs"),
        ({"urls": [URL], "timeout_s": 0}, "timeout_s must be greater than 0"),
    ],
)
@pytest.mark.parametrize("tool", ["fetch", "download"])
async def test_argument_errors_fetch_nothing(
    tmp_path: Path, tool: str, args: dict[str, Any], message: str
) -> None:
    server, crawler, http = make_server(tmp_path, max_urls=2)
    result = await call(server, tool, args)
    assert result.is_error
    assert message in texts(result)[0]
    assert crawler.calls == []
    assert http.requests == []


async def test_fetch_duplicates_fetched_once(tmp_path: Path) -> None:
    server, crawler, _ = make_server(tmp_path)
    result = await call(server, "fetch", {"urls": [URL, URL]})
    assert not result.is_error
    assert [url for url, _ in crawler.calls] == [URL]
    blocks = texts(result)
    assert blocks[0] == f"note: duplicate URL ignored: {URL}"
    # A single unique URL gets no url= header.
    assert blocks[1] == "# Hello"
    assert structured(result)["duplicates"] == [URL]
    assert len(structured(result)["pages"]) == 1


async def test_fetch_long_timeout_note(tmp_path: Path) -> None:
    server, crawler, _ = make_server(tmp_path, timeout_s=10)
    result = await call(server, "fetch", {"urls": [URL], "timeout_s": 101})
    assert not result.is_error
    assert "timeout" in texts(result)[0]
    assert texts(result)[0].startswith("note: ")
    assert crawler.calls[0][1].page_timeout == 101000

    server, _, _ = make_server(tmp_path, timeout_s=10)
    result = await call(server, "fetch", {"urls": [URL], "timeout_s": 100})
    assert texts(result) == ["# Hello"]


async def test_fetch_per_call_options_reach_crawler(tmp_path: Path) -> None:
    from crawl4ai import PruningContentFilterLXML

    server, crawler, http = make_server(tmp_path)
    result = await call(
        server, "fetch", {"urls": [URL], "fit": True, "timeout_s": 5, "ignore_links": True}
    )
    assert not result.is_error
    assert texts(result) == ["# Hello (fit)"]
    config = crawler.calls[0][1]
    assert config.page_timeout == 5000
    assert isinstance(config.markdown_generator.content_filter, PruningContentFilterLXML)
    assert config.markdown_generator.options["ignore_links"] is True
    assert [timeout for _, timeout in http.client_calls] == [5]
    assert structured(result)["pages"][0]["text"] == without_header(texts(result)[0])


async def test_fetch_citations(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path)
    result = await call(server, "fetch", {"urls": [URL], "citations": True})
    assert texts(result) == ["# Hello [1]\n\n## References\n[1]: x"]
    assert structured(result)["pages"][0]["text"] == without_header(texts(result)[0])


async def test_fetch_structured_text_matches_content_with_fit_and_citations(
    tmp_path: Path,
) -> None:
    server, _, _ = make_server(tmp_path)
    result = await call(server, "fetch", {"urls": [URL], "fit": True, "citations": True})
    assert not result.is_error
    assert len(result.content) == 1
    assert structured(result)["pages"][0]["text"] == without_header(texts(result)[0])


async def test_settings_proxy_is_used(tmp_path: Path) -> None:
    server, crawler, http = make_server(tmp_path, proxy="http://proxy.example:8080")
    result = await call(server, "fetch", {"urls": [URL]})
    assert not result.is_error
    assert http.proxies == ["http://proxy.example:8080"]
    assert crawler.calls[0][1].proxy_config.server == "http://proxy.example:8080"


async def test_proxy_fallback_note_in_fetch(tmp_path: Path) -> None:
    def handler(url: str, config: Any) -> Any:
        if config.proxy_config is not None:
            return fail("net::ERR_PROXY_CONNECTION_FAILED at " + url)
        return make_result()

    server, crawler, _ = make_server(
        tmp_path, crawler=FakeCrawler(handler), proxy="http://proxy.example:8080"
    )
    result = await call(server, "fetch", {"urls": [URL]})
    assert not result.is_error
    text = texts(result)[0]
    assert text.startswith("<!-- note: proxy failed")
    assert text.endswith("# Hello")
    assert len(crawler.calls) == 2


async def test_concurrency_is_shared_across_calls(tmp_path: Path) -> None:
    crawler = FakeCrawler(delay=0.05)
    server, _, _ = make_server(tmp_path, crawler=crawler, concurrency=3)
    async with Client(server) as client:
        first, second = await asyncio.gather(
            client.call_tool("fetch", {"urls": ["https://a.example/1", "https://a.example/2"]}),
            client.call_tool("fetch", {"urls": ["https://b.example/1", "https://b.example/2"]}),
        )
    assert not first.is_error
    assert not second.is_error
    assert len(crawler.calls) == 4
    assert crawler.max_in_flight == 3
    # One browser for the life of the server, closed when it stops.
    assert crawler.factory_calls == 1
    assert crawler.entered == 1
    assert crawler.exited == 1


async def test_progress_is_reported(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path)
    updates: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        updates.append((progress, total, message))

    async with Client(server) as client:
        result = await client.call_tool(
            "fetch", {"urls": [URL, URL2]}, progress_callback=on_progress
        )
    assert not result.is_error
    assert sorted(progress for progress, _, _ in updates) == [1, 2]
    assert all(total == 2 for _, total, _ in updates)
    assert {message for _, _, message in updates} == {URL, URL2}


# --- download -----------------------------------------------------------------


async def test_download_markdown(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path)
    result = await call(server, "download", {"urls": [URL]})
    assert not result.is_error
    path = tmp_path.resolve() / filename_for(URL, ".md")
    assert path.read_text(encoding="utf-8") == "# Hello"
    data = structured(result)
    record = data["files"][0]
    assert record["ok"] is True
    assert record["path"] == str(path)
    assert record["bytes"] == len("# Hello")
    assert record["file_url"] is None
    assert data["directory"] == str(tmp_path.resolve())
    assert data["duplicates"] == []
    lines = texts(result)[0].splitlines()
    assert lines == [f"saved: {URL} -> {path} (7 bytes)", "done: 1 saved, 0 failed"]


async def test_download_into_subdirectory(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path)
    result = await call(server, "download", {"urls": [URL], "directory": "sub/dir"})
    assert not result.is_error
    target = tmp_path.resolve() / "sub" / "dir"
    assert (target / filename_for(URL, ".md")).read_text(encoding="utf-8") == "# Hello"
    assert structured(result)["directory"] == str(target)


@pytest.mark.parametrize("directory", ["../x", "/tmp/crawl4tools-abs", "sub/../../x"])
async def test_download_directory_outside_root(tmp_path: Path, directory: str) -> None:
    server, crawler, http = make_server(tmp_path / "root")
    result = await call(server, "download", {"urls": [URL], "directory": directory})
    assert result.is_error
    assert "directory must" in texts(result)[0]
    assert crawler.calls == []
    assert http.requests == []
    assert not (tmp_path / "x").exists()


async def test_download_raw_pdf(tmp_path: Path, sample_pdf: bytes) -> None:
    server, _, _ = make_server(tmp_path, http=typed_http(sample_pdf, "application/pdf"))
    result = await call(server, "download", {"urls": [URL], "format": "raw"})
    assert not result.is_error
    record = structured(result)["files"][0]
    assert record["ok"] is True
    assert Path(record["path"]).suffix == ".pdf"
    assert Path(record["path"]).read_bytes() == sample_pdf
    assert record["bytes"] == len(sample_pdf)


async def test_download_screenshot_writes_png(tmp_path: Path) -> None:
    crawler = FakeCrawler(make_result(screenshot=base64.b64encode(PNG).decode("ascii")))
    server, _, _ = make_server(tmp_path, crawler=crawler)
    result = await call(server, "download", {"urls": [URL], "format": "screenshot"})
    assert not result.is_error
    path = tmp_path.resolve() / filename_for(URL, ".png")
    assert path.read_bytes() == PNG


async def test_download_partial_failure(tmp_path: Path) -> None:
    crawler = FakeCrawler({URL: make_result(status_code=404), URL2: make_result()})
    server, _, _ = make_server(tmp_path, crawler=crawler)
    result = await call(server, "download", {"urls": [URL, URL2]})
    assert not result.is_error
    lines = texts(result)[0].splitlines()
    assert any(line.startswith("error: HTTP 404") for line in lines)
    assert lines[-1] == "done: 1 saved, 1 failed"
    files = structured(result)["files"]
    assert [record["ok"] for record in files] == [False, True]
    assert files[0]["path"] is None
    assert not (tmp_path / filename_for(URL, ".md")).exists()


async def test_download_all_failed_is_error(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path, crawler=FakeCrawler(make_result(status_code=500)))
    result = await call(server, "download", {"urls": [URL]})
    assert result.is_error
    assert texts(result)[0].splitlines()[-1] == "done: 0 saved, 1 failed"


async def test_download_write_failure(tmp_path: Path) -> None:
    (tmp_path / filename_for(URL, ".md")).mkdir()
    server, _, _ = make_server(tmp_path)
    result = await call(server, "download", {"urls": [URL]})
    assert result.is_error
    record = structured(result)["files"][0]
    assert record["ok"] is False
    assert record["path"] is None
    assert record["error"].startswith("could not write ")
    assert "error: could not write" in texts(result)[0]


async def test_download_directory_creation_failure(tmp_path: Path) -> None:
    (tmp_path / "blocker").write_text("not a directory", encoding="utf-8")
    server, _, _ = make_server(tmp_path)
    result = await call(server, "download", {"urls": [URL], "directory": "blocker/sub"})
    assert result.is_error
    assert "could not create" in texts(result)[0]


async def test_download_duplicates_and_same_name(tmp_path: Path) -> None:
    other = "https://example.com/page?"  # same filename as URL, different URL
    server, crawler, _ = make_server(tmp_path)
    result = await call(server, "download", {"urls": [URL, URL, other]})
    assert not result.is_error
    data = structured(result)
    assert data["duplicates"] == [URL]
    lines = texts(result)[0].splitlines()
    assert lines[0] == f"note: duplicate URL ignored: {URL}"
    assert lines[-1] == "done: 2 saved, 0 failed"
    paths = [record["path"] for record in data["files"]]
    assert len(set(paths)) == len(paths) == 2
    assert len(crawler.calls) == 2


async def test_download_in_memory_has_no_file_url(tmp_path: Path) -> None:
    crawler = FakeCrawler({URL: make_result(status_code=404), URL2: make_result()})
    server, _, _ = make_server(tmp_path, crawler=crawler)
    result = await call(server, "download", {"urls": [URL, URL2]})
    assert [record["file_url"] for record in structured(result)["files"]] == [None, None]
    assert not any(line.startswith("file:") for line in texts(result)[0].splitlines())


@pytest.mark.parametrize(
    ("headers", "base"),
    [
        ({}, "http://testserver"),
        (
            {"x-forwarded-proto": "https", "x-forwarded-host": "tools.example"},
            "https://tools.example",
        ),
    ],
    ids=["host", "forwarded"],
)
async def test_download_over_http_returns_a_file_url(
    tmp_path: Path, headers: dict[str, str], base: str
) -> None:
    crawler = FakeCrawler({URL: make_result(), URL2: make_result(status_code=404)})
    server, _, _ = make_server(tmp_path, crawler=crawler)
    app = server.streamable_http_app(host="testserver")
    async with (
        server.session_manager.run(),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver", headers=headers
        ) as http,
    ):
        async with Client(
            streamable_http_client("http://testserver/mcp", http_client=http)
        ) as client:
            result = await client.call_tool("download", {"urls": [URL, URL2]})
        assert not result.is_error
        saved, failed = structured(result)["files"]
        file_url = saved["file_url"]
        assert isinstance(file_url, str)
        prefix = f"{base}/files/"
        assert file_url.startswith(prefix)
        assert len(file_url.removeprefix(prefix)) >= 32
        assert failed["file_url"] is None
        path = tmp_path.resolve() / filename_for(URL, ".md")
        assert texts(result)[0].splitlines()[:2] == [
            f"saved: {URL} -> {path} (7 bytes)",
            f"file: {file_url}",
        ]
        assert path.read_bytes() == b"# Hello"
        # Fetch the file back through the same app, whatever the public base URL.
        local_url = file_url.replace(base, "http://testserver", 1)
        response = await http.get(local_url)
        # Once fetched, the server's copy is deleted and the URL is gone.
        assert not path.exists()
        again = await http.get(local_url)
    assert response.status_code == 200
    assert response.content == b"# Hello"
    assert filename_for(URL, ".md") in response.headers["content-disposition"]
    assert again.status_code == 404


async def test_download_over_http_keeps_the_files_with_keep_downloads(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path, keep_downloads=True)
    app = server.streamable_http_app(host="testserver")
    async with (
        server.session_manager.run(),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
        ) as http,
    ):
        async with Client(
            streamable_http_client("http://testserver/mcp", http_client=http)
        ) as client:
            result = await client.call_tool("download", {"urls": [URL]})
        file_url = structured(result)["files"][0]["file_url"]
        assert isinstance(file_url, str)
        responses = [await http.get(file_url) for _ in range(2)]
    assert [response.status_code for response in responses] == [200, 200]
    assert [response.content for response in responses] == [b"# Hello", b"# Hello"]
    assert (tmp_path / filename_for(URL, ".md")).read_bytes() == b"# Hello"


# --- shared state (open_state / build_server(state=) / fetch_all) ----------------


async def test_open_state_creates_a_file_registry_for_the_download_root(tmp_path: Path) -> None:
    settings = ServerSettings(download_root=tmp_path)
    async with open_state(settings, fake_factory(FakeCrawler())) as state:
        assert isinstance(state.files, FileRegistry)
        path = tmp_path / "page.md"
        path.write_bytes(b"x")
        assert state.files.lookup(state.files.register(path)) == path.resolve()
        outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
        assert state.files.lookup(state.files.register(outside)) is None


async def test_open_state_uses_the_given_file_registry(tmp_path: Path) -> None:
    settings = ServerSettings(download_root=tmp_path)
    registry = FileRegistry(tmp_path)
    async with open_state(settings, fake_factory(FakeCrawler()), files=registry) as state:
        assert state.files is registry


def fake_factory(crawler: FakeCrawler) -> Any:
    """Return a fetcher factory whose Fetcher uses *crawler*."""

    def factory(options: FetchOptions) -> Fetcher:
        return Fetcher(options, crawler_factory=crawler.factory, http_client_factory=FakeHttp())

    return factory


class StaggeredCrawler(FakeCrawler):
    """A FakeCrawler that waits a per-URL delay before answering."""

    def __init__(self, delays: dict[str, float]) -> None:
        super().__init__()
        self.delays = delays

    async def arun(self, url: str, config: Any) -> Any:
        await asyncio.sleep(self.delays.get(url, 0.0))
        return await super().arun(url, config)


async def test_open_state_caps_concurrency_and_closes_fetcher(tmp_path: Path) -> None:
    crawler = FakeCrawler(delay=0.02)
    settings = ServerSettings(download_root=tmp_path, concurrency=3)
    urls = [f"https://example.com/{i}" for i in range(7)]
    async with open_state(settings, fake_factory(crawler)) as state:
        assert isinstance(state, ServerState)
        assert state.settings is settings
        outcomes = await fetch_all(state, urls, settings.fetch_options())
        assert all(outcome.ok for outcome in outcomes)
        assert crawler.exited == 0
    assert crawler.max_in_flight == 3
    assert crawler.exited == 1


async def test_build_server_with_shared_state_does_not_close_it(tmp_path: Path) -> None:
    crawler = FakeCrawler()
    settings = ServerSettings(download_root=tmp_path)

    def unused_factory(options: FetchOptions) -> Fetcher:
        raise AssertionError("fetcher_factory must not be called when state is given")

    async with open_state(settings, fake_factory(crawler)) as state:
        server = build_server(settings, fetcher_factory=unused_factory, state=state)
        result = await call(server, "fetch", {"urls": [URL]})
        assert not result.is_error
        assert [url for url, _ in crawler.calls] == [URL]
        # The client session has ended, but the shared fetcher stays open.
        assert crawler.exited == 0
        result = await call(server, "fetch", {"urls": [URL2]})
        assert not result.is_error
        assert [url for url, _ in crawler.calls] == [URL, URL2]
        assert crawler.factory_calls == 1
        assert crawler.exited == 0
    assert crawler.exited == 1


async def test_shared_state_caps_concurrency_across_servers(tmp_path: Path) -> None:
    crawler = FakeCrawler(delay=0.02)
    settings = ServerSettings(download_root=tmp_path, concurrency=2)
    urls = [f"https://example.com/{i}" for i in range(5)]
    async with open_state(settings, fake_factory(crawler)) as state:
        first = build_server(settings, state=state)
        second = build_server(settings, state=state)
        first_result, second_result, direct = await asyncio.gather(
            call(first, "fetch", {"urls": urls}),
            call(second, "fetch", {"urls": urls}),
            fetch_all(state, urls, settings.fetch_options()),
        )
    assert not first_result.is_error
    assert not second_result.is_error
    assert all(outcome.ok for outcome in direct)
    assert len(crawler.calls) == 15
    assert crawler.max_in_flight == 2


async def test_fetch_all_keeps_input_order_and_reports_done(tmp_path: Path) -> None:
    urls = ["https://example.com/slow", "https://example.com/mid", "https://example.com/fast"]
    crawler = StaggeredCrawler({urls[0]: 0.06, urls[1]: 0.03, urls[2]: 0.0})
    settings = ServerSettings(download_root=tmp_path, concurrency=3)
    events: list[tuple[str, int, int]] = []

    async def on_done(url: str, done: int, total: int) -> None:
        events.append((url, done, total))

    async with open_state(settings, fake_factory(crawler)) as state:
        outcomes = await fetch_all(state, urls, settings.fetch_options(), on_done)
    assert [outcome.url for outcome in outcomes] == urls
    assert events == [(urls[2], 1, 3), (urls[1], 2, 3), (urls[0], 3, 3)]


async def test_fetch_all_empty(tmp_path: Path) -> None:
    settings = ServerSettings(download_root=tmp_path)
    events: list[tuple[str, int, int]] = []

    async def on_done(url: str, done: int, total: int) -> None:
        events.append((url, done, total))

    async with open_state(settings, fake_factory(FakeCrawler())) as state:
        assert await fetch_all(state, [], settings.fetch_options(), on_done) == []
    assert events == []


# --- message language ----------------------------------------------------------------

EN_TITLES = {"fetch": "Fetch web pages", "download": "Download web pages to files"}
EN_DESCRIPTIONS = {
    "fetch": (
        "Fetch one or more web pages and return their content directly: Markdown "
        "(default), HTML, or a PNG screenshot. PDFs are transcribed to Markdown and "
        "images are returned as images. When several URLs are given, each result "
        "starts with a '<!-- crawl4tools: url=... status=... -->' header. Failed "
        "URLs are reported as 'error: ...' lines; the call fails only if every URL "
        "fails. Use `download` to save other formats to files."
    ),
    "download": (
        "Fetch one or more URLs and save each result as a file in a directory on "
        "the server, returning the saved paths. Supports Markdown (default), HTML, "
        "PDF, PNG screenshot, MHTML, and the raw source. PDFs are transcribed to "
        "Markdown unless format is 'raw'; non-web content (images, archives, ...) is "
        "saved as served. Existing files are overwritten. The call fails only if no "
        "file was saved. When the server is reached over HTTP, each saved file also has a "
        "`file_url`; fetch it (for example `curl -o <name> <file_url>`) to save the file "
        "on your own machine without passing its content through the conversation. Over "
        "stdio the server runs on your machine, so the returned paths are local. When the "
        "server is used over HTTP, it deletes its own copy of a file once a client has "
        "fetched it from its `file_url` (unless the server was started with "
        "`--keep-downloads`); over stdio the files stay where the returned paths say."
    ),
}
EN_PARAMETERS = {
    "urls": (
        "URLs to fetch (http or https), at most the server's per-call limit (see the "
        "server instructions); duplicates are fetched once."
    ),
    "fit": (
        "Keep only the main content (drops menus, footers, and the like); falls back to "
        "the full page when nothing is left. Markdown only."
    ),
    "citations": "Turn links into numbered references listed at the end. Markdown only.",
    "ignore_links": "Drop links from the Markdown output. Markdown only.",
    "ignore_images": "Drop images from the Markdown output. Markdown only.",
    "timeout_s": "Page load timeout per URL in seconds; defaults to the server setting.",
    "directory": (
        "Subdirectory of the server's download directory to save into; must stay inside "
        "it. Defaults to the download directory itself."
    ),
}
EN_FORMATS = {
    "fetch": (
        "Output format: 'markdown' (default), 'html' (rendered page HTML), or "
        "'screenshot' (full-page PNG image)."
    ),
    "download": (
        "File format: 'markdown' (default), 'html', 'pdf' (page printed to PDF), "
        "'screenshot' (PNG), 'mhtml' (single-file web archive), or 'raw' (the original "
        "response bytes, e.g. a PDF or image as served)."
    ),
}
EN_INSTRUCTIONS_7 = (
    "Web fetching tools built on crawl4ai. "
    "`fetch` returns page content directly: Markdown by default, or HTML, or a PNG "
    "screenshot. "
    "`download` saves files in any format (including PDF, MHTML, and the raw "
    "source) into a directory on the server and returns their paths. "
    "Over HTTP, `download` also returns a `file_url` per file to fetch it from the "
    "server (e.g. with curl). "
    "The server deletes its copy of a file once it has been fetched from its "
    "`file_url` (unless the server was started with `--keep-downloads`). "
    "PDFs are transcribed to Markdown. Duplicate URLs are fetched once. "
    "At most 7 URLs per call."
)

JA_TITLES = {"fetch": "Web ページを取得", "download": "Web ページをファイルにダウンロード"}
JA_DESCRIPTIONS = {
    "fetch": (
        "1 つ以上の Web ページを取得し、その内容を直接返します。形式は Markdown(既定)、HTML、"
        "PNG のスクリーンショットのいずれかです。PDF は Markdown に書き起こし、画像は画像として"
        "返します。複数の URL を指定すると、各結果の先頭に "
        "'<!-- crawl4tools: url=... status=... -->' ヘッダが付きます。失敗した URL は "
        "'error: ...' 行で報告します。呼び出しが失敗になるのは、すべての URL が失敗したとき"
        "だけです。ほかの形式をファイルに保存するには `download` を使ってください。"
    ),
    "download": (
        "1 つ以上の URL を取得し、それぞれの結果をサーバー上のディレクトリにファイルとして保存"
        "して、保存したパスを返します。Markdown(既定)、HTML、PDF、PNG のスクリーンショット、"
        "MHTML、元のソースに対応します。PDF は format が 'raw' でなければ Markdown に書き起こし"
        "ます。Web ページ以外の内容(画像、アーカイブなど)は配信されたまま保存します。既存の"
        "ファイルは上書きします。呼び出しが失敗になるのは、ファイルを 1 つも保存できなかった"
        "ときだけです。サーバーに HTTP で接続しているときは、保存した各ファイルに `file_url` "
        "も付きます。これを取得すると(例: `curl -o <name> <file_url>`)、内容を会話に通さずに"
        "手元のマシンにファイルを保存できます。stdio ではサーバーが手元のマシンで動いているので、"
        "返されるパスはローカルのパスです。サーバーに HTTP で接続しているときは、クライアントが "
        "`file_url` からファイルを取得し終えるとサーバー側のコピーを削除します(サーバーを "
        "`--keep-downloads` で起動した場合を除きます)。stdio では返されたパスの場所にファイルが"
        "残ります。"
    ),
}
JA_PARAMETERS = {
    "urls": (
        "取得する URL(http または https)です。1 回の呼び出しにつきサーバーの上限まで指定"
        "できます(サーバーの instructions を参照)。重複した URL は 1 回だけ取得します。"
    ),
    "fit": (
        "本文だけを残します(メニューやフッターなどを除きます)。何も残らなければページ全体を"
        "使います。Markdown のときだけ有効です。"
    ),
    "citations": (
        "リンクを番号付きの参照に置き換え、末尾に一覧にします。Markdown のときだけ有効です。"
    ),
    "ignore_links": "Markdown の出力からリンクを除きます。Markdown のときだけ有効です。",
    "ignore_images": "Markdown の出力から画像を除きます。Markdown のときだけ有効です。",
    "timeout_s": (
        "URL ごとのページ読み込みのタイムアウト(秒)です。省略時はサーバーの設定値を使います。"
    ),
    "directory": (
        "保存先とする、サーバーのダウンロード先ディレクトリの下のサブディレクトリです。その外"
        "は指定できません。省略時はダウンロード先ディレクトリそのものに保存します。"
    ),
}
JA_FORMATS = {
    "fetch": (
        "出力形式です。'markdown'(既定)、'html'(描画後のページの HTML)、'screenshot'"
        "(ページ全体の PNG 画像)のいずれかです。"
    ),
    "download": (
        "ファイル形式です。'markdown'(既定)、'html'、'pdf'(ページを PDF に印刷したもの)、"
        "'screenshot'(PNG)、'mhtml'(1 ファイルにまとめた Web アーカイブ)、'raw'(応答の元の"
        "バイト列。例: 配信されたままの PDF や画像)のいずれかです。"
    ),
}
JA_INSTRUCTIONS_7 = (
    "crawl4ai を使った Web 取得ツールです。`fetch` はページの内容を直接返します(既定は "
    "Markdown で、HTML や PNG のスクリーンショットも選べます)。`download` は任意の形式(PDF、"
    "MHTML、元のソースを含む)のファイルをサーバー上のディレクトリに保存し、そのパスを返します。"
    "HTTP 接続では、`download` はファイルごとにサーバーから取得するための `file_url` も返します"
    "(curl などで取得できます)。"
    "`file_url` から取得し終えたファイルは、サーバーを `--keep-downloads` で起動した場合を"
    "除き、サーバー側から削除します。"
    "PDF は Markdown に書き起こします。重複した URL は 1 回だけ取得します。1 回の呼び出しで"
    "指定できる URL は最大 7 件です。"
)

PAGE_KEYS = {
    "url",
    "final_url",
    "title",
    "ok",
    "status_code",
    "content_kind",
    "content_type",
    "text",
    "chars",
    "bytes",
    "error",
    "notes",
}
FILE_KEYS = {
    "url",
    "ok",
    "path",
    "bytes",
    "content_kind",
    "content_type",
    "status_code",
    "error",
    "notes",
    "file_url",
}


async def tool_texts(server: MCPServer[Any]) -> dict[str, dict[str, Any]]:
    """Return each tool's title, description and parameter descriptions."""
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    return {
        tool.name: {
            "title": tool.title,
            "description": tool.description,
            "parameters": {
                name: spec["description"] for name, spec in tool.input_schema["properties"].items()
            },
        }
        for tool in tools
    }


def expected_tool_texts(
    titles: dict[str, str],
    descriptions: dict[str, str],
    parameters: dict[str, str],
    formats: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Build the value :func:`tool_texts` should return for one language."""
    common = {name: text for name, text in parameters.items() if name != "directory"}
    return {
        "fetch": {
            "title": titles["fetch"],
            "description": descriptions["fetch"],
            "parameters": {**common, "format": formats["fetch"]},
        },
        "download": {
            "title": titles["download"],
            "description": descriptions["download"],
            "parameters": {**parameters, "format": formats["download"]},
        },
    }


async def test_english_tool_texts_are_unchanged(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path)
    expected = expected_tool_texts(EN_TITLES, EN_DESCRIPTIONS, EN_PARAMETERS, EN_FORMATS)
    assert await tool_texts(server) == expected


async def test_japanese_tool_texts(tmp_path: Path) -> None:
    server, _, _ = make_server(tmp_path, lang="ja")
    expected = expected_tool_texts(JA_TITLES, JA_DESCRIPTIONS, JA_PARAMETERS, JA_FORMATS)
    assert await tool_texts(server) == expected


@pytest.mark.parametrize(
    ("settings", "expected"),
    [({}, EN_INSTRUCTIONS_7), ({"lang": "ja"}, JA_INSTRUCTIONS_7)],
    ids=["en", "ja"],
)
def test_instructions_follow_the_language(
    tmp_path: Path, settings: dict[str, Any], expected: str
) -> None:
    server, _, _ = make_server(tmp_path, max_urls=7, **settings)
    assert server.instructions == expected


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"urls": ["not-a-url"]}, "不正な URL です: not-a-url"),
        ({"urls": []}, "URL を 1 つ以上指定してください"),
        (
            {"urls": [URL, URL2, "https://example.com/3"]},
            "URL が多すぎます: 3 件(1 回あたり最大 2 件)",
        ),
        ({"urls": [URL], "timeout_s": 0}, "timeout_s は 0 より大きくしてください"),
    ],
    ids=["invalid-url", "no-url", "too-many", "zero-timeout"],
)
@pytest.mark.parametrize("tool", ["fetch", "download"])
async def test_japanese_argument_errors(
    tmp_path: Path, tool: str, args: dict[str, Any], message: str
) -> None:
    server, crawler, http = make_server(tmp_path, max_urls=2, lang="ja")
    result = await call(server, tool, args)
    assert result.is_error
    assert message in texts(result)[0]
    assert crawler.calls == []
    assert http.requests == []


@pytest.mark.parametrize(
    ("directory", "message"),
    [
        ("../x", "directory にダウンロード先ディレクトリの外は指定できません: ../x"),
        ("/tmp/crawl4tools-abs", "directory は相対パスにしてください: /tmp/crawl4tools-abs"),
    ],
    ids=["outside", "absolute"],
)
async def test_japanese_directory_errors(tmp_path: Path, directory: str, message: str) -> None:
    server, crawler, _ = make_server(tmp_path / "root", lang="ja")
    result = await call(server, "download", {"urls": [URL], "directory": directory})
    assert result.is_error
    assert message in texts(result)[0]
    assert crawler.calls == []


@pytest.mark.parametrize("tool", ["fetch", "download"])
async def test_japanese_duplicate_note_keeps_the_english_prefix(tmp_path: Path, tool: str) -> None:
    server, _, _ = make_server(tmp_path, lang="ja")
    result = await call(server, tool, {"urls": [URL, URL]})
    assert not result.is_error
    assert texts(result)[0].splitlines()[0] == f"note: 重複した URL を無視しました: {URL}"


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        (
            "en",
            "note: timeout_s=101 is more than 10 times the server default (10 s); "
            "slow pages may hold the call open for a long time",
        ),
        (
            "ja",
            "note: timeout_s=101 はサーバーの既定値(10 秒)の 10 倍を超えています。"
            "遅いページでは呼び出しが長時間終わらないことがあります",
        ),
    ],
    ids=["en", "ja"],
)
async def test_long_timeout_note_follows_the_language(
    tmp_path: Path, lang: str, expected: str
) -> None:
    server, _, _ = make_server(tmp_path, timeout_s=10, lang=lang)
    result = await call(server, "fetch", {"urls": [URL], "timeout_s": 101})
    assert not result.is_error
    assert texts(result)[0] == expected


async def test_japanese_fetch_error_line_and_structured_content(tmp_path: Path) -> None:
    crawler = FakeCrawler({URL: fail("net::ERR_NAME_NOT_RESOLVED"), URL2: make_result()})
    server, _, _ = make_server(tmp_path, crawler=crawler, lang="ja")
    result = await call(server, "fetch", {"urls": [URL, URL2]})
    assert not result.is_error
    blocks = texts(result)
    assert blocks[0] == (
        f"<!-- crawl4tools: url={URL} status=- -->\nerror: ホスト名を解決できません: {URL}"
    )
    assert blocks[1].startswith(f"<!-- crawl4tools: url={URL2} status=200 -->")
    pages = structured(result)["pages"]
    assert set(pages[0]) == PAGE_KEYS
    assert pages[0]["error"] == f"ホスト名を解決できません: {URL}"
    assert pages[1]["error"] is None


async def test_japanese_notes_in_fetch_blocks_and_structured_content(tmp_path: Path) -> None:
    def handler(url: str, config: Any) -> Any:
        if config.proxy_config is not None:
            return fail("net::ERR_PROXY_CONNECTION_FAILED at " + url)
        return make_result()

    server, _, _ = make_server(
        tmp_path, crawler=FakeCrawler(handler), proxy="http://proxy.example:8080", lang="ja"
    )
    result = await call(server, "fetch", {"urls": [URL]})
    assert not result.is_error
    note = (
        f"プロキシ経由で失敗しました(プロキシに接続できません(http://proxy.example:8080): "
        f"{URL})。直接接続で再試行しました"
    )
    assert texts(result)[0] == f"<!-- note: {note} -->\n# Hello"
    page = structured(result)["pages"][0]
    assert set(page) == PAGE_KEYS
    assert page["notes"] == [note]


async def test_japanese_download_lines_keep_the_english_prefixes(tmp_path: Path) -> None:
    crawler = FakeCrawler({URL: make_result(), URL2: fail("net::ERR_NAME_NOT_RESOLVED")})
    server, _, _ = make_server(tmp_path, crawler=crawler, lang="ja")
    result = await call(server, "download", {"urls": [URL, URL2]})
    assert not result.is_error
    path = tmp_path.resolve() / filename_for(URL, ".md")
    assert texts(result)[0].splitlines() == [
        f"saved: {URL} -> {path}(7 バイト)",
        f"error: ホスト名を解決できません: {URL2}",
        "done: 保存 1 件、失敗 1 件",
    ]
    files = structured(result)["files"]
    assert [set(record) for record in files] == [FILE_KEYS, FILE_KEYS]
    assert files[1]["error"] == f"ホスト名を解決できません: {URL2}"


async def test_japanese_download_all_failed(tmp_path: Path) -> None:
    server, _, _ = make_server(
        tmp_path, crawler=FakeCrawler(make_result(status_code=500)), lang="ja"
    )
    result = await call(server, "download", {"urls": [URL]})
    assert result.is_error
    assert texts(result)[0].splitlines()[-1] == "done: 保存 0 件、失敗 1 件"


async def test_japanese_download_write_failure(tmp_path: Path) -> None:
    (tmp_path / filename_for(URL, ".md")).mkdir()
    server, _, _ = make_server(tmp_path, lang="ja")
    result = await call(server, "download", {"urls": [URL]})
    assert result.is_error
    path = tmp_path.resolve() / filename_for(URL, ".md")
    record = structured(result)["files"][0]
    assert record["error"].startswith(f"{path} に書き込めません: ")
    assert f"error: {path} に書き込めません: " in texts(result)[0]


async def test_japanese_directory_creation_failure(tmp_path: Path) -> None:
    (tmp_path / "blocker").write_text("not a directory", encoding="utf-8")
    server, _, _ = make_server(tmp_path, lang="ja")
    result = await call(server, "download", {"urls": [URL], "directory": "blocker/sub"})
    assert result.is_error
    target = tmp_path.resolve() / "blocker" / "sub"
    assert f"{target} を作成できません: " in texts(result)[0]


# --- uvicorn log level of the http transport ----------------------------------------


@pytest.mark.parametrize(
    ("log_level", "expected"),
    [("debug", "INFO"), ("info", "WARNING"), ("error", "WARNING")],
    ids=["debug", "info", "error"],
)
def test_server_log_level_follows_the_settings(
    tmp_path: Path, log_level: str, expected: str
) -> None:
    server, _, _ = make_server(tmp_path, log_level=log_level)
    assert server.settings.log_level == expected
