from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crawl4tools.engine.models import FetchOptions, OutputFormat
from crawl4tools.i18n import ENGLISH, LocalizedError, get_translator
from crawl4tools.server.settings import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_URLS,
    DEFAULT_TIMEOUT_S,
    LOG_LEVELS,
    LogLevel,
    ServerSettings,
    SettingsError,
)

JA = get_translator("ja")


def test_defaults() -> None:
    settings = ServerSettings()
    assert settings.proxy is None
    assert settings.fallback is True
    assert settings.timeout_s == DEFAULT_TIMEOUT_S
    assert settings.concurrency == DEFAULT_CONCURRENCY
    assert settings.max_urls == DEFAULT_MAX_URLS
    assert settings.download_root == Path(".")
    assert settings.log_level == "info"
    assert settings.keep_downloads is False


def test_constants() -> None:
    assert DEFAULT_TIMEOUT_S == 60.0
    assert DEFAULT_CONCURRENCY == 3
    assert DEFAULT_MAX_URLS == 20
    assert LOG_LEVELS == ("debug", "info", "error")


@pytest.mark.parametrize("log_level", ["debug", "info", "error"])
def test_accepts_every_log_level(log_level: LogLevel) -> None:
    assert ServerSettings(log_level=log_level).log_level == log_level


@pytest.mark.parametrize("log_level", ["warning", "DEBUG", ""])
def test_rejects_unsupported_log_level(log_level: str) -> None:
    with pytest.raises(SettingsError) as info:
        ServerSettings(log_level=log_level)  # type: ignore[arg-type]
    assert isinstance(info.value, ValueError)
    assert str(info.value) == (
        f"unsupported log level: {log_level} (choose from debug, info, error)"
    )


def test_log_level_error_in_japanese() -> None:
    with pytest.raises(SettingsError) as info:
        ServerSettings(log_level="warning")  # type: ignore[arg-type]
    assert info.value.render(JA) == (
        "対応していないログレベルです: warning(選択肢: debug, info, error)"
    )


@pytest.mark.parametrize(
    ("log_level", "verbose"),
    [("debug", True), ("info", False), ("error", False)],
)
def test_fetch_options_verbose_only_for_debug(log_level: LogLevel, verbose: bool) -> None:
    assert ServerSettings(log_level=log_level).fetch_options().verbose is verbose


def test_keep_downloads_is_stored() -> None:
    assert ServerSettings(keep_downloads=True).keep_downloads is True


def test_is_frozen() -> None:
    settings = ServerSettings()
    with pytest.raises(AttributeError):
        settings.log_level = "debug"  # type: ignore[misc]


@pytest.mark.parametrize("concurrency", [0, -1])
def test_rejects_non_positive_concurrency(concurrency: int) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        ServerSettings(concurrency=concurrency)


@pytest.mark.parametrize("max_urls", [0, -1])
def test_rejects_non_positive_max_urls(max_urls: int) -> None:
    with pytest.raises(ValueError, match="max_urls"):
        ServerSettings(max_urls=max_urls)


@pytest.mark.parametrize("timeout_s", [0, -1.0])
def test_rejects_non_positive_timeout(timeout_s: float) -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        ServerSettings(timeout_s=timeout_s)


def test_normalizes_proxy() -> None:
    settings = ServerSettings(proxy="example.com:8080")
    assert settings.proxy == "http://example.com:8080"


def test_rejects_invalid_proxy() -> None:
    with pytest.raises(ValueError, match="proxy"):
        ServerSettings(proxy="not a proxy")


def test_fetch_options_uses_settings_defaults() -> None:
    settings = ServerSettings(proxy="example.com:8080", fallback=False, log_level="debug")
    options = settings.fetch_options()
    assert options == FetchOptions(
        format=OutputFormat.MARKDOWN,
        proxy="http://example.com:8080",
        fallback=False,
        timeout_s=DEFAULT_TIMEOUT_S,
        citations=False,
        ignore_links=False,
        ignore_images=False,
        fit=False,
        verbose=True,
    )


def test_fetch_options_overrides_format_and_flags() -> None:
    settings = ServerSettings()
    options = settings.fetch_options(
        format=OutputFormat.HTML,
        fit=True,
        citations=True,
        ignore_links=True,
        ignore_images=True,
    )
    assert options.format is OutputFormat.HTML
    assert options.fit is True
    assert options.citations is True
    assert options.ignore_links is True
    assert options.ignore_images is True


def test_fetch_options_timeout_none_uses_settings_timeout() -> None:
    settings = ServerSettings(timeout_s=12.5)
    options = settings.fetch_options(timeout_s=None)
    assert options.timeout_s == 12.5


def test_fetch_options_timeout_override() -> None:
    settings = ServerSettings()
    options = settings.fetch_options(timeout_s=5.0)
    assert options.timeout_s == 5.0


@pytest.mark.parametrize("timeout_s", [0, -1.0])
def test_fetch_options_rejects_non_positive_timeout_override(timeout_s: float) -> None:
    settings = ServerSettings()
    with pytest.raises(ValueError, match="timeout_s must be greater than 0"):
        settings.fetch_options(timeout_s=timeout_s)


# --- lang and translator --------------------------------------------------------


def test_default_lang_is_english() -> None:
    settings = ServerSettings()
    assert settings.lang == "en"
    assert settings.translator is ENGLISH


def test_translator_follows_lang() -> None:
    assert ServerSettings(lang="ja").translator is get_translator("ja")


def test_rejects_unsupported_lang() -> None:
    with pytest.raises(SettingsError) as info:
        ServerSettings(lang="de")
    assert str(info.value) == "unsupported language: de (supported: en, ja)"
    assert info.value.render(JA) == "対応していない言語です: de(対応言語: en, ja)"


# --- SettingsError ---------------------------------------------------------------


def test_settings_error_is_a_localized_value_error() -> None:
    assert issubclass(SettingsError, LocalizedError)
    assert issubclass(SettingsError, ValueError)


@pytest.mark.parametrize(
    ("overrides", "english", "japanese"),
    [
        (
            {"concurrency": 0},
            "concurrency must be at least 1, got 0",
            "concurrency は 1 以上にしてください(指定値: 0)",
        ),
        (
            {"max_urls": -1},
            "max_urls must be at least 1, got -1",
            "max_urls は 1 以上にしてください(指定値: -1)",
        ),
        (
            {"timeout_s": -1.5},
            "timeout_s must be greater than 0, got -1.5",
            "timeout_s は 0 より大きくしてください(指定値: -1.5)",
        ),
    ],
    ids=["concurrency", "max_urls", "timeout_s"],
)
def test_validation_errors_in_english_and_japanese(
    overrides: dict[str, Any], english: str, japanese: str
) -> None:
    with pytest.raises(SettingsError) as info:
        ServerSettings(**overrides)
    assert str(info.value) == english
    assert info.value.render(JA) == japanese


def test_fetch_options_timeout_error_in_english_and_japanese() -> None:
    with pytest.raises(SettingsError) as info:
        ServerSettings().fetch_options(timeout_s=0)
    assert str(info.value) == "timeout_s must be greater than 0"
    assert info.value.render(JA) == "timeout_s は 0 より大きくしてください"
