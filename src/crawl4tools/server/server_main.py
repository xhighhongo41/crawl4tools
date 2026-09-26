"""Entry point of the crawl4server command.

crawl4server runs one process that listens on two ports: one serves the
Open WebUI external web loader contract, the other serves MCP over
Streamable HTTP. Both share one browser and one concurrency limit.

:func:`build_command` builds the click command for one translator, which
fixes the language of the help texts and of the argument errors. The
language of everything the server says while running (the warnings, the
startup lines, and the lines printed on stderr) is chosen by ``--lang``,
then ``CRAWL4SERVER_LANG``, then the ``lang`` key of the config file, else
English; the locale is not consulted. The console script :func:`entry`
makes the same choice from the command line and ``CRAWL4SERVER_LANG``
before building the command, so ``--help`` is in that language too (the
config file is not read for it). The ``warning:`` / ``crawl4server:`` /
``error:`` prefixes and the ``--version`` text never change.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import uvicorn

from crawl4tools import __version__
from crawl4tools.cli.main import CRAWL4AI_ATTRIBUTION
from crawl4tools.i18n import (
    ENGLISH,
    Translator,
    get_translator,
    language_from_argv,
    render_exception,
    resolve_language,
)
from crawl4tools.server import host as host_module
from crawl4tools.server.cli_options import (
    LOOPBACK_HOSTS,
    StrCallback,
    config_option,
    fetch_option_decorators,
    lang_option,
    package_version,
    path_validator,
    setup_logging,
    version_option,
)
from crawl4tools.server.host import McpSettings, StartedCallback
from crawl4tools.server.loader import LoaderSettings
from crawl4tools.server.settings import LogLevel, ServerSettings

# The variable naming the message language. The --lang option reads it as
# well, and click rejects unsupported values there.
_LANG_ENVVAR = "CRAWL4SERVER_LANG"

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
        "log_level",
        "keep_downloads",
        "lang",
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


def loader_path_validator(t: Translator) -> StrCallback:
    """Return the click callback for ``--loader-path``: a path, distinct from ``/health``.

    Runs :func:`~crawl4tools.server.cli_options.path_validator` first (the
    path must start with ``/``), then rejects :data:`_HEALTH_PATH`. Both
    errors are translated by *t*.
    """
    validate_path = path_validator(t)

    def validate(ctx: click.Context, param: click.Parameter, value: str) -> str:
        value = validate_path(ctx, param, value)
        if value == _HEALTH_PATH:
            raise click.BadParameter(
                t.gettext("'{path}' is reserved for the health check").format(path=_HEALTH_PATH)
            )
        return value

    return validate


def run_server(
    settings: ServerSettings,
    loader: LoaderSettings,
    mcp: McpSettings,
    *,
    host: str,
    loader_port: int,
    mcp_port: int,
    log_level: LogLevel,
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
        log_level=log_level,
        on_started=on_started,
    )


def build_command(t: Translator) -> click.Command:
    """Return the crawl4server click command with its texts translated by *t*.

    *t* translates what is fixed when the command is built: the command and
    option help and the errors of the ``--loader-path``, ``--mcp-path``,
    ``--proxy`` and ``--config`` arguments. The lines printed while running
    follow the language resolved from ``--lang``, ``CRAWL4SERVER_LANG`` and
    the config file instead.
    """

    # The command is still named "main" (click derives the name from the
    # function), as it was before the command was built by this factory.
    @click.command(
        help=t.gettext("Serve the Open WebUI web loader and MCP from one process."),
        context_settings={"auto_envvar_prefix": "CRAWL4SERVER"},
    )
    @click.option(
        "--host",
        "host",
        default="127.0.0.1",
        show_default=True,
        help=t.gettext("Host to listen on (both ports)."),
    )
    @click.option(
        "--loader-port",
        "loader_port",
        type=click.IntRange(1, 65535),
        default=8766,
        show_default=True,
        help=t.gettext("Open WebUI web loader port."),
    )
    @click.option(
        "--loader-path",
        "loader_path",
        default="/crawl",
        show_default=True,
        callback=loader_path_validator(t),
        help=t.gettext("HTTP path the Open WebUI web loader listens on."),
    )
    @click.option(
        "--loader-api-key",
        "loader_api_key",
        default=None,
        show_default=False,
        help=t.gettext(
            "Require 'Authorization: Bearer <key>' on the web loader (Open WebUI's "
            "External Web Loader API Key)."
        ),
    )
    @click.option(
        "--loader-fit/--no-loader-fit",
        "loader_fit",
        default=False,
        help=t.gettext("Keep only the main content of each page (crawl4ai content filter)."),
    )
    @click.option(
        "--mcp-port",
        "mcp_port",
        type=click.IntRange(1, 65535),
        default=8765,
        show_default=True,
        help=t.gettext("MCP Streamable HTTP port."),
    )
    @click.option(
        "--mcp-path",
        "mcp_path",
        default="/mcp",
        show_default=True,
        callback=path_validator(t),
        help=t.gettext("HTTP path to serve the MCP endpoint at."),
    )
    @fetch_option_decorators(
        t,
        timeout_help=t.gettext(
            "Default per-URL timeout in seconds, for web loader requests and MCP tool "
            "calls that omit timeout_s."
        ),
        concurrency_help=t.gettext("Maximum number of URLs fetched at once across both ports."),
        max_urls_help=t.gettext(
            "Maximum number of URLs accepted in one web loader request or MCP tool call "
            "(Open WebUI sends up to 20)."
        ),
        download_dir_help=t.gettext("Root directory the MCP download tool saves files into."),
    )
    @lang_option(t, _LANG_ENVVAR)
    @config_option(
        t,
        envvar="CRAWL4SERVER_CONFIG",
        allowed_keys=SERVER_CONFIG_KEYS,
        help=t.gettext(
            "YAML or JSON config file; command-line options and CRAWL4SERVER_* environment "
            "variables take precedence."
        ),
    )
    @version_option(t, version_text)
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
        log_level: LogLevel,
        keep_downloads: bool,
        lang: str,
    ) -> None:
        # No docstring: the help comes from help= above so that it is translated.
        setup_logging(log_level)
        # The version comes first on stderr, so every log tells which build wrote it.
        click.echo(version_text().splitlines()[0], err=True)

        settings = ServerSettings(
            proxy=proxy,
            fallback=fallback,
            timeout_s=timeout,
            concurrency=concurrency,
            max_urls=max_urls,
            download_root=download_dir.resolve(),
            log_level=log_level,
            keep_downloads=keep_downloads,
            lang=lang,
        )
        runtime = settings.translator

        if loader_port == mcp_port:
            raise click.UsageError(runtime.gettext("--loader-port and --mcp-port must differ"))

        loader = LoaderSettings(path=loader_path, api_key=loader_api_key or None, fit=loader_fit)
        mcp = McpSettings(path=mcp_path)

        if host not in LOOPBACK_HOSTS:
            click.echo(
                "crawl4server: warning: "
                + runtime.gettext(
                    "the MCP endpoint has no authentication on a non-loopback host; "
                    "anyone who can reach it can use this server"
                ),
                err=True,
            )
            if loader.api_key is None:
                click.echo(
                    "crawl4server: warning: "
                    + runtime.gettext(
                        "the web loader has no authentication either; set --loader-api-key "
                        "to require one"
                    ),
                    err=True,
                )

        def on_started(server: uvicorn.Server, ports: dict[str, int]) -> None:
            loader_url = f"http://{_address(host, ports['loader'])}{loader_path}"
            click.echo(
                "crawl4server: "
                + runtime.gettext("serving Open WebUI web loader on {url}").format(url=loader_url),
                err=True,
            )
            mcp_url = f"http://{_address(host, ports['mcp'])}{mcp_path}"
            click.echo(
                "crawl4server: " + runtime.gettext("serving MCP on {url}").format(url=mcp_url),
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
                log_level=log_level,
                on_started=on_started,
            )
        except OSError as exc:
            # ListenError (raised when a port cannot be bound) is an OSError
            # subclass whose message already reads "cannot listen on ...".
            click.echo(f"error: {render_exception(exc, runtime)}", err=True)
            sys.exit(1)
        except KeyboardInterrupt:
            sys.exit(0)

    return main


main = build_command(ENGLISH)
"""The command with English help, for importing and testing.

Its run-time messages still follow ``--lang``, ``CRAWL4SERVER_LANG`` and
the config file; only the help and the argument errors are always English.
"""


def entry() -> None:
    """Run crawl4server as the console script.

    The language is chosen from ``--lang`` in ``sys.argv``, then
    ``CRAWL4SERVER_LANG``, else English, before the command is built, so
    the help and the argument errors are in that language as well. The
    locale and the config file are not consulted for this.
    """
    lang = resolve_language(
        language_from_argv(sys.argv[1:]),
        env=os.environ,
        envvar=_LANG_ENVVAR,
        follow_locale=False,
    )
    build_command(get_translator(lang)).main(prog_name="crawl4server")


if __name__ == "__main__":
    entry()
