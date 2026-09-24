"""Click options and helpers shared by the crawl4mcp and crawl4server commands.

Both commands expose the same server-wide fetch options (proxy, timeout,
concurrency, ...), the same ``--config``/``--version`` behaviour and the
same logging setup. Only the help texts that describe the scope of a
setting differ, so those are parameters of :func:`fetch_option_decorators`.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, TypeVar

import click

from crawl4tools.engine import normalize_proxy
from crawl4tools.server.config import ConfigError, load_config
from crawl4tools.server.settings import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_URLS,
    DEFAULT_TIMEOUT_S,
)

F = TypeVar("F", bound=Callable[..., Any])

# Loggers that crawl4ai's HTTP dependencies use directly; silenced unless
# --verbose is given so ordinary runs stay quiet on stderr.
_NOISY_LOGGERS = ("httpx", "httpcore")

#: Hosts considered local to the machine running the server; binding to any
#: other host earns a warning since the server has no authentication.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def package_version(name: str) -> str:
    """Return the installed version of distribution *name*, or ``"unknown"``."""
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def setup_logging(verbose: bool) -> None:
    """Configure logging to stderr; INFO when *verbose*, WARNING otherwise.

    Unless *verbose*, the noisy HTTP client loggers are held at WARNING too.
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if not verbose:
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)


def validate_proxy(_ctx: click.Context, _param: click.Parameter, value: str | None) -> str | None:
    """Click callback normalizing a ``--proxy`` value (``None`` passes through)."""
    if value is None:
        return None
    try:
        return normalize_proxy(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc


def validate_path(_ctx: click.Context, _param: click.Parameter, value: str) -> str:
    """Click callback requiring an HTTP path option to start with ``/``."""
    if not value.startswith("/"):
        raise click.BadParameter("must start with '/'")
    return value


def fetch_option_decorators(
    *,
    timeout_help: str,
    concurrency_help: str,
    max_urls_help: str,
    download_dir_help: str,
) -> Callable[[F], F]:
    """Return a decorator adding the server-wide fetch options to a command.

    The options are, in ``--help`` order: ``--proxy``,
    ``--fallback/--no-fallback``, ``--timeout``, ``-j/--concurrency``,
    ``--max-urls``, ``--download-dir`` and ``-v/--verbose``. The help texts
    of the four options whose scope differs between commands are given by
    the caller.
    """
    options: list[Callable[[F], F]] = [
        click.option(
            "--proxy",
            "proxy",
            default=None,
            callback=validate_proxy,
            help="Proxy URL (http, https, or socks5); e.g. socks5://host:1080.",
        ),
        click.option(
            "--fallback/--no-fallback",
            "fallback",
            default=True,
            help="Retry without the proxy when the proxy itself appears to be at fault.",
        ),
        click.option(
            "--timeout",
            "timeout",
            type=click.FloatRange(min=0, min_open=True),
            default=DEFAULT_TIMEOUT_S,
            show_default=True,
            help=timeout_help,
        ),
        click.option(
            "-j",
            "--concurrency",
            "concurrency",
            type=click.IntRange(min=1),
            default=DEFAULT_CONCURRENCY,
            show_default=True,
            help=concurrency_help,
        ),
        click.option(
            "--max-urls",
            "max_urls",
            type=click.IntRange(min=1),
            default=DEFAULT_MAX_URLS,
            show_default=True,
            help=max_urls_help,
        ),
        click.option(
            "--download-dir",
            "download_dir",
            type=click.Path(file_okay=False, path_type=Path),
            default=".",
            show_default=True,
            help=download_dir_help,
        ),
        click.option(
            "-v",
            "--verbose",
            "verbose",
            is_flag=True,
            default=False,
            help="Enable verbose logging.",
        ),
    ]

    def decorate(function: F) -> F:
        # Apply bottom-up, as stacked decorators would, so --help lists the
        # options in the order above.
        for option in reversed(options):
            function = option(function)
        return function

    return decorate


def config_option(envvar: str, allowed_keys: frozenset[str], help: str) -> Callable[[F], F]:
    """Return the eager ``--config`` option loading a YAML/JSON config file.

    The file's values become the command's ``default_map``, so command-line
    options and environment variables still take precedence. Keys outside
    *allowed_keys* are rejected as a bad parameter.
    """

    def load(ctx: click.Context, _param: click.Parameter, value: Path | None) -> None:
        if value is None:
            return
        try:
            ctx.default_map = load_config(value, allowed_keys)
        except ConfigError as exc:
            raise click.BadParameter(str(exc)) from exc

    return click.option(
        "--config",
        "config",
        type=click.Path(dir_okay=False, path_type=Path),
        envvar=envvar,
        is_eager=True,
        expose_value=False,
        callback=load,
        help=help,
    )


def version_option(text: Callable[[], str]) -> Callable[[F], F]:
    """Return the eager ``--version`` flag printing ``text()`` and exiting 0."""

    def show(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
        if not value or ctx.resilient_parsing:
            return
        click.echo(text())
        ctx.exit(0)

    return click.option(
        "--version",
        is_flag=True,
        expose_value=False,
        is_eager=True,
        callback=show,
        help="Show the version and exit.",
    )
