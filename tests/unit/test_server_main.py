"""Tests for the crawl4server command."""

from __future__ import annotations

from click.testing import CliRunner

from crawl4tools.server.server_main import main


def test_version_names_command_and_dependencies() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    first_line = result.output.splitlines()[0]
    assert first_line.startswith("crawl4server 0.3.0 (crawl4ai 0.9.")
    assert ", mcp 2." in first_line
    assert ", starlette 1." in first_line
    assert "crawl4ai" in result.output.splitlines()[1]
