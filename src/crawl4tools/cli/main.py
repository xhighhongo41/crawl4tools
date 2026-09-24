"""Entry point of the crawl4cli command.

:func:`build_command` builds the click command for one translator, which
fixes the language of the help texts and of the argument errors. Messages
printed while running use the language chosen by ``--lang``, then
``CRAWL4CLI_LANG``, then the locale, else English. The console script
:func:`entry` makes the same choice from the command line and the
environment before building the command, so ``--help`` is in that language
too. The ``note:`` / ``error:`` / ``saved:`` / ``done:`` / ``failed:``
prefixes, the ``--version`` text and the fetched document never change.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click

from crawl4tools import __version__
from crawl4tools.cli.output import write_outcome
from crawl4tools.cli.report import error_line, exit_code, note_line, summary_lines
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
from crawl4tools.i18n import (
    ENGLISH,
    SUPPORTED_LANGUAGES,
    Translator,
    get_translator,
    language_from_argv,
    render_exception,
    resolve_language,
)

CRAWL4AI_ATTRIBUTION = (
    "This product includes software developed by UncleCode (https://x.com/unclecode) "
    "as part of the Crawl4AI project (https://github.com/unclecode/crawl4ai)."
)

# Loggers that crawl4ai's HTTP dependencies use directly; silenced unless
# --verbose is given so ordinary runs stay quiet on stderr.
_NOISY_LOGGERS = ("httpx", "httpcore")

# The variable naming the message language. click also reads it for --lang
# through auto_envvar_prefix="CRAWL4CLI", and rejects unsupported values.
_LANG_ENVVAR = "CRAWL4CLI_LANG"


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


def _message_language(explicit: str | None) -> str:
    """Return the message language: *explicit*, ``CRAWL4CLI_LANG``, the locale, else ``en``."""
    return resolve_language(explicit, env=os.environ, envvar=_LANG_ENVVAR, follow_locale=True)


def _run(
    runtime: Translator,
    urls: tuple[str, ...],
    options: FetchOptions,
    *,
    output: Path | None,
    output_dir: Path,
    concurrency: int,
    quiet: bool,
) -> None:
    """Fetch *urls*, write the results, and exit; messages are translated with *runtime*.

    Raises:
        click.UsageError: if ``--output`` is given with more than one URL.
    """
    unique_urls, duplicate_urls = dedupe_urls(urls)

    if output is not None and len(unique_urls) > 1:
        raise click.UsageError(
            runtime.gettext(
                "--output can only be used with a single URL; use --output-dir for several URLs"
            )
        )

    if not quiet:
        for url in duplicate_urls:
            message = runtime.gettext("duplicate URL ignored: {url}").format(url=url)
            click.echo(f"note: {message}", err=True)

    if not options.verbose:
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

    outcomes = asyncio.run(_fetch_all(options, unique_urls, concurrency))

    allocator = NameAllocator(output_dir)
    single = len(unique_urls) == 1
    succeeded = 0
    failed_urls: list[str] = []

    for url, outcome in zip(unique_urls, outcomes, strict=True):
        if not quiet:
            for note in outcome.notes:
                click.echo(note_line(url, note, runtime), err=True)

        if not outcome.ok:
            click.echo(error_line(outcome, runtime), err=True)
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
            # Keep this msgid as it is: crawl4mcp is to report write failures with it too.
            message = runtime.gettext("could not write {path}: {reason}").format(
                path=exc.filename, reason=exc.strerror
            )
            click.echo(f"error: {message}", err=True)
            failed_urls.append(url)
            continue

        succeeded += 1
        if saved is not None and not quiet:
            click.echo(f"saved: {url} -> {saved}", err=True)

    if len(unique_urls) > 1 and (failed_urls or not quiet):
        for line in summary_lines(succeeded, failed_urls, runtime):
            click.echo(line, err=True)

    sys.exit(exit_code(len(failed_urls)))


def build_command(t: Translator) -> click.Command:
    """Return the crawl4cli click command with its texts translated by *t*.

    *t* translates what is fixed when the command is built: the command and
    option help and the errors of the URL and ``--proxy`` arguments. The
    messages printed while running follow the language resolved at run
    time from ``--lang``, ``CRAWL4CLI_LANG`` and the locale instead.
    """

    def validate_urls(
        _ctx: click.Context, _param: click.Parameter, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        try:
            return tuple(validate_url(url) for url in value)
        except ValueError as exc:
            raise click.BadParameter(render_exception(exc, t)) from exc

    def validate_proxy(
        _ctx: click.Context, _param: click.Parameter, value: str | None
    ) -> str | None:
        if value is None:
            return None
        try:
            return normalize_proxy(value)
        except ValueError as exc:
            raise click.BadParameter(render_exception(exc, t)) from exc

    # The command is still named "main" (click derives the name from the
    # function), as it was before the command was built by this factory.
    @click.command(
        help=t.gettext("Download web pages as Markdown and other formats."),
        context_settings={"auto_envvar_prefix": "CRAWL4CLI"},
    )
    @click.argument("urls", nargs=-1, required=True, callback=validate_urls)
    @click.option(
        "-f",
        "--format",
        "format",
        type=click.Choice([fmt.value for fmt in OutputFormat], case_sensitive=False),
        default=OutputFormat.MARKDOWN.value,
        show_default=True,
        help=t.gettext("Output format."),
    )
    @click.option(
        "-o",
        "--output",
        "output",
        type=click.Path(dir_okay=False, path_type=Path),
        default=None,
        help=t.gettext("Write the (single) fetched URL to this file instead of stdout."),
    )
    @click.option(
        "-d",
        "--output-dir",
        "output_dir",
        type=click.Path(file_okay=False, path_type=Path),
        default=".",
        show_default=True,
        help=t.gettext("Directory to save fetched URLs into."),
    )
    @click.option(
        "--proxy",
        "proxy",
        default=None,
        callback=validate_proxy,
        help=t.gettext("Proxy URL (http, https, or socks5); e.g. socks5://host:1080."),
    )
    @click.option(
        "--fallback/--no-fallback",
        "fallback",
        default=True,
        help=t.gettext("Retry without the proxy when the proxy itself appears to be at fault."),
    )
    @click.option(
        "-j",
        "--concurrency",
        "concurrency",
        type=click.IntRange(min=1),
        default=3,
        show_default=True,
        help=t.gettext("Maximum number of URLs fetched at once."),
    )
    @click.option(
        "--timeout",
        "timeout",
        type=click.FloatRange(min=0, min_open=True),
        default=60.0,
        show_default=True,
        help=t.gettext("Per-URL timeout in seconds."),
    )
    @click.option(
        "--citations",
        "citations",
        is_flag=True,
        default=False,
        help=t.gettext("Add Markdown citations."),
    )
    @click.option(
        "--fit",
        "fit",
        is_flag=True,
        default=False,
        help=t.gettext(
            "Keep only the main content (drop menus, footers, and the like) in the Markdown."
        ),
    )
    @click.option(
        "--no-links",
        "no_links",
        is_flag=True,
        default=False,
        help=t.gettext("Strip links from the Markdown."),
    )
    @click.option(
        "--no-images",
        "no_images",
        is_flag=True,
        default=False,
        help=t.gettext("Strip images from the Markdown."),
    )
    @click.option(
        "-q",
        "--quiet",
        "quiet",
        is_flag=True,
        default=False,
        help=t.gettext("Suppress progress and note lines."),
    )
    @click.option(
        "-v",
        "--verbose",
        "verbose",
        is_flag=True,
        default=False,
        help=t.gettext("Enable verbose engine logging."),
    )
    @click.option(
        "--lang",
        "lang",
        type=click.Choice(list(SUPPORTED_LANGUAGES)),
        default=None,
        help=t.gettext(
            "Language of messages: en or ja. "
            "Defaults to the locale (LANGUAGE, LC_ALL, LC_MESSAGES, LANG), else en."
        ),
    )
    @click.option(
        "--version",
        is_flag=True,
        expose_value=False,
        is_eager=True,
        callback=_print_version,
        help=t.gettext("Show the version and exit."),
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
        fit: bool,
        no_links: bool,
        no_images: bool,
        quiet: bool,
        verbose: bool,
        lang: str | None,
    ) -> None:
        # No docstring: the help comes from help= above so that it is translated.
        runtime = get_translator(_message_language(lang))
        options = FetchOptions(
            format=OutputFormat(format.lower()),
            proxy=proxy,
            fallback=fallback,
            timeout_s=timeout,
            citations=citations,
            fit=fit,
            ignore_links=no_links,
            ignore_images=no_images,
            verbose=verbose,
        )
        _run(
            runtime,
            urls,
            options,
            output=output,
            output_dir=output_dir,
            concurrency=concurrency,
            quiet=quiet,
        )

    return main


main = build_command(ENGLISH)
"""The command with English help, for importing and testing.

Its run-time messages still follow ``--lang``, ``CRAWL4CLI_LANG`` and the
locale; only the help and the argument errors are always English.
"""


def entry() -> None:
    """Run crawl4cli as the console script.

    The language is chosen from ``--lang`` in ``sys.argv``, then
    ``CRAWL4CLI_LANG``, then the locale, before the command is built, so the
    help and the argument errors are in that language as well.
    """
    lang = _message_language(language_from_argv(sys.argv[1:]))
    build_command(get_translator(lang)).main(prog_name="crawl4cli")
