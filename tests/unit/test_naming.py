import hashlib
from pathlib import Path

import pytest

from crawl4tools.engine.models import OutputFormat
from crawl4tools.engine.naming import (
    NameAllocator,
    dedupe_urls,
    extension_for,
    filename_for,
    validate_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://example.com/path?query=1",
        "  https://example.com/  ",
    ],
)
def test_validate_url_accepts_http_and_https(url: str) -> None:
    assert validate_url(url) == url.strip()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "example.com/path",
        "not a url",
        "",
        "http://",
    ],
)
def test_validate_url_rejects_non_http_urls(url: str) -> None:
    with pytest.raises(ValueError, match="not an http"):
        validate_url(url)


def test_dedupe_urls_preserves_first_seen_order() -> None:
    urls = ["a", "b", "a", "c", "b", "b"]
    unique, duplicates = dedupe_urls(urls)
    assert unique == ["a", "b", "c"]
    assert duplicates == ["a", "b", "b"]


def test_dedupe_urls_no_duplicates() -> None:
    unique, duplicates = dedupe_urls(["a", "b", "c"])
    assert unique == ["a", "b", "c"]
    assert duplicates == []


def test_dedupe_urls_empty() -> None:
    unique, duplicates = dedupe_urls([])
    assert unique == []
    assert duplicates == []


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        (OutputFormat.MARKDOWN, ".md"),
        (OutputFormat.HTML, ".html"),
        (OutputFormat.PDF, ".pdf"),
        (OutputFormat.SCREENSHOT, ".png"),
        (OutputFormat.MHTML, ".mhtml"),
    ],
)
def test_extension_for_fixed_formats(fmt: OutputFormat, expected: str) -> None:
    assert extension_for(fmt) == expected


@pytest.mark.parametrize(
    ("content_type", "url", "expected"),
    [
        ("image/jpeg", None, ".jpg"),
        ("image/jpeg; charset=binary", None, ".jpg"),
        ("text/html", None, ".html"),
        ("text/html; charset=utf-8", None, ".html"),
        ("text/plain", None, ".txt"),
        ("application/pdf", None, ".pdf"),
        ("application/zip", None, ".zip"),
        (None, "https://example.com/archive.tar.gz", ".gz"),
        (None, "https://example.com/report.CSV", ".CSV"),
        (None, "https://example.com/no-extension-here", ".bin"),
        (None, "https://example.com/path.toolongextension", ".bin"),
        (None, None, ".bin"),
        ("application/unknown-nonsense-type", None, ".bin"),
    ],
    ids=[
        "image-jpeg",
        "image-jpeg-with-params",
        "text-html",
        "text-html-with-charset",
        "text-plain",
        "application-pdf",
        "application-zip-guessed",
        "url-suffix-gz",
        "url-suffix-preserves-case",
        "url-no-suffix",
        "url-suffix-too-long",
        "no-content-type-no-url",
        "unknown-content-type-no-url",
    ],
)
def test_extension_for_raw(content_type: str | None, url: str | None, expected: str) -> None:
    assert extension_for(OutputFormat.RAW, content_type=content_type, url=url) == expected


@pytest.mark.parametrize(
    ("url", "extension", "expected"),
    [
        ("https://example.com/", ".md", "example.com.md"),
        ("https://example.com/a/b/", ".md", "example.com_a_b.md"),
        ("https://Example.com:8443/x?q=1", ".html", "example.com_8443_x_q=1.html"),
        ("https://example.com/dir/a.pdf", ".pdf", "example.com_dir_a.pdf"),
    ],
    ids=["root", "nested-path", "port-and-query", "strip-duplicate-suffix"],
)
def test_filename_for_examples(url: str, extension: str, expected: str) -> None:
    assert filename_for(url, extension) == expected


def test_filename_for_percent_decodes_non_ascii_path() -> None:
    url = "https://example.com/%E6%97%A5%E6%9C%AC%E8%AA%9E"
    assert filename_for(url, ".md") == "example.com_日本語.md"


def test_filename_for_sanitizes_dangerous_characters() -> None:
    url = "https://example.com/%3Aweird%3Fname%2A"
    result = filename_for(url, ".md")
    for char in '/\\:*?"<>|':
        assert char not in result


def test_filename_for_collapses_repeated_underscores() -> None:
    url = "https://example.com/a//b"
    result = filename_for(url, ".md")
    assert "__" not in result


def test_filename_for_truncates_long_stem_and_stays_under_255_bytes() -> None:
    long_segment = "a" * 400
    url = f"https://example.com/{long_segment}"
    result = filename_for(url, ".md")

    assert len(result.encode("utf-8")) <= 255
    stem = result[: -len(".md")]
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    assert stem.endswith(f"-{digest}")
    assert len(stem.encode("utf-8")) <= 200 + 1 + 8


def test_filename_for_truncation_does_not_split_multibyte_char() -> None:
    # Many multi-byte (3-byte, UTF-8) characters to force a truncation that
    # must not land in the middle of a character's byte sequence.
    long_segment = "あ" * 200
    url = f"https://example.com/{long_segment}"
    result = filename_for(url, ".md")

    # Decoding must succeed without error; garbage split bytes would raise.
    result.encode("utf-8").decode("utf-8")
    assert len(result.encode("utf-8")) <= 255


def test_name_allocator_returns_directory_joined_path(tmp_path: Path) -> None:
    allocator = NameAllocator(tmp_path)
    path = allocator.allocate("example.com.md")
    assert path == tmp_path / "example.com.md"


def test_name_allocator_dedupes_within_a_run(tmp_path: Path) -> None:
    allocator = NameAllocator(tmp_path)
    first = allocator.allocate("example.com.md")
    second = allocator.allocate("example.com.md")
    third = allocator.allocate("example.com.md")

    assert first == tmp_path / "example.com.md"
    assert second == tmp_path / "example.com-2.md"
    assert third == tmp_path / "example.com-3.md"


def test_name_allocator_does_not_check_disk(tmp_path: Path) -> None:
    existing = tmp_path / "example.com.md"
    existing.write_text("already here", encoding="utf-8")

    allocator = NameAllocator(tmp_path)
    path = allocator.allocate("example.com.md")

    # The allocator only tracks names it has handed out itself, not the disk.
    assert path == existing


def test_name_allocator_tracks_different_names_independently(tmp_path: Path) -> None:
    allocator = NameAllocator(tmp_path)
    a1 = allocator.allocate("a.md")
    b1 = allocator.allocate("b.md")
    a2 = allocator.allocate("a.md")

    assert a1 == tmp_path / "a.md"
    assert b1 == tmp_path / "b.md"
    assert a2 == tmp_path / "a-2.md"


def test_name_allocator_skips_names_that_collide_with_generated_suffixes(tmp_path: Path) -> None:
    allocator = NameAllocator(tmp_path)
    assert allocator.allocate("a.md") == tmp_path / "a.md"
    assert allocator.allocate("a.md") == tmp_path / "a-2.md"
    assert allocator.allocate("a-2.md") == tmp_path / "a-2-2.md"
    assert allocator.allocate("a.md") == tmp_path / "a-3.md"
