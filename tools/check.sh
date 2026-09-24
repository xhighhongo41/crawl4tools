#!/usr/bin/env bash
# Run lint, format check, type check and unit tests in order. Exits non-zero on the first failure.
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n==> %s\n' "$*"; }

step "ruff check"
uv run ruff check src tests
step "ruff format --check"
uv run ruff format --check src tests
step "mypy (strict)"
uv run mypy src tests
step "pytest (unit)"
uv run pytest -q tests/unit

printf '\ncheck.sh: all checks passed\n'
