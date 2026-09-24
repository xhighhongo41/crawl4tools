"""Entry point of the crawl4mcp command."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import click

from crawl4tools import __version__
from crawl4tools.cli.main import CRAWL4AI_ATTRIBUTION


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


@click.command(context_settings={"auto_envvar_prefix": "CRAWL4MCP"})
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_print_version,
    help="Show the version and exit.",
)
def main() -> None:
    """Run the crawl4tools MCP server."""
