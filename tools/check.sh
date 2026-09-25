#!/usr/bin/env bash
# Run every mechanical check short of a release: lock file freshness, shellcheck,
# actionlint (GitHub Actions workflow linter), lint, format check, type check, and
# unit tests, in order. Exits non-zero on the first failure. With --integration, also
# run the integration tests (real browser).
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

integration=0
if (($# == 1)) && [[ "$1" == "--integration" ]]; then
    integration=1
elif (($# != 0)); then
    printf 'usage: tools/check.sh [--integration]\n' >&2
    exit 2
fi

step() { printf '\n==> %s\n' "$*"; }

step "uv lock --check"
uv lock --check

if command -v shellcheck >/dev/null 2>&1; then
    step "shellcheck"
    shellcheck tools/*.sh
else
    step "shellcheck (skipped: not installed)"
fi

if command -v actionlint >/dev/null 2>&1; then
    step "actionlint"
    actionlint
else
    step "actionlint (skipped: not installed)"
fi

step "ruff check"
uv run ruff check src tests
step "ruff format --check"
uv run ruff format --check src tests
step "mypy (strict)"
uv run mypy src tests
step "pytest (unit)"
uv run pytest -q tests/unit

if ((integration)); then
    step "pytest (integration)"
    CRAWL4TOOLS_INTEGRATION=1 uv run pytest -q tests/integration
fi

printf '\ncheck.sh: all checks passed\n'
