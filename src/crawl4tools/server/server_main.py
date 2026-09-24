"""Entry point of the crawl4server command.

crawl4server runs one process that listens on two ports: one serves the
Open WebUI external web loader contract, the other serves MCP over
Streamable HTTP. Both share one browser and one concurrency limit.
"""

from __future__ import annotations

import click

from crawl4tools import __version__
from crawl4tools.cli.main import CRAWL4AI_ATTRIBUTION
from crawl4tools.server.cli_options import package_version


def version_text() -> str:
    """Return the text printed by ``crawl4server --version``."""
    return (
        f"crawl4server {__version__} (crawl4ai {package_version('crawl4ai')}, "
        f"mcp {package_version('mcp')}, starlette {package_version('starlette')})\n"
        f"{CRAWL4AI_ATTRIBUTION}"
    )


def _print_version(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(version_text())
    ctx.exit(0)


@click.command(context_settings={"auto_envvar_prefix": "CRAWL4SERVER"})
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_print_version,
    help="Show the version and exit.",
)
def main() -> None:
    """Serve the Open WebUI web loader and MCP from one process."""


if __name__ == "__main__":
    main()
