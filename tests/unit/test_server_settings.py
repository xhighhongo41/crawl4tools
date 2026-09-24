from __future__ import annotations

from pathlib import Path

import pytest

from crawl4tools.engine.models import FetchOptions, OutputFormat
from crawl4tools.server.settings import (
    DEFAULT_CONCURRENCY,
    DEFAULT_MAX_URLS,
    DEFAULT_TIMEOUT_S,
    ServerSettings,
)


def test_defaults() -> None:
    settings = ServerSettings()
    assert settings.proxy is None
    assert settings.fallback is True
    assert settings.timeout_s == DEFAULT_TIMEOUT_S
    assert settings.concurrency == DEFAULT_CONCURRENCY
    assert settings.max_urls == DEFAULT_MAX_URLS
    assert settings.download_root == Path(".")
    assert settings.verbose is False


def test_constants() -> None:
    assert DEFAULT_TIMEOUT_S == 60.0
    assert DEFAULT_CONCURRENCY == 3
    assert DEFAULT_MAX_URLS == 20


def test_is_frozen() -> None:
    settings = ServerSettings()
    with pytest.raises(AttributeError):
        settings.verbose = True  # type: ignore[misc]


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
    settings = ServerSettings(proxy="example.com:8080", fallback=False, verbose=True)
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
