from __future__ import annotations

from crawl4tools.cli.report import error_line, exit_code, summary_lines
from crawl4tools.engine.errors import HttpStatusError, NameResolutionError
from crawl4tools.engine.models import FetchOutcome


def test_error_line_uses_str_of_the_error() -> None:
    outcome = FetchOutcome(
        url="https://example.com/x",
        ok=False,
        status_code=404,
        error=HttpStatusError("https://example.com/x", 404),
    )
    assert error_line(outcome) == "error: HTTP 404 Not Found: https://example.com/x"


def test_error_line_falls_back_when_error_is_none() -> None:
    outcome = FetchOutcome(url="https://example.com/x", ok=False, error=None)
    assert error_line(outcome) == "error: fetch failed: https://example.com/x"


def test_error_line_name_resolution() -> None:
    outcome = FetchOutcome(
        url="https://bad.example/",
        ok=False,
        error=NameResolutionError("https://bad.example/"),
    )
    assert error_line(outcome) == "error: could not resolve host: https://bad.example/"


def test_summary_lines_all_succeeded() -> None:
    assert summary_lines(3, []) == ["done: 3 succeeded, 0 failed"]


def test_summary_lines_lists_each_failure() -> None:
    lines = summary_lines(2, ["https://a.example/", "https://b.example/"])
    assert lines == [
        "done: 2 succeeded, 2 failed",
        "  failed: https://a.example/",
        "  failed: https://b.example/",
    ]


def test_exit_code_zero_when_no_failures() -> None:
    assert exit_code(0) == 0


def test_exit_code_one_when_any_failure() -> None:
    assert exit_code(1) == 1
    assert exit_code(5) == 1
