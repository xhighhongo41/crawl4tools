from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from crawl4tools.i18n import ENGLISH, LOCALE_ENV_VARS, Translator, get_translator
from crawl4tools.server import mcp_main
from crawl4tools.server.config import CONFIG_KEYS
from crawl4tools.server.settings import ServerSettings

# --- helpers ------------------------------------------------------------------


class _FakeServer:
    """Records the keyword arguments passed to run(), optionally raising *run_effect*."""

    def __init__(self, run_effect: BaseException | None = None) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self._run_effect = run_effect

    def run(self, **kwargs: Any) -> None:
        self.run_calls.append(kwargs)
        if self._run_effect is not None:
            raise self._run_effect


def install(
    monkeypatch: pytest.MonkeyPatch, *, run_effect: BaseException | None = None
) -> tuple[_FakeServer, dict[str, ServerSettings]]:
    """Replace create_server() with a fake that records the settings it received."""
    fake = _FakeServer(run_effect)
    captured: dict[str, ServerSettings] = {}

    def create_server(settings: ServerSettings) -> _FakeServer:
        captured["settings"] = settings
        return fake

    monkeypatch.setattr(mcp_main, "create_server", create_server)
    return fake, captured


def invoke(args: list[str], **kwargs: Any) -> Any:
    return CliRunner().invoke(mcp_main.main, args, **kwargs)


# --- --version -----------------------------------------------------------------


def test_version_shows_versions_and_attribution() -> None:
    result = CliRunner().invoke(mcp_main.main, ["--version"])
    assert result.exit_code == 0
    first_line = result.output.splitlines()[0]
    assert first_line.startswith("crawl4mcp 1.0.0b3 (crawl4ai 0.9.")
    assert ", mcp 2." in first_line
    assert "UncleCode" in result.output


def test_version_ignores_broken_config_env(tmp_path: Path) -> None:
    broken = tmp_path / "does-not-exist.yaml"
    result = CliRunner().invoke(mcp_main.main, ["--version"], env={"CRAWL4MCP_CONFIG": str(broken)})
    assert result.exit_code == 0


# --- defaults --------------------------------------------------------------


def test_defaults_are_stdio_and_settings_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    fake, captured = install(monkeypatch)
    result = invoke([])
    assert result.exit_code == 0, result.output
    assert captured["settings"] == ServerSettings(download_root=tmp_path.resolve())
    assert fake.run_calls == [{"transport": "stdio"}]


# --- http options ------------------------------------------------------------


def test_http_options_passed_through_and_serving_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    fake, _captured = install(monkeypatch)
    result = invoke(
        ["--transport", "http", "--host", "127.0.0.1", "--port", "9001", "--path", "/mcp2"]
    )
    assert result.exit_code == 0, result.output
    assert fake.run_calls == [
        {
            "transport": "streamable-http",
            "host": "127.0.0.1",
            "port": 9001,
            "streamable_http_path": "/mcp2",
        }
    ]
    assert "crawl4mcp: serving MCP on http://127.0.0.1:9001/mcp2" in result.stderr


def test_path_must_start_with_slash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--path", "mcp"])
    assert result.exit_code == 2


# --- environment variables ---------------------------------------------------


def test_env_vars_set_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    fake, captured = install(monkeypatch)
    result = invoke(
        [],
        env={
            "CRAWL4MCP_MAX_URLS": "50",
            "CRAWL4MCP_CONCURRENCY": "5",
            "CRAWL4MCP_TRANSPORT": "HTTP",
        },
    )
    assert result.exit_code == 0, result.output
    settings = captured["settings"]
    assert settings.max_urls == 50
    assert settings.concurrency == 5
    assert fake.run_calls[0]["transport"] == "streamable-http"


# --- config file --------------------------------------------------------------


def test_config_file_yaml_sets_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    fake, captured = install(monkeypatch)
    download_dir = tmp_path / "downloads"
    config = tmp_path / "config.yaml"
    config.write_text(
        "max_urls: 42\n"
        "concurrency: 4\n"
        "transport: http\n"
        "port: 9100\n"
        "proxy: proxy.example:8080\n"
        "fallback: false\n"
        f"download_dir: {download_dir}\n",
        encoding="utf-8",
    )
    result = invoke(["--config", str(config)])
    assert result.exit_code == 0, result.output
    settings = captured["settings"]
    assert settings.max_urls == 42
    assert settings.concurrency == 4
    assert settings.fallback is False
    assert settings.proxy == "http://proxy.example:8080"
    assert settings.download_root == download_dir.resolve()
    assert fake.run_calls[0]["transport"] == "streamable-http"
    assert fake.run_calls[0]["port"] == 9100


def test_config_file_json_sets_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    fake, captured = install(monkeypatch)
    download_dir = tmp_path / "downloads"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "max_urls": 17,
                "concurrency": 6,
                "transport": "http",
                "port": 9200,
                "proxy": "proxy.example:9090",
                "fallback": False,
                "download_dir": str(download_dir),
            }
        ),
        encoding="utf-8",
    )
    result = invoke(["--config", str(config)])
    assert result.exit_code == 0, result.output
    settings = captured["settings"]
    assert settings.max_urls == 17
    assert settings.concurrency == 6
    assert settings.fallback is False
    assert settings.proxy == "http://proxy.example:9090"
    assert settings.download_root == download_dir.resolve()
    assert fake.run_calls[0]["port"] == 9200


def test_config_via_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    fake, captured = install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text("max_urls: 33\n", encoding="utf-8")
    result = invoke([], env={"CRAWL4MCP_CONFIG": str(config)})
    assert result.exit_code == 0, result.output
    assert captured["settings"].max_urls == 33


def test_precedence_cli_over_env_over_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    fake, captured = install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text("concurrency: 2\nmax_urls: 10\n", encoding="utf-8")
    result = invoke(
        ["--config", str(config), "--concurrency", "9"],
        env={"CRAWL4MCP_MAX_URLS": "77"},
    )
    assert result.exit_code == 0, result.output
    settings = captured["settings"]
    assert settings.concurrency == 9
    assert settings.max_urls == 77


@pytest.mark.parametrize(
    ("filename", "content", "written"),
    [
        ("unknown.yaml", "bogus: 1\n", True),
        ("broken.yaml", "transport: [unterminated\n", True),
        ("list.yaml", "- a\n- b\n", True),
        ("does-not-exist.yaml", "", False),
    ],
    ids=["unknown-key", "syntax-error", "non-mapping", "missing-file"],
)
def test_config_errors_exit_2_and_mention_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    filename: str,
    content: str,
    written: bool,
) -> None:
    install(monkeypatch)
    config = tmp_path / filename
    if written:
        config.write_text(content, encoding="utf-8")
    result = invoke(["--config", str(config)])
    assert result.exit_code == 2
    assert str(config) in result.output


def test_config_out_of_range_value_exit_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text("concurrency: 0\n", encoding="utf-8")
    result = invoke(["--config", str(config)])
    assert result.exit_code == 2


def test_config_keys_match_main_params() -> None:
    assert CONFIG_KEYS == {p.name for p in mcp_main.main.params} - {"config", "version"}


# --- proxy validation ---------------------------------------------------------


def test_invalid_proxy_exit_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch)
    result = invoke(["--proxy", "not a proxy"])
    assert result.exit_code == 2


# --- non-loopback host warning ------------------------------------------------


def test_non_loopback_host_warns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--transport", "http", "--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert (
        "warning: listening on 0.0.0.0:8765 without authentication; "
        "anyone who can reach it can use this server" in result.stderr
    )


def test_loopback_host_does_not_warn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--transport", "http", "--host", "127.0.0.1"])
    assert result.exit_code == 0, result.output
    assert "warning:" not in result.stderr


def test_stdio_never_warns_about_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert "warning:" not in result.stderr


# --- run() failures ------------------------------------------------------------


def test_run_oserror_exits_1_with_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch, run_effect=OSError(98, "Address already in use"))
    result = invoke(["--transport", "http"])
    assert result.exit_code == 1
    assert "error: cannot listen on 127.0.0.1:8765: Address already in use" in result.stderr


def test_run_keyboard_interrupt_exits_0_silently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch, run_effect=KeyboardInterrupt())
    result = invoke([])
    assert result.exit_code == 0
    assert result.stderr == ""


# --- message language -------------------------------------------------------------

JA = get_translator("ja")


def squash(text: str) -> str:
    """Remove every whitespace character, so help text wrapping does not matter."""
    return "".join(text.split())


def clear_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every locale variable, including the LANGUAGE=en set by conftest."""
    for name in LOCALE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("args", "env", "config", "expected"),
    [
        ([], {}, None, "en"),
        (["--lang", "ja"], {}, None, "ja"),
        ([], {"CRAWL4MCP_LANG": "ja"}, None, "ja"),
        ([], {}, "lang: ja\n", "ja"),
        (["--lang", "en"], {"CRAWL4MCP_LANG": "ja"}, None, "en"),
        ([], {"CRAWL4MCP_LANG": "en"}, "lang: ja\n", "en"),
        (["--lang", "ja"], {"CRAWL4MCP_LANG": "en"}, "lang: en\n", "ja"),
    ],
    ids=[
        "nothing-set",
        "lang-option",
        "variable",
        "config",
        "option-beats-variable",
        "variable-beats-config",
        "option-beats-all",
    ],
)
def test_language_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    args: list[str],
    env: dict[str, str],
    config: str | None,
    expected: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    _fake, captured = install(monkeypatch)
    if config is not None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config, encoding="utf-8")
        args = ["--config", str(config_file), *args]
    result = invoke(args, env=env)
    assert result.exit_code == 0, result.output
    assert captured["settings"].lang == expected


def test_locale_does_not_choose_the_language(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    _fake, captured = install(monkeypatch)
    result = invoke(["--transport", "http", "--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert captured["settings"].lang == "en"
    assert "warning: listening on 0.0.0.0:8765 without authentication" in result.stderr


@pytest.mark.parametrize(
    ("args", "env", "config"),
    [
        (["--lang", "de"], {}, None),
        ([], {"CRAWL4MCP_LANG": "de"}, None),
        ([], {}, "lang: de\n"),
    ],
    ids=["lang-option", "variable", "config"],
)
def test_unsupported_language_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    args: list[str],
    env: dict[str, str],
    config: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    _fake, captured = install(monkeypatch)
    if config is not None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config, encoding="utf-8")
        args = ["--config", str(config_file), *args]
    result = invoke(args, env=env)
    assert result.exit_code == 2
    assert "Invalid value for '--lang'" in result.stderr
    assert captured == {}


def test_japanese_warning_and_serving_lines_keep_the_english_prefixes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--lang", "ja", "--transport", "http", "--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        "warning: 0.0.0.0:8765 で認証なしで待ち受けています。"
        "接続できる人は誰でもこのサーバーを使えます",
        "crawl4mcp: http://0.0.0.0:8765/mcp で MCP を提供しています",
    ]


def test_japanese_listen_error_keeps_the_english_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch, run_effect=OSError(98, "Address already in use"))
    result = invoke(["--lang", "ja", "--transport", "http"])
    assert result.exit_code == 1
    assert "error: 127.0.0.1:8765 で待ち受けできません: Address already in use" in result.stderr


def test_english_help_is_unchanged_and_lists_lang() -> None:
    result = invoke(["--help"])
    assert result.exit_code == 0
    output = squash(result.output)
    for text in [
        "Run the crawl4tools MCP server.",
        "Transport to serve the MCP protocol over.",
        "Host to listen on (http transport only).",
        "Port to listen on (http transport only).",
        "HTTP path to serve the MCP endpoint at (http transport only).",
        "Default per-URL timeout in seconds, used when a tool call omits timeout_s.",
        "Maximum number of URLs fetched at once across every tool call.",
        "Maximum number of URLs accepted in a single tool call.",
        "Root directory the download tool saves files into.",
        "YAML or JSON config file; command-line options and CRAWL4MCP_* environment "
        "variables take precedence.",
        "Language of messages: en or ja.",
        "Show the version and exit.",
    ]:
        assert squash(text) in output, text
    assert "--lang [en|ja]" in result.output


def test_japanese_help_shows_translated_texts() -> None:
    result = CliRunner().invoke(mcp_main.build_command(JA), ["--help"])
    assert result.exit_code == 0
    output = squash(result.output)
    for text in [
        "crawl4tools の MCP サーバーを起動します。",
        "MCP プロトコルを提供するトランスポートです。",
        "待ち受けるホストです(http トランスポートのみ)。",
        "待ち受けるポートです(http トランスポートのみ)。",
        "MCP エンドポイントを提供する HTTP パスです(http トランスポートのみ)。",
        "URL ごとのタイムアウト(秒)の既定値です。ツール呼び出しで timeout_s を省略したときに"
        "使います。",
        "すべてのツール呼び出しを通じて同時に取得する URL の最大数です。",
        "1 回のツール呼び出しで受け付ける URL の最大数です。",
        "download ツールがファイルを保存するルートディレクトリです。",
        "YAML または JSON の設定ファイルです。コマンドラインのオプションと CRAWL4MCP_* "
        "環境変数が優先されます。",
        "プロキシ URL(http、https、socks5 のいずれか)です。例: socks5://host:1080。",
        "詳細なログを出力します。",
        "メッセージの言語(en または ja)です。",
        "バージョンを表示して終了します。",
    ]:
        assert squash(text) in output, text
    assert "--lang [en|ja]" in result.output
    # click's own texts stay in English.
    assert "Usage:" in result.output
    assert "Show this message and exit." in result.output
    assert "Host to listen on" not in result.output


def test_japanese_path_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = CliRunner().invoke(mcp_main.build_command(JA), ["--path", "mcp"])
    assert result.exit_code == 2
    assert "Invalid value for '--path': '/' で始めてください" in result.stderr


# --- entry (the console script) ------------------------------------------------------


class CommandRecorder:
    """Stands in for build_command(): records the translator and the main() call."""

    def __init__(self) -> None:
        self.translators: list[Translator] = []
        self.main_calls: list[dict[str, Any]] = []

    def __call__(self, t: Translator) -> CommandRecorder:
        self.translators.append(t)
        return self

    def main(self, **kwargs: Any) -> None:
        self.main_calls.append(kwargs)


def run_entry(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> CommandRecorder:
    """Run entry() with *argv* and a recording build_command()."""
    monkeypatch.setattr(sys, "argv", ["crawl4mcp", *argv])
    recorder = CommandRecorder()
    monkeypatch.setattr(mcp_main, "build_command", recorder)
    mcp_main.entry()
    return recorder


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        (["--lang", "ja"], {}, JA),
        (["--lang=ja"], {}, JA),
        ([], {"CRAWL4MCP_LANG": "ja"}, JA),
        ([], {"LANG": "ja_JP.UTF-8"}, ENGLISH),
        ([], {}, ENGLISH),
        (["--lang", "en"], {"CRAWL4MCP_LANG": "ja"}, ENGLISH),
    ],
    ids=[
        "lang-option",
        "lang-option-equals",
        "variable",
        "locale-ignored",
        "nothing-set",
        "option-beats-variable",
    ],
)
def test_entry_builds_the_command_in_the_chosen_language(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], env: dict[str, str], expected: Translator
) -> None:
    clear_locale(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    recorder = run_entry(monkeypatch, argv)
    assert len(recorder.translators) == 1
    assert recorder.translators[0] is expected
    assert recorder.main_calls == [{"prog_name": "crawl4mcp"}]


def test_entry_ignores_the_config_file_language(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("lang: ja\n", encoding="utf-8")
    monkeypatch.setenv("CRAWL4MCP_CONFIG", str(config))
    recorder = run_entry(monkeypatch, ["--config", str(config), "--help"])
    assert recorder.translators == [ENGLISH]
    assert recorder.main_calls == [{"prog_name": "crawl4mcp"}]
