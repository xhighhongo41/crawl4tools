"""Entry point of the crawl4cli command."""

from __future__ import annotations

import asyncio
import logging
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click

from crawl4tools import __version__
from crawl4tools.cli.output import write_outcome
from crawl4tools.cli.report import error_line, exit_code, summary_lines
from crawl4tools.engine import (
    Fetcher,
    FetchOptions,
    FetchOutcome,
    NameAllocator,
    OutputFormat,
    dedupe_urls,
    normalize_proxy,
    validate_url,
)

CRAWL4AI_ATTRIBUTION = (
    "This product includes software developed by UncleCode (https://x.com/unclecode) "
    "as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai)."
)

# Loggers that crawl4ai's HTTP dependencies use directly; silenced unless
# --verbose is given so ordinary runs stay quiet on stderr.
_NOISY_LOGGERS = ("httpx", "httpcore")


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


def create_fetcher(options: FetchOptions) -> Fetcher:
    """Build the :class:`Fetcher` used to run a batch of fetches.

    A thin, monkeypatchable seam: tests replace this to inject a Fetcher
    built with fake crawler/HTTP client factories instead of real ones.
    """
    return Fetcher(options)


async def _fetch_all(
    options: FetchOptions, urls: list[str], concurrency: int
) -> list[FetchOutcome]:
    async with create_fetcher(options) as fetcher:
        return await fetcher.fetch_many(urls, concurrency)


def _validate_urls(
    _ctx: click.Context, _param: click.Parameter, value: tuple[str, ...]
) -> tuple[str, ...]:
    try:
        return tuple(validate_url(url) for url in value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc


def _validate_proxy(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return normalize_proxy(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc


@click.command(context_settings={"auto_envvar_prefix": "CRAWL4CLI"})
@click.argument("urls", nargs=-1, required=True, callback=_validate_urls)
@click.option(
    "-f",
    "--format",
    "format",
    type=click.Choice([fmt.value for fmt in OutputFormat], case_sensitive=False),
    default=OutputFormat.MARKDOWN.value,
    show_default=True,
    help="Output format.",
)
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the (single) fetched URL to this file instead of stdout.",
)
@click.option(
    "-d",
    "--output-dir",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=".",
    show_default=True,
    help="Directory to save fetched URLs into.",
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
    "-j",
    "--concurrency",
    "concurrency",
    type=click.IntRange(min=1),
    default=3,
    show_default=True,
    help="Maximum number of URLs fetched at once.",
)
@click.option(
    "--timeout",
    "timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=60.0,
    show_default=True,
    help="Per-URL timeout in seconds.",
)
@click.option(
    "--citations", "citations", is_flag=True, default=False, help="Add Markdown citations."
)
@click.option(
    "--no-links", "no_links", is_flag=True, default=False, help="Strip links from the Markdown."
)
@click.option(
    "--no-images",
    "no_images",
    is_flag=True,
    default=False,
    help="Strip images from the Markdown.",
)
@click.option(
    "-q", "--quiet", "quiet", is_flag=True, default=False, help="Suppress progress and note lines."
)
@click.option(
    "-v", "--verbose", "verbose", is_flag=True, default=False, help="Enable verbose engine logging."
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
    urls: tuple[str, ...],
    format: str,
    output: Path | None,
    output_dir: Path,
    proxy: str | None,
    fallback: bool,
    concurrency: int,
    timeout: float,
    citations: bool,
    no_links: bool,
    no_images: bool,
    quiet: bool,
    verbose: bool,
) -> None:
    """Download web pages as Markdown and other formats."""
    unique_urls, duplicate_urls = dedupe_urls(urls)

    if output is not None and len(unique_urls) > 1:
        raise click.UsageError(
            "--output can only be used with a single URL; use --output-dir for several URLs"
        )

    if not quiet:
        for url in duplicate_urls:
            click.echo(f"note: duplicate URL ignored: {url}", err=True)

    if not verbose:
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

    options = FetchOptions(
        format=OutputFormat(format.lower()),
        proxy=proxy,
        fallback=fallback,
        timeout_s=timeout,
        citations=citations,
        ignore_links=no_links,
        ignore_images=no_images,
        verbose=verbose,
    )

    outcomes = asyncio.run(_fetch_all(options, unique_urls, concurrency))

    allocator = NameAllocator(output_dir)
    single = len(unique_urls) == 1
    succeeded = 0
    failed_urls: list[str] = []

    for url, outcome in zip(unique_urls, outcomes, strict=True):
        if not quiet:
            for note in outcome.notes:
                click.echo(f"note: {url}: {note}", err=True)

        if not outcome.ok:
            click.echo(error_line(outcome), err=True)
            failed_urls.append(url)
            continue

        to_stdout = single and output is None and outcome.text is not None
        try:
            saved = write_outcome(
                outcome,
                url,
                output_path=output if single else None,
                directory=output_dir,
                allocator=allocator,
                to_stdout=to_stdout,
            )
        except OSError as exc:
            click.echo(f"error: could not write {exc.filename}: {exc.strerror}", err=True)
            failed_urls.append(url)
            continue

        succeeded += 1
        if saved is not None and not quiet:
            click.echo(f"saved: {url} -> {saved}", err=True)

    if len(unique_urls) > 1 and (failed_urls or not quiet):
        for line in summary_lines(succeeded, failed_urls):
            click.echo(line, err=True)

    sys.exit(exit_code(len(failed_urls)))
