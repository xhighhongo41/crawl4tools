"""Server-wide settings shared by all crawl4mcp tools.

:class:`ServerSettings` is the single place that owns the defaults for
proxy/fallback/timeout/concurrency/etc. so the CLI options that build it
and the tool implementations that consume it never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from crawl4tools.engine import normalize_proxy
from crawl4tools.engine.models import FetchOptions, OutputFormat
from crawl4tools.i18n import (
    DEFAULT_LANGUAGE,
    N_,
    SUPPORTED_LANGUAGES,
    LocalizedError,
    Translator,
    get_translator,
)

#: Default per-fetch timeout, in seconds.
DEFAULT_TIMEOUT_S = 60.0
#: Default number of URLs fetched concurrently.
DEFAULT_CONCURRENCY = 3
#: Default upper bound on the number of URLs accepted in a single call.
DEFAULT_MAX_URLS = 20


class SettingsError(LocalizedError, ValueError):
    """An out-of-range server setting.

    Also a ``ValueError``, so existing ``except ValueError`` clauses keep
    catching it; ``str()`` is the English message.
    """


@dataclass(frozen=True)
class ServerSettings:
    """Process-wide defaults for the crawl4mcp tools.

    Instances are immutable; a new instance is created for each server
    process from CLI options / config file / environment variables.
    ``lang`` is the language of the messages the server sends to its
    clients (see :attr:`translator`); logs stay in English.
    """

    proxy: str | None = None
    fallback: bool = True
    timeout_s: float = DEFAULT_TIMEOUT_S
    concurrency: int = DEFAULT_CONCURRENCY
    max_urls: int = DEFAULT_MAX_URLS
    download_root: Path = Path(".")
    verbose: bool = False
    lang: str = DEFAULT_LANGUAGE

    def __post_init__(self) -> None:
        """Validate the settings and normalize the proxy URL.

        Raises:
            SettingsError: (a ``ValueError``) if concurrency/max_urls/
                timeout_s are out of range, or ``lang`` is not one of
                :data:`~crawl4tools.i18n.SUPPORTED_LANGUAGES`.
            ProxyUrlError: (a ``ValueError``) if the proxy URL is invalid.
        """
        if self.concurrency < 1:
            raise SettingsError(
                N_("concurrency must be at least 1, got {value}"), value=self.concurrency
            )
        if self.max_urls < 1:
            raise SettingsError(N_("max_urls must be at least 1, got {value}"), value=self.max_urls)
        if self.timeout_s <= 0:
            raise SettingsError(
                N_("timeout_s must be greater than 0, got {value}"), value=self.timeout_s
            )
        if self.lang not in SUPPORTED_LANGUAGES:
            raise SettingsError(
                N_("unsupported language: {lang} (supported: {supported})"),
                lang=self.lang,
                supported=", ".join(SUPPORTED_LANGUAGES),
            )
        if self.proxy is not None:
            # Frozen dataclass: bypass __setattr__ to store the normalized form.
            object.__setattr__(self, "proxy", normalize_proxy(self.proxy))

    @property
    def translator(self) -> Translator:
        """Return the translator of :attr:`lang` (the same object for the same language)."""
        return get_translator(self.lang)

    def fetch_options(
        self,
        *,
        format: OutputFormat = OutputFormat.MARKDOWN,
        fit: bool = False,
        citations: bool = False,
        ignore_links: bool = False,
        ignore_images: bool = False,
        timeout_s: float | None = None,
    ) -> FetchOptions:
        """Build a :class:`FetchOptions` for one call, layering per-call flags.

        ``proxy``, ``fallback``, and ``verbose`` always come from these
        settings; the other arguments let a single tool call override the
        format and content-shaping flags without mutating the settings.

        Raises:
            SettingsError: (a ``ValueError``) if *timeout_s* is given and
                not greater than 0.
        """
        resolved_timeout = self.timeout_s if timeout_s is None else timeout_s
        if resolved_timeout <= 0:
            raise SettingsError(N_("timeout_s must be greater than 0"))
        return FetchOptions(
            format=format,
            proxy=self.proxy,
            fallback=self.fallback,
            timeout_s=resolved_timeout,
            citations=citations,
            ignore_links=ignore_links,
            ignore_images=ignore_images,
            fit=fit,
            verbose=self.verbose,
        )
