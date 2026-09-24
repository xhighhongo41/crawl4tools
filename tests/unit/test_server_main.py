"""Tests for the crawl4server command."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

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
        verbose: bool,
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
                "verbose": verbose,
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


# --- --version -----------------------------------------------------------------


def test_version_names_command_and_dependencies() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    first_line = result.output.splitlines()[0]
    assert first_line.startswith("crawl4server 0.3.0 (crawl4ai 0.9.")
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
    assert call["verbose"] is False
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
            "--verbose",
        ]
    )
    assert result.exit_code == 0, result.output
    call = recorder.calls[0]
    assert call["host"] == "127.0.0.1"
    assert call["loader_port"] == 9001
    assert call["mcp_port"] == 9002
    assert call["verbose"] is True
    assert call["loader"] == LoaderSettings(path="/ingest", api_key="s3cr3t-value", fit=True)
    assert call["mcp"] == McpSettings(path="/mcp2")
    settings = call["settings"]
    assert settings.proxy == "http://proxy.example:8080"
    assert settings.fallback is False
    assert settings.timeout_s == 30
    assert settings.concurrency == 7
    assert settings.max_urls == 5
    assert settings.download_root == download_dir.resolve()
    assert settings.verbose is True


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
        },
    )
    assert result.exit_code == 0, result.output
    call = recorder.calls[0]
    assert call["loader_port"] == 9500
    assert call["loader"].api_key == "env-key"
    assert call["settings"].concurrency == 9


# --- config file --------------------------------------------------------------


def test_config_file_yaml_sets_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    recorder = install(monkeypatch)
    config = tmp_path / "config.yaml"
    config.write_text(
        "loader_port: 9100\nmcp_port: 9200\nmax_urls: 42\nconcurrency: 4\n",
        encoding="utf-8",
    )
    result = invoke(["--config", str(config)])
    assert result.exit_code == 0, result.output
    call = recorder.calls[0]
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
    config.write_text("concurrency: 2\nmax_urls: 10\nloader_port: 8800\n", encoding="utf-8")
    result = invoke(
        ["--config", str(config), "--concurrency", "9"],
        env={"CRAWL4SERVER_MAX_URLS": "77", "CRAWL4SERVER_LOADER_PORT": "8900"},
    )
    assert result.exit_code == 0, result.output
    call = recorder.calls[0]
    assert call["settings"].concurrency == 9  # CLI beats config
    assert call["settings"].max_urls == 77  # env beats config
    assert call["loader_port"] == 8900  # env beats config


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
    assert (
        "crawl4server: serving Open WebUI web loader on http://127.0.0.1:40001/crawl"
        in result.stderr
    )
    assert "crawl4server: serving MCP on http://127.0.0.1:40002/mcp" in result.stderr
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
