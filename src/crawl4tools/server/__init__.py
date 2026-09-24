"""MCP server and Open WebUI web loader built on the shared fetch engine."""

from __future__ import annotations

from crawl4tools.server.loader import LoaderSettings, build_loader_app
from crawl4tools.server.mcp_server import build_server, fetch_all, open_state
from crawl4tools.server.settings import ServerSettings

__all__ = [
    "LoaderSettings",
    "ServerSettings",
    "build_loader_app",
    "build_server",
    "fetch_all",
    "open_state",
]
