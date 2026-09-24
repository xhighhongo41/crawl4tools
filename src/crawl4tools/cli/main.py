"""Entry point of the crawl4cli command."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import click

from crawl4tools import __version__

CRAWL4AI_ATTRIBUTION = (
    "This product includes software developed by UncleCode (https://x.com/unclecode) "
    "as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai)."
)


def _crawl4ai_version() -> str:
    try:
        return version("crawl4ai")
    except PackageNotFoundError:
        return "unknown"


def version_text() -> str:
    """Return the text printed by ``crawl4cli --version``."""
    return f"crawl4cli {__version__} (crawl4ai {_crawl4ai_version()})\n{CRAWL4AI_ATTRIBUTION}"


def _print_version(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    click.echo(version_text())
    ctx.exit(0)


@click.command(context_settings={"auto_envvar_prefix": "CRAWL4CLI"})
@click.option(
    "--version",
    is_flag=True,
    expose_value=False,
    is_eager=True,
    callback=_print_version,
    help="Show the version and exit.",
)
def main() -> None:
    """Download web pages as Markdown and other formats."""
