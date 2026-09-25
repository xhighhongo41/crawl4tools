"""Design invariants of the GitHub Actions workflows.

actionlint checks that the workflows are valid; these tests check the
commitments made in their design: when they run, which jobs they have, how
little the token may do, and how the actions they use are referenced.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

CI_JOBS = {"check", "py311", "package", "docker", "actionlint"}

# An action pinned to a major tag (``@v7``), a full version tag (``@v10.2.0``,
# for actions such as setup-uv that publish no major tag) or pypa's
# ``release/v1`` branch; anything else (``@main``, ``@master``, other branches,
# a SHA) is rejected.
_PINNED_ACTION = re.compile(r"^[^@\s]+@(?:v\d+(?:\.\d+\.\d+)?|release/v1)$")


# --- helpers ------------------------------------------------------------------


def workflow_files() -> list[Path]:
    """Return every workflow file, sorted by name."""
    return sorted([*WORKFLOWS_DIR.glob("*.yml"), *WORKFLOWS_DIR.glob("*.yaml")])


def load_workflow(name: str) -> dict[str, Any]:
    """Parse ``.github/workflows/<name>`` and return its top-level mapping."""
    data = yaml.safe_load((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name} is not a YAML mapping"
    return data


def triggers(workflow: Mapping[Any, Any]) -> dict[str, Any]:
    """Return the ``on:`` mapping of a workflow.

    YAML 1.1 reads the bare key ``on`` as the boolean ``True``, so that key is
    tried first (hence the non-``str`` key type of ``workflow``).
    """
    on = workflow.get(True, workflow.get("on"))
    assert isinstance(on, dict), f"`on:` is not a mapping: {on!r}"
    return on


def jobs(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the ``jobs:`` mapping of a workflow, keyed by job id."""
    found = workflow.get("jobs")
    assert isinstance(found, dict), f"`jobs:` is not a mapping: {found!r}"
    return found


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the steps of a job (none for a job that calls a reusable workflow)."""
    found = job.get("steps", [])
    assert isinstance(found, list), f"`steps:` is not a list: {found!r}"
    return found


def iter_uses(workflow: dict[str, Any]) -> Iterator[str]:
    """Yield every ``uses:`` reference of a workflow.

    Covers each step of each job, and a job-level ``uses:`` (a reusable
    workflow) as well.
    """
    for job in jobs(workflow).values():
        if "uses" in job:
            yield str(job["uses"])
        for step in steps(job):
            if "uses" in step:
                yield str(step["uses"])


def is_pinned(uses: str) -> bool:
    """Tell whether a ``uses:`` reference is local, a Docker image, or version-pinned."""
    if uses.startswith(("./", "docker://")):
        return True
    return _PINNED_ACTION.match(uses) is not None


def branches(event: object) -> list[str]:
    """Return the ``branches:`` filter of a push / pull_request trigger."""
    assert isinstance(event, dict), f"trigger is not a mapping: {event!r}"
    found = event.get("branches")
    assert isinstance(found, list), f"`branches:` is not a list: {found!r}"
    return [str(branch) for branch in found]


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    """The parsed ci.yml."""
    return load_workflow("ci.yml")


# --- every workflow -------------------------------------------------------------


def test_workflow_files_include_ci() -> None:
    assert "ci.yml" in [path.name for path in workflow_files()]


@pytest.mark.parametrize("path", workflow_files(), ids=lambda path: path.name)
def test_uses_references_are_pinned_to_a_version_tag(path: Path) -> None:
    references = list(iter_uses(load_workflow(path.name)))
    assert references, f"{path.name} uses no actions"
    unpinned = [uses for uses in references if not is_pinned(uses)]
    assert unpinned == [], f"{path.name}: not @v<N>, @v<N>.<N>.<N> or @release/v1"


@pytest.mark.parametrize(
    ("uses", "expected"),
    [
        ("actions/checkout@v7", True),
        ("astral-sh/setup-uv@v10.2.0", True),
        ("pypa/gh-action-pypi-publish@release/v1", True),
        ("./.github/actions/setup", True),
        ("docker://rhysd/actionlint:1.7.12", True),
        ("actions/checkout@main", False),
        ("actions/checkout@master", False),
        ("actions/checkout@v7.5", False),
        ("actions/checkout@v7.0.1-rc.1", False),
        ("actions/checkout@0123456789abcdef0123456789abcdef01234567", False),
        ("actions/checkout@releases/v7", False),
        ("actions/checkout", False),
        ("pypa/gh-action-pypi-publish@release/v2", False),
    ],
    ids=[
        "major-tag",
        "full-version",
        "release-v1",
        "local",
        "docker",
        "main",
        "master",
        "minor-only",
        "pre-release",
        "sha",
        "other-branch",
        "no-ref",
        "release-v2",
    ],
)
def test_is_pinned(uses: str, expected: bool) -> None:
    assert is_pinned(uses) is expected


# --- ci.yml ---------------------------------------------------------------------


def test_ci_runs_on_pull_requests_to_main(ci: dict[str, Any]) -> None:
    on = triggers(ci)
    assert "pull_request" in on
    assert "main" in branches(on["pull_request"])


def test_ci_runs_on_pushes_to_main_and_dev_branches(ci: dict[str, Any]) -> None:
    on = triggers(ci)
    assert "push" in on
    assert {"main", "dev/**"} <= set(branches(on["push"]))


def test_ci_has_exactly_the_five_jobs(ci: dict[str, Any]) -> None:
    assert set(jobs(ci)) == CI_JOBS


def test_ci_jobs_never_continue_on_error(ci: dict[str, Any]) -> None:
    lenient = [
        name
        for name, job in jobs(ci).items()
        if "continue-on-error" in job or any("continue-on-error" in step for step in steps(job))
    ]
    assert lenient == []


def test_ci_token_can_only_read_contents(ci: dict[str, Any]) -> None:
    assert ci.get("permissions") == {"contents": "read"}


def test_ci_jobs_do_not_widen_permissions(ci: dict[str, Any]) -> None:
    widened = [name for name, job in jobs(ci).items() if "permissions" in job]
    assert widened == []


def test_ci_py311_job_uses_python_3_11(ci: dict[str, Any]) -> None:
    job = jobs(ci)["py311"]
    requested = [
        str(step.get("with", {}).get("python-version"))
        for step in steps(job)
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
    ]
    requested.append(str(job.get("env", {}).get("UV_PYTHON")))
    assert "3.11" in requested, f"py311 asks for {requested}"
