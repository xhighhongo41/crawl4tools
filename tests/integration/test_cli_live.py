"""End-to-end checks of crawl4cli against real sites with a real browser."""

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from click.testing import CliRunner
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from crawl4tools.cli.main import main
from crawl4tools.engine.errors import HttpStatusError
from crawl4tools.engine.fetcher import Fetcher
from crawl4tools.engine.models import FetchOptions
from crawl4tools.i18n import get_translator

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


# The installed console script (crawl4cli = crawl4tools.cli.main:entry).
CRAWL4CLI = str(Path(sys.executable).parent / "crawl4cli")


def test_console_script_reports_errors_in_japanese() -> None:
    url = "https://no-such-host.invalid/"
    result = subprocess.run(
        [CRAWL4CLI, "--lang", "ja", "--timeout", "30", url],
        capture_output=True,
        text=True,
        timeout=120,
    )
    japanese = get_translator("ja").gettext("could not resolve host: {url}").format(url=url)
    assert japanese != f"could not resolve host: {url}"
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"error: {japanese}\n"


def test_console_script_help_follows_the_locale() -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("LANGUAGE", "LC_ALL", "LC_MESSAGES")}
    env["LANG"] = "ja_JP.UTF-8"
    result = subprocess.run(
        [CRAWL4CLI, "--help"], capture_output=True, text=True, timeout=60, env=env
    )
    japanese = get_translator("ja").gettext("Download web pages as Markdown and other formats.")
    assert result.returncode == 0
    assert japanese in result.stdout
    assert "Download web pages as Markdown" not in result.stdout


# --- redirects: the final response is reported (C12) ----------------------------------

# Enough text that crawl4ai's anti-bot check does not take the pages for empty shells.
_FINAL_PAGE = (
    "<html><head><title>Final</title></head><body><h1>Final page</h1>"
    "<p>This is where the moved page ended up after a permanent redirect.</p></body></html>"
)
_MISSING_PAGE = (
    "<html><head><title>Not Found</title></head><body><h1>Not here</h1>"
    "<p>The page this redirect points to does not exist on this server.</p></body></html>"
)


async def _moved(request: Request) -> Response:
    return RedirectResponse("/final", status_code=301)


async def _final(request: Request) -> Response:
    return HTMLResponse(_FINAL_PAGE)


async def _gone(request: Request) -> Response:
    return RedirectResponse("/missing", status_code=301)


async def _missing(request: Request) -> Response:
    return HTMLResponse(_MISSING_PAGE, status_code=404)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def redirect_site() -> Iterator[str]:
    """Serve /moved -> 301 -> /final (200) and /gone -> 301 -> /missing (404) locally."""
    app = Starlette(
        routes=[
            Route("/moved", _moved),
            Route("/final", _final),
            Route("/gone", _gone),
            Route("/missing", _missing),
        ]
    )
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                pytest.fail("the redirect test server did not start")
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


async def test_redirects_are_judged_by_the_final_response(redirect_site: str) -> None:
    async with Fetcher(FetchOptions(timeout_s=30)) as fetcher:
        moved, gone = await fetcher.fetch_many([f"{redirect_site}/moved", f"{redirect_site}/gone"])

    assert moved.ok, moved.error
    assert moved.status_code == 200
    assert moved.final_url == f"{redirect_site}/final"
    # The 301 has no content type: this one comes from the after_goto hook.
    assert moved.content_type == "text/html; charset=utf-8"
    assert moved.text is not None
    assert "Final page" in moved.text

    assert not gone.ok
    assert isinstance(gone.error, HttpStatusError)
    assert gone.status_code == 404
    assert str(gone.error) == f"HTTP 404 Not Found: {redirect_site}/gone"
