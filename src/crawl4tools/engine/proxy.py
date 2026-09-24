"""Proxy URL validation, redaction, and fallback policy.

Kept dependency-free (stdlib only, and only :mod:`crawl4tools.engine.models`
from this project) so :mod:`crawl4tools.engine.errors` can safely import
:func:`redact_proxy` without creating an import cycle.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from crawl4tools.engine.models import FailureKind, FetchOptions

SUPPORTED_SCHEMES = ("http", "https", "socks5")

# HTTP status codes that suggest the *proxy* (not the origin server) is the
# problem, and are therefore worth retrying without the proxy.
FALLBACK_STATUS_CODES = frozenset({407, 502, 503, 504})

# Failure kinds that are worth retrying without the configured proxy.
FALLBACK_KINDS = frozenset({FailureKind.PROXY, FailureKind.CONNECTION_REFUSED, FailureKind.TIMEOUT})

# Matches an optional "scheme://" followed by userinfo ("user[:pass]") and
# an "@". Used to redact credentials from proxy URLs, with or without a
# scheme prefix.
_USERINFO_RE = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)?(?P<userinfo>[^/\s]+)@")


def normalize_proxy(value: str) -> str:
    """Normalize a proxy URL, defaulting to the ``http`` scheme if omitted.

    The scheme is lowercased; credentials (if any) are preserved in the
    returned value, since callers need them to actually connect.

    Raises:
        ValueError: if the scheme is unsupported, or the host/port are
            missing or invalid. The message never includes credentials.
    """
    stripped = value.strip()
    candidate = stripped if "://" in stripped else f"http://{stripped}"
    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()

    if scheme not in SUPPORTED_SCHEMES:
        raise ValueError(f"unsupported proxy scheme: {redact_proxy(candidate)}")
    if not parts.hostname:
        raise ValueError(f"proxy URL is missing a host: {redact_proxy(candidate)}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"proxy URL has an invalid port: {redact_proxy(candidate)}") from exc
    if port is None:
        raise ValueError(f"proxy URL is missing a port: {redact_proxy(candidate)}")

    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


def redact_proxy(value: str) -> str:
    """Return *value* with any embedded ``user[:pass]@`` credentials masked.

    Works for values with or without a scheme prefix. Returns *value*
    unchanged when it has no userinfo to redact.
    """
    match = _USERINFO_RE.match(value)
    if not match:
        return value
    scheme = match.group("scheme") or ""
    return f"{scheme}***@{value[match.end() :]}"


def is_socks(proxy: str) -> bool:
    """Return True if *proxy* uses the SOCKS5 scheme."""
    return urlsplit(proxy).scheme.lower() == "socks5"


def should_fallback(
    *,
    options: FetchOptions,
    kind: FailureKind | None,
    status_code: int | None,
    already_retried: bool,
) -> bool:
    """Decide whether a failed fetch should be retried without the proxy.

    Only applies when a proxy is configured, fallback is enabled, and this
    is not already a fallback retry. Beyond that, only failures plausibly
    caused by the proxy itself (rather than the origin server) qualify.
    """
    if options.proxy is None or not options.fallback or already_retried:
        return False
    if kind in FALLBACK_KINDS:
        return True
    return kind is FailureKind.HTTP_STATUS and status_code in FALLBACK_STATUS_CODES
