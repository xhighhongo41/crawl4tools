"""Detection of responses broken by a proxy that intercepts TLS ("SSL bump").

A proxy that decrypts HTTPS (Squid's ``ssl_bump``) can break a fetch in
ways the plain proxy failures do not cover. crawl4ai's browser accepts the
proxy's certificate silently (``ignore_https_errors`` is on by default), so
the breakage shows up as one of three signs:

* S1, a TLS-layer error in the browser's or httpx's error text
  (:func:`is_tls_message`);
* S2, an error response that the proxy generated itself instead of the
  page (:func:`detect_interference`);
* S3, a bot challenge from the site, typically provoked by the proxy's TLS
  fingerprint (:func:`detect_interference`).

The fetcher only uses these signs to decide on a direct-connection
fallback. The marker tables are module-level data so more proxies and
challenge headers can be added without touching the matching logic; only
Squid is recognized for now. Stdlib only, and nothing from the rest of the
engine, so any engine module can import it without creating an import cycle.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class InterferenceSign(StrEnum):
    """Which sign of proxy interference a response shows."""

    # S2: the proxy answered with its own error page.
    PROXY_ERROR_PAGE = "proxy_error_page"
    # S3: the site answered with a bot challenge.
    BOT_CHALLENGE = "bot_challenge"


@dataclass(frozen=True)
class Interference:
    """A response that the proxy produced, or that the site refused because of it.

    ``detail`` is the evidence found: the proxy's error header value or
    error code (or the page title when there is no code), or the challenge
    header value.
    """

    sign: InterferenceSign
    detail: str


@dataclass(frozen=True)
class ProxyErrorPage:
    """How one proxy product marks the error responses it generates itself."""

    # Lowercase name of the response header that carries the error code.
    header: str
    # The stock <title> of the product's (English) error pages.
    title: str
    # Finds the error code in the page, for when the header was removed.
    code_pattern: re.Pattern[str]
    # Error codes that mean the administrator refused the request on purpose.
    policy_denials: frozenset[str]
    # HTTP statuses of a refusal: covers the administrator's own deny pages too,
    # whose codes no table can list.
    policy_statuses: frozenset[int]


# Squid names the error in the X-Squid-Error header ("ERR_SECURE_CONNECT_FAIL 0")
# and, in its stock templates, in the id of the <body> element.
SQUID_ERROR_PAGE = ProxyErrorPage(
    header="x-squid-error",
    title="ERROR: The requested URL could not be retrieved",
    code_pattern=re.compile(
        r"<body\b[^>]{0,256}?\bid\s{0,8}=\s{0,8}[\"']?(ERR_[A-Z0-9_]+)", re.IGNORECASE
    ),
    policy_denials=frozenset(
        {
            "ERR_ACCESS_DENIED",
            "ERR_CACHE_ACCESS_DENIED",
            "ERR_FORWARDING_DENIED",
            "ERR_CACHE_MGR_ACCESS_DENIED",
        }
    ),
    # Squid refuses with 403, custom deny_info pages included; its failures to
    # reach or talk TLS to the server are 502, 503 and 504.
    policy_statuses=frozenset({403}),
)

# The proxies whose own error responses are recognized (sign S2).
PROXY_ERROR_PAGES: tuple[ProxyErrorPage, ...] = (SQUID_ERROR_PAGE,)

# (lowercase header name, lowercase value) pairs that mark a bot challenge
# (sign S3). Cloudflare sets "cf-mitigated: challenge" on its challenge pages.
BOT_CHALLENGE_HEADERS: tuple[tuple[str, str], ...] = (("cf-mitigated", "challenge"),)

# Lowercase fragments of TLS-layer error texts (sign S1): Chromium's
# net::ERR_CERT_* / net::ERR_SSL_* codes, and the OpenSSL texts httpx passes on.
TLS_PATTERNS: tuple[str, ...] = (
    "net::err_cert_",
    "net::err_ssl_",
    "err_bad_ssl_client_auth_cert",
    "certificate verify failed",
    "[ssl:",
    "tlsv1 alert",
    "sslv3 alert",
    "wrong version number",
    "ssl handshake",
)

# Only the start of a page is searched: a proxy's error page is small, with its
# <title> and <body> first, and a bounded scan keeps a hostile page cheap. The
# patterns use bounded repetition for the same reason.
_SCAN_CHARS = 64 * 1024
_TITLE_OPEN_RE = re.compile(r"<title(?:\s[^>]{0,256})?>", re.IGNORECASE)
_TITLE_CLOSE_RE = re.compile(r"</title\s{0,8}>", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def is_tls_message(text: str | None) -> bool:
    """Return True if *text* reports a TLS-layer failure (sign S1).

    Recognizes Chromium's ``net::ERR_CERT_*`` / ``net::ERR_SSL_*`` codes and
    the OpenSSL texts that httpx passes on (``[SSL: ...]``, ``certificate
    verify failed``, TLS alerts). Matching is case-insensitive; ``None`` and
    empty text are not TLS failures.
    """
    if not text:
        return False
    lowered = text.lower()
    return any(pattern in lowered for pattern in TLS_PATTERNS)


def detect_interference(
    headers: Mapping[str, Any] | None,
    html: str | None,
    status_code: int | None,
) -> Interference | None:
    """Return the sign of proxy interference a response shows, or None.

    S2 (a proxy error page) is recognized by the proxy's error header, or,
    when the header is absent, by the stock error page title together with
    an error status (>= 400) or an unknown one. A policy denial never
    counts as interference, whether recognized by its error code or by a
    refusal status (403, which also covers the administrator's own deny
    pages): the administrator meant it, so it must not be bypassed. A 403
    from the site itself carries no proxy marker, so it is not affected.
    S3 (a bot challenge) is recognized by its
    response header. Header names are matched case-insensitively; missing
    headers or HTML are treated as empty.
    """
    lowered = _lowercase_headers(headers)
    for page in PROXY_ERROR_PAGES:
        header_value = lowered.get(page.header, "").strip()
        if header_value:
            if _is_policy_denial(page, _error_code(header_value), status_code):
                return None
            return Interference(InterferenceSign.PROXY_ERROR_PAGE, header_value)
        found = _error_page_by_title(page, html, status_code)
        if found is not None:
            return None if _is_policy_denial(page, found.detail, status_code) else found
    for name, value in BOT_CHALLENGE_HEADERS:
        header_value = lowered.get(name, "").strip()
        if header_value.lower() == value:
            return Interference(InterferenceSign.BOT_CHALLENGE, header_value.lower())
    return None


def _lowercase_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Return *headers* with lowercase names, dropping headers without a value."""
    if not headers:
        return {}
    return {str(name).lower(): str(value) for name, value in headers.items() if value is not None}


def _is_policy_denial(page: ProxyErrorPage, code: str, status_code: int | None) -> bool:
    """Tell whether *page*'s error *code* or *status_code* marks a deliberate refusal."""
    return code in page.policy_denials or status_code in page.policy_statuses


def _error_code(header_value: str) -> str:
    """Return the error code of a header value such as ``ERR_ACCESS_DENIED 0``."""
    return header_value.split(maxsplit=1)[0].upper()


def _error_page_by_title(
    page: ProxyErrorPage, html: str | None, status_code: int | None
) -> Interference | None:
    """Recognize *page* by its stock title, for when the proxy hid its header.

    The detail is the error code found in the page, or else the title. An
    ordinary page may carry the same title, so a success status rules it out.
    """
    if not html or (status_code is not None and status_code < 400):
        return None
    head = html[:_SCAN_CHARS]
    title = _page_title(head)
    if title is None or title.casefold() != page.title.casefold():
        return None
    code = page.code_pattern.search(head)
    detail = code.group(1).upper() if code else title
    return Interference(InterferenceSign.PROXY_ERROR_PAGE, detail)


def _page_title(html: str) -> str | None:
    """Return the text of the first ``<title>`` of *html*, with whitespace collapsed."""
    opening = _TITLE_OPEN_RE.search(html)
    if opening is None:
        return None
    closing = _TITLE_CLOSE_RE.search(html, opening.end())
    if closing is None:
        return None
    text = html_lib.unescape(html[opening.end() : closing.start()])
    return _WHITESPACE_RE.sub(" ", text).strip()
