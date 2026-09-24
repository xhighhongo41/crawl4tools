"""Entry point of the crawl4mcp command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click
from mcp.server.mcpserver import MCPServer

from crawl4tools import __version__
from crawl4tools.cli.main import CRAWL4AI_ATTRIBUTION
from crawl4tools.server.cli_options import (
    LOOPBACK_HOSTS,
    config_option,
    fetch_option_decorators,
    package_version,
    setup_logging,
    validate_path,
    version_option,
)
from crawl4tools.server.config import CONFIG_KEYS
from crawl4tools.server.mcp_server import build_server
from crawl4tools.server.settings import ServerSettings


def version_text() -> str:
    """Return the text printed by ``crawl4mcp --version``."""
    return (
        f"crawl4mcp {__version__} (crawl4ai {package_version('crawl4ai')}, "
        f"mcp {package_version('mcp')})\n{CRAWL4AI_ATTRIBUTION}"
    )


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
    callback=validate_path,
    help="HTTP path to serve the MCP endpoint at (http transport only).",
)
@fetch_option_decorators(
    timeout_help="Default per-URL timeout in seconds, used when a tool call omits timeout_s.",
    concurrency_help="Maximum number of URLs fetched at once across every tool call.",
    max_urls_help="Maximum number of URLs accepted in a single tool call.",
    download_dir_help="Root directory the download tool saves files into.",
)
@config_option(
    envvar="CRAWL4MCP_CONFIG",
    allowed_keys=CONFIG_KEYS,
    help=(
        "YAML or JSON config file; command-line options and CRAWL4MCP_* environment "
        "variables take precedence."
    ),
)
@version_option(version_text)
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
    setup_logging(verbose)

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

    if transport == "http" and host not in LOOPBACK_HOSTS:
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
