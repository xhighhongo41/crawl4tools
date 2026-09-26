"""Tests for the crawl4server command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from crawl4tools.i18n import ENGLISH, LOCALE_ENV_VARS, Translator, get_translator
from crawl4tools.server import host as host_module
from crawl4tools.server import server_main
from crawl4tools.server.host import McpSettings
from crawl4tools.server.loader import LoaderSettings
from crawl4tools.server.server_main import main
from crawl4tools.server.settings import ServerSettings

# --- helpers ------------------------------------------------------------------


class _Recorder:
    """Records run_server() calls and fires on_started() like host.serve() would.

    When *run_effect* is an :class:`OSError` (e.g. :class:`~crawl4tools.server.
    host.ListenError`), it is raised before ``on_started`` is called, mirroring
    a bind failure. Any other *run_effect* (e.g. ``KeyboardInterrupt``) is
    raised after ``on_started`` fires, mirroring a signal received once the
    server is already listening.
    """

    def __init__(
        self,
        *,
        run_effect: BaseException | None = None,
        ports: dict[str, int] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._run_effect = run_effect
        self._ports = ports if ports is not None else {"loader": 40001, "mcp": 40002}

    def __call__(
        self,
        settings: ServerSettings,
        loader: LoaderSettings,
        mcp: McpSettings,
        *,
        host: str,
        loader_port: int,
        mcp_port: int,
        log_level: str,
        on_started: Any,
    ) -> None:
        self.calls.append(
            {
                "settings": settings,
                "loader": loader,
                "mcp": mcp,
                "host": host,
                "loader_port": loader_port,
                "mcp_port": mcp_port,
                "log_level": log_level,
            }
        )
        if isinstance(self._run_effect, OSError):
            raise self._run_effect
        if on_started is not None:
            on_started(None, dict(self._ports))
        if self._run_effect is not None:
            raise self._run_effect


def install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_effect: BaseException | None = None,
    ports: dict[str, int] | None = None,
) -> _Recorder:
    """Replace run_server() with a fake that records its call and fires on_started()."""
    recorder = _Recorder(run_effect=run_effect, ports=ports)
    monkeypatch.setattr(server_main, "run_server", recorder)
    return recorder


def invoke(args: list[str], **kwargs: Any) -> Any:
    return CliRunner().invoke(main, args, **kwargs)


def version_line() -> str:
    """Return the first line of ``crawl4server --version``, printed first on every start."""
    return server_main.version_text().splitlines()[0]


# --- --version -----------------------------------------------------------------


def test_version_names_command_and_dependencies() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    first_line = result.output.splitlines()[0]
    assert first_line.startswith("crawl4server 1.0.0b3 (crawl4ai 0.9.")
    assert ", mcp 2." in first_line
    assert ", starlette 1." in first_line
    assert "crawl4ai" in result.output.splitlines()[1]


# --- defaults --------------------------------------------------------------


def test_defaults_are_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    recorder = install(monkeypatch)
    result = invoke([])
    assert result.exit_code == 0, result.output
    call = recorder.calls[0]
    assert call["host"] == "127.0.0.1"
    assert call["loader_port"] == 8766
    assert call["mcp_port"] == 8765
    assert call["log_level"] == "info"
    assert call["settings"] == ServerSettings(download_root=tmp_path.resolve())
    assert call["loader"] == LoaderSettings(path="/crawl", api_key=None, fit=False)
    assert call["mcp"] == McpSettings(path="/mcp")


# --- every option -------------------------------------------------------------


def test_all_cli_options_reach_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    download_dir = tmp_path / "downloads"
    recorder = install(monkeypatch)
    result = invoke(
        [
            "--host",
            "127.0.0.1",
            "--loader-port",
            "9001",
            "--loader-path",
            "/ingest",
            "--loader-api-key",
            "s3cr3t-value",
            "--loader-fit",
            "--mcp-port",
            "9002",
            "--mcp-path",
            "/mcp2",
            "--proxy",
            "proxy.example:8080",
            "--no-fallback",
            "--timeout",
            "30",
            "--concurrency",
            "7",
            "--max-urls",
            "5",
            "--download-dir",
            str(download_dir),
            "--log-level",
            "debug",
            "--keep-downloads",
        ]
    )
    assert result.exit_code == 0, result.output
    call = recorder.calls[0]
    assert call["host"] == "127.0.0.1"
    assert call["loader_port"] == 9001
    assert call["mcp_port"] == 9002
    assert call["log_level"] == "debug"
    assert call["loader"] == LoaderSettings(path="/ingest", api_key="s3cr3t-value", fit=True)
    assert call["mcp"] == McpSettings(path="/mcp2")
    settings = call["settings"]
    assert settings.proxy == "http://proxy.example:8080"
    assert settings.fallback is False
    assert settings.timeout_s == 30
    assert settings.concurrency == 7
    assert settings.max_urls == 5
    assert settings.download_root == download_dir.resolve()
    assert settings.log_level == "debug"
    assert settings.keep_downloads is True


# --- environment variables ---------------------------------------------------


def test_env_vars_set_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    recorder = install(monkeypatch)
    result = invoke(
        [],
        env={
            "CRAWL4SERVER_LOADER_PORT": "9500",
            "CRAWL4SERVER_LOADER_API_KEY": "env-key",
            "CRAWL4SERVER_CONCURRENCY": "9",
            "CRAWL4SERVER_LOG_LEVEL": "error",
            "CRAWL4SERVER_KEEP_DOWNLOADS": "true",
        },
    )
    assert result.exit_code == 0, result.output
    call = recorder.calls[0]
    assert call["loader_port"] == 9500
    assert call["loader"].api_key == "env-key"
    assert call["settings"].concurrency == 9
    assert call["log_level"] == "error"
    assert call["settings"].log_level == "error"
    assert call["settings"].keep_downloads is True


# --- config file --------------------------------------------------------------


def test_config_file_yaml_sets_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    recorder = install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(
        "loader_port: 9100\nmcp_port: 9200\nmax_urls: 42\nconcurrency: 4\n"
        "log_level: debug\nkeep_downloads: true\n",
        encoding="utf-8",
    )
    result = invoke(["--config", str(config)])
    assert result.exit_code == 0, result.output
    call = recorder.calls[0]
    assert call["log_level"] == "debug"
    assert call["settings"].log_level == "debug"
    assert call["settings"].keep_downloads is True
    assert call["loader_port"] == 9100
    assert call["mcp_port"] == 9200
    assert call["settings"].max_urls == 42
    assert call["settings"].concurrency == 4


def test_config_via_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    recorder = install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text("max_urls: 33\n", encoding="utf-8")
    result = invoke([], env={"CRAWL4SERVER_CONFIG": str(config)})
    assert result.exit_code == 0, result.output
    assert recorder.calls[0]["settings"].max_urls == 33


def test_precedence_cli_over_env_over_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    recorder = install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(
        "concurrency: 2\nmax_urls: 10\nloader_port: 8800\nlog_level: debug\n",
        encoding="utf-8",
    )
    result = invoke(
        ["--config", str(config), "--concurrency", "9"],
        env={
            "CRAWL4SERVER_MAX_URLS": "77",
            "CRAWL4SERVER_LOADER_PORT": "8900",
            "CRAWL4SERVER_LOG_LEVEL": "error",
        },
    )
    assert result.exit_code == 0, result.output
    call = recorder.calls[0]
    assert call["settings"].concurrency == 9  # CLI beats config
    assert call["settings"].max_urls == 77  # env beats config
    assert call["loader_port"] == 8900  # env beats config
    assert call["log_level"] == "error"  # env beats config


@pytest.mark.parametrize(
    ("args", "env", "config"),
    [
        (["--log-level", "verbose"], {}, None),
        ([], {"CRAWL4SERVER_LOG_LEVEL": "warning"}, None),
        ([], {}, "log_level: warning\n"),
    ],
    ids=["option", "variable", "config"],
)
def test_unsupported_log_level_exits_2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    args: list[str],
    env: dict[str, str],
    config: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    recorder = install(monkeypatch)
    if config is not None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config, encoding="utf-8")
        args = ["--config", str(config_file), *args]
    result = invoke(args, env=env)
    assert result.exit_code == 2
    assert "Invalid value for '--log-level'" in result.stderr
    assert recorder.calls == []


def test_log_level_reaches_setup_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    levels: list[str] = []
    monkeypatch.setattr(server_main, "setup_logging", levels.append)
    result = invoke(["--log-level", "error"])
    assert result.exit_code == 0, result.output
    assert levels == ["error"]


def test_config_unknown_key_exit_2_lists_allowed_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text("bogus: 1\n", encoding="utf-8")
    result = invoke(["--config", str(config)])
    assert result.exit_code == 2
    assert "bogus" in result.output
    for key in server_main.SERVER_CONFIG_KEYS:
        assert key in result.output


def test_config_mcp_only_key_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text("transport: http\n", encoding="utf-8")
    result = invoke(["--config", str(config)])
    assert result.exit_code == 2
    assert "transport" in result.output


def test_server_config_keys_match_main_params() -> None:
    assert server_main.SERVER_CONFIG_KEYS == {p.name for p in server_main.main.params if p.name} - {
        "config",
        "version",
    }


# --- port validation -----------------------------------------------------------


def test_same_port_via_cli_exit_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch)
    result = invoke(["--loader-port", "9000", "--mcp-port", "9000"])
    assert result.exit_code == 2
    assert "--loader-port and --mcp-port must differ" in result.output


def test_same_port_via_config_exit_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text("loader_port: 9000\nmcp_port: 9000\n", encoding="utf-8")
    result = invoke(["--config", str(config)])
    assert result.exit_code == 2
    assert "--loader-port and --mcp-port must differ" in result.output


# --- loader-path validation -----------------------------------------------------


def test_loader_path_must_start_with_slash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch)
    result = invoke(["--loader-path", "crawl"])
    assert result.exit_code == 2


def test_loader_path_health_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch)
    result = invoke(["--loader-path", "/health"])
    assert result.exit_code == 2
    assert "reserved for the health check" in result.output


# --- non-loopback host warnings --------------------------------------------------


def test_non_loopback_host_warns_both_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert "crawl4server: warning:" in result.stderr
    assert "MCP endpoint has no authentication" in result.stderr
    assert "web loader has no authentication" in result.stderr


def test_non_loopback_host_warns_mcp_only_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--host", "0.0.0.0", "--loader-api-key", "k"])
    assert result.exit_code == 0, result.output
    assert "MCP endpoint has no authentication" in result.stderr
    assert "web loader has no authentication" not in result.stderr


def test_loopback_host_does_not_warn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--host", "127.0.0.1"])
    assert result.exit_code == 0, result.output
    assert "warning" not in result.stderr


# --- serving lines --------------------------------------------------------------


def test_serving_lines_use_on_started_ports_and_stdout_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch, ports={"loader": 40001, "mcp": 40002})
    result = invoke(["--loader-path", "/crawl", "--mcp-path", "/mcp"])
    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        version_line(),
        "crawl4server: serving Open WebUI web loader on http://127.0.0.1:40001/crawl",
        "crawl4server: serving MCP on http://127.0.0.1:40002/mcp",
    ]
    assert result.stdout == ""


# --- run_server() failures -------------------------------------------------------


def test_listen_error_exits_1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(
        monkeypatch,
        run_effect=host_module.ListenError("127.0.0.1", 8766, "Address already in use"),
    )
    result = invoke([])
    assert result.exit_code == 1
    assert "error: cannot listen on 127.0.0.1:8766: Address already in use" in result.stderr


def test_keyboard_interrupt_exits_0_quietly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch, run_effect=KeyboardInterrupt())
    result = invoke([])
    assert result.exit_code == 0
    assert "error" not in result.stderr.lower()


# --- secrecy of the loader API key ----------------------------------------------


def test_loader_api_key_never_in_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--loader-api-key", "s3cr3t-value", "--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert "s3cr3t-value" not in result.stdout
    assert "s3cr3t-value" not in result.stderr

    help_result = CliRunner().invoke(main, ["--help"])
    assert "s3cr3t-value" not in help_result.output


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
        ([], {"CRAWL4SERVER_LANG": "ja"}, None, "ja"),
        ([], {}, "lang: ja\n", "ja"),
        (["--lang", "en"], {"CRAWL4SERVER_LANG": "ja"}, None, "en"),
        ([], {"CRAWL4SERVER_LANG": "en"}, "lang: ja\n", "en"),
        (["--lang", "ja"], {"CRAWL4SERVER_LANG": "en"}, "lang: en\n", "ja"),
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
    recorder = install(monkeypatch)
    if config is not None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config, encoding="utf-8")
        args = ["--config", str(config_file), *args]
    result = invoke(args, env=env)
    assert result.exit_code == 0, result.output
    assert recorder.calls[0]["settings"].lang == expected


def test_locale_does_not_choose_the_language(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", "ja_JP.UTF-8")
    recorder = install(monkeypatch)
    result = invoke([])
    assert result.exit_code == 0, result.output
    assert recorder.calls[0]["settings"].lang == "en"


@pytest.mark.parametrize(
    ("args", "env", "config"),
    [
        (["--lang", "de"], {}, None),
        ([], {"CRAWL4SERVER_LANG": "de"}, None),
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
    recorder = install(monkeypatch)
    if config is not None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config, encoding="utf-8")
        args = ["--config", str(config_file), *args]
    result = invoke(args, env=env)
    assert result.exit_code == 2
    assert "Invalid value for '--lang'" in result.stderr
    assert recorder.calls == []


def test_japanese_warnings_and_serving_lines_keep_the_english_prefixes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch, ports={"loader": 40001, "mcp": 40002})
    result = invoke(["--lang", "ja", "--host", "0.0.0.0"])
    assert result.exit_code == 0, result.output
    assert result.stderr.splitlines() == [
        version_line(),
        "crawl4server: warning: MCP エンドポイントはループバック以外のホストで認証なしになって"
        "います。接続できる人は誰でもこのサーバーを使えます",
        "crawl4server: warning: web loader も認証なしです。認証を必須にするには "
        "--loader-api-key を指定してください",
        "crawl4server: http://0.0.0.0:40001/crawl で Open WebUI の web loader を提供しています",
        "crawl4server: http://0.0.0.0:40002/mcp で MCP を提供しています",
    ]


def test_japanese_listen_error_keeps_the_english_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(
        monkeypatch,
        run_effect=host_module.ListenError("127.0.0.1", 8766, "Address already in use"),
    )
    result = invoke(["--lang", "ja"])
    assert result.exit_code == 1
    assert "error: 127.0.0.1:8766 で待ち受けできません: Address already in use" in result.stderr


def test_japanese_usage_error_for_equal_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    install(monkeypatch)
    result = invoke(["--lang", "ja", "--loader-port", "9000", "--mcp-port", "9000"])
    assert result.exit_code == 2
    assert "--loader-port と --mcp-port は異なる値にしてください" in result.output


def test_japanese_help_shows_translated_texts() -> None:
    result = CliRunner().invoke(server_main.build_command(JA), ["--help"])
    assert result.exit_code == 0
    output = squash(result.output)
    for text in [
        "Open WebUI の web loader と MCP を 1 つのプロセスから提供します。",
        "待ち受けるホストです(両方のポート)。",
        "Open WebUI の web loader が使うポートです。",
        "Open WebUI の web loader が待ち受ける HTTP パスです。",
        "MCP の Streamable HTTP ポートです。",
        "MCP エンドポイントを提供する HTTP パスです。",
        "MCP の download ツールがファイルを保存するルートディレクトリです。",
        "ログレベルです。debug(すべて)、info(取得、警告、エラー)、error(警告とエラーのみ)"
        "のいずれかです。",
        "YAML または JSON の設定ファイルです。コマンドラインのオプションと CRAWL4SERVER_* "
        "環境変数が優先されます。",
        "メッセージの言語(en または ja)です。",
        "バージョンを表示して終了します。",
    ]:
        assert squash(text) in output, text
    assert "--lang [en|ja]" in result.output
    assert "Usage:" in result.output
    assert "Host to listen on" not in result.output


def test_japanese_loader_path_health_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install(monkeypatch)
    result = CliRunner().invoke(server_main.build_command(JA), ["--loader-path", "/health"])
    assert result.exit_code == 2
    assert "'/health' はヘルスチェック用に予約されています" in result.output


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
    monkeypatch.setattr(sys, "argv", ["crawl4server", *argv])
    recorder = CommandRecorder()
    monkeypatch.setattr(server_main, "build_command", recorder)
    server_main.entry()
    return recorder


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        (["--lang", "ja"], {}, JA),
        (["--lang=ja"], {}, JA),
        ([], {"CRAWL4SERVER_LANG": "ja"}, JA),
        ([], {"LANG": "ja_JP.UTF-8"}, ENGLISH),
        ([], {}, ENGLISH),
        (["--lang", "en"], {"CRAWL4SERVER_LANG": "ja"}, ENGLISH),
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
    assert recorder.main_calls == [{"prog_name": "crawl4server"}]
