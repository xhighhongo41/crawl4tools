"""End-to-end checks of crawl4cli against real sites with a real browser."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from crawl4tools.cli.main import main

pytestmark = pytest.mark.integration


def _run(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, ["--timeout", "30", *args])
    return result.exit_code, result.stdout, result.stderr


def test_single_page_markdown_to_stdout() -> None:
    code, out, _ = _run("-q", "https://example.com/")
    assert code == 0
    assert "Example Domain" in out


def test_http_404_is_reported() -> None:
    code, out, err = _run("https://example.com/no-such-page-for-crawl4tools")
    assert code == 1
    assert out == ""
    assert "error: HTTP 404 Not Found" in err


def test_unknown_host_is_reported() -> None:
    code, _, err = _run("https://no-such-host.invalid/")
    assert code == 1
    assert "error: could not resolve host" in err


def test_connection_refused_is_reported() -> None:
    code, _, err = _run("http://127.0.0.1:59999/")
    assert code == 1
    assert "error: connection refused" in err


def test_pdf_is_transcribed() -> None:
    code, out, _ = _run("-q", "https://pdfobject.com/pdf/sample.pdf")
    assert code == 0
    assert out.strip() != ""


def test_several_urls_are_saved_to_directory(tmp_path: Path) -> None:
    code, out, err = _run(
        "-d",
        str(tmp_path),
        "https://example.com/",
        "https://example.com/no-such-page-for-crawl4tools",
    )
    assert code == 1
    assert out == ""
    assert (tmp_path / "example.com.md").read_text(encoding="utf-8").count("Example Domain") >= 1
    assert "done: 1 succeeded, 1 failed" in err


def test_fit_drops_navigation_but_keeps_the_article() -> None:
    url = "https://en.wikipedia.org/wiki/Web_crawler"
    code_full, full, _ = _run("-q", url)
    code_fit, fit, _ = _run("-q", "--fit", url)
    assert code_full == 0
    assert code_fit == 0
    assert "Main menu" in full
    assert "Main menu" not in fit
    assert "Software that systematically browses the World Wide Web" in fit
    assert len(fit) < len(full)
