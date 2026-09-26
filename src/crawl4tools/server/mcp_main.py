"""Entry point of the crawl4mcp command.

:func:`build_command` builds the click command for one translator, which
fixes the language of the help texts and of the argument errors. The
language of everything the server says while running (the tool
descriptions, the notes and errors sent to MCP clients, and the lines
printed on stderr) is chosen by ``--lang``, then ``CRAWL4MCP_LANG``, then
the ``lang`` key of the config file, else English; the locale is not
consulted. The console script :func:`entry` makes the same choice from the
command line and ``CRAWL4MCP_LANG`` before building the command, so
``--help`` is in that language too (the config file is not read for it).
The ``warning:`` / ``crawl4mcp:`` / ``error:`` prefixes and the
``--version`` text never change.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import click
from mcp.server.mcpserver import MCPServer

from crawl4tools import __version__
from crawl4tools.cli.main import CRAWL4AI_ATTRIBUTION
from crawl4tools.i18n import (
    ENGLISH,
    Translator,
    get_translator,
    language_from_argv,
    resolve_language,
)
from crawl4tools.server.cli_options import (
    LOOPBACK_HOSTS,
    config_option,
    fetch_option_decorators,
    lang_option,
    package_version,
    path_validator,
    setup_logging,
    version_option,
)
from crawl4tools.server.config import CONFIG_KEYS
from crawl4tools.server.mcp_server import build_server
from crawl4tools.server.settings import LogLevel, ServerSettings

# The variable naming the message language. The --lang option reads it as
# well, and click rejects unsupported values there.
_LANG_ENVVAR = "CRAWL4MCP_LANG"


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


def build_command(t: Translator) -> click.Command:
    """Return the crawl4mcp click command with its texts translated by *t*.

    *t* translates what is fixed when the command is built: the command and
    option help and the errors of the ``--path``, ``--proxy`` and
    ``--config`` arguments. The server and the lines printed while running
    follow the language resolved from ``--lang``, ``CRAWL4MCP_LANG`` and
    the config file instead.
    """

    # The command is still named "main" (click derives the name from the
    # function), as it was before the command was built by this factory.
    @click.command(
        help=t.gettext("Run the crawl4tools MCP server."),
        context_settings={"auto_envvar_prefix": "CRAWL4MCP"},
    )
    @click.option(
        "--transport",
        "transport",
        type=click.Choice(["stdio", "http"], case_sensitive=False),
        default="stdio",
        show_default=True,
        help=t.gettext("Transport to serve the MCP protocol over."),
    )
    @click.option(
        "--host",
        "host",
        default="127.0.0.1",
        show_default=True,
        help=t.gettext("Host to listen on (http transport only)."),
    )
    @click.option(
        "--port",
        "port",
        type=click.IntRange(1, 65535),
        default=8765,
        show_default=True,
        help=t.gettext("Port to listen on (http transport only)."),
    )
    @click.option(
        "--path",
        "path",
        default="/mcp",
        show_default=True,
        callback=path_validator(t),
        help=t.gettext("HTTP path to serve the MCP endpoint at (http transport only)."),
    )
    @fetch_option_decorators(
        t,
        timeout_help=t.gettext(
            "Default per-URL timeout in seconds, used when a tool call omits timeout_s."
        ),
        concurrency_help=t.gettext(
            "Maximum number of URLs fetched at once across every tool call."
        ),
        max_urls_help=t.gettext("Maximum number of URLs accepted in a single tool call."),
        download_dir_help=t.gettext("Root directory the download tool saves files into."),
    )
    @lang_option(t, _LANG_ENVVAR)
    @config_option(
        t,
        envvar="CRAWL4MCP_CONFIG",
        allowed_keys=CONFIG_KEYS,
        help=t.gettext(
            "YAML or JSON config file; command-line options and CRAWL4MCP_* environment "
            "variables take precedence."
        ),
    )
    @version_option(t, version_text)
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
        log_level: LogLevel,
        keep_downloads: bool,
        lang: str,
    ) -> None:
        # No docstring: the help comes from help= above so that it is translated.
        setup_logging(log_level)
        # The version comes first on stderr over both transports, so every log
        # tells which build wrote it.
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
        server = create_server(settings)

        if transport == "http" and host not in LOOPBACK_HOSTS:
            message = runtime.gettext(
                "listening on {host}:{port} without authentication; "
                "anyone who can reach it can use this server"
            ).format(host=host, port=port)
            click.echo(f"warning: {message}", err=True)

        try:
            if transport == "stdio":
                server.run(transport="stdio")
            else:
                url = f"http://{host}:{port}{path}"
                message = runtime.gettext("serving MCP on {url}").format(url=url)
                click.echo(f"crawl4mcp: {message}", err=True)
                server.run(
                    transport="streamable-http",
                    host=host,
                    port=port,
                    streamable_http_path=path,
                )
        except OSError as exc:
            reason = exc.strerror or str(exc)
            message = runtime.gettext("cannot listen on {address}: {reason}").format(
                address=f"{host}:{port}", reason=reason
            )
            click.echo(f"error: {message}", err=True)
            sys.exit(1)
        except KeyboardInterrupt:
            sys.exit(0)

    return main


main = build_command(ENGLISH)
"""The command with English help, for importing and testing.

Its server and run-time messages still follow ``--lang``,
``CRAWL4MCP_LANG`` and the config file; only the help and the argument
errors are always English.
"""


def entry() -> None:
    """Run crawl4mcp as the console script.

    The language is chosen from ``--lang`` in ``sys.argv``, then
    ``CRAWL4MCP_LANG``, else English, before the command is built, so the
    help and the argument errors are in that language as well. The locale
    and the config file are not consulted for this.
    """
    lang = resolve_language(
        language_from_argv(sys.argv[1:]),
        env=os.environ,
        envvar=_LANG_ENVVAR,
        follow_locale=False,
    )
    build_command(get_translator(lang)).main(prog_name="crawl4mcp")


if __name__ == "__main__":
    entry()
