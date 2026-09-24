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
from crawl4tools.engine.models import (
    ContentKind,
    FailureKind,
    FetchOptions,
    FetchOutcome,
    OutputFormat,
)
from crawl4tools.engine.naming import (
    NameAllocator,
    dedupe_urls,
    extension_for,
    filename_for,
    validate_url,
)
from crawl4tools.engine.proxy import (
    FALLBACK_KINDS,
    FALLBACK_STATUS_CODES,
    SUPPORTED_SCHEMES,
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
    "FailureKind",
    "FetchError",
    "FetchOptions",
    "FetchOutcome",
    "FetchTimeoutError",
    "HttpStatusError",
    "NameAllocator",
    "NameResolutionError",
    "NonHtmlContentError",
    "OutputFormat",
    "ProxyFetchError",
    "build_error",
    "classify_error_message",
    "dedupe_urls",
    "error_for_status",
    "extension_for",
    "filename_for",
    "is_socks",
    "normalize_proxy",
    "redact_proxy",
    "should_fallback",
    "validate_url",
]
