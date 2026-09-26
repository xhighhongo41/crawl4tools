"""End-to-end checks of crawl4mcp over stdio and Streamable HTTP with a real browser."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import CallToolResult, TextContent

from crawl4tools import __version__

pytestmark = pytest.mark.integration

_MAIN = [sys.executable, "-m", "crawl4tools.server.mcp_main"]

# The start of the first line crawl4mcp prints on stderr, over either transport.
_VERSION_PREFIX = f"crawl4mcp {__version__} ("


def _text(result: CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _stdio_server(download_dir: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=_MAIN[0],
        args=[*_MAIN[1:], "--timeout", "30", "--download-dir", str(download_dir)],
    )


async def test_stdio_fetch_returns_markdown(tmp_path: Path) -> None:
    async with Client(_stdio_server(tmp_path)) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        assert tools == {"fetch", "download"}
        result = await client.call_tool("fetch", {"urls": ["https://example.com/"]})
    assert result.is_error is False
    assert "Example Domain" in _text(result)


async def test_stdio_fetch_reports_partial_failure(tmp_path: Path) -> None:
    urls = ["https://example.com/", "https://example.com/no-such-page-for-crawl4tools"]
    async with Client(_stdio_server(tmp_path)) as client:
        result = await client.call_tool("fetch", {"urls": urls})
    assert result.is_error is False
    text = _text(result)
    assert "<!-- crawl4tools: url=https://example.com/ status=200 -->" in text
    assert "error: HTTP 404 Not Found" in text


async def test_stdio_download_saves_file(tmp_path: Path) -> None:
    async with Client(_stdio_server(tmp_path)) as client:
        result = await client.call_tool(
            "download", {"urls": ["https://example.com/"], "directory": "pages"}
        )
    assert result.is_error is False
    saved = tmp_path / "pages" / "example.com.md"
    assert "Example Domain" in saved.read_text(encoding="utf-8")


def test_stdio_prints_the_version_first(tmp_path: Path) -> None:
    # An empty stdin ends the stdio session at once, so the server exits by itself.
    completed = subprocess.run(
        [*_MAIN, "--download-dir", str(tmp_path)],
        input=b"",
        capture_output=True,
        timeout=60,
        check=False,
    )
    stderr = completed.stderr.decode()
    assert stderr.splitlines()[0].startswith(_VERSION_PREFIX), stderr
    assert completed.stdout == b""


@dataclass
class HttpServer:
    process: subprocess.Popen[bytes]
    url: str


@pytest.fixture
def http_process(tmp_path: Path) -> Iterator[HttpServer]:
    port = _free_port()
    process = subprocess.Popen(
        [
            *_MAIN,
            "--transport",
            "http",
            "--port",
            str(port),
            "--timeout",
            "30",
            "--download-dir",
            str(tmp_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = process.stderr.read().decode() if process.stderr else ""
                pytest.fail(f"crawl4mcp exited early: {stderr}")
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        else:
            pytest.fail("crawl4mcp did not start listening")
        yield HttpServer(process, f"http://127.0.0.1:{port}/mcp")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture
def http_server(http_process: HttpServer) -> str:
    return http_process.url


def test_http_prints_the_version_first(http_process: HttpServer) -> None:
    assert http_process.process.stderr is not None
    # The server is listening, so the lines printed before that are already written.
    first_line = http_process.process.stderr.readline().decode()
    assert first_line.startswith(_VERSION_PREFIX), first_line


async def test_http_fetch_returns_markdown(http_server: str) -> None:
    async with Client(http_server) as client:
        result = await client.call_tool("fetch", {"urls": ["https://example.com/"]})
    assert result.is_error is False
    assert "Example Domain" in _text(result)


async def test_http_fetch_screenshot_returns_image(http_server: str) -> None:
    async with Client(http_server) as client:
        result = await client.call_tool(
            "fetch", {"urls": ["https://example.com/"], "format": "screenshot"}
        )
    assert result.is_error is False
    assert [block.type for block in result.content] == ["image"]


async def test_http_download_returns_a_file_url(http_server: str, tmp_path: Path) -> None:
    async with Client(http_server) as client:
        result = await client.call_tool("download", {"urls": ["https://example.com/"]})
    assert result.is_error is False
    assert result.structured_content is not None
    record = result.structured_content["files"][0]
    file_url = record["file_url"]
    assert file_url.startswith(http_server.removesuffix("/mcp") + "/files/")
    assert f"file: {file_url}" in _text(result)
    response = httpx.get(file_url, timeout=30)
    assert response.status_code == 200
    saved = Path(record["path"])
    assert saved.parent == tmp_path.resolve()
    assert response.content == saved.read_bytes()
    assert b"Example Domain" in response.content
