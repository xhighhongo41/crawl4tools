from __future__ import annotations

from pathlib import Path

import pytest

from crawl4tools.i18n import LocalizedError, get_translator
from crawl4tools.server.config import CONFIG_KEYS, ConfigError, load_config

JA = get_translator("ja")


def test_config_keys_contents() -> None:
    assert CONFIG_KEYS == frozenset(
        {
            "transport",
            "host",
            "port",
            "path",
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


def test_load_config_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "transport: stdio\nport: 8080\nlog_level: debug\nkeep_downloads: true\n",
        encoding="utf-8",
    )
    assert load_config(config_file) == {
        "transport": "stdio",
        "port": 8080,
        "log_level": "debug",
        "keep_downloads": True,
    }


def test_load_config_json(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        '{"transport": "http", "host": "127.0.0.1", "port": 9000}', encoding="utf-8"
    )
    assert load_config(config_file) == {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 9000,
    }


def test_load_config_empty_file_returns_empty_dict(tmp_path: Path) -> None:
    config_file = tmp_path / "empty.yaml"
    config_file.write_text("", encoding="utf-8")
    assert load_config(config_file) == {}


def test_load_config_rejects_list_at_top_level(tmp_path: Path) -> None:
    config_file = tmp_path / "list.yaml"
    config_file.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="top level must be a mapping") as exc_info:
        load_config(config_file)
    assert str(config_file) in str(exc_info.value)


def test_load_config_rejects_unknown_key(tmp_path: Path) -> None:
    config_file = tmp_path / "unknown.yaml"
    config_file.write_text("transport: stdio\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="bogus") as exc_info:
        load_config(config_file)
    assert str(config_file) in str(exc_info.value)


def test_load_config_rejects_non_string_key(tmp_path: Path) -> None:
    config_file = tmp_path / "nonstring.yaml"
    config_file.write_text("1: stdio\n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load_config(config_file)
    assert str(config_file) in str(exc_info.value)


def test_load_config_rejects_syntax_error(tmp_path: Path) -> None:
    config_file = tmp_path / "broken.yaml"
    config_file.write_text("transport: [unterminated\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML/JSON") as exc_info:
        load_config(config_file)
    assert str(config_file) in str(exc_info.value)


def test_load_config_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ConfigError, match="cannot read config file") as exc_info:
        load_config(missing)
    assert str(missing) in str(exc_info.value)


def test_config_error_is_a_value_error() -> None:
    assert issubclass(ConfigError, ValueError)


def test_load_config_values_are_untouched(tmp_path: Path) -> None:
    config_file = tmp_path / "types.yaml"
    config_file.write_text(
        "timeout: 12.5\nconcurrency: 4\nmax_urls: 10\nfallback: false\n",
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config["timeout"] == 12.5
    assert isinstance(config["concurrency"], int)
    assert config["fallback"] is False


CUSTOM_KEYS = frozenset({"loader_port", "mcp_port", "timeout"})


def test_load_config_custom_allowed_keys_accepts(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("loader_port: 8080\nmcp_port: 8765\n", encoding="utf-8")
    assert load_config(config_file, CUSTOM_KEYS) == {"loader_port": 8080, "mcp_port": 8765}


def test_load_config_custom_allowed_keys_rejects(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    # "transport" is a crawl4mcp key but not part of the custom set.
    config_file.write_text("transport: stdio\nloader_port: 8080\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(config_file, allowed_keys=CUSTOM_KEYS)
    message = str(info.value)
    assert "unknown config key(s): transport" in message
    assert "(allowed keys: loader_port, mcp_port, timeout)" in message


# --- Japanese messages -------------------------------------------------------------


def test_config_error_is_localized() -> None:
    assert issubclass(ConfigError, LocalizedError)


def test_missing_file_error_in_japanese(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ConfigError) as info:
        load_config(missing)
    reason = info.value.params["reason"]
    assert str(info.value) == f"{missing}: cannot read config file: {reason}"
    assert info.value.render(JA) == f"{missing}: 設定ファイルを読み込めません: {reason}"


def test_syntax_error_in_japanese(tmp_path: Path) -> None:
    config_file = tmp_path / "broken.yaml"
    config_file.write_text("transport: [unterminated\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(config_file)
    reason = info.value.params["reason"]
    assert str(info.value) == f"{config_file}: invalid YAML/JSON: {reason}"
    assert info.value.render(JA) == f"{config_file}: YAML/JSON として不正です: {reason}"


def test_top_level_error_in_japanese(tmp_path: Path) -> None:
    config_file = tmp_path / "list.yaml"
    config_file.write_text("- a\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(config_file)
    assert str(info.value) == (
        f"{config_file}: top level must be a mapping of option names to values"
    )
    assert info.value.render(JA) == (
        f"{config_file}: 最上位はオプション名と値の対応(マッピング)にしてください"
    )


def test_unknown_keys_error_in_japanese(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("zeta: 1\nalpha: 2\ntimeout: 3\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(config_file, allowed_keys=CUSTOM_KEYS)
    assert str(info.value) == (
        f"{config_file}: unknown config key(s): alpha, zeta "
        "(allowed keys: loader_port, mcp_port, timeout)"
    )
    assert info.value.render(JA) == (
        f"{config_file}: 不明な設定キーです: alpha, zeta"
        "(使えるキー: loader_port, mcp_port, timeout)"
    )
