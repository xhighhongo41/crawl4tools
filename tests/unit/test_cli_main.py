from __future__ import annotations

import base64
import errno
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner
from conftest import FakeCrawler, FakeHttp, make_result

from crawl4tools.cli import main as main_module
from crawl4tools.engine.fetcher import Fetcher
from crawl4tools.engine.models import FetchOptions
from crawl4tools.i18n import ENGLISH, LOCALE_ENV_VARS, Translator, get_translator

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


def test_fit_flag_prints_filtered_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL, "-q", "--fit"])
    assert result.exit_code == 0
    assert result.stdout == "# Hello (fit)\n"


def test_fit_can_be_set_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL, "-q"], env={"CRAWL4CLI_FIT": "1"})
    assert result.exit_code == 0
    assert result.stdout == "# Hello (fit)\n"


# --- message language -------------------------------------------------------------

JA = get_translator("ja")


def squash(text: str) -> str:
    """Remove every whitespace character, so help text wrapping does not matter."""
    return "".join(text.split())


def clear_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every locale variable, including the LANGUAGE=en set by conftest."""
    for name in LOCALE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_english_help_is_unchanged_and_lists_lang() -> None:
    result = invoke(["--help"])
    assert result.exit_code == 0
    output = squash(result.output)
    for text in [
        "Download web pages as Markdown and other formats.",
        "Output format.",
        "Write the (single) fetched URL to this file instead of stdout.",
        "Proxy URL (http, https, or socks5); e.g. socks5://host:1080.",
        "Show the version and exit.",
        "Language of messages: en or ja.",
    ]:
        assert squash(text) in output, text
    assert "--lang [en|ja]" in result.output


def test_japanese_help_shows_translated_texts() -> None:
    result = CliRunner().invoke(main_module.build_command(JA), ["--help"])
    assert result.exit_code == 0
    output = squash(result.output)
    for text in [
        "Web ページを Markdown などの形式でダウンロードします。",
        "出力形式を指定します。",
        "取得した(1 つの)URL を標準出力ではなくこのファイルに書き込みます。",
        "取得した URL を保存するディレクトリです。",
        "プロキシ URL(http、https、socks5 のいずれか)です。例: socks5://host:1080。",
        "プロキシ自体に問題があると見られる場合は、プロキシなしで再試行します。",
        "同時に取得する URL の最大数です。",
        "URL ごとのタイムアウト(秒)です。",
        "Markdown のリンクを番号付きの参照にします。",
        "Markdown からリンクを除きます。",
        "Markdown から画像を除きます。",
        "進捗と note 行を表示しません。",
        "エンジンの詳細なログを出力します。",
        "バージョンを表示して終了します。",
        "メッセージの言語(en または ja)です。",
    ]:
        assert squash(text) in output, text
    assert "--lang [en|ja]" in result.output
    # click's own texts stay in English.
    assert "Usage:" in result.output
    assert "Show this message and exit." in result.output
    assert "Output format." not in result.output


def test_japanese_duplicate_note_keeps_the_english_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke(["--lang", "ja", URL, URL])
    assert result.exit_code == 0
    assert f"note: 重複した URL を無視しました: {URL}" in result.stderr
    assert result.stdout == "# Hello\n"


def test_japanese_note_for_a_non_web_page(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    http = FakeHttp(lambda request: response("image/jpeg", b"\xff\xd8JPEG"))
    install_fetcher(monkeypatch, http=http)
    url = "https://example.com/photo"
    result = invoke(["--lang", "ja", url, "-d", str(tmp_path)])
    assert result.exit_code == 0
    expected = f"note: {url}: Web ページではありません(image/jpeg)。元のファイルを保存しました"
    assert expected in result.stderr
    assert f"saved: {url} -> " in result.stderr


def test_japanese_error_and_summary_lines(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    urls = [f"https://example.com/{i}" for i in range(3)]
    handler = {
        urls[0]: make_result(url=urls[0]),
        urls[1]: _failing_result("net::ERR_NAME_NOT_RESOLVED"),
        urls[2]: make_result(url=urls[2]),
    }
    install_fetcher(monkeypatch, crawler=FakeCrawler(handler))
    result = invoke(["--lang", "ja", *urls, "-d", str(tmp_path)])
    assert result.exit_code == 1
    assert f"error: ホスト名を解決できません: {urls[1]}" in result.stderr
    assert "done: 成功 2 件、失敗 1 件" in result.stderr
    assert f"  failed: {urls[1]}" in result.stderr


def test_japanese_output_with_several_urls_is_usage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fetcher(monkeypatch)
    result = invoke(["--lang", "ja", URL, "https://example.com/b", "-o", "out.md"])
    assert result.exit_code == 2
    assert (
        "Error: --output は URL が 1 つのときだけ使えます。"
        "複数の URL には --output-dir を使ってください"
    ) in result.stderr


def _fail_to_write(*args: Any, **kwargs: Any) -> None:
    raise OSError(errno.EACCES, "Permission denied", "/x/out.md")


@pytest.mark.parametrize(
    ("lang", "expected_line"),
    [
        ("en", "error: could not write /x/out.md: Permission denied"),
        ("ja", "error: /x/out.md に書き込めません: Permission denied"),
    ],
    ids=["en", "ja"],
)
def test_write_failure_line(monkeypatch: pytest.MonkeyPatch, lang: str, expected_line: str) -> None:
    install_fetcher(monkeypatch)
    monkeypatch.setattr(main_module, "write_outcome", _fail_to_write)
    result = invoke(["--lang", lang, URL, "-o", "/x/out.md"])
    assert result.exit_code == 1
    assert expected_line in result.stderr.splitlines()


def test_language_variable_selects_japanese(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL, URL], env={"CRAWL4CLI_LANG": "ja"})
    assert result.exit_code == 0
    assert f"note: 重複した URL を無視しました: {URL}" in result.stderr


def test_lang_option_beats_the_language_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke(["--lang", "en", URL, URL], env={"CRAWL4CLI_LANG": "ja"})
    assert result.exit_code == 0
    assert f"note: duplicate URL ignored: {URL}" in result.stderr


def test_unsupported_lang_option_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke(["--lang", "de", URL])
    assert result.exit_code == 2
    assert "Invalid value for '--lang'" in result.stderr


def test_unsupported_language_variable_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = invoke([URL], env={"CRAWL4CLI_LANG": "de"})
    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("lang_value", "expected_note"),
    [
        ("ja_JP.UTF-8", f"note: 重複した URL を無視しました: {URL}"),
        ("C", f"note: duplicate URL ignored: {URL}"),
    ],
    ids=["ja_JP", "C"],
)
def test_locale_chooses_the_runtime_language(
    monkeypatch: pytest.MonkeyPatch, lang_value: str, expected_note: str
) -> None:
    install_fetcher(monkeypatch)
    clear_locale(monkeypatch)
    monkeypatch.setenv("LANG", lang_value)
    result = invoke([URL, URL])
    assert result.exit_code == 0
    assert expected_note in result.stderr


def test_japanese_invalid_url_message(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fetcher(monkeypatch)
    result = CliRunner().invoke(main_module.build_command(JA), ["ftp://x"])
    assert result.exit_code == 2
    assert "Error: Invalid value for 'URLS...': http(s) の URL ではありません: ftp://x" in (
        result.stderr
    )


def test_japanese_invalid_proxy_message_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fetcher(monkeypatch)
    command = main_module.build_command(JA)
    result = CliRunner().invoke(command, [URL, "--proxy", "ftp://user:s3cr3t@proxy.example:21"])
    assert result.exit_code == 2
    assert (
        "Error: Invalid value for '--proxy': "
        "対応していないプロキシのスキームです: ftp://***@proxy.example:21"
    ) in result.stderr
    assert "s3cr3t" not in result.output


# --- entry (the console script) ------------------------------------------------------


class CommandRecorder:
    """Stands in for build_command(): records the translator and the main() call."""

    def __init__(self) -> None:
        self.translators: list[Translator] = []
        self.main_calls: list[dict[str, Any]] = []

    def __call__(self, t: Translator) -> CommandRecorder:
        self.translators.append(t)
        return self

    def main(self, **kwargs: Any) -> None:
        self.main_calls.append(kwargs)


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        (["--lang", "ja", URL], {}, JA),
        (["--lang=ja", URL], {}, JA),
        ([URL], {"CRAWL4CLI_LANG": "ja"}, JA),
        ([URL], {"LANG": "ja_JP.UTF-8"}, JA),
        ([URL], {}, ENGLISH),
        ([URL], {"LANG": "C"}, ENGLISH),
        (["--lang", "en", URL], {"CRAWL4CLI_LANG": "ja"}, ENGLISH),
    ],
    ids=[
        "lang-option",
        "lang-option-equals",
        "variable",
        "locale-ja",
        "nothing-set",
        "locale-C",
        "option-beats-variable",
    ],
)
def test_entry_builds_the_command_in_the_chosen_language(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], env: dict[str, str], expected: Translator
) -> None:
    clear_locale(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["crawl4cli", *argv])
    recorder = CommandRecorder()
    monkeypatch.setattr(main_module, "build_command", recorder)
    main_module.entry()
    assert len(recorder.translators) == 1
    assert recorder.translators[0] is expected
    assert recorder.main_calls == [{"prog_name": "crawl4cli"}]
