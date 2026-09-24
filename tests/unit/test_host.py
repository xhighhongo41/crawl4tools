from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import uvicorn
from conftest import FakeCrawler, FakeHttp
from mcp.client.client import Client
from mcp.types import TextContent
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from crawl4tools.engine.fetcher import Fetcher
from crawl4tools.engine.models import FetchOptions
from crawl4tools.i18n import ENGLISH, LocalizedError, Translator, get_translator
from crawl4tools.server import (
    ListenError,
    LoaderSettings,
    McpSettings,
    PortDispatcher,
    ServerSettings,
    bind_sockets,
)
from crawl4tools.server import host as host_module
from crawl4tools.server.host import serve_async

URL = "https://example.com/page"
URL2 = "https://example.com/other"
URL3 = "https://example.com/third"
TIMEOUT = 20.0
JA = get_translator("ja")

# --- PortDispatcher -------------------------------------------------------------


class RecordingApp:
    """A tiny ASGI app that records its scopes and answers 200 with its name."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.scopes: list[Scope] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scopes.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": self.name.encode()})


def http_scope(server: tuple[str, int] | None) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/x",
        "raw_path": b"/x",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": server,
        "client": ("127.0.0.1", 50000),
    }


async def run_asgi(
    app: Callable[[Scope, Receive, Send], Awaitable[None]],
    scope: Scope,
    incoming: list[Message] | None = None,
) -> list[Message]:
    """Run *app* for *scope*, feeding *incoming* messages; return what it sent."""
    queue = list(incoming if incoming is not None else [{"type": "http.request", "body": b""}])
    sent: list[Message] = []

    async def receive() -> Message:
        if queue:
            return queue.pop(0)
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def send(message: Message) -> None:
        sent.append(message)

    await asyncio.wait_for(app(scope, receive, send), TIMEOUT)
    return sent


def body_of(sent: list[Message]) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


async def test_dispatcher_routes_by_local_port() -> None:
    one, two = RecordingApp("one"), RecordingApp("two")
    dispatcher = PortDispatcher({8001: one, 8002: two}, ENGLISH)

    sent = await run_asgi(dispatcher, http_scope(("127.0.0.1", 8002)))
    assert body_of(sent) == b"two"
    sent = await run_asgi(dispatcher, http_scope(("127.0.0.1", 8001)))
    assert body_of(sent) == b"one"
    assert len(one.scopes) == 1
    assert len(two.scopes) == 1


@pytest.mark.parametrize("server", [("127.0.0.1", 9999), None])
async def test_dispatcher_unknown_port_is_404_json(server: tuple[str, int] | None) -> None:
    app = RecordingApp("one")
    sent = await run_asgi(PortDispatcher({8001: app}, ENGLISH), http_scope(server))
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 404
    assert json.loads(body_of(sent)) == {"error": "not found"}
    assert app.scopes == []


async def test_dispatcher_unknown_port_404_in_japanese() -> None:
    app = RecordingApp("one")
    sent = await run_asgi(PortDispatcher({8001: app}, JA), http_scope(("127.0.0.1", 9999)))
    assert sent[0]["status"] == 404
    assert json.loads(body_of(sent)) == {"error": "見つかりません"}


async def test_dispatcher_unknown_port_websocket_is_closed() -> None:
    app = RecordingApp("one")
    scope = {**http_scope(("127.0.0.1", 9999)), "type": "websocket"}
    sent = await run_asgi(
        PortDispatcher({8001: app}, ENGLISH), scope, [{"type": "websocket.connect"}]
    )
    assert sent[-1]["type"] == "websocket.close"
    assert app.scopes == []


async def test_dispatcher_completes_lifespan() -> None:
    app = RecordingApp("one")
    scope: Scope = {"type": "lifespan", "asgi": {"version": "3.0"}}
    sent = await run_asgi(
        PortDispatcher({8001: app}, ENGLISH),
        scope,
        [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}],
    )
    assert [m["type"] for m in sent] == [
        "lifespan.startup.complete",
        "lifespan.shutdown.complete",
    ]
    assert app.scopes == []


# --- bind_sockets ---------------------------------------------------------------


def test_bind_sockets_two_ephemeral_ports() -> None:
    sockets = bind_sockets("127.0.0.1", [0, 0])
    try:
        assert len(sockets) == 2
        ports = [sock.getsockname()[1] for sock in sockets]
        assert all(port > 0 for port in ports)
        assert ports[0] != ports[1]
        assert all(sock.getsockname()[0] == "127.0.0.1" for sock in sockets)
    finally:
        for sock in sockets:
            sock.close()


def test_bind_sockets_port_in_use_raises_listen_error() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy = blocker.getsockname()[1]

        opened: list[socket.socket] = []
        real_socket = socket.socket

        def tracking_socket(*args: Any, **kwargs: Any) -> socket.socket:
            sock = real_socket(*args, **kwargs)
            opened.append(sock)
            return sock

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(socket, "socket", tracking_socket)
            with pytest.raises(ListenError) as info:
                bind_sockets("127.0.0.1", [0, busy])

    error = info.value
    assert isinstance(error, OSError)
    assert error.host == "127.0.0.1"
    assert error.port == busy
    assert error.reason
    assert isinstance(error.__cause__, OSError)
    assert str(busy) in str(error)
    assert len(opened) == 2
    assert all(sock.fileno() == -1 for sock in opened)


def test_bind_sockets_unresolvable_host_raises_listen_error() -> None:
    with pytest.raises(ListenError) as info:
        bind_sockets("no-such-host.invalid", [0])
    assert info.value.host == "no-such-host.invalid"
    assert info.value.port == 0


def test_listen_error_message_in_english_and_japanese() -> None:
    error = ListenError("127.0.0.1", 8766, "Address already in use")
    assert isinstance(error, OSError)
    assert isinstance(error, LocalizedError)
    assert (error.host, error.port, error.reason) == ("127.0.0.1", 8766, "Address already in use")
    assert str(error) == "cannot listen on 127.0.0.1:8766: Address already in use"
    assert error.render(ENGLISH) == str(error)
    assert error.render(JA) == "127.0.0.1:8766 で待ち受けできません: Address already in use"


def test_listen_error_brackets_ipv6_addresses() -> None:
    error = ListenError("::1", 8765, "busy")
    assert str(error) == "cannot listen on [::1]:8765: busy"
    assert error.render(JA) == "[::1]:8765 で待ち受けできません: busy"


def test_listen_error_is_caught_as_os_error() -> None:
    with pytest.raises(OSError) as info:
        raise ListenError("127.0.0.1", 1, "denied")
    assert str(info.value) == "cannot listen on 127.0.0.1:1: denied"


def _ipv6_available() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock:
            sock.bind(("::1", 0))
    except OSError:
        return False
    return True


@pytest.mark.skipif(not _ipv6_available(), reason="IPv6 loopback unavailable")
def test_bind_sockets_ipv6_loopback() -> None:
    sockets = bind_sockets("::1", [0])
    try:
        assert sockets[0].family == socket.AF_INET6
        assert sockets[0].getsockname()[1] > 0
    finally:
        for sock in sockets:
            sock.close()


# --- serve_async end to end -----------------------------------------------------


class CountingFetcher(Fetcher):
    """A Fetcher that counts how often it is closed."""

    closes = 0

    async def aclose(self) -> None:
        type(self).closes += 1
        await super().aclose()


@dataclass
class Running:
    """A serve_async task running in the background, with its actual ports."""

    task: asyncio.Task[None]
    server: uvicorn.Server
    ports: dict[str, int]
    crawler: FakeCrawler
    fetchers: list[CountingFetcher] = field(default_factory=list)

    def url(self, name: str, path: str) -> str:
        return f"http://127.0.0.1:{self.ports[name]}{path}"


@asynccontextmanager
async def running_host(
    *, crawler: FakeCrawler | None = None, **settings: Any
) -> AsyncIterator[Running]:
    """Run serve_async on 127.0.0.1 with ephemeral ports and fake fetch doubles."""
    fake = crawler if crawler is not None else FakeCrawler()
    fake_http = FakeHttp()
    fetchers: list[CountingFetcher] = []

    def factory(options: FetchOptions) -> Fetcher:
        fetcher = CountingFetcher(
            options, crawler_factory=fake.factory, http_client_factory=fake_http
        )
        fetchers.append(fetcher)
        return fetcher

    started: asyncio.Future[tuple[uvicorn.Server, dict[str, int]]] = (
        asyncio.get_running_loop().create_future()
    )

    def on_started(server: uvicorn.Server, ports: dict[str, int]) -> None:
        started.set_result((server, ports))

    task = asyncio.create_task(
        serve_async(
            ServerSettings(**settings),
            LoaderSettings(),
            McpSettings(),
            host="127.0.0.1",
            loader_port=0,
            mcp_port=0,
            fetcher_factory=factory,
            on_started=on_started,
        )
    )
    try:
        waiting: set[asyncio.Future[Any]] = {task, started}
        done, _ = await asyncio.wait(waiting, timeout=TIMEOUT, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            task.result()  # raise the startup error
            pytest.fail("serve_async returned before starting")
        assert started in done, "serve_async did not start in time"
        server, ports = started.result()
        yield Running(task, server, ports, fake, fetchers)
    finally:
        if "server" in locals():
            server.should_exit = True
        if not task.done():
            try:
                await asyncio.wait_for(task, TIMEOUT)
            except TimeoutError:
                task.cancel()
                raise


async def stop(running: Running) -> None:
    running.server.should_exit = True
    await asyncio.wait_for(running.task, TIMEOUT)


def texts(result: Any) -> list[str]:
    return [block.text for block in result.content if isinstance(block, TextContent)]


async def test_serve_async_serves_loader_and_mcp_on_two_ports() -> None:
    CountingFetcher.closes = 0
    async with running_host() as running:
        assert set(running.ports) == {"loader", "mcp"}
        assert running.ports["loader"] != running.ports["mcp"]
        assert all(port > 0 for port in running.ports.values())

        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            health = await http.get(running.url("loader", "/health"))
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            crawled = await http.post(running.url("loader", "/crawl"), json={"urls": [URL]})
            assert crawled.status_code == 200
            documents = crawled.json()
            assert isinstance(documents, list)
            assert len(documents) == 1
            assert documents[0]["metadata"]["source"] == URL
            assert "Hello" in documents[0]["page_content"]

            wrong_port = await http.post(running.url("mcp", "/crawl"), json={"urls": [URL]})
            assert wrong_port.status_code == 404

        async with Client(running.url("mcp", "/mcp")) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert tools == {"fetch", "download"}
            result = await client.call_tool("fetch", {"urls": [URL]})
        assert result.is_error is False
        assert any("Hello" in text for text in texts(result))

        await stop(running)
        # Finished cleanly: no KeyboardInterrupt or other error surfaced.
        assert running.task.exception() is None

    assert len(running.fetchers) == 1
    assert CountingFetcher.closes == 1
    assert running.crawler.exited == running.crawler.entered == 1


async def test_serve_async_shares_one_semaphore_across_apps() -> None:
    crawler = FakeCrawler(delay=0.2)
    async with running_host(crawler=crawler, concurrency=2) as running:
        async with (
            httpx.AsyncClient(timeout=TIMEOUT) as http,
            Client(running.url("mcp", "/mcp")) as client,
        ):
            loader_call = http.post(
                running.url("loader", "/crawl"), json={"urls": [URL, URL2, URL3]}
            )
            mcp_call = client.call_tool(
                "fetch", {"urls": ["https://example.org/a", "https://example.org/b"]}
            )
            crawled, result = await asyncio.wait_for(asyncio.gather(loader_call, mcp_call), TIMEOUT)
        await stop(running)

    assert crawled.status_code == 200
    assert len(crawled.json()) == 3
    assert result.is_error is False
    assert len(crawler.calls) == 5
    assert crawler.max_in_flight == 2


async def test_serve_async_uses_prebound_sockets() -> None:
    sockets = bind_sockets("127.0.0.1", [0, 0])
    expected = {"loader": sockets[0].getsockname()[1], "mcp": sockets[1].getsockname()[1]}
    seen: list[dict[str, int]] = []
    servers: list[uvicorn.Server] = []

    def on_started(server: uvicorn.Server, ports: dict[str, int]) -> None:
        seen.append(ports)
        servers.append(server)
        server.should_exit = True

    fake = FakeCrawler()

    def factory(options: FetchOptions) -> Fetcher:
        return Fetcher(options, crawler_factory=fake.factory, http_client_factory=FakeHttp())

    await asyncio.wait_for(
        serve_async(
            ServerSettings(),
            LoaderSettings(),
            McpSettings(),
            host="127.0.0.1",
            loader_port=0,
            mcp_port=0,
            fetcher_factory=factory,
            sockets=sockets,
            on_started=on_started,
        ),
        TIMEOUT,
    )
    assert seen == [expected]
    assert all(sock.fileno() == -1 for sock in sockets)


async def test_serve_async_port_in_use_raises_listen_error() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy = blocker.getsockname()[1]
        factory_calls = 0

        def factory(options: FetchOptions) -> Fetcher:
            nonlocal factory_calls
            factory_calls += 1
            return Fetcher(options)

        with pytest.raises(ListenError) as info:
            await serve_async(
                ServerSettings(),
                LoaderSettings(),
                McpSettings(),
                host="127.0.0.1",
                loader_port=0,
                mcp_port=busy,
                fetcher_factory=factory,
            )
    assert info.value.port == busy
    assert factory_calls == 0


@pytest.mark.parametrize("lang", ["en", "ja"])
async def test_serve_async_dispatcher_uses_the_settings_translator(
    monkeypatch: pytest.MonkeyPatch, lang: str
) -> None:
    translators: list[Translator] = []

    class RecordingDispatcher(PortDispatcher):
        def __init__(self, apps: Mapping[int, ASGIApp], t: Translator) -> None:
            translators.append(t)
            super().__init__(apps, t)

    monkeypatch.setattr(host_module, "PortDispatcher", RecordingDispatcher)

    def on_started(server: uvicorn.Server, ports: dict[str, int]) -> None:
        server.should_exit = True

    fake = FakeCrawler()

    def factory(options: FetchOptions) -> Fetcher:
        return Fetcher(options, crawler_factory=fake.factory, http_client_factory=FakeHttp())

    await asyncio.wait_for(
        serve_async(
            ServerSettings(lang=lang),
            LoaderSettings(),
            McpSettings(),
            host="127.0.0.1",
            loader_port=0,
            mcp_port=0,
            fetcher_factory=factory,
            on_started=on_started,
        ),
        TIMEOUT,
    )
    assert translators == [get_translator(lang)]
