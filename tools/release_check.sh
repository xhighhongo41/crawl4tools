#!/usr/bin/env bash
# Pre-release checks: version declarations agree, the Changelog and READMEs are in their
# final release-day state, the tag is free, the working tree is clean and pushed, check.sh
# passes, the package builds cleanly with the right contents, and (best-effort) the docker
# compose file is valid.
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

# 1. compose.yaml builds/publishes the release image tag.
compose_image=$(sed -n 's/^[[:space:]]*image:[[:space:]]*//p' compose.yaml | head -1)
if [[ "$compose_image" == "crawl4tools:$version" ]]; then
    ok "compose.yaml: image is crawl4tools:$version"
else
    fail "compose.yaml: image is '${compose_image:-none}', expected 'crawl4tools:$version'"
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

# 5. The wheel builds cleanly and ships the right files (Babel stays dev-only).
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
    wheel_report=$(uv run python - "$wheel" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    names = archive.namelist()
    metadata_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
    metadata = archive.read(metadata_name).decode("utf-8")

has_mo = "crawl4tools/locale/ja/LC_MESSAGES/crawl4tools.mo" in names
has_typed = "crawl4tools/py.typed" in names
has_babel = any(
    line.lower().startswith("requires-dist: babel") for line in metadata.splitlines()
)
print(int(has_mo), int(has_typed), int(has_babel))
PY
)
    read -r mo_ok typed_ok babel_present <<<"$wheel_report"
    if [[ "$mo_ok" == "1" ]]; then
        ok "wheel: contains crawl4tools/locale/ja/LC_MESSAGES/crawl4tools.mo"
    else
        fail "wheel: missing crawl4tools/locale/ja/LC_MESSAGES/crawl4tools.mo"
    fi
    if [[ "$typed_ok" == "1" ]]; then
        ok "wheel: contains crawl4tools/py.typed"
    else
        fail "wheel: missing crawl4tools/py.typed"
    fi
    if [[ "$babel_present" == "0" ]]; then
        ok "wheel: METADATA has no Requires-Dist: babel line"
    else
        fail "wheel: METADATA declares a runtime dependency on babel (must stay dev-only)"
    fi
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
