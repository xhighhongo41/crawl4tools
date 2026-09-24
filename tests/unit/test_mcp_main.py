from click.testing import CliRunner

from crawl4tools.server.mcp_main import main


def test_version_shows_versions_and_attribution() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    first_line = result.output.splitlines()[0]
    assert first_line.startswith("crawl4mcp 0.2.0 (crawl4ai 0.9.")
    assert ", mcp 2." in first_line
    assert "UncleCode" in result.output
