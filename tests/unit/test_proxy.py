import pytest

from crawl4tools.engine.models import FailureKind, FetchOptions
from crawl4tools.engine.proxy import (
    FALLBACK_KINDS,
    FALLBACK_STATUS_CODES,
    SUPPORTED_SCHEMES,
    is_socks,
    normalize_proxy,
    redact_proxy,
    should_fallback,
)

URL = "https://example.com/page"


def test_supported_schemes_and_fallback_sets() -> None:
    assert SUPPORTED_SCHEMES == ("http", "https", "socks5")
    assert FALLBACK_STATUS_CODES == frozenset({407, 502, 503, 504})
    assert FALLBACK_KINDS == frozenset(
        {
            FailureKind.PROXY,
            FailureKind.CONNECTION_REFUSED,
            FailureKind.TIMEOUT,
            FailureKind.TLS,
            FailureKind.BLOCKED,
        }
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("proxy.example:8080", "http://proxy.example:8080"),
        ("http://proxy.example:8080", "http://proxy.example:8080"),
        ("HTTP://proxy.example:8080", "http://proxy.example:8080"),
        ("https://proxy.example:443", "https://proxy.example:443"),
        ("socks5://proxy.example:1080", "socks5://proxy.example:1080"),
        ("  http://proxy.example:8080  ", "http://proxy.example:8080"),
        (
            "http://user:pass@proxy.example:8080",
            "http://user:pass@proxy.example:8080",
        ),
    ],
    ids=[
        "no-scheme-defaults-http",
        "http-scheme",
        "uppercase-scheme-lowercased",
        "https-scheme",
        "socks5-scheme",
        "strips-whitespace",
        "keeps-credentials",
    ],
)
def test_normalize_proxy_valid(value: str, expected: str) -> None:
    assert normalize_proxy(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "ftp://proxy.example:8080",
        "http://:8080",
        "http://proxy.example",
        "http://proxy.example:not-a-port",
        "http://proxy.example:99999999",
    ],
    ids=[
        "unsupported-scheme",
        "missing-host",
        "missing-port",
        "invalid-port",
        "out-of-range-port",
    ],
)
def test_normalize_proxy_invalid_raises_value_error(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_proxy(value)


def test_normalize_proxy_error_message_never_leaks_credentials() -> None:
    with pytest.raises(ValueError) as exc_info:
        normalize_proxy("ftp://user:s3cr3t@proxy.example:8080")
    assert "s3cr3t" not in str(exc_info.value)
    assert "user" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://user:pw@h:8080", "http://***@h:8080"),
        ("http://user@h:8080", "http://***@h:8080"),
        ("user:pw@h:8080", "***@h:8080"),
        ("http://h:8080", "http://h:8080"),
        ("h:8080", "h:8080"),
        ("socks5://user:pw@h:1080", "socks5://***@h:1080"),
    ],
    ids=[
        "scheme-user-and-password",
        "scheme-user-only",
        "no-scheme-with-credentials",
        "no-credentials-with-scheme",
        "no-credentials-no-scheme",
        "socks-with-credentials",
    ],
)
def test_redact_proxy(value: str, expected: str) -> None:
    assert redact_proxy(value) == expected


def test_redact_proxy_never_leaks_password_for_typical_proxy_url() -> None:
    redacted = redact_proxy("http://alice:hunter2@proxy.example:3128")
    assert "hunter2" not in redacted
    assert "alice" not in redacted


@pytest.mark.parametrize(
    ("proxy", "expected"),
    [
        ("socks5://proxy.example:1080", True),
        ("http://proxy.example:8080", False),
        ("https://proxy.example:8080", False),
    ],
)
def test_is_socks(proxy: str, expected: bool) -> None:
    assert is_socks(proxy) is expected


def _options(
    *, proxy: str | None = "http://proxy.example:8080", fallback: bool = True
) -> FetchOptions:
    return FetchOptions(proxy=proxy, fallback=fallback)


@pytest.mark.parametrize(
    ("kind", "status_code", "expected"),
    [
        (FailureKind.HTTP_STATUS, 404, False),
        (FailureKind.HTTP_STATUS, 407, True),
        (FailureKind.HTTP_STATUS, 502, True),
        (FailureKind.HTTP_STATUS, 503, True),
        (FailureKind.HTTP_STATUS, 504, True),
        (FailureKind.PROXY, None, True),
        (FailureKind.CONNECTION_REFUSED, None, True),
        (FailureKind.TIMEOUT, None, True),
        (FailureKind.TLS, None, True),
        (FailureKind.BLOCKED, 403, True),
        (FailureKind.NAME_RESOLUTION, None, False),
        (FailureKind.OTHER, None, False),
        (None, None, False),
    ],
    ids=[
        "http-404-no-fallback",
        "http-407-fallback",
        "http-502-fallback",
        "http-503-fallback",
        "http-504-fallback",
        "proxy-kind-fallback",
        "connection-refused-fallback",
        "timeout-fallback",
        "tls-fallback",
        "blocked-fallback",
        "name-resolution-no-fallback",
        "other-no-fallback",
        "none-kind-no-fallback",
    ],
)
def test_should_fallback_truth_table(
    kind: FailureKind | None, status_code: int | None, expected: bool
) -> None:
    result = should_fallback(
        options=_options(),
        kind=kind,
        status_code=status_code,
        already_retried=False,
    )
    assert result is expected


@pytest.mark.parametrize("kind", [FailureKind.TLS, FailureKind.BLOCKED])
def test_should_fallback_new_kinds_need_a_proxy_and_fallback(kind: FailureKind) -> None:
    for options in (_options(proxy=None), _options(fallback=False)):
        assert not should_fallback(
            options=options, kind=kind, status_code=403, already_retried=False
        )
    assert not should_fallback(options=_options(), kind=kind, status_code=403, already_retried=True)


def test_should_fallback_false_when_proxy_not_set() -> None:
    result = should_fallback(
        options=_options(proxy=None),
        kind=FailureKind.PROXY,
        status_code=None,
        already_retried=False,
    )
    assert result is False


def test_should_fallback_false_when_fallback_disabled() -> None:
    result = should_fallback(
        options=_options(fallback=False),
        kind=FailureKind.PROXY,
        status_code=None,
        already_retried=False,
    )
    assert result is False


def test_should_fallback_false_when_already_retried() -> None:
    result = should_fallback(
        options=_options(),
        kind=FailureKind.PROXY,
        status_code=None,
        already_retried=True,
    )
    assert result is False


def test_redact_proxy_password_containing_at_sign() -> None:
    redacted = redact_proxy("http://user:p@ss@proxy.local:8080")
    assert redacted == "http://***@proxy.local:8080"
