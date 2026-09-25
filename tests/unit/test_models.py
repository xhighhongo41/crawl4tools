from crawl4tools.engine.errors import HttpStatusError
from crawl4tools.engine.models import (
    ContentKind,
    FailureKind,
    FetchOptions,
    FetchOutcome,
    Note,
    OutputFormat,
)


def test_output_format_values() -> None:
    assert OutputFormat.MARKDOWN.value == "markdown"
    assert OutputFormat.HTML.value == "html"
    assert OutputFormat.PDF.value == "pdf"
    assert OutputFormat.SCREENSHOT.value == "screenshot"
    assert OutputFormat.MHTML.value == "mhtml"
    assert OutputFormat.RAW.value == "raw"


def test_output_format_is_text() -> None:
    assert OutputFormat.MARKDOWN.is_text is True
    assert OutputFormat.HTML.is_text is True
    assert OutputFormat.MHTML.is_text is True
    assert OutputFormat.PDF.is_text is False
    assert OutputFormat.SCREENSHOT.is_text is False
    assert OutputFormat.RAW.is_text is False


def test_content_kind_values() -> None:
    assert ContentKind.HTML.value == "html"
    assert ContentKind.PDF.value == "pdf"
    assert ContentKind.BINARY.value == "binary"


def test_failure_kind_values() -> None:
    assert FailureKind.HTTP_STATUS.value == "http_status"
    assert FailureKind.NAME_RESOLUTION.value == "name_resolution"
    assert FailureKind.CONNECTION_REFUSED.value == "connection_refused"
    assert FailureKind.TIMEOUT.value == "timeout"
    assert FailureKind.PROXY.value == "proxy"
    assert FailureKind.TLS.value == "tls"
    assert FailureKind.BLOCKED.value == "blocked"
    assert FailureKind.BROWSER_NOT_INSTALLED.value == "browser_not_installed"
    assert FailureKind.NON_HTML.value == "non_html"
    assert FailureKind.OTHER.value == "other"


def test_fetch_options_defaults() -> None:
    options = FetchOptions()
    assert options.format is OutputFormat.MARKDOWN
    assert options.proxy is None
    assert options.fallback is True
    assert options.timeout_s == 60.0
    assert options.citations is False
    assert options.ignore_links is False
    assert options.ignore_images is False
    assert options.verbose is False


def test_fetch_options_is_frozen() -> None:
    options = FetchOptions()
    try:
        options.timeout_s = 5.0  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("FetchOptions should be frozen")


def test_fetch_outcome_defaults() -> None:
    outcome = FetchOutcome(url="https://example.com/", ok=True)
    assert outcome.final_url is None
    assert outcome.status_code is None
    assert outcome.content_kind is ContentKind.HTML
    assert outcome.content_type is None
    assert outcome.text is None
    assert outcome.data is None
    assert outcome.suggested_extension == ".md"
    assert outcome.error is None
    assert outcome.notes == []


def test_fetch_outcome_notes_default_is_independent_list() -> None:
    first = FetchOutcome(url="https://example.com/", ok=True)
    second = FetchOutcome(url="https://example.com/", ok=True)
    first.notes.append(Note("note"))
    assert second.notes == []


def test_fetch_outcome_can_carry_an_error() -> None:
    error = HttpStatusError("https://example.com/", 404)
    outcome = FetchOutcome(url="https://example.com/", ok=False, error=error)
    assert outcome.error is error
