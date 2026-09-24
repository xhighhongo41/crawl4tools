#!/usr/bin/env bash
# Pre-release checks: version declarations agree, the working tree is clean, and check.sh passes.
# Prints every problem before exiting non-zero.
set -uo pipefail
cd "$(dirname "$0")/.."

failures=0
fail() { printf 'NG  %s\n' "$*"; failures=$((failures + 1)); }
ok() { printf 'ok  %s\n' "$*"; }

version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)
if [[ -z "$version" ]]; then
    fail "pyproject.toml: version not found"
else
    ok "pyproject.toml: version $version"
fi

# Changelog: the newest released section must be the pyproject version.
latest=$(sed -n 's/^## \[\([0-9][^]]*\)\].*/\1/p' Changelog.md | head -1)
if [[ "$latest" == "$version" ]]; then
    ok "Changelog.md: latest release section is $latest"
else
    fail "Changelog.md: latest release section is '${latest:-none}', expected '$version'" \
        "(move the Unreleased entries into a [$version] section)"
fi
if grep -q "^\[$version\]: " Changelog.md; then
    ok "Changelog.md: link reference for $version"
else
    fail "Changelog.md: missing link reference '[$version]: ...'"
fi

# READMEs state the current version in their status section.
for readme in README.md README_ja.md; do
    if grep -qF "($version)" "$readme"; then
        ok "$readme: mentions ($version)"
    else
        fail "$readme: does not mention ($version) in the status section"
    fi
done

# Installed package metadata must match pyproject (catches a stale environment).
installed=$(uv run python -c 'import crawl4tools; print(crawl4tools.__version__)' 2>/dev/null)
if [[ "$installed" == "$version" ]]; then
    ok "installed crawl4tools: $installed"
else
    fail "installed crawl4tools is '${installed:-unknown}', expected '$version' (run 'uv sync')"
fi

if [[ -z "$(git status --porcelain)" ]]; then
    ok "git: working tree is clean"
else
    fail "git: working tree has uncommitted changes"
fi

if tools/check.sh >/dev/null 2>&1; then
    ok "tools/check.sh passed"
else
    fail "tools/check.sh failed (run it directly to see the output)"
fi

if ((failures > 0)); then
    printf '\nrelease_check.sh: %d problem(s)\n' "$failures"
    exit 1
fi
printf '\nrelease_check.sh: ready to release %s\n' "$version"
