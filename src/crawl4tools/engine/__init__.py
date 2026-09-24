"""Shared fetch engine used by the CLI and the (future) servers."""

from __future__ import annotations

from crawl4tools.engine.classify import build_error, classify_error_message, error_for_status
from crawl4tools.engine.errors import (
    BrowserNotInstalledError,
    ConnectionRefusedFetchError,
    FetchError,
    FetchTimeoutError,
    HttpStatusError,
    NameResolutionError,
    NonHtmlContentError,
    ProxyFetchError,
)
from crawl4tools.engine.fetcher import (
    CrawlerFactory,
    CrawlerLike,
    Fetcher,
    build_run_config,
    default_crawler_factory,
)
from crawl4tools.engine.models import (
    ContentKind,
    FailureKind,
    FetchOptions,
    FetchOutcome,
    Note,
    OutputFormat,
)
from crawl4tools.engine.naming import (
    InvalidUrlError,
    NameAllocator,
    dedupe_urls,
    extension_for,
    filename_for,
    validate_url,
)
from crawl4tools.engine.pdf import PdfConversionError, pdf_bytes_to_markdown, pdf_to_markdown
from crawl4tools.engine.probe import HttpClientFactory, ProbeResult, default_http_client, probe
from crawl4tools.engine.proxy import (
    FALLBACK_KINDS,
    FALLBACK_STATUS_CODES,
    SUPPORTED_SCHEMES,
    ProxyUrlError,
    is_socks,
    normalize_proxy,
    redact_proxy,
    should_fallback,
)

__all__ = [
    "FALLBACK_KINDS",
    "FALLBACK_STATUS_CODES",
    "SUPPORTED_SCHEMES",
    "BrowserNotInstalledError",
    "ConnectionRefusedFetchError",
    "ContentKind",
    "CrawlerFactory",
    "CrawlerLike",
    "FailureKind",
    "FetchError",
    "FetchOptions",
    "FetchOutcome",
    "FetchTimeoutError",
    "Fetcher",
    "HttpClientFactory",
    "HttpStatusError",
    "InvalidUrlError",
    "NameAllocator",
    "NameResolutionError",
    "NonHtmlContentError",
    "Note",
    "OutputFormat",
    "PdfConversionError",
    "ProbeResult",
    "ProxyFetchError",
    "ProxyUrlError",
    "build_error",
    "build_run_config",
    "classify_error_message",
    "dedupe_urls",
    "default_crawler_factory",
    "default_http_client",
    "error_for_status",
    "extension_for",
    "filename_for",
    "is_socks",
    "normalize_proxy",
    "pdf_bytes_to_markdown",
    "pdf_to_markdown",
    "probe",
    "redact_proxy",
    "should_fallback",
    "validate_url",
]
