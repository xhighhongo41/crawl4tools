from __future__ import annotations

import base64
from pathlib import Path

import pytest
from mcp.types import ImageContent, TextContent

from crawl4tools.engine.errors import HttpStatusError, NameResolutionError
from crawl4tools.engine.models import ContentKind, FetchOutcome, Note
from crawl4tools.server.results import (
    check_urls,
    download_lines,
    download_record,
    page_blocks,
    page_meta,
    resolve_directory,
)

# --- check_urls -------------------------------------------------------------


def test_check_urls_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one URL is required"):
        check_urls([], max_urls=10)


def test_check_urls_reports_all_invalid_urls() -> None:
    with pytest.raises(ValueError, match=r"invalid URL\(s\): not-a-url, ftp://x") as exc_info:
        check_urls(
            ["not-a-url", "https://good.example/", "ftp://x"],
            max_urls=10,
        )
    assert "not-a-url" in str(exc_info.value)
    assert "ftp://x" in str(exc_info.value)


def test_check_urls_dedupes_and_returns_duplicates() -> None:
    unique, duplicates = check_urls(
        ["https://a.example/", "https://a.example/", "https://b.example/"],
        max_urls=10,
    )
    assert unique == ["https://a.example/", "https://b.example/"]
    assert duplicates == ["https://a.example/"]


def test_check_urls_no_duplicates() -> None:
    unique, duplicates = check_urls(["https://a.example/"], max_urls=10)
    assert unique == ["https://a.example/"]
    assert duplicates == []


def test_check_urls_rejects_too_many() -> None:
    with pytest.raises(ValueError, match="too many URLs: 2 given, at most 1 per call"):
        check_urls(["https://a.example/", "https://b.example/"], max_urls=1)


def test_check_urls_strips_whitespace_via_validate_url() -> None:
    unique, _duplicates = check_urls(["  https://a.example/  "], max_urls=10)
    assert unique == ["https://a.example/"]


# --- resolve_directory -------------------------------------------------------


def test_resolve_directory_none_returns_root(tmp_path: Path) -> None:
    assert resolve_directory(tmp_path, None) == tmp_path.resolve()


def test_resolve_directory_empty_string_returns_root(tmp_path: Path) -> None:
    assert resolve_directory(tmp_path, "") == tmp_path.resolve()


def test_resolve_directory_relative_inside_root(tmp_path: Path) -> None:
    assert resolve_directory(tmp_path, "sub/dir") == (tmp_path / "sub" / "dir").resolve()


def test_resolve_directory_rejects_absolute_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_directory(tmp_path, "/etc")


def test_resolve_directory_rejects_escaping_with_dotdot(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="download root"):
        resolve_directory(tmp_path, "../escaped")


def test_resolve_directory_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="download root"):
        resolve_directory(root, "escape")


def test_resolve_directory_does_not_create_directories(tmp_path: Path) -> None:
    resolve_directory(tmp_path, "not/created/yet")
    assert not (tmp_path / "not").exists()


# --- page_blocks --------------------------------------------------------------


def _ok_text_outcome(**overrides: object) -> FetchOutcome:
    defaults: dict[str, object] = dict(
        url="https://example.com/",
        ok=True,
        final_url="https://example.com/",
        status_code=200,
        content_kind=ContentKind.HTML,
        content_type="text/html; charset=utf-8",
        text="# Hello",
    )
    defaults.update(overrides)
    return FetchOutcome(**defaults)  # type: ignore[arg-type]


def test_page_blocks_text_single_no_header_when_no_notes() -> None:
    outcome = _ok_text_outcome()
    blocks = page_blocks(outcome, outcome.url, multiple=False)
    assert blocks == [TextContent(type="text", text="# Hello")]


def test_page_blocks_text_multiple_adds_url_status_header() -> None:
    outcome = _ok_text_outcome()
    blocks = page_blocks(outcome, outcome.url, multiple=True)
    assert len(blocks) == 1
    block = blocks[0]
    assert isinstance(block, TextContent)
    assert block.text == ("<!-- crawl4tools: url=https://example.com/ status=200 -->\n# Hello")


def test_page_blocks_text_multiple_status_dash_when_missing() -> None:
    outcome = _ok_text_outcome(status_code=None)
    blocks = page_blocks(outcome, outcome.url, multiple=True)
    block = blocks[0]
    assert isinstance(block, TextContent)
    assert block.text.startswith("<!-- crawl4tools: url=https://example.com/ status=- -->\n")


def test_page_blocks_notes_included_when_single() -> None:
    outcome = _ok_text_outcome(notes=[Note("saved the full page")])
    blocks = page_blocks(outcome, outcome.url, multiple=False)
    block = blocks[0]
    assert isinstance(block, TextContent)
    assert block.text == "<!-- note: saved the full page -->\n# Hello"


def test_page_blocks_notes_and_header_combined_when_multiple() -> None:
    outcome = _ok_text_outcome(notes=[Note("note one"), Note("note two")])
    blocks = page_blocks(outcome, outcome.url, multiple=True)
    block = blocks[0]
    assert isinstance(block, TextContent)
    assert block.text == (
        "<!-- crawl4tools: url=https://example.com/ status=200 -->\n"
        "<!-- note: note one -->\n"
        "<!-- note: note two -->\n"
        "# Hello"
    )


def test_page_blocks_failure_uses_error_line() -> None:
    outcome = FetchOutcome(
        url="https://bad.example/",
        ok=False,
        error=HttpStatusError("https://bad.example/", 404),
    )
    blocks = page_blocks(outcome, outcome.url, multiple=False)
    assert blocks == [
        TextContent(type="text", text="error: HTTP 404 Not Found: https://bad.example/")
    ]


def test_page_blocks_failure_with_header_when_multiple() -> None:
    outcome = FetchOutcome(
        url="https://bad.example/",
        ok=False,
        status_code=None,
        error=NameResolutionError("https://bad.example/"),
    )
    blocks = page_blocks(outcome, outcome.url, multiple=True)
    block = blocks[0]
    assert isinstance(block, TextContent)
    assert block.text == (
        "<!-- crawl4tools: url=https://bad.example/ status=- -->\n"
        "error: could not resolve host: https://bad.example/"
    )


def test_page_blocks_failure_without_error_object() -> None:
    outcome = FetchOutcome(url="https://bad.example/", ok=False, error=None)
    blocks = page_blocks(outcome, outcome.url, multiple=False)
    assert blocks == [TextContent(type="text", text="error: fetch failed: https://bad.example/")]


def test_page_blocks_screenshot_returns_header_text_then_image() -> None:
    payload = b"\x89PNG\r\n fake"
    outcome = FetchOutcome(
        url="https://example.com/",
        ok=True,
        status_code=200,
        content_kind=ContentKind.HTML,
        content_type="text/html; charset=utf-8",
        data=payload,
        notes=[Note("a note")],
    )
    blocks = page_blocks(outcome, outcome.url, multiple=True)
    assert len(blocks) == 2
    header_block, image_block = blocks
    assert isinstance(header_block, TextContent)
    assert header_block.text == (
        "<!-- crawl4tools: url=https://example.com/ status=200 -->\n<!-- note: a note -->"
    )
    assert isinstance(image_block, ImageContent)
    assert image_block.mime_type == "image/png"
    assert base64.b64decode(image_block.data) == payload


def test_page_blocks_screenshot_no_header_text_block_when_no_header() -> None:
    payload = b"pngbytes"
    outcome = FetchOutcome(
        url="https://example.com/",
        ok=True,
        content_kind=ContentKind.HTML,
        data=payload,
    )
    blocks = page_blocks(outcome, outcome.url, multiple=False)
    assert len(blocks) == 1
    assert isinstance(blocks[0], ImageContent)
    assert base64.b64decode(blocks[0].data) == payload


def test_page_blocks_binary_image_media_type() -> None:
    payload = b"\xff\xd8\xff jpegbytes"
    outcome = FetchOutcome(
        url="https://example.com/photo.jpg",
        ok=True,
        content_kind=ContentKind.BINARY,
        content_type="image/jpeg; charset=binary",
        data=payload,
    )
    blocks = page_blocks(outcome, outcome.url, multiple=False)
    assert len(blocks) == 1
    image_block = blocks[0]
    assert isinstance(image_block, ImageContent)
    assert image_block.mime_type == "image/jpeg"
    assert base64.b64decode(image_block.data) == payload


def test_page_blocks_non_image_binary_reports_not_included() -> None:
    payload = b"%PDF-1.4 ..."
    outcome = FetchOutcome(
        url="https://example.com/doc.pdf",
        ok=True,
        content_kind=ContentKind.PDF,
        content_type="application/pdf",
        data=payload,
    )
    blocks = page_blocks(outcome, outcome.url, multiple=False)
    assert blocks == [
        TextContent(
            type="text",
            text=(
                "binary content (application/pdf, "
                f"{len(payload)} bytes) was not included; use the download tool "
                "to save it: https://example.com/doc.pdf"
            ),
        )
    ]


def test_page_blocks_binary_unknown_content_type() -> None:
    payload = b"binarydata"
    outcome = FetchOutcome(
        url="https://example.com/file.bin",
        ok=True,
        content_kind=ContentKind.BINARY,
        content_type=None,
        data=payload,
    )
    blocks = page_blocks(outcome, outcome.url, multiple=False)
    block = blocks[0]
    assert isinstance(block, TextContent)
    assert "unknown type" in block.text
    assert f"{len(payload)} bytes" in block.text


# --- page_meta ----------------------------------------------------------------


def test_page_meta_success() -> None:
    outcome = _ok_text_outcome(notes=[Note("a note")])
    meta = page_meta(outcome, outcome.url)
    assert meta == {
        "url": "https://example.com/",
        "final_url": "https://example.com/",
        "title": None,
        "ok": True,
        "status_code": 200,
        "content_kind": "html",
        "content_type": "text/html; charset=utf-8",
        "chars": len("# Hello"),
        "bytes": None,
        "error": None,
        "notes": ["a note"],
    }


def test_page_meta_notes_are_english_strings() -> None:
    note = Note("PDF saved as-is (format '{format}' does not apply)", {"format": "html"})
    meta = page_meta(_ok_text_outcome(notes=[note]), "https://example.com/")
    assert meta["notes"] == ["PDF saved as-is (format 'html' does not apply)"]


def test_page_meta_includes_title() -> None:
    outcome = _ok_text_outcome(title="Example Domain")
    meta = page_meta(outcome, outcome.url)
    assert meta["title"] == "Example Domain"


def test_page_meta_failure() -> None:
    outcome = FetchOutcome(
        url="https://bad.example/",
        ok=False,
        error=HttpStatusError("https://bad.example/", 404),
    )
    meta = page_meta(outcome, outcome.url)
    assert meta["ok"] is False
    assert meta["error"] == "HTTP 404 Not Found: https://bad.example/"
    assert meta["chars"] is None
    assert meta["bytes"] is None


def test_page_meta_binary_reports_bytes() -> None:
    outcome = FetchOutcome(
        url="https://example.com/x.bin",
        ok=True,
        content_kind=ContentKind.BINARY,
        data=b"12345",
    )
    meta = page_meta(outcome, outcome.url)
    assert meta["bytes"] == 5
    assert meta["chars"] is None


def test_page_meta_notes_is_a_copy() -> None:
    outcome = _ok_text_outcome()
    meta = page_meta(outcome, outcome.url)
    notes = meta["notes"]
    assert isinstance(notes, list)
    notes.append("mutated")
    assert outcome.notes == []


# --- download_record / download_lines ------------------------------------------


def test_download_record_success_with_text() -> None:
    outcome = _ok_text_outcome(notes=[Note("kept full page")])
    path = Path("/downloads/example.md")
    record = download_record(outcome, outcome.url, path)
    assert record == {
        "url": "https://example.com/",
        "ok": True,
        "path": str(path),
        "bytes": len(b"# Hello"),
        "content_kind": "html",
        "content_type": "text/html; charset=utf-8",
        "status_code": 200,
        "error": None,
        "notes": ["kept full page"],
    }


def test_download_record_notes_are_english_strings() -> None:
    note = Note("content filter failed ({reason}); saved the full page", {"reason": "boom"})
    outcome = _ok_text_outcome(notes=[note])
    record = download_record(outcome, outcome.url, Path("/d/e.md"))
    assert record["notes"] == ["content filter failed (boom); saved the full page"]


def test_download_record_success_with_binary_data() -> None:
    payload = b"binarydata"
    outcome = FetchOutcome(
        url="https://example.com/x.bin",
        ok=True,
        content_kind=ContentKind.BINARY,
        content_type="application/octet-stream",
        data=payload,
    )
    path = Path("/downloads/x.bin")
    record = download_record(outcome, outcome.url, path)
    assert record["bytes"] == len(payload)
    assert record["ok"] is True


def test_download_record_not_ok_when_path_is_none() -> None:
    outcome = _ok_text_outcome()
    record = download_record(outcome, outcome.url, None)
    assert record["ok"] is False
    assert record["path"] is None
    assert record["bytes"] is None


def test_download_record_not_ok_when_outcome_failed() -> None:
    outcome = FetchOutcome(
        url="https://bad.example/",
        ok=False,
        error=HttpStatusError("https://bad.example/", 500),
    )
    record = download_record(outcome, outcome.url, None)
    assert record["ok"] is False
    assert record["error"] == "HTTP 500 Internal Server Error: https://bad.example/"
    assert record["bytes"] is None


def test_download_record_explicit_error_overrides_outcome_error() -> None:
    outcome = _ok_text_outcome()
    record = download_record(outcome, outcome.url, None, error="disk full while writing file")
    assert record["ok"] is False
    assert record["error"] == "disk full while writing file"


def test_download_lines_success_no_notes() -> None:
    record = download_record(_ok_text_outcome(), "https://example.com/", Path("/d/e.md"))
    lines = download_lines(record)
    assert lines == [f"saved: https://example.com/ -> /d/e.md ({len(b'# Hello')} bytes)"]


def test_download_lines_success_with_notes() -> None:
    outcome = _ok_text_outcome(notes=[Note("saved the full page")])
    record = download_record(outcome, "https://example.com/", Path("/d/e.md"))
    lines = download_lines(record)
    assert lines[0] == "note: https://example.com/: saved the full page"
    assert lines[1].startswith("saved: https://example.com/ -> /d/e.md")


def test_download_lines_error() -> None:
    outcome = FetchOutcome(
        url="https://bad.example/",
        ok=False,
        error=HttpStatusError("https://bad.example/", 404),
    )
    record = download_record(outcome, outcome.url, None)
    lines = download_lines(record)
    assert lines == ["error: HTTP 404 Not Found: https://bad.example/"]


def test_download_lines_error_fallback_when_no_error_message() -> None:
    outcome = FetchOutcome(url="https://bad.example/", ok=False, error=None)
    record = download_record(outcome, outcome.url, None)
    lines = download_lines(record)
    assert lines == ["error: fetch failed: https://bad.example/"]
