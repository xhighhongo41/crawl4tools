from click.testing import CliRunner

from crawl4tools.cli.main import main


def test_version_shows_versions_and_attribution() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "crawl4cli 0.1.0" in result.output
    assert "crawl4ai 0.9." in result.output
    assert "UncleCode" in result.output
