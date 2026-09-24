"""Entry point of the crawl4server command.

crawl4server runs one process that listens on two ports: one serves the
Open WebUI external web loader contract, the other serves MCP over
Streamable HTTP. Both share one browser and one concurrency limit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
import uvicorn

from crawl4tools import __version__
from crawl4tools.cli.main import CRAWL4AI_ATTRIBUTION
from crawl4tools.i18n import ENGLISH
from crawl4tools.server import host as host_module
from crawl4tools.server.cli_options import (
    LOOPBACK_HOSTS,
    config_option,
    fetch_option_decorators,
    package_version,
    path_validator,
    setup_logging,
    version_option,
)
from crawl4tools.server.host import McpSettings, StartedCallback
from crawl4tools.server.loader import LoaderSettings
from crawl4tools.server.settings import ServerSettings

#: Path reserved for the web loader app's health check; --loader-path may
#: not be set to it.
_HEALTH_PATH = "/health"

#: The click option names accepted in a crawl4server config file, i.e.
#: every top-level key that this module's ``main`` recognizes.
SERVER_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "host",
        "loader_port",
        "loader_path",
        "loader_api_key",
        "loader_fit",
        "mcp_port",
        "mcp_path",
        "proxy",
        "fallback",
        "timeout",
        "concurrency",
        "max_urls",
        "download_dir",
        "verbose",
    }
)


def version_text() -> str:
    """Return the text printed by ``crawl4server --version``."""
    return (
        f"crawl4server {__version__} (crawl4ai {package_version('crawl4ai')}, "
        f"mcp {package_version('mcp')}, starlette {package_version('starlette')})\n"
        f"{CRAWL4AI_ATTRIBUTION}"
    )


def _address(host: str, port: int) -> str:
    """Return ``host:port``, bracketing IPv6 addresses."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def validate_loader_path(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Click callback for ``--loader-path``: a path, distinct from ``/health``."""
    value = path_validator(ENGLISH)(ctx, param, value)
    if value == _HEALTH_PATH:
        raise click.BadParameter(f"'{_HEALTH_PATH}' is reserved for the health check")
    return value


def run_server(
    settings: ServerSettings,
    loader: LoaderSettings,
    mcp: McpSettings,
    *,
    host: str,
    loader_port: int,
    mcp_port: int,
    verbose: bool,
    on_started: StartedCallback | None,
) -> None:
    """Serve *settings* on *host*, both ports at once.

    A thin, monkeypatchable seam: tests replace this to inject a fake
    server instead of actually binding sockets and running uvicorn.
    """
    host_module.serve(
        settings,
        loader,
        mcp,
        host=host,
        loader_port=loader_port,
        mcp_port=mcp_port,
        verbose=verbose,
        on_started=on_started,
    )


@click.command(context_settings={"auto_envvar_prefix": "CRAWL4SERVER"})
@click.option(
    "--host",
    "host",
    default="127.0.0.1",
    show_default=True,
    help="Host to listen on (both ports).",
)
@click.option(
    "--loader-port",
    "loader_port",
    type=click.IntRange(1, 65535),
    default=8766,
    show_default=True,
    help="Open WebUI web loader port.",
)
@click.option(
    "--loader-path",
    "loader_path",
    default="/crawl",
    show_default=True,
    callback=validate_loader_path,
    help="HTTP path the Open WebUI web loader listens on.",
)
@click.option(
    "--loader-api-key",
    "loader_api_key",
    default=None,
    show_default=False,
    help=(
        "Require 'Authorization: Bearer <key>' on the web loader (Open WebUI's "
        "External Web Loader API Key)."
    ),
)
@click.option(
    "--loader-fit/--no-loader-fit",
    "loader_fit",
    default=False,
    help="Keep only the main content of each page (crawl4ai content filter).",
)
@click.option(
    "--mcp-port",
    "mcp_port",
    type=click.IntRange(1, 65535),
    default=8765,
    show_default=True,
    help="MCP Streamable HTTP port.",
)
@click.option(
    "--mcp-path",
    "mcp_path",
    default="/mcp",
    show_default=True,
    callback=path_validator(ENGLISH),
    help="HTTP path to serve the MCP endpoint at.",
)
@fetch_option_decorators(
    ENGLISH,
    timeout_help=(
        "Default per-URL timeout in seconds, for web loader requests and MCP tool "
        "calls that omit timeout_s."
    ),
    concurrency_help="Maximum number of URLs fetched at once across both ports.",
    max_urls_help=(
        "Maximum number of URLs accepted in one web loader request or MCP tool call "
        "(Open WebUI sends up to 20)."
    ),
    download_dir_help="Root directory the MCP download tool saves files into.",
)
@config_option(
    ENGLISH,
    envvar="CRAWL4SERVER_CONFIG",
    allowed_keys=SERVER_CONFIG_KEYS,
    help=(
        "YAML or JSON config file; command-line options and CRAWL4SERVER_* environment "
        "variables take precedence."
    ),
)
@version_option(ENGLISH, version_text)
def main(
    host: str,
    loader_port: int,
    loader_path: str,
    loader_api_key: str | None,
    loader_fit: bool,
    mcp_port: int,
    mcp_path: str,
    proxy: str | None,
    fallback: bool,
    timeout: float,
    concurrency: int,
    max_urls: int,
    download_dir: Path,
    verbose: bool,
) -> None:
    """Serve the Open WebUI web loader and MCP from one process."""
    setup_logging(verbose)

    if loader_port == mcp_port:
        raise click.UsageError("--loader-port and --mcp-port must differ")

    settings = ServerSettings(
        proxy=proxy,
        fallback=fallback,
        timeout_s=timeout,
        concurrency=concurrency,
        max_urls=max_urls,
        download_root=download_dir.resolve(),
        verbose=verbose,
    )
    loader = LoaderSettings(path=loader_path, api_key=loader_api_key or None, fit=loader_fit)
    mcp = McpSettings(path=mcp_path)

    if host not in LOOPBACK_HOSTS:
        click.echo(
            "crawl4server: warning: the MCP endpoint has no authentication on a "
            "non-loopback host; anyone who can reach it can use this server",
            err=True,
        )
        if loader.api_key is None:
            click.echo(
                "crawl4server: warning: the web loader has no authentication either; "
                "set --loader-api-key to require one",
                err=True,
            )

    def on_started(server: uvicorn.Server, ports: dict[str, int]) -> None:
        click.echo(
            f"crawl4server: serving Open WebUI web loader on "
            f"http://{_address(host, ports['loader'])}{loader_path}",
            err=True,
        )
        click.echo(
            f"crawl4server: serving MCP on http://{_address(host, ports['mcp'])}{mcp_path}",
            err=True,
        )

    try:
        run_server(
            settings,
            loader,
            mcp,
            host=host,
            loader_port=loader_port,
            mcp_port=mcp_port,
            verbose=verbose,
            on_started=on_started,
        )
    except OSError as exc:
        # ListenError (raised when a port cannot be bound) is an OSError
        # subclass whose message already reads "cannot listen on ...".
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
