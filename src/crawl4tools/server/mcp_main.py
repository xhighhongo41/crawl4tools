"""Entry point of the crawl4mcp command."""

from __future__ import annotations

import logging
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import click
from mcp.server.mcpserver import MCPServer

from crawl4tools import __version__
from crawl4tools.cli.main import CRAWL4AI_ATTRIBUTION
from crawl4tools.engine import normalize_proxy
from crawl4tools.server.config import ConfigError, load_config
from crawl4tools.server.mcp_server import build_server
from crawl4tools.server.settings import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_URLS,
    DEFAULT_TIMEOUT_S,
    ServerSettings,
)

# Loggers that crawl4ai's HTTP dependencies use directly; silenced unless
# --verbose is given so ordinary runs stay quiet on stderr.
_NOISY_LOGGERS = ("httpx", "httpcore")

#: Hosts considered local to the machine running the server; binding to any
#: other host earns a warning since the server has no authentication.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def version_text() -> str:
    """Return the text printed by ``crawl4mcp --version``."""
    return (
        f"crawl4mcp {__version__} (crawl4ai {_package_version('crawl4ai')}, "
        f"mcp {_package_version('mcp')})\n{CRAWL4AI_ATTRIBUTION}"
    )


def _print_version(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(version_text())
    ctx.exit(0)


def _validate_proxy(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return normalize_proxy(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc


def _validate_path(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    if not value.startswith("/"):
        raise click.BadParameter("must start with '/'")
    return value


def _load_config_option(ctx: click.Context, _param: click.Parameter, value: Path | None) -> None:
    if value is None:
        return
    try:
        ctx.default_map = load_config(value)
    except ConfigError as exc:
        raise click.BadParameter(str(exc)) from exc


def create_server(settings: ServerSettings) -> MCPServer[Any]:
    """Build the crawl4mcp MCP server for *settings*.

    A thin, monkeypatchable seam: tests replace this to inject a fake server
    instead of building the real fetch/browser machinery.
    """
    return build_server(settings)


@click.command(context_settings={"auto_envvar_prefix": "CRAWL4MCP"})
@click.option(
    "--transport",
    "transport",
    type=click.Choice(["stdio", "http"], case_sensitive=False),
    default="stdio",
    show_default=True,
    help="Transport to serve the MCP protocol over.",
)
@click.option(
    "--host",
    "host",
    default="127.0.0.1",
    show_default=True,
    help="Host to listen on (http transport only).",
)
@click.option(
    "--port",
    "port",
    type=click.IntRange(1, 65535),
    default=8765,
    show_default=True,
    help="Port to listen on (http transport only).",
)
@click.option(
    "--path",
    "path",
    default="/mcp",
    show_default=True,
    callback=_validate_path,
    help="HTTP path to serve the MCP endpoint at (http transport only).",
)
@click.option(
    "--proxy",
    "proxy",
    default=None,
    callback=_validate_proxy,
    help="Proxy URL (http, https, or socks5); e.g. socks5://host:1080.",
)
@click.option(
    "--fallback/--no-fallback",
    "fallback",
    default=True,
    help="Retry without the proxy when the proxy itself appears to be at fault.",
)
@click.option(
    "--timeout",
    "timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=DEFAULT_TIMEOUT_S,
    show_default=True,
    help="Default per-URL timeout in seconds, used when a tool call omits timeout_s.",
)
@click.option(
    "-j",
    "--concurrency",
    "concurrency",
    type=click.IntRange(min=1),
    default=DEFAULT_CONCURRENCY,
    show_default=True,
    help="Maximum number of URLs fetched at once across every tool call.",
)
@click.option(
    "--max-urls",
    "max_urls",
    type=click.IntRange(min=1),
    default=DEFAULT_MAX_URLS,
    show_default=True,
    help="Maximum number of URLs accepted in a single tool call.",
)
@click.option(
    "--download-dir",
    "download_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=".",
    show_default=True,
    help="Root directory the download tool saves files into.",
)
@click.option(
    "-v",
    "--verbose",
    "verbose",
    is_flag=True,
    default=False,
    help="Enable verbose logging.",
)
@click.option(
    "--config",
    "config",
    type=click.Path(dir_okay=False, path_type=Path),
    envvar="CRAWL4MCP_CONFIG",
    is_eager=True,
    expose_value=False,
    callback=_load_config_option,
    help=(
        "YAML or JSON config file; command-line options and CRAWL4MCP_* environment "
        "variables take precedence."
    ),
)
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_print_version,
    help="Show the version and exit.",
)
def main(
    transport: str,
    host: str,
    port: int,
    path: str,
    proxy: str | None,
    fallback: bool,
    timeout: float,
    concurrency: int,
    max_urls: int,
    download_dir: Path,
    verbose: bool,
) -> None:
    """Run the crawl4tools MCP server."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not verbose:
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

    settings = ServerSettings(
        proxy=proxy,
        fallback=fallback,
        timeout_s=timeout,
        concurrency=concurrency,
        max_urls=max_urls,
        download_root=download_dir.resolve(),
        verbose=verbose,
    )
    server = create_server(settings)

    if transport == "http" and host not in _LOOPBACK_HOSTS:
        click.echo(
            f"warning: listening on {host}:{port} without authentication; "
            "anyone who can reach it can use this server",
            err=True,
        )

    try:
        if transport == "stdio":
            server.run(transport="stdio")
        else:
            click.echo(f"crawl4mcp: serving MCP on http://{host}:{port}{path}", err=True)
            server.run(
                transport="streamable-http",
                host=host,
                port=port,
                streamable_http_path=path,
            )
    except OSError as exc:
        reason = exc.strerror or str(exc)
        click.echo(f"error: cannot listen on {host}:{port}: {reason}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
