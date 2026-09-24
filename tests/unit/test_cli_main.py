from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner
from conftest import FakeCrawler, FakeHttp, make_result

from crawl4tools.cli import main as main_module
from crawl4tools.engine.fetcher import Fetcher
from crawl4tools.engine.models import FetchOptions

URL = "https://example.com/"


def install_fetcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    crawler: FakeCrawler | None = None,
    http: FakeHttp | None = None,
) -> tuple[FakeCrawler, FakeHttp]:
    """Replace create_fetcher() so it wires in fake crawler/HTTP factories."""
    crawler = crawler if crawler is not None else FakeCrawler()
    http = http if http is not None else FakeHttp()

    def create_fetcher(options: FetchOptions) -> Fetcher:
        return Fetcher(options, crawler_factory=crawler.factory, http_client_factory=http)

    monkeypatch.setattr(main_module, "create_fetcher", create_fetcher)
    return crawler, http


def invoke(args: list[str], **kwargs: Any) -> Any:
    return CliRunner().invoke(main_module.main, args, **kwargs)


def response(content_type: str, content: bytes, status: int = 200) -> httpx.Response:
    return httpx.Response(status, headers={"content-type": content_type}, content=content)


# --- happy paths -------------------------------------------------------------


def test_single_url_markdown_to_stdout_only_in_quiet_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL, "-q"])
    assert result.exit_code == 0
    assert result.stdout == "# Hello\n"
    assert result.stderr == ""


def test_single_url_markdown_to_stdout_without_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL])
    assert result.exit_code == 0
    assert result.stdout == "# Hello\n"
    # Nothing goes to stdout besides the document itself.
    assert "saved:" not in result.stdout


def test_single_url_with_output_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    install_fetcher(monkeypatch)
    target = tmp_path / "out.md"
    result = invoke([URL, "-o", str(target)])
    assert result.exit_code == 0
    assert result.stdout == ""
    assert target.read_text(encoding="utf-8") == "# Hello"
    assert f"saved: {URL} -> {target}" in result.stderr


@pytest.mark.parametrize(
    ("fmt", "overrides", "expected", "ext", "via_output_file"),
    [
        ("html", {}, b"<html><body><h1>Hello</h1></body></html>", ".html", True),
        ("mhtml", {"mhtml": "MIME-Version: 1.0"}, b"MIME-Version: 1.0", ".mhtml", True),
        ("pdf", {"pdf": b"%PDF-bytes"}, b"%PDF-bytes", ".pdf", False),
        (
            "screenshot",
            {"screenshot": base64.b64encode(b"\x89PNG-data").decode()},
            b"\x89PNG-data",
            ".png",
            False,
        ),
        ("raw", {"html": "<html>raw</html>"}, b"<html>raw</html>", ".html", False),
    ],
)
def test_formats_saved_with_right_extension_and_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fmt: str,
    overrides: dict[str, Any],
    expected: bytes,
    ext: str,
    via_output_file: bool,
) -> None:
    crawler = FakeCrawler(make_result(**overrides))
    install_fetcher(monkeypatch, crawler=crawler)
    if via_output_file:
        target = tmp_path / f"out{ext}"
        args = [URL, "--format", fmt, "-o", str(target)]
    else:
        args = [URL, "--format", fmt, "-d", str(tmp_path)]
    result = invoke(args)
    assert result.exit_code == 0, result.stderr

    if via_output_file:
        assert target.read_bytes() == expected
    else:
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].suffix == ext
        assert files[0].read_bytes() == expected


def test_three_urls_one_404_saves_two_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    urls = [f"https://example.com/{i}" for i in range(3)]
    handler = {
        urls[0]: make_result(url=urls[0]),
        urls[1]: make_result(url=urls[1], status_code=404),
        urls[2]: make_result(url=urls[2]),
    }
    crawler = FakeCrawler(handler)
    install_fetcher(monkeypatch, crawler=crawler)
    result = invoke([*urls, "-d", str(tmp_path)])
    assert result.exit_code == 1
    files = list(tmp_path.iterdir())
    assert len(files) == 2
    assert "done: 2 succeeded, 1 failed" in result.stderr
    assert f"  failed: {urls[1]}" in result.stderr
    assert f"error: HTTP 404 Not Found: {urls[1]}" in result.stderr


def test_duplicate_url_note(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL, URL])
    assert result.exit_code == 0
    assert f"note: duplicate URL ignored: {URL}" in result.stderr
    assert result.stdout == "# Hello\n"


def test_name_collision_across_urls_gets_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fetcher(monkeypatch)
    urls = ["https://example.com/page#a", "https://example.com/page#b"]
    result = invoke([*urls, "-d", str(tmp_path)])
    assert result.exit_code == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["example.com_page-2.md", "example.com_page.md"]


def test_format_env_var_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = CliRunner().invoke(main_module.main, [URL], env={"CRAWL4CLI_FORMAT": "HTML"})
    assert result.exit_code == 0
    assert result.stdout == "<html><body><h1>Hello</h1></body></html>\n"


# --- usage errors (exit code 2) -----------------------------------------------


def test_output_file_with_multiple_urls_is_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL, "https://example.com/b", "-o", "out.md"])
    assert result.exit_code == 2
    assert "--output can only be used with a single URL" in result.stderr


@pytest.mark.parametrize("url", ["ftp://x", "example.com"])
def test_invalid_url_exits_2(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    install_fetcher(monkeypatch)
    result = invoke([url])
    assert result.exit_code == 2


def test_invalid_proxy_exits_2_without_leaking_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL, "--proxy", "ftp://user:s3cr3t@proxy.example:21"])
    assert result.exit_code == 2
    assert "s3cr3t" not in result.output


def test_concurrency_zero_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL, "-j", "0"])
    assert result.exit_code == 2


# --- proxy fallback ------------------------------------------------------------


def test_proxy_fallback_note_on_stderr_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = "http://user:s3cr3t@proxy.example:8080"

    def handler(url: str, config: Any) -> Any:
        if config.proxy_config is not None:
            return make_result(
                success=False,
                error_message="net::ERR_PROXY_CONNECTION_FAILED",
                status_code=None,
                html="",
            )
        return make_result()

    crawler = FakeCrawler(handler)
    install_fetcher(monkeypatch, crawler=crawler)
    result = invoke([URL, "--proxy", proxy])
    assert result.exit_code == 0
    assert "proxy failed (" in result.stderr
    assert "retried with a direct connection" in result.stderr
    assert "s3cr3t" not in result.output


def test_no_fallback_exits_1_without_leaking_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = "http://user:s3cr3t@proxy.example:8080"
    crawler = FakeCrawler(
        make_result(
            success=False,
            error_message="net::ERR_PROXY_CONNECTION_FAILED",
            status_code=None,
            html="",
        )
    )
    install_fetcher(monkeypatch, crawler=crawler)
    result = invoke([URL, "--proxy", proxy, "--no-fallback"])
    assert result.exit_code == 1
    assert "proxy connection failed" in result.stderr
    assert "s3cr3t" not in result.output


# --- error message formatting --------------------------------------------------


def _failing_result(message: str) -> Any:
    return make_result(success=False, error_message=message, status_code=None, html="")


@pytest.mark.parametrize(
    ("result_or_message", "expected_line"),
    [
        (make_result(status_code=404), f"error: HTTP 404 Not Found: {URL}"),
        (
            _failing_result("net::ERR_NAME_NOT_RESOLVED"),
            f"error: could not resolve host: {URL}",
        ),
        (
            _failing_result("net::ERR_CONNECTION_REFUSED"),
            f"error: connection refused: {URL}",
        ),
        (
            _failing_result("net::ERR_TIMED_OUT"),
            f"error: timed out after 60s: {URL}",
        ),
    ],
)
def test_error_message_formatting(
    monkeypatch: pytest.MonkeyPatch, result_or_message: Any, expected_line: str
) -> None:
    crawler = FakeCrawler(result_or_message)
    install_fetcher(monkeypatch, crawler=crawler)
    result = invoke([URL])
    assert result.exit_code == 1
    assert expected_line in result.stderr


def test_browser_not_installed_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    crawler = FakeCrawler(
        start_error=RuntimeError(
            "BrowserType.launch: Executable doesn't exist at /x/chrome\n"
            "Please run the following command: playwright install"
        )
    )
    install_fetcher(monkeypatch, crawler=crawler)
    result = invoke([URL])
    assert result.exit_code == 1
    assert "browser is not installed" in result.stderr


# --- non-HTML resources --------------------------------------------------------


def test_pdf_url_is_converted_to_markdown_on_stdout(
    monkeypatch: pytest.MonkeyPatch, sample_pdf: bytes
) -> None:
    http = FakeHttp(lambda request: response("application/pdf", sample_pdf))
    install_fetcher(monkeypatch, http=http)
    result = invoke(["https://example.com/doc.pdf"])
    assert result.exit_code == 0
    assert "Hello crawl4tools" in result.stdout


def test_image_is_saved_as_jpg_with_a_note(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    http = FakeHttp(lambda request: response("image/jpeg", b"\xff\xd8JPEG"))
    install_fetcher(monkeypatch, http=http)
    result = invoke(["https://example.com/photo", "-d", str(tmp_path)])
    assert result.exit_code == 0
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].suffix == ".jpg"
    assert files[0].read_bytes() == b"\xff\xd8JPEG"
    assert "not a web page (image/jpeg); saved the original file" in result.stderr
