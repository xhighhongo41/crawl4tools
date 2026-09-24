from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from crawl4tools.cli.output import payload_bytes, write_outcome, write_stdout
from crawl4tools.engine.models import FetchOutcome
from crawl4tools.engine.naming import NameAllocator


def text_outcome(url: str = "https://example.com/page", text: str = "# Hello") -> FetchOutcome:
    return FetchOutcome(url=url, ok=True, text=text, suggested_extension=".md")


def data_outcome(
    url: str = "https://example.com/image.jpg", data: bytes = b"\xff\xd8JPEG"
) -> FetchOutcome:
    return FetchOutcome(url=url, ok=True, data=data, suggested_extension=".jpg")


def test_payload_bytes_prefers_data_over_text() -> None:
    outcome = FetchOutcome(url="u", ok=True, text="text", data=b"binary")
    assert payload_bytes(outcome) == b"binary"


def test_payload_bytes_encodes_text_as_utf8() -> None:
    outcome = FetchOutcome(url="u", ok=True, text="héllo")
    assert payload_bytes(outcome) == "héllo".encode()


def test_payload_bytes_empty_when_neither_set() -> None:
    outcome = FetchOutcome(url="u", ok=True)
    assert payload_bytes(outcome) == b""


def test_write_stdout_appends_missing_newline() -> None:
    runner = CliRunner()
    with runner.isolation() as (out, _err, _mixed):
        write_stdout(text_outcome(text="no newline"))
        result = out.getvalue().decode()
    assert result == "no newline\n"


def test_write_stdout_does_not_duplicate_existing_newline() -> None:
    runner = CliRunner()
    with runner.isolation() as (out, _err, _mixed):
        write_stdout(text_outcome(text="already has one\n"))
        result = out.getvalue().decode()
    assert result == "already has one\n"


def test_write_outcome_to_stdout_returns_none(tmp_path: Path) -> None:
    outcome = text_outcome()
    allocator = NameAllocator(tmp_path)
    runner = CliRunner()
    with runner.isolation() as (out, _err, _mixed):
        result = write_outcome(
            outcome,
            outcome.url,
            output_path=None,
            directory=tmp_path,
            allocator=allocator,
            to_stdout=True,
        )
        printed = out.getvalue().decode()
    assert result is None
    assert printed == "# Hello\n"


def test_write_outcome_to_explicit_path(tmp_path: Path) -> None:
    outcome = text_outcome()
    target = tmp_path / "nested" / "out.md"
    allocator = NameAllocator(tmp_path)
    result = write_outcome(
        outcome,
        outcome.url,
        output_path=target,
        directory=tmp_path,
        allocator=allocator,
        to_stdout=False,
    )
    assert result == target
    assert target.read_text(encoding="utf-8") == "# Hello"


def test_write_outcome_to_explicit_path_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    target.write_text("stale", encoding="utf-8")
    outcome = text_outcome(text="fresh")
    allocator = NameAllocator(tmp_path)
    write_outcome(
        outcome,
        outcome.url,
        output_path=target,
        directory=tmp_path,
        allocator=allocator,
        to_stdout=False,
    )
    assert target.read_text(encoding="utf-8") == "fresh"


def test_write_outcome_to_directory_uses_filename_for(tmp_path: Path) -> None:
    outcome = data_outcome()
    directory = tmp_path / "downloads"
    allocator = NameAllocator(directory)
    result = write_outcome(
        outcome,
        outcome.url,
        output_path=None,
        directory=directory,
        allocator=allocator,
        to_stdout=False,
    )
    assert result is not None
    assert result.parent == directory
    assert result.suffix == ".jpg"
    assert result.read_bytes() == b"\xff\xd8JPEG"


def test_write_outcome_to_directory_collisions_get_suffixed(tmp_path: Path) -> None:
    directory = tmp_path / "downloads"
    allocator = NameAllocator(directory)
    first = write_outcome(
        text_outcome("https://example.com/page#a", "one"),
        "https://example.com/page#a",
        output_path=None,
        directory=directory,
        allocator=allocator,
        to_stdout=False,
    )
    second = write_outcome(
        text_outcome("https://example.com/page#b", "two"),
        "https://example.com/page#b",
        output_path=None,
        directory=directory,
        allocator=allocator,
        to_stdout=False,
    )
    assert first is not None and second is not None
    assert first != second
    assert first.name == "example.com_page.md"
    assert second.name == "example.com_page-2.md"
    assert first.read_text(encoding="utf-8") == "one"
    assert second.read_text(encoding="utf-8") == "two"


def test_write_outcome_overwrites_file_left_from_an_earlier_run(tmp_path: Path) -> None:
    directory = tmp_path / "downloads"
    directory.mkdir()
    existing = directory / "example.com_page.md"
    existing.write_text("old run", encoding="utf-8")
    allocator = NameAllocator(directory)
    result = write_outcome(
        text_outcome("https://example.com/page", "new run"),
        "https://example.com/page",
        output_path=None,
        directory=directory,
        allocator=allocator,
        to_stdout=False,
    )
    assert result == existing
    assert existing.read_text(encoding="utf-8") == "new run"


def test_write_outcome_raises_oserror_with_filename_set(tmp_path: Path) -> None:
    # A regular file sits where the target directory should be, so mkdir
    # fails deterministically with an OSError (no permission hacks needed).
    blocked = tmp_path / "blocked"
    blocked.write_text("I am a file, not a directory", encoding="utf-8")
    allocator = NameAllocator(blocked)
    outcome = text_outcome()
    with pytest.raises(OSError) as exc_info:
        write_outcome(
            outcome,
            outcome.url,
            output_path=None,
            directory=blocked,
            allocator=allocator,
            to_stdout=False,
        )
    assert exc_info.value.filename is not None
