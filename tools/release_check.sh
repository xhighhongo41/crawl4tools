#!/usr/bin/env bash
# Pre-release checks: version declarations agree and are normalized PEP 440, the Changelog
# and READMEs are in their final release-day state, the tag is free, the working tree is
# clean and pushed, check.sh passes, the package builds cleanly with the right contents
# (via tools/wheel_check.sh), compose.yaml points at the published Docker Hub image tag,
# the release workflow exists and triggers on a published GitHub Release, and (best-effort,
# needs the gh CLI, logged in) the release-time GitHub secrets are registered, the pypi
# deployment environment exists, and CI (ci.yml) succeeded on HEAD.
# Prints one line per check ("ok", "NG", or "skip" for a check that cannot run here) and
# every problem before exiting non-zero.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

check_args=()
if (($# == 1)) && [[ "$1" == "--integration" ]]; then
    check_args=(--integration)
elif (($# != 0)); then
    printf 'usage: tools/release_check.sh [--integration]\n' >&2
    exit 2
fi

failures=0
skipped=0
fail() { printf 'NG  %s\n' "$*"; failures=$((failures + 1)); }
ok() { printf 'ok  %s\n' "$*"; }
skip() { printf 'skip %s\n' "$*"; skipped=$((skipped + 1)); }

version=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -1)
if [[ -z "$version" ]]; then
    fail "pyproject.toml: version not found"
else
    ok "pyproject.toml: version $version"
fi

# The version is written in normalized PEP 440 form, so it matches the tag and the
# Docker image tag byte for byte (str(Version(v)) == v).
if [[ -n "$version" ]]; then
    normalized=$(uv run python -c '
import sys

from packaging.version import Version

v = sys.argv[1]
print(str(Version(v)) == v)
' "$version")
    if [[ "$normalized" == "True" ]]; then
        ok "pyproject.toml: version $version is normalized PEP 440"
    else
        fail "pyproject.toml: version '$version' is not normalized PEP 440" \
            "(str(Version(v)) != v; use the normal form, e.g. '1.0.0b1' not '1.0.0beta1')"
    fi
else
    skip "pyproject.toml: version is normalized PEP 440 (version not found)"
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

if tools/check.sh "${check_args[@]}" >/dev/null 2>&1; then
    ok "tools/check.sh passed"
else
    fail "tools/check.sh failed (run it directly to see the output)"
fi

# 1. compose.yaml pulls the published Docker Hub image at the release tag.
compose_image=$(sed -n 's/^[[:space:]]*image:[[:space:]]*//p' compose.yaml | head -1)
if [[ "$compose_image" == "xhighhongo41/crawl4tools:$version" ]]; then
    ok "compose.yaml: image is xhighhongo41/crawl4tools:$version"
else
    fail "compose.yaml: image is '${compose_image:-none}'," \
        "expected 'xhighhongo41/crawl4tools:$version'"
fi

# 2a. The [Unreleased] section holds no unreleased entries.
unreleased_body=$(awk '
    /^## \[Unreleased\]/ { found = 1; next }
    found && /^## \[/ { exit }
    found { print }
' Changelog.md)
if grep -q '^## \[Unreleased\]' Changelog.md; then
    if printf '%s\n' "$unreleased_body" | grep -q '^- '; then
        fail "Changelog.md: [Unreleased] section is not empty (move its entries into [$version])"
    else
        ok "Changelog.md: [Unreleased] section is empty"
    fi
else
    fail "Changelog.md: missing '## [Unreleased]' heading"
fi

# 2b. The release heading carries a release date (YYYY-MM-DD).
version_date=$(sed -n "s/^## \[$version\] - \([0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]\)\$/\1/p" \
    Changelog.md | head -1)
if [[ -n "$version_date" ]]; then
    ok "Changelog.md: [$version] heading has a release date ($version_date)"
elif grep -q "^## \[$version\]" Changelog.md; then
    fail "Changelog.md: '## [$version]' heading is missing a release date" \
        "(expected '## [$version] - YYYY-MM-DD')"
else
    fail "Changelog.md: missing '## [$version]' heading"
fi

# 2c. The [Unreleased] link reference compares from this release to HEAD.
unreleased_link=$(grep -m1 "^\[Unreleased\]: " Changelog.md)
if [[ "$unreleased_link" == *"/compare/v$version...HEAD" ]]; then
    ok "Changelog.md: [Unreleased] link reference targets v$version...HEAD"
else
    fail "Changelog.md: '[Unreleased]:' link reference is '${unreleased_link:-missing}'," \
        "expected to end with '/compare/v$version...HEAD'"
fi

# 2d. The release link reference mentions the release tag.
version_link=$(grep -m1 "^\[$version\]: " Changelog.md)
if [[ "$version_link" == *"v$version"* ]]; then
    ok "Changelog.md: [$version] link reference mentions v$version"
else
    fail "Changelog.md: '[$version]:' link reference is '${version_link:-missing}'," \
        "expected to contain 'v$version'"
fi

# 3. The release tag does not exist yet, locally or on origin.
if git tag -l "v$version" | grep -q .; then
    fail "git: tag v$version already exists locally"
else
    ok "git: tag v$version does not exist locally"
fi
remote_tags=$(git ls-remote --tags origin "refs/tags/v$version" 2>&1)
remote_status=$?
if ((remote_status != 0)); then
    skip "git: could not query origin for tag v$version ($remote_tags)"
elif [[ -n "$remote_tags" ]]; then
    fail "git: tag v$version already exists on origin"
else
    ok "git: tag v$version does not exist on origin"
fi

# 4. Every console script reports the release version.
for cmd in crawl4cli crawl4mcp crawl4server; do
    version_line=$(uv run "$cmd" --version 2>&1 | head -1)
    if [[ "$version_line" == "$cmd $version "* ]]; then
        ok "$cmd --version: $version_line"
    else
        fail "$cmd --version starts with '${version_line:-nothing}', expected '$cmd $version ...'"
    fi
done

# 5. The wheel builds cleanly and ships the right files (checked by tools/wheel_check.sh,
# shared with CI's package job).
build_dir=$(mktemp -d)
trap 'rm -rf "$build_dir"' EXIT
build_log=$(uv build --out-dir "$build_dir" 2>&1)
build_status=$?
if ((build_status == 0)); then
    ok "uv build: succeeded"
else
    fail "uv build failed: $(printf '%s' "$build_log" | tail -3)"
fi
wheel=$(find "$build_dir" -name '*.whl' | head -1)
if [[ -n "$wheel" ]]; then
    wheel_output=$(tools/wheel_check.sh "$wheel")
    printf '%s\n' "$wheel_output"
    wheel_ng=$(grep -c '^NG  ' <<<"$wheel_output" || true)
    failures=$((failures + wheel_ng))
else
    fail "wheel: no wheel found in $build_dir (uv build failed)"
    fail "wheel: missing crawl4tools/locale/ja/LC_MESSAGES/crawl4tools.mo (no wheel to check)"
    fail "wheel: missing crawl4tools/py.typed (no wheel to check)"
fi

# 6. Both READMEs have the same heading structure (lines outside fenced code blocks).
readme_h2=$(awk '/^```/{c=!c; next} !c && /^## /{n++} END{print n+0}' README.md)
readme_h3=$(awk '/^```/{c=!c; next} !c && /^### /{n++} END{print n+0}' README.md)
readme_ja_h2=$(awk '/^```/{c=!c; next} !c && /^## /{n++} END{print n+0}' README_ja.md)
readme_ja_h3=$(awk '/^```/{c=!c; next} !c && /^### /{n++} END{print n+0}' README_ja.md)
if [[ "$readme_h2" == "$readme_ja_h2" && "$readme_h3" == "$readme_ja_h3" ]]; then
    ok "READMEs: same heading counts (## $readme_h2, ### $readme_h3)"
else
    fail "READMEs: heading counts differ (README.md: ## $readme_h2 ### $readme_h3;" \
        "README_ja.md: ## $readme_ja_h2 ### $readme_ja_h3)"
fi

# 7. HEAD has been pushed to its upstream branch (no fetch; local refs only).
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)
if [[ -z "$upstream" ]]; then
    fail "git: no upstream branch (push the branch first)"
else
    upstream_sha=$(git rev-parse '@{u}')
    head_sha=$(git rev-parse HEAD)
    if [[ "$upstream_sha" == "$head_sha" ]]; then
        ok "git: HEAD is pushed to $upstream"
    else
        fail "git: HEAD is not pushed to $upstream"
    fi
fi

# 8. docker compose config is valid, when docker is available here.
if command -v docker >/dev/null 2>&1; then
    if docker compose config -q; then
        ok "docker compose config: valid"
    else
        fail "docker compose config: invalid (see output above)"
    fi
else
    skip "docker compose config: docker is not installed"
fi

# 9. release.yml exists and publishes on a published GitHub Release.
release_workflow=.github/workflows/release.yml
if [[ -f "$release_workflow" ]]; then
    has_published=$(uv run python - "$release_workflow" <<'PY'
import sys

import yaml

with open(sys.argv[1], encoding="utf-8") as f:
    data = yaml.safe_load(f)

# PyYAML parses the unquoted key "on:" as the boolean True.
on = data.get(True, data.get("on")) or {}
types = (on.get("release") or {}).get("types") or []
print("published" in types)
PY
)
    if [[ "$has_published" == "True" ]]; then
        ok "$release_workflow: release.types includes 'published'"
    else
        fail "$release_workflow: release.types does not include 'published'"
    fi
else
    fail "$release_workflow: not found"
fi

# 10.-12. GitHub-side release readiness (best-effort: needs gh, installed and logged in).
gh_ready=0
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh_ready=1
fi
repo_slug=""
if ((gh_ready)); then
    repo_slug=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)
fi
if [[ -z "$repo_slug" ]]; then
    origin_url=$(git remote get-url origin 2>/dev/null)
    repo_slug=$(sed -E 's#^(git@github\.com:|https://github\.com/)([^/]+/[^/.]+)(\.git)?$#\2#' \
        <<<"$origin_url")
fi
if ((gh_ready)) && [[ -z "$repo_slug" ]]; then
    gh_ready=0
fi

# 10. The Docker Hub publishing secrets are registered (names only; values are never
# read or printed).
if ((gh_ready)); then
    if secret_names=$(gh secret list --repo "$repo_slug" --json name --jq '.[].name' 2>&1); then
        for secret in DOCKERHUB_USERNAME DOCKERHUB_TOKEN; do
            if grep -qx "$secret" <<<"$secret_names"; then
                ok "gh secret: $secret is registered"
            else
                fail "gh secret: $secret is not registered"
            fi
        done
    else
        fail "gh secret: could not list the repository secrets ($secret_names)"
    fi
else
    skip "gh secret: DOCKERHUB_USERNAME is registered (gh is not installed or not logged in)"
    skip "gh secret: DOCKERHUB_TOKEN is registered (gh is not installed or not logged in)"
fi

# 11. The pypi deployment environment exists (release.yml's pypi job needs it).
if ((gh_ready)); then
    if gh api "repos/$repo_slug/environments/pypi" >/dev/null 2>&1; then
        ok "gh api: environment 'pypi' exists"
    else
        fail "gh api: environment 'pypi' does not exist" \
            "(repos/$repo_slug/environments/pypi; create it before the release)"
    fi
else
    skip "gh api: environment 'pypi' exists (gh is not installed or not logged in)"
fi

# 12. CI (ci.yml) succeeded on HEAD.
if ((gh_ready)); then
    head_sha=$(git rev-parse HEAD)
    run_query=$(gh run list --commit "$head_sha" --workflow ci.yml --json status,conclusion \
        --jq '(.[0].status // "") + "\t" + (.[0].conclusion // "")' 2>&1)
    run_status=$?
    if ((run_status != 0)); then
        fail "gh run: could not query ci.yml runs for HEAD ($head_sha): $run_query" \
            "(push the branch and wait for CI)"
    else
        IFS=$'\t' read -r ci_status ci_conclusion <<<"$run_query"
        if [[ -z "$ci_status" ]]; then
            fail "gh run: no ci.yml run found for HEAD ($head_sha) (push the branch and wait" \
                "for CI)"
        elif [[ "$ci_status" != "completed" ]]; then
            fail "gh run: ci.yml run for HEAD is '$ci_status' (wait for CI to finish)"
        elif [[ "$ci_conclusion" == "success" ]]; then
            ok "gh run: ci.yml succeeded on HEAD ($head_sha)"
        else
            fail "gh run: ci.yml run for HEAD concluded '$ci_conclusion', expected 'success'"
        fi
    fi
else
    skip "gh run: ci.yml succeeded on HEAD (gh is not installed or not logged in)"
fi

# 13. Every classifier in pyproject.toml is a real, registered trove classifier
# (PyPI rejects an unregistered one at upload time). trove-classifiers is not a
# project dependency, so it is pulled on the fly with --with; when uv cannot
# reach the network to do that, the check is skipped rather than failed.
classifier_check=$(uv run --quiet --no-project --isolated --with trove-classifiers python - <<'PY' 2>&1
import tomllib

from trove_classifiers import classifiers

with open("pyproject.toml", "rb") as f:
    data = tomllib.load(f)

unknown = [c for c in data["project"]["classifiers"] if c not in classifiers]
print("UNKNOWN:" + "|".join(unknown) if unknown else "OK")
PY
)
classifier_status=$?
if ((classifier_status != 0)); then
    skip "pyproject.toml: classifiers are registered trove classifiers" \
        "(could not run: ${classifier_check//$'\n'/ })"
elif [[ "$classifier_check" == "OK" ]]; then
    ok "pyproject.toml: classifiers are registered trove classifiers"
else
    fail "pyproject.toml: unknown classifier(s): ${classifier_check#UNKNOWN:}"
fi

if ((failures > 0)); then
    printf '\nrelease_check.sh: %d problem(s)' "$failures"
    if ((skipped > 0)); then
        printf ' (%d check(s) skipped)' "$skipped"
    fi
    printf '\n'
    exit 1
fi
if ((skipped > 0)); then
    printf '\nrelease_check.sh: ready to release %s (%d check(s) skipped)\n' "$version" "$skipped"
else
    printf '\nrelease_check.sh: ready to release %s\n' "$version"
fi
