from __future__ import annotations

from crawl4tools.cli.report import error_line, exit_code, note_line, summary_lines
from crawl4tools.engine.errors import HttpStatusError, NameResolutionError
from crawl4tools.engine.models import FetchOutcome, Note
from crawl4tools.i18n import ENGLISH, N_, get_translator

JA = get_translator("ja")


def test_error_line_uses_str_of_the_error() -> None:
    outcome = FetchOutcome(
        url="https://example.com/x",
        ok=False,
        status_code=404,
        error=HttpStatusError("https://example.com/x", 404),
    )
    assert error_line(outcome, ENGLISH) == "error: HTTP 404 Not Found: https://example.com/x"


def test_error_line_falls_back_when_error_is_none() -> None:
    outcome = FetchOutcome(url="https://example.com/x", ok=False, error=None)
    assert error_line(outcome, ENGLISH) == "error: fetch failed: https://example.com/x"


def test_error_line_name_resolution() -> None:
    outcome = FetchOutcome(
        url="https://bad.example/",
        ok=False,
        error=NameResolutionError("https://bad.example/"),
    )
    assert error_line(outcome, ENGLISH) == "error: could not resolve host: https://bad.example/"


def test_summary_lines_all_succeeded() -> None:
    assert summary_lines(3, [], ENGLISH) == ["done: 3 succeeded, 0 failed"]


def test_summary_lines_lists_each_failure() -> None:
    lines = summary_lines(2, ["https://a.example/", "https://b.example/"], ENGLISH)
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


# --- Japanese ------------------------------------------------------------------


def test_error_line_in_japanese_keeps_the_english_prefix() -> None:
    outcome = FetchOutcome(
        url="https://bad.example/",
        ok=False,
        error=NameResolutionError("https://bad.example/"),
    )
    assert error_line(outcome, JA) == "error: ホスト名を解決できません: https://bad.example/"


def test_error_line_in_japanese_keeps_the_http_reason() -> None:
    outcome = FetchOutcome(
        url="https://example.com/x",
        ok=False,
        status_code=404,
        error=HttpStatusError("https://example.com/x", 404),
    )
    assert error_line(outcome, JA) == "error: HTTP 404 Not Found: https://example.com/x"


def test_error_line_in_japanese_falls_back_when_error_is_none() -> None:
    outcome = FetchOutcome(url="https://example.com/x", ok=False, error=None)
    assert error_line(outcome, JA) == "error: 取得に失敗しました: https://example.com/x"


# --- note_line -------------------------------------------------------------------

KEPT_NOTHING = Note(N_("content filter kept nothing; saved the full page"))


def test_note_line_in_english() -> None:
    assert note_line("https://example.com/", KEPT_NOTHING, ENGLISH) == (
        "note: https://example.com/: content filter kept nothing; saved the full page"
    )


def test_note_line_in_japanese_keeps_the_english_prefix() -> None:
    assert note_line("https://example.com/", KEPT_NOTHING, JA) == (
        "note: https://example.com/: "
        "コンテンツフィルタで何も残りませんでした。ページ全体を保存しました"
    )


def test_note_line_renders_a_quoted_error_in_the_same_language() -> None:
    note = Note(
        N_("proxy failed ({error}); retried with a direct connection"),
        {"error": NameResolutionError("https://bad.example/")},
    )
    assert note_line("https://example.com/", note, JA) == (
        "note: https://example.com/: "
        "プロキシ経由で失敗しました(ホスト名を解決できません: https://bad.example/)。"
        "直接接続で再試行しました"
    )


# --- summary_lines in Japanese ----------------------------------------------------


def test_summary_lines_in_japanese() -> None:
    lines = summary_lines(2, ["https://a.example/", "https://b.example/"], JA)
    assert lines == [
        "done: 成功 2 件、失敗 2 件",
        "  failed: https://a.example/",
        "  failed: https://b.example/",
    ]


def test_summary_lines_in_japanese_all_succeeded() -> None:
    assert summary_lines(3, [], JA) == ["done: 成功 3 件、失敗 0 件"]
