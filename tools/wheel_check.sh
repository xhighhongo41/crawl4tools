#!/usr/bin/env bash
# Check that a built wheel ships the expected package data (the compiled Japanese
# message catalog and the py.typed marker) and does not declare a runtime dependency
# on Babel (Babel is a dev-only build tool). Prints one "ok"/"NG  " line per check.
#
# Shared by tools/release_check.sh and CI's package job:
#   tools/wheel_check.sh dist/crawl4tools-*.whl
#
# Exits 1 if any check fails, 2 on a usage error (wrong argument count, or the file
# does not exist).
set -uo pipefail

if (($# != 1)); then
    printf 'usage: tools/wheel_check.sh <wheel>\n' >&2
    exit 2
fi
wheel=$1
if [[ ! -f "$wheel" ]]; then
    printf 'usage: tools/wheel_check.sh <wheel>: no such file: %s\n' "$wheel" >&2
    exit 2
fi
# Resolve to an absolute path against the caller's cwd, before the "cd" below (to
# this repo's root, so "uv run" finds the project) can change what a relative path
# the caller gave us would mean.
wheel=$(cd "$(dirname -- "$wheel")" && pwd)/$(basename -- "$wheel")

cd "$(dirname "$0")/.." || exit 1

failures=0
fail() { printf 'NG  %s\n' "$*"; failures=$((failures + 1)); }
ok() { printf 'ok  %s\n' "$*"; }

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

((failures == 0))
