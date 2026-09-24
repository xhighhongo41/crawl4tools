"""End-to-end checks of crawl4server (web loader + MCP) with a real browser."""

from __future__ import annotations

import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx
import pytest
from mcp.client.client import Client
from mcp.types import TextContent

pytestmark = pytest.mark.integration

_MAIN = [sys.executable, "-m", "crawl4tools.server.server_main"]

# The User-Agent Open WebUI's ExternalWebLoader sends. (Its empty-key "Bearer " header
# cannot be sent through httpx; the unit tests cover it.)
_USER_AGENT = "Open WebUI (https://github.com/open-webui/open-webui) External Web Loader"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _listening(port: int) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


@dataclass
class Running:
    process: subprocess.Popen[bytes]
    loader: str
    mcp: str

    def stop(self) -> tuple[int, str, str]:
        """Send SIGINT like Ctrl-C and return the exit code, stdout and stderr."""
        self.process.send_signal(signal.SIGINT)
        try:
            stdout, stderr = self.process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            stdout, stderr = self.process.communicate()
        return self.process.returncode, stdout.decode(), stderr.decode()


@pytest.fixture
def start_server() -> Iterator[Callable[..., Running]]:
    started: list[Running] = []

    def start(*extra: str) -> Running:
        loader_port, mcp_port = _free_port(), _free_port()
        process = subprocess.Popen(
            [
                *_MAIN,
                "--loader-port",
                str(loader_port),
                "--mcp-port",
                str(mcp_port),
                "--timeout",
                "30",
                *extra,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        running = Running(
            process, f"http://127.0.0.1:{loader_port}", f"http://127.0.0.1:{mcp_port}/mcp"
        )
        started.append(running)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = process.stderr.read().decode() if process.stderr else ""
                pytest.fail(f"crawl4server exited early: {stderr}")
            if _listening(loader_port) and _listening(mcp_port):
                return running
            time.sleep(0.2)
        pytest.fail("crawl4server did not start listening")

    yield start
    for running in started:
        if running.process.poll() is None:
            running.process.kill()
            running.process.communicate()


async def test_loader_and_mcp_share_one_process(start_server: Callable[..., Running]) -> None:
    server = start_server()
    async with httpx.AsyncClient(timeout=60) as http:
        health = await http.get(f"{server.loader}/health")
        response = await http.post(
            f"{server.loader}/crawl",
            json={"urls": ["https://example.com/", "not a url"]},
            headers={"User-Agent": _USER_AGENT},
        )
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert response.status_code == 200
    documents = response.json()
    assert len(documents) == 1
    assert "Example Domain" in documents[0]["page_content"]
    assert documents[0]["metadata"]["source"] == "https://example.com/"
    assert documents[0]["metadata"]["title"] == "Example Domain"

    async with Client(server.mcp) as client:
        result = await client.call_tool("fetch", {"urls": ["https://example.com/"]})
    assert result.is_error is False
    text = "\n".join(b.text for b in result.content if isinstance(b, TextContent))
    assert "Example Domain" in text

    returncode, stdout, stderr = server.stop()
    assert returncode == 0
    assert stdout == ""
    assert "crawl4server: serving Open WebUI web loader on http://127.0.0.1:" in stderr
    assert "crawl4server: serving MCP on http://127.0.0.1:" in stderr
    assert "Traceback" not in stderr


async def test_loader_api_key_is_enforced(start_server: Callable[..., Running]) -> None:
    key = "integration-test-key"
    server = start_server("--loader-api-key", key)
    body = {"urls": ["https://example.com/"]}
    async with httpx.AsyncClient(timeout=60) as http:
        wrong = await http.post(
            f"{server.loader}/crawl", json=body, headers={"Authorization": "Bearer nope"}
        )
        missing = await http.post(f"{server.loader}/crawl", json=body)
        right = await http.post(
            f"{server.loader}/crawl", json=body, headers={"Authorization": f"Bearer {key}"}
        )
    assert wrong.status_code == 401
    assert missing.status_code == 401
    assert right.status_code == 200
    assert "Example Domain" in right.json()[0]["page_content"]

    returncode, stdout, stderr = server.stop()
    assert returncode == 0
    assert key not in stdout + stderr
