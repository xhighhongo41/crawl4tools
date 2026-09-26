from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from crawl4tools.i18n import ENGLISH, Translator, get_translator
from crawl4tools.server.cli_options import (
    LOOPBACK_HOSTS,
    config_option,
    fetch_option_decorators,
    lang_option,
    package_version,
    path_validator,
    proxy_validator,
    setup_logging,
    version_option,
)
from crawl4tools.server.settings import LogLevel

JA = get_translator("ja")

HELPS = {
    "timeout_help": "TIMEOUT-HELP",
    "concurrency_help": "CONCURRENCY-HELP",
    "max_urls_help": "MAX-URLS-HELP",
    "download_dir_help": "DOWNLOAD-DIR-HELP",
}


def make_command(captured: dict[str, Any], t: Translator = ENGLISH) -> click.Command:
    """Return a command using every shared option, recording its arguments."""

    @click.command(context_settings={"auto_envvar_prefix": "TESTCMD"})
    @click.option("--name", "name", default="x")
    @fetch_option_decorators(t, **HELPS)
    @config_option(
        t,
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
        "log_level",
        "keep_downloads",
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
    assert captured["log_level"] == "info"
    assert captured["keep_downloads"] is False
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
    @version_option(ENGLISH, lambda: "tool 1.2.3")
    def command() -> None:
        raise AssertionError("must not run")

    result = CliRunner().invoke(command, ["--version"])
    assert result.exit_code == 0
    assert result.output == "tool 1.2.3\n"


def test_validators() -> None:
    ctx = click.Context(click.Command("c"))
    param = click.Option(["--x"])
    assert path_validator(ENGLISH)(ctx, param, "/mcp") == "/mcp"
    with pytest.raises(click.BadParameter):
        path_validator(ENGLISH)(ctx, param, "mcp")
    assert proxy_validator(ENGLISH)(ctx, param, None) is None
    with pytest.raises(click.BadParameter):
        proxy_validator(ENGLISH)(ctx, param, "ftp://host:21")


def test_misc_helpers() -> None:
    assert LOOPBACK_HOSTS == frozenset({"127.0.0.1", "localhost", "::1"})
    assert package_version("no-such-package-crawl4tools-test") == "unknown"
    assert package_version("click") != "unknown"


@pytest.fixture
def basic_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Record the logging.basicConfig() calls instead of configuring the root logger.

    pytest keeps its own handlers on the root logger, which would turn a real
    basicConfig() into a no-op. The HTTP client loggers start at NOTSET and
    get their levels back afterwards.
    """
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: calls.append(kwargs))
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    saved = [logger.level for logger in loggers]
    for logger in loggers:
        logger.setLevel(logging.NOTSET)
    yield calls
    for logger, level in zip(loggers, saved, strict=True):
        logger.setLevel(level)


@pytest.mark.parametrize(
    ("level", "root_level", "noisy_level"),
    [
        ("debug", logging.DEBUG, logging.NOTSET),
        ("info", logging.INFO, logging.WARNING),
        ("error", logging.WARNING, logging.WARNING),
    ],
    ids=["debug", "info", "error"],
)
def test_setup_logging_levels(
    basic_config: list[dict[str, Any]], level: LogLevel, root_level: int, noisy_level: int
) -> None:
    setup_logging(level)
    assert basic_config == [
        {
            "stream": sys.stderr,
            "level": root_level,
            "format": "%(levelname)s %(name)s: %(message)s",
        }
    ]
    assert logging.getLogger("httpx").level == noisy_level
    assert logging.getLogger("httpcore").level == noisy_level


@pytest.mark.parametrize(
    ("args", "env", "expected"),
    [
        ([], {}, "info"),
        (["--log-level", "debug"], {}, "debug"),
        (["--log-level", "error"], {}, "error"),
        ([], {"TESTCMD_LOG_LEVEL": "debug"}, "debug"),
    ],
    ids=["default", "debug", "error", "envvar"],
)
def test_log_level_option_values(args: list[str], env: dict[str, str], expected: str) -> None:
    captured: dict[str, Any] = {}
    result = CliRunner().invoke(make_command(captured), args, env=env)
    assert result.exit_code == 0, result.output
    assert captured["log_level"] == expected


def test_log_level_option_rejects_unknown_level() -> None:
    captured: dict[str, Any] = {}
    result = CliRunner().invoke(make_command(captured), ["--log-level", "warning"])
    assert result.exit_code == 2
    assert "Invalid value for '--log-level'" in result.output
    assert captured == {}


def test_keep_downloads_option() -> None:
    captured: dict[str, Any] = {}
    result = CliRunner().invoke(make_command(captured), ["--keep-downloads"])
    assert result.exit_code == 0, result.output
    assert captured["keep_downloads"] is True


def test_log_level_help_lists_levels_and_default() -> None:
    result = CliRunner().invoke(make_command({}), ["--help"])
    assert result.exit_code == 0
    assert "--log-level [debug|info|error]" in result.output
    assert "[default: info]" in result.output
    assert result.output.index("--download-dir") < result.output.index("--log-level")
    assert result.output.index("--log-level") < result.output.index("--keep-downloads")
    assert "--verbose" not in result.output


# --- translated texts --------------------------------------------------------------


def _helps(command: click.Command) -> dict[str | None, str | None]:
    return {param.name: param.help for param in command.params if isinstance(param, click.Option)}


def test_shared_option_help_in_english() -> None:
    helps = _helps(make_command({}))
    assert helps["proxy"] == "Proxy URL (http, https, or socks5); e.g. socks5://host:1080."
    assert helps["fallback"] == (
        "Retry without the proxy when the proxy itself appears to be at fault."
    )
    assert helps["log_level"] == (
        "Log level: debug (everything), info (fetches, warnings and errors), "
        "or error (warnings and errors only)."
    )
    assert helps["keep_downloads"] == (
        "Keep the files saved by the download tool on the server after a client fetched "
        "them over HTTP (by default the server deletes its copy then)."
    )


def test_shared_option_help_in_japanese() -> None:
    helps = _helps(make_command({}, JA))
    assert helps["proxy"] == (
        "プロキシ URL(http、https、socks5 のいずれか)です。例: socks5://host:1080。"
    )
    assert helps["fallback"] == (
        "プロキシ自体に問題があると見られる場合は、プロキシなしで再試行します。"
    )
    assert helps["log_level"] == (
        "ログレベルです。debug(すべて)、info(取得、警告、エラー)、"
        "error(警告とエラーのみ)のいずれかです。"
    )
    assert helps["keep_downloads"] == (
        "download ツールが保存したファイルを、クライアントが HTTP で取得した後もサーバーに"
        "残します(既定ではその時点でサーバー側のコピーを削除します)。"
    )
    # The help texts given by the caller are used as they are.
    assert helps["timeout"] == HELPS["timeout_help"]
    assert helps["config"] == "CONFIG-HELP"


def test_version_option_help_follows_the_translator() -> None:
    for t, expected in [
        (ENGLISH, "Show the version and exit."),
        (JA, "バージョンを表示して終了します。"),
    ]:

        @click.command()
        @version_option(t, lambda: "tool 1.2.3")
        def command() -> None:
            pass

        assert _helps(command)["version"] == expected


def test_config_option_error_in_japanese(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("max_urls: 3\n", encoding="utf-8")
    result = CliRunner().invoke(make_command({}, JA), ["--config", str(bad)])
    assert result.exit_code == 2
    assert (
        f"{bad}: 不明な設定キーです: max_urls(使えるキー: concurrency, name, timeout)"
        in result.output
    )


def test_proxy_validator_error_in_english_and_japanese() -> None:
    ctx = click.Context(click.Command("c"))
    param = click.Option(["--x"])
    with pytest.raises(click.BadParameter) as info:
        proxy_validator(ENGLISH)(ctx, param, "ftp://host:21")
    assert info.value.message == "unsupported proxy scheme: ftp://host:21"
    with pytest.raises(click.BadParameter) as info:
        proxy_validator(JA)(ctx, param, "ftp://host:21")
    assert info.value.message == "対応していないプロキシのスキームです: ftp://host:21"


def test_proxy_validator_normalizes() -> None:
    ctx = click.Context(click.Command("c"))
    param = click.Option(["--x"])
    assert proxy_validator(JA)(ctx, param, "proxy.example:8080") == "http://proxy.example:8080"


def test_path_validator_error_in_english_and_japanese() -> None:
    ctx = click.Context(click.Command("c"))
    param = click.Option(["--x"])
    with pytest.raises(click.BadParameter) as info:
        path_validator(ENGLISH)(ctx, param, "mcp")
    assert info.value.message == "must start with '/'"
    with pytest.raises(click.BadParameter) as info:
        path_validator(JA)(ctx, param, "mcp")
    assert info.value.message == "'/' で始めてください"


# --- lang_option -------------------------------------------------------------------


def make_lang_command(captured: dict[str, Any], t: Translator = ENGLISH) -> click.Command:
    """Return a command with only ``--lang``, recording the chosen language."""

    @click.command()
    @lang_option(t, envvar="TESTCMD_LANG")
    def command(lang: str) -> None:
        captured["lang"] = lang

    return command


@pytest.mark.parametrize(
    ("args", "env", "expected"),
    [
        ([], {}, "en"),
        (["--lang", "ja"], {}, "ja"),
        (["--lang", "en"], {}, "en"),
        ([], {"TESTCMD_LANG": "ja"}, "ja"),
        (["--lang", "en"], {"TESTCMD_LANG": "ja"}, "en"),
    ],
    ids=["default", "option-ja", "option-en", "envvar", "option-beats-envvar"],
)
def test_lang_option_values(args: list[str], env: dict[str, str], expected: str) -> None:
    captured: dict[str, Any] = {}
    result = CliRunner().invoke(make_lang_command(captured), args, env=env)
    assert result.exit_code == 0, result.output
    assert captured == {"lang": expected}


@pytest.mark.parametrize(
    ("args", "env"),
    [(["--lang", "de"], {}), ([], {"TESTCMD_LANG": "de"})],
    ids=["option", "envvar"],
)
def test_lang_option_rejects_unsupported_language(args: list[str], env: dict[str, str]) -> None:
    captured: dict[str, Any] = {}
    result = CliRunner().invoke(make_lang_command(captured), args, env=env)
    assert result.exit_code == 2
    assert captured == {}


def test_lang_option_help() -> None:
    result = CliRunner().invoke(make_lang_command({}), ["--help"])
    assert result.exit_code == 0
    assert "Language of messages: en or ja." in result.output
    assert "[default: en]" in result.output
    assert _helps(make_lang_command({}))["lang"] == "Language of messages: en or ja."


def test_lang_option_help_in_japanese() -> None:
    command = make_lang_command({}, JA)
    assert _helps(command)["lang"] == "メッセージの言語(en または ja)です。"
    result = CliRunner().invoke(command, ["--help"])
    assert result.exit_code == 0
    assert "メッセージの言語(en または ja)です。" in result.output
