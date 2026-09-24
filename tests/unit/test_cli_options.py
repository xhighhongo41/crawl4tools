from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from crawl4tools.server.cli_options import (
    LOOPBACK_HOSTS,
    config_option,
    fetch_option_decorators,
    package_version,
    setup_logging,
    validate_path,
    validate_proxy,
    version_option,
)

HELPS = {
    "timeout_help": "TIMEOUT-HELP",
    "concurrency_help": "CONCURRENCY-HELP",
    "max_urls_help": "MAX-URLS-HELP",
    "download_dir_help": "DOWNLOAD-DIR-HELP",
}


def make_command(captured: dict[str, Any]) -> click.Command:
    """Return a command using every shared option, recording its arguments."""

    @click.command(context_settings={"auto_envvar_prefix": "TESTCMD"})
    @click.option("--name", "name", default="x")
    @fetch_option_decorators(**HELPS)
    @config_option(
        envvar="TESTCMD_CONFIG",
        allowed_keys=frozenset({"name", "timeout", "concurrency"}),
        help="CONFIG-HELP",
    )
    def command(**kwargs: Any) -> None:
        captured.update(kwargs)

    return command


def test_fetch_options_order_and_help() -> None:
    command = make_command({})
    names = [param.name for param in command.params]
    assert names == [
        "name",
        "proxy",
        "fallback",
        "timeout",
        "concurrency",
        "max_urls",
        "download_dir",
        "verbose",
        "config",
    ]
    result = CliRunner().invoke(command, ["--help"])
    assert result.exit_code == 0
    for text in [*HELPS.values(), "CONFIG-HELP"]:
        assert text in result.output


def test_fetch_options_defaults() -> None:
    captured: dict[str, Any] = {}
    result = CliRunner().invoke(make_command(captured), [])
    assert result.exit_code == 0, result.output
    assert captured["proxy"] is None
    assert captured["fallback"] is True
    assert captured["download_dir"] == Path(".")
    assert captured["verbose"] is False
    assert "config" not in captured


def test_config_option_uses_allowed_keys(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    good = tmp_path / "good.yaml"
    good.write_text("name: from-file\nconcurrency: 7\n", encoding="utf-8")
    result = CliRunner().invoke(make_command(captured), ["--config", str(good)])
    assert result.exit_code == 0, result.output
    assert captured["name"] == "from-file"
    assert captured["concurrency"] == 7

    bad = tmp_path / "bad.yaml"
    bad.write_text("max_urls: 3\n", encoding="utf-8")
    result = CliRunner().invoke(make_command(captured), ["--config", str(bad)])
    assert result.exit_code == 2
    assert "unknown config key(s): max_urls" in result.output
    assert "allowed keys: concurrency, name, timeout" in result.output


def test_config_option_env_var(tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    config_file = tmp_path / "c.yaml"
    config_file.write_text("timeout: 12.5\n", encoding="utf-8")
    env = {"TESTCMD_CONFIG": str(config_file)}
    result = CliRunner().invoke(make_command(captured), [], env=env)
    assert result.exit_code == 0, result.output
    assert captured["timeout"] == 12.5


def test_version_option() -> None:
    @click.command()
    @version_option(lambda: "tool 1.2.3")
    def command() -> None:
        raise AssertionError("must not run")

    result = CliRunner().invoke(command, ["--version"])
    assert result.exit_code == 0
    assert result.output == "tool 1.2.3\n"


def test_validators() -> None:
    ctx = click.Context(click.Command("c"))
    param = click.Option(["--x"])
    assert validate_path(ctx, param, "/mcp") == "/mcp"
    with pytest.raises(click.BadParameter):
        validate_path(ctx, param, "mcp")
    assert validate_proxy(ctx, param, None) is None
    with pytest.raises(click.BadParameter):
        validate_proxy(ctx, param, "ftp://host:21")


def test_misc_helpers() -> None:
    assert LOOPBACK_HOSTS == frozenset({"127.0.0.1", "localhost", "::1"})
    assert package_version("no-such-package-crawl4tools-test") == "unknown"
    assert package_version("click") != "unknown"


def test_setup_logging_silences_noisy_loggers() -> None:
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    setup_logging(False)
    assert logging.getLogger("httpx").level == logging.WARNING
