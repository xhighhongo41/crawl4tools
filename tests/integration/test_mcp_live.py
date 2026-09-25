"""End-to-end checks of crawl4mcp over stdio and Streamable HTTP with a real browser."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import CallToolResult, TextContent

pytestmark = pytest.mark.integration

_MAIN = [sys.executable, "-m", "crawl4tools.server.mcp_main"]


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


@pytest.fixture
def http_server(tmp_path: Path) -> Iterator[str]:
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
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


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
