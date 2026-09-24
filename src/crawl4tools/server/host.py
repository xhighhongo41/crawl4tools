"""Host the web loader and MCP apps in one process on two ports.

:func:`serve` runs a single uvicorn server listening on two sockets: the
Open WebUI web loader app answers on one port and the MCP Streamable HTTP
app on the other. Both apps fetch through one shared
:class:`~crawl4tools.server.mcp_server.ServerState`, so one fetcher (and
at most one browser) and one semaphore serve the whole process.

One server with two sockets (rather than two servers) keeps uvicorn's
SIGINT/SIGTERM handling in a single place: :class:`PortDispatcher` routes
each request by the local port it arrived on.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import uvicorn
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from crawl4tools.engine.fetcher import Fetcher
from crawl4tools.server.loader import LoaderSettings, build_loader_app
from crawl4tools.server.mcp_server import FetcherFactory, build_server, open_state
from crawl4tools.server.settings import ServerSettings

#: Called once the server listens, with the server and the actual ports
#: (``{"loader": ..., "mcp": ...}``).
StartedCallback = Callable[[uvicorn.Server, dict[str, int]], None]

#: Backlog of the listening sockets (uvicorn's default).
_BACKLOG = 2048


@dataclass(frozen=True)
class McpSettings:
    """Settings of the MCP app: ``path`` is its Streamable HTTP endpoint."""

    path: str = "/mcp"


class ListenError(OSError):
    """Raised when a listening socket cannot be set up for ``host:port``.

    ``reason`` is the operating system's description of the failure; the
    original :class:`OSError` is chained as ``__cause__``.
    """

    def __init__(self, host: str, port: int, reason: str) -> None:
        super().__init__(f"cannot listen on {_address(host, port)}: {reason}")
        self.host = host
        self.port = port
        self.reason = reason


def _address(host: str, port: int) -> str:
    """Return ``host:port``, bracketing IPv6 addresses."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _bind_one(host: str, port: int) -> socket.socket:
    """Return a socket bound to *host*:*port* and listening.

    Raises:
        OSError: if the address cannot be resolved or bound.
    """
    family, type_, proto, _canonname, sockaddr = socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )[0]
    sock = socket.socket(family, type_, proto)
    try:
        if sys.platform != "win32":
            # On Windows SO_REUSEADDR would let two processes share a port.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6 and host == "::":
            # Best effort: also accept IPv4 connections on the IPv6 wildcard.
            try:
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except (AttributeError, OSError):
                pass
        sock.bind(sockaddr)
        sock.listen(_BACKLOG)
    except BaseException:
        sock.close()
        raise
    return sock


def bind_sockets(host: str, ports: Sequence[int]) -> list[socket.socket]:
    """Bind one listening socket per port of *ports* on *host*, in order.

    Port 0 binds an ephemeral port; read the actual port with
    ``sock.getsockname()[1]``. If any port fails, the sockets already
    opened are closed.

    Raises:
        ListenError: naming the first ``host:port`` that could not be
            resolved or bound.
    """
    sockets: list[socket.socket] = []
    try:
        for port in ports:
            try:
                sockets.append(_bind_one(host, port))
            except OSError as exc:
                raise ListenError(host, port, exc.strerror or str(exc)) from exc
    except BaseException:
        _close_all(sockets)
        raise
    return sockets


def _close_all(sockets: Sequence[socket.socket]) -> None:
    """Close every socket of *sockets*, ignoring errors."""
    for sock in sockets:
        try:
            sock.close()
        except OSError:
            pass


class PortDispatcher:
    """An ASGI app routing each connection by the local port it arrived on.

    Requests to a port without an app answer 404 JSON (websockets are
    closed). Lifespan events are acknowledged immediately: the host runs
    the apps' shared resources itself.
    """

    def __init__(self, apps: Mapping[int, ASGIApp]) -> None:
        self.apps = dict(apps)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Dispatch *scope* to the app of its local port."""
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        server = scope.get("server")
        app = self.apps.get(server[1]) if server is not None else None
        if app is not None:
            await app(scope, receive, send)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        else:
            await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)

    @staticmethod
    async def _lifespan(receive: Receive, send: Send) -> None:
        """Complete lifespan startup and shutdown without doing anything."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return


class _Server(uvicorn.Server):
    """A uvicorn server calling a hook once it listens."""

    def __init__(
        self, config: uvicorn.Config, ports: dict[str, int], on_started: StartedCallback | None
    ) -> None:
        super().__init__(config)
        self._ports = ports
        self._on_started = on_started

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        """Start listening, then call the ``on_started`` hook."""
        await super().startup(sockets=sockets)
        if not self.started or self._on_started is None:
            return
        try:
            self._on_started(self, dict(self._ports))
        except BaseException:
            for server in self.servers:
                server.close()
            raise


async def serve_async(
    settings: ServerSettings,
    loader: LoaderSettings,
    mcp: McpSettings,
    *,
    host: str,
    loader_port: int,
    mcp_port: int,
    verbose: bool = False,
    fetcher_factory: FetcherFactory = Fetcher,
    sockets: list[socket.socket] | None = None,
    on_started: StartedCallback | None = None,
) -> None:
    """Serve the web loader and MCP apps until the server is told to exit.

    Binds *loader_port* and *mcp_port* on *host* (unless *sockets*, the
    already bound loader and MCP sockets in that order, are given), opens
    the shared state with *fetcher_factory*, and runs one uvicorn server on
    both sockets. *on_started* is called once the server listens, with the
    server (set its ``should_exit`` to stop it) and the actual ports.

    On SIGINT/SIGTERM uvicorn shuts down gracefully and then re-raises the
    signal, so a :class:`KeyboardInterrupt` surfaces after cleanup.

    Raises:
        ListenError: if a port cannot be bound; nothing is started then.
    """
    if sockets is None:
        sockets = bind_sockets(host, [loader_port, mcp_port])
    try:
        ports = {"loader": sockets[0].getsockname()[1], "mcp": sockets[1].getsockname()[1]}
        async with open_state(settings, fetcher_factory) as state:
            mcp_server = build_server(settings, state=state)
            mcp_app = mcp_server.streamable_http_app(streamable_http_path=mcp.path, host=host)
            loader_app = build_loader_app(state, loader)
            app = PortDispatcher({ports["loader"]: loader_app, ports["mcp"]: mcp_app})
            async with mcp_server.session_manager.run():
                config = uvicorn.Config(
                    app,
                    lifespan="off",
                    log_level="info" if verbose else "warning",
                    access_log=verbose,
                )
                await _Server(config, ports, on_started).serve(sockets=sockets)
    finally:
        _close_all(sockets)


def serve(
    settings: ServerSettings,
    loader: LoaderSettings,
    mcp: McpSettings,
    *,
    host: str,
    loader_port: int,
    mcp_port: int,
    verbose: bool = False,
    fetcher_factory: FetcherFactory = Fetcher,
    on_started: StartedCallback | None = None,
) -> None:
    """Run :func:`serve_async` in a new event loop until it finishes.

    :class:`KeyboardInterrupt` (after Ctrl-C) and :class:`ListenError`
    propagate to the caller.
    """
    asyncio.run(
        serve_async(
            settings,
            loader,
            mcp,
            host=host,
            loader_port=loader_port,
            mcp_port=mcp_port,
            verbose=verbose,
            fetcher_factory=fetcher_factory,
            on_started=on_started,
        )
    )
