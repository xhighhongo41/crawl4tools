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
RELEASE_JOBS = {"verify", "build", "pypi", "docker", "manifest", "assets"}

# The release jobs that publish something; a dry run must skip every one of them.
PUBLISHING_JOBS = ["pypi", "manifest", "assets"]

# Each image platform and the GitHub-hosted runner that builds it natively.
DOCKER_PLATFORMS = {"linux/amd64": "ubuntu-latest", "linux/arm64": "ubuntu-24.04-arm"}

# An action pinned to a major tag (``@v7``), a full version tag (``@v10.2.0``,
# for actions such as setup-uv that publish no major tag) or pypa's
# ``release/v1`` branch; anything else (``@main``, ``@master``, other branches,
# a SHA) is rejected.
_PINNED_ACTION = re.compile(r"^[^@\s]+@(?:v\d+(?:\.\d+\.\d+)?|release/v1)$")

# An expression that pastes event payload or workflow_dispatch input text into a
# ``run:`` script, where the shell would parse it as code; such values must reach
# the script through ``env:`` instead.
_EVENT_DATA_IN_SCRIPT = re.compile(r"\$\{\{\s*(?:github\.event\b|inputs\.)")


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


def lenient_jobs(workflow: dict[str, Any]) -> list[str]:
    """Return the ids of the jobs that, or one of whose steps, set continue-on-error."""
    return [
        name
        for name, job in jobs(workflow).items()
        if "continue-on-error" in job or any("continue-on-error" in step for step in steps(job))
    ]


def job_permissions(job: dict[str, Any]) -> dict[str, str]:
    """Return the ``permissions:`` a job sets, by scope (empty when it sets none).

    A shorthand string (``write-all`` / ``read-all``) fails the assertion: the
    workflows grant scopes one by one.
    """
    found = job.get("permissions", {})
    assert isinstance(found, dict), f"`permissions:` is not a mapping: {found!r}"
    return {str(scope): str(level) for scope, level in found.items()}


def steps_using(job: dict[str, Any], action: str) -> list[dict[str, Any]]:
    """Return the steps of a job whose ``uses:`` is ``action`` at any ref."""
    return [step for step in steps(job) if str(step.get("uses", "")).startswith(f"{action}@")]


def step_inputs(step: dict[str, Any]) -> dict[str, Any]:
    """Return the ``with:`` mapping of a step (empty when it has none)."""
    found = step.get("with", {})
    assert isinstance(found, dict), f"`with:` is not a mapping: {found!r}"
    return found


def input_lines(value: object) -> list[str]:
    """Split a multi-line action input into its non-blank, stripped lines."""
    return [line.strip() for line in str(value).splitlines() if line.strip()]


def is_true(value: object) -> bool:
    """Tell whether a YAML value means ``true`` (the boolean or the string)."""
    return value is True or str(value).lower() == "true"


def environment_name(job: dict[str, Any]) -> str:
    """Return a job's deployment environment (given as a name or a mapping with ``name``)."""
    found = job.get("environment")
    if isinstance(found, dict):
        found = found.get("name")
    assert isinstance(found, str), f"`environment:` has no name: {found!r}"
    return found


def run_scripts(workflow: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield ``(job id, script)`` for every ``run:`` step of a workflow."""
    for name, job in jobs(workflow).items():
        for step in steps(job):
            if "run" in step:
                yield name, str(step["run"])


@pytest.fixture(scope="module")
def ci() -> dict[str, Any]:
    """The parsed ci.yml."""
    return load_workflow("ci.yml")


@pytest.fixture(scope="module")
def release() -> dict[str, Any]:
    """The parsed release.yml."""
    return load_workflow("release.yml")


# --- every workflow -------------------------------------------------------------


def test_workflow_files_include_ci_and_release() -> None:
    assert {"ci.yml", "release.yml"} <= {path.name for path in workflow_files()}


@pytest.mark.parametrize("path", workflow_files(), ids=lambda path: path.name)
def test_uses_references_are_pinned_to_a_version_tag(path: Path) -> None:
    references = list(iter_uses(load_workflow(path.name)))
    assert references, f"{path.name} uses no actions"
    unpinned = [uses for uses in references if not is_pinned(uses)]
    assert unpinned == [], f"{path.name}: not @v<N>, @v<N>.<N>.<N> or @release/v1"


@pytest.mark.parametrize("path", workflow_files(), ids=lambda path: path.name)
def test_every_job_has_a_timeout(path: Path) -> None:
    untimed = [
        name for name, job in jobs(load_workflow(path.name)).items() if "timeout-minutes" not in job
    ]
    assert untimed == [], f"{path.name}: jobs without timeout-minutes"


@pytest.mark.parametrize("path", workflow_files(), ids=lambda path: path.name)
def test_run_scripts_take_event_data_only_through_env(path: Path) -> None:
    pasted = [
        name
        for name, script in run_scripts(load_workflow(path.name))
        if _EVENT_DATA_IN_SCRIPT.search(script)
    ]
    assert pasted == [], f"{path.name}: pass github.event.* / inputs.* to run: via env:"


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ('echo "${{ github.event.release.tag_name }}"', True),
        ("echo ${{github.event.release.prerelease}}", True),
        ('echo "${{ inputs.dry_run }}"', True),
        ('echo "$RELEASE_TAG"', False),
        ('echo "${{ github.event_name }}"', False),
        ('echo "${{ runner.temp }}"', False),
    ],
    ids=["release-tag", "no-spaces", "dispatch-input", "env-var", "event-name", "runner-temp"],
)
def test_event_data_pattern(script: str, expected: bool) -> None:
    assert (_EVENT_DATA_IN_SCRIPT.search(script) is not None) is expected


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
    assert lenient_jobs(ci) == []


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


# --- release.yml ----------------------------------------------------------------


def test_release_runs_when_a_release_is_published(release: dict[str, Any]) -> None:
    event = triggers(release).get("release")
    assert isinstance(event, dict), f"`release:` trigger is not a mapping: {event!r}"
    assert event.get("types") == ["published"]


def test_release_dispatch_defaults_to_a_dry_run(release: dict[str, Any]) -> None:
    dispatch = triggers(release).get("workflow_dispatch")
    assert isinstance(dispatch, dict), f"`workflow_dispatch:` is not a mapping: {dispatch!r}"
    dry_run = dispatch.get("inputs", {}).get("dry_run")
    assert isinstance(dry_run, dict), f"no dry_run input: {dry_run!r}"
    assert dry_run.get("type") == "boolean"
    assert dry_run.get("default") is True


def test_release_has_exactly_the_six_jobs(release: dict[str, Any]) -> None:
    assert set(jobs(release)) == RELEASE_JOBS


def test_release_jobs_never_continue_on_error(release: dict[str, Any]) -> None:
    # The manifest job's Docker Hub overview update is the one deliberate
    # exception: it is a best-effort courtesy that must never fail the release.
    assert lenient_jobs(release) == ["manifest"]


def test_release_token_can_only_read_contents(release: dict[str, Any]) -> None:
    assert release.get("permissions") == {"contents": "read"}


def test_release_only_the_pypi_job_may_request_an_id_token(release: dict[str, Any]) -> None:
    granted = [
        name
        for name, job in jobs(release).items()
        if job_permissions(job).get("id-token") == "write"
    ]
    assert granted == ["pypi"]


def test_release_only_the_assets_job_may_write_contents(release: dict[str, Any]) -> None:
    granted = [
        name
        for name, job in jobs(release).items()
        if job_permissions(job).get("contents") == "write"
    ]
    assert granted == ["assets"]


def test_release_jobs_widen_permissions_only_where_needed(release: dict[str, Any]) -> None:
    widened = {
        name: job_permissions(job) for name, job in jobs(release).items() if "permissions" in job
    }
    assert widened == {"pypi": {"id-token": "write"}, "assets": {"contents": "write"}}


def test_release_verify_job_outputs_what_the_other_jobs_read(release: dict[str, Any]) -> None:
    outputs = jobs(release)["verify"].get("outputs", {})
    assert set(outputs) == {"version", "tag", "is_prerelease", "dry_run", "description"}


def test_release_pypi_job_deploys_to_the_pypi_environment(release: dict[str, Any]) -> None:
    assert environment_name(jobs(release)["pypi"]) == "pypi"


def test_release_pypi_job_publishes_with_pypa_skipping_existing_files(
    release: dict[str, Any],
) -> None:
    found = steps_using(jobs(release)["pypi"], "pypa/gh-action-pypi-publish")
    assert [step["uses"] for step in found] == ["pypa/gh-action-pypi-publish@release/v1"]
    inputs = step_inputs(found[0])
    assert is_true(inputs.get("skip-existing"))
    # PEP 740 attestations are on by default; they must stay on.
    assert "attestations" not in inputs or is_true(inputs["attestations"])


@pytest.mark.parametrize("name", PUBLISHING_JOBS)
def test_release_publishing_jobs_are_skipped_in_a_dry_run(
    release: dict[str, Any], name: str
) -> None:
    assert "dry_run" in str(jobs(release)[name].get("if", ""))


def test_release_docker_job_pushes_only_outside_a_dry_run(release: dict[str, Any]) -> None:
    builds = steps_using(jobs(release)["docker"], "docker/build-push-action")
    pushing = [
        step
        for step in builds
        if is_true(step_inputs(step).get("push"))
        or "push=true" in str(step_inputs(step).get("outputs", ""))
    ]
    dry = [step for step in builds if step not in pushing]
    assert pushing, "no build step pushes the image"
    assert dry, "no build-only step for the dry run"
    unguarded = [step.get("name") for step in builds if "dry_run" not in str(step.get("if", ""))]
    assert unguarded == [], "each build step must run in only one of real run / dry run"
    assert all(step_inputs(step).get("push") is False for step in dry)


def test_release_docker_matrix_builds_each_platform_natively(release: dict[str, Any]) -> None:
    job = jobs(release)["docker"]
    include = job.get("strategy", {}).get("matrix", {}).get("include")
    assert isinstance(include, list), f"matrix has no include list: {include!r}"
    assert {str(row["platform"]): str(row["runner"]) for row in include} == DOCKER_PLATFORMS
    assert job.get("runs-on") == "${{ matrix.runner }}"


def test_release_manifest_updates_the_docker_hub_description(release: dict[str, Any]) -> None:
    found = steps_using(jobs(release)["manifest"], "peter-evans/dockerhub-description")
    assert len(found) == 1, f"expected one dockerhub-description step, found {len(found)}"
    step = found[0]
    # Best-effort only: it must never fail the release.
    assert is_true(step.get("continue-on-error"))
    inputs = step_inputs(step)
    assert inputs.get("readme-filepath") == "./.github/dockerhub-overview.md"
    assert "needs.verify.outputs.description" in str(inputs.get("short-description", ""))


def test_release_manifest_tags_latest_only_for_final_releases(release: dict[str, Any]) -> None:
    found = steps_using(jobs(release)["manifest"], "docker/metadata-action")
    assert len(found) == 1, f"expected one metadata step, found {len(found)}"
    inputs = step_inputs(found[0])
    assert "latest=false" in input_lines(inputs.get("flavor", ""))
    tags = input_lines(inputs.get("tags", ""))
    assert len(tags) == 2, f"expected the version tag and the latest tag only: {tags}"
    latest = [tag for tag in tags if tag.startswith("type=raw,value=latest")]
    assert len(latest) == 1, f"expected one latest tag line: {tags}"
    assert "enable=" in latest[0]
    assert "is_prerelease" in latest[0]
