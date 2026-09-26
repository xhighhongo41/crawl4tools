"""Documentation invariants checked against the actual click commands.

These tests read README.md and README_ja.md as plain text; they have no
effect on the running commands and take no network access. Checked here:
(1) the ``compose.yaml`` block quoted in the "Docker" section of each
README, and in the Docker Hub overview, is a byte-for-byte copy of the
repository's compose.yaml; (2) every command-line option of the three
commands (except ``--help`` and ``--version``, which are not settings) is
documented, with its environment variable, in the right Options table of
both READMEs, and the config file keys of crawl4mcp/crawl4server are
listed in the same table; and (3) the Docker Hub overview and the
package's ``description`` meet Docker Hub / PyPI's own constraints.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import click
import pytest

import crawl4tools.cli.main as cli_main
import crawl4tools.server.mcp_main as mcp_main
import crawl4tools.server.server_main as server_main
from crawl4tools.server.config import CONFIG_KEYS
from crawl4tools.server.server_main import SERVER_CONFIG_KEYS

REPO_ROOT = Path(__file__).resolve().parents[2]
README_EN = REPO_ROOT / "README.md"
README_JA = REPO_ROOT / "README_ja.md"
COMPOSE_YAML = REPO_ROOT / "compose.yaml"
DOCKERHUB_OVERVIEW = REPO_ROOT / ".github" / "dockerhub-overview.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Docker Hub truncates a repository's short description past this many characters.
_DOCKERHUB_SHORT_DESCRIPTION_LIMIT = 100

# A Markdown link whose target is not an absolute URL, e.g. "[x](./foo.md)" or
# "[x](#section)"; the Docker Hub overview is read on its own, without the rest
# of the repository, so every link in it must be absolute.
_RELATIVE_LINK_RE = re.compile(r"\]\((?!https?://)")

_COMPOSE_MARKER = "<!-- compose.yaml -->"

# A Markdown ATX heading line, e.g. "## Usage" or "### Options".
_HEADING_RE = re.compile(r"^(#{1,6}) ")


# --- helpers: reading a compose.yaml block out of a README ----------------------


def compose_yaml_block(readme_text: str) -> str:
    """Return the contents of the fenced ```yaml block after ``_COMPOSE_MARKER``.

    Raises:
        ValueError: if the marker or the following code fence is missing.
    """
    after_marker = readme_text[readme_text.index(_COMPOSE_MARKER) + len(_COMPOSE_MARKER) :]
    start = after_marker.index("```yaml") + len("```yaml")
    end = after_marker.index("```", start)
    return after_marker[start:end]


# --- helpers: reading a heading's section out of Markdown text ------------------


def _heading_level(heading: str) -> int:
    match = _HEADING_RE.match(heading)
    assert match is not None, f"not a Markdown ATX heading: {heading!r}"
    return len(match.group(1))


def section_body(text: str, heading: str) -> str:
    """Return the body of the first *heading* line, up to the next heading of
    the same level or shallower.

    *heading* is matched as a whole line (e.g. ``"## Usage"``) and must
    appear in *text*; a deeper heading (e.g. ``"### Options"`` inside a
    ``"## ..."`` section) stays part of the returned body.

    Raises:
        AssertionError: if *heading* is not found in *text*.
    """
    level = _heading_level(heading)
    boundary = re.compile(rf"^#{{1,{level}}} ")
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line == heading:
            start = index + 1
            break
    assert start is not None, f"heading not found: {heading!r}"

    body_lines = []
    for line in lines[start:]:
        if boundary.match(line):
            break
        body_lines.append(line)
    return "\n".join(body_lines)


# --- helpers: the options a click command actually has ---------------------------


def expected_options(command: click.Command, envvar_prefix: str) -> list[tuple[str, str]]:
    """Return ``(table name, environment variable)`` for every documented option.

    "Documented option" is every :class:`click.Option` of *command* except
    ``--version`` (a click.Argument, e.g. crawl4cli's ``urls``, is not an
    option either; ``--help`` never appears in ``command.params`` since
    click adds it dynamically). A two-name flag such as ``--fallback/
    --no-fallback`` is combined into that one table entry, matching how
    click derives its own ``auto_envvar_prefix`` variables.

    The table name is the option's first ``--``-long name (plus, for a
    flag pair, its negative long name); the environment variable is the
    option's own ``envvar`` if it declares one (``--lang``, ``--config``),
    else ``<envvar_prefix>_<PARAM_NAME>``, click's ``auto_envvar_prefix``
    rule.
    """
    documented = []
    for param in command.params:
        if not isinstance(param, click.Option) or "--version" in param.opts:
            continue
        primary = next(opt for opt in param.opts if opt.startswith("--"))
        if param.secondary_opts:
            secondary = next(opt for opt in param.secondary_opts if opt.startswith("--"))
            table_name = f"{primary}/{secondary}"
        else:
            table_name = primary
        envvar = param.envvar if isinstance(param.envvar, str) else None
        if envvar is None:
            envvar = f"{envvar_prefix}_{param.name.upper()}" if param.name else None
        assert envvar is not None, f"{command.name}: option {table_name} has no envvar"
        documented.append((table_name, envvar))
    return documented


# --- the three commands and where their Options tables live ---------------------


@dataclass(frozen=True)
class CommandDoc:
    """Where one command's Options table lives in each README, and its config keys."""

    label: str
    command: click.Command
    envvar_prefix: str
    # The "## ..." section holding the Options table.
    top_heading_en: str
    top_heading_ja: str
    # A "### ..." sub-heading of the table within that section, or None when
    # the table is directly under the top heading (crawl4cli's "## Usage").
    sub_heading_en: str | None
    sub_heading_ja: str | None
    # The config file keys that must also be listed in the table, or None for
    # crawl4cli, which has no config file.
    config_keys: frozenset[str] | None


COMMANDS = [
    CommandDoc(
        label="crawl4cli",
        command=cli_main.main,
        envvar_prefix="CRAWL4CLI",
        top_heading_en="## Usage",
        top_heading_ja="## 使い方",
        sub_heading_en=None,
        sub_heading_ja=None,
        config_keys=None,
    ),
    CommandDoc(
        label="crawl4mcp",
        command=mcp_main.main,
        envvar_prefix="CRAWL4MCP",
        top_heading_en="## MCP server",
        top_heading_ja="## MCPサーバー",
        sub_heading_en="### Options",
        sub_heading_ja="### オプション",
        config_keys=CONFIG_KEYS,
    ),
    CommandDoc(
        label="crawl4server",
        command=server_main.main,
        envvar_prefix="CRAWL4SERVER",
        top_heading_en="## Open WebUI web loader (crawl4server)",
        top_heading_ja="## Open WebUI Web loader(crawl4server)",
        sub_heading_en="### Options",
        sub_heading_ja="### オプション",
        config_keys=SERVER_CONFIG_KEYS,
    ),
]


def _options_table_text(readme_text: str, doc: CommandDoc, *, japanese: bool) -> str:
    """Return the text of *doc*'s Options table section in one README's text."""
    top_heading = doc.top_heading_ja if japanese else doc.top_heading_en
    sub_heading = doc.sub_heading_ja if japanese else doc.sub_heading_en
    body = section_body(readme_text, top_heading)
    if sub_heading is not None:
        body = section_body(body, sub_heading)
    return body


# --- tests: compose.yaml -----------------------------------------------------------


def test_readme_compose_yaml_block_matches_compose_yaml() -> None:
    """Both READMEs quote compose.yaml verbatim, not a hand-copied approximation."""
    expected = COMPOSE_YAML.read_text(encoding="utf-8").strip()
    for readme_path in (README_EN, README_JA):
        block = compose_yaml_block(readme_path.read_text(encoding="utf-8")).strip()
        assert block == expected, f"{readme_path.name}: compose.yaml block is out of date"


def test_dockerhub_overview_compose_yaml_block_matches_compose_yaml() -> None:
    """The Docker Hub overview quotes compose.yaml verbatim too."""
    expected = COMPOSE_YAML.read_text(encoding="utf-8").strip()
    block = compose_yaml_block(DOCKERHUB_OVERVIEW.read_text(encoding="utf-8")).strip()
    assert block == expected, "dockerhub-overview.md: compose.yaml block is out of date"


# --- tests: every option is documented, with its environment variable -----------


@pytest.mark.parametrize("doc", COMMANDS, ids=lambda doc: doc.label)
def test_every_option_is_documented_with_its_envvar(doc: CommandDoc) -> None:
    for readme_path, japanese in ((README_EN, False), (README_JA, True)):
        readme_text = readme_path.read_text(encoding="utf-8")
        table_lines = _options_table_text(readme_text, doc, japanese=japanese).splitlines()
        for table_name, envvar in expected_options(doc.command, doc.envvar_prefix):
            matches = [line for line in table_lines if table_name in line and envvar in line]
            assert matches, (
                f"{readme_path.name}: {doc.label}'s Options table has no row with both "
                f"'{table_name}' and '{envvar}'"
            )


@pytest.mark.parametrize(
    "doc", [doc for doc in COMMANDS if doc.config_keys is not None], ids=lambda doc: doc.label
)
def test_every_config_key_is_listed_in_the_options_table(doc: CommandDoc) -> None:
    assert doc.config_keys is not None  # narrows the type for mypy; the parametrize already did
    for readme_path, japanese in ((README_EN, False), (README_JA, True)):
        readme_text = readme_path.read_text(encoding="utf-8")
        table_text = _options_table_text(readme_text, doc, japanese=japanese)
        for key in sorted(doc.config_keys):
            assert f"`{key}`" in table_text, (
                f"{readme_path.name}: {doc.label}'s Options table does not list config key '{key}'"
            )


# --- tests: Docker Hub / PyPI metadata ------------------------------------------


def test_pyproject_description_fits_the_dockerhub_short_description() -> None:
    """Docker Hub reuses ``description`` as the repository's short description."""
    with PYPROJECT.open("rb") as f:
        description = tomllib.load(f)["project"]["description"]
    assert len(description) <= _DOCKERHUB_SHORT_DESCRIPTION_LIMIT, (
        f"pyproject.toml: description is {len(description)} characters, "
        f"over Docker Hub's {_DOCKERHUB_SHORT_DESCRIPTION_LIMIT}-character limit"
    )


def test_dockerhub_overview_has_no_relative_links() -> None:
    """The overview is read on Docker Hub alone, so every link must be absolute."""
    text = DOCKERHUB_OVERVIEW.read_text(encoding="utf-8")
    relative = _RELATIVE_LINK_RE.findall(text)
    assert relative == [], "dockerhub-overview.md: has a relative Markdown link"
