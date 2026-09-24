#!/usr/bin/env bash
# Maintain the message catalogs under src/crawl4tools/locale (needs the dev dependencies).
#
#   tools/i18n.sh update   extract the messages from src/crawl4tools and merge them into
#                          every catalog, creating a missing catalog first
#   tools/i18n.sh compile  compile the catalogs to the .mo files that ship with the package
#
# After "update", translate the new or empty entries in the .po files, then run "compile".
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

LOCALE_DIR=src/crawl4tools/locale
DOMAIN=crawl4tools
LANGUAGES=(ja)

usage() {
    echo "usage: tools/i18n.sh update|compile" >&2
    exit 2
}

project_version() {
    uv run python -c \
        'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
}

update() {
    local tmp
    tmp=$(mktemp -d)
    # shellcheck disable=SC2064  # expand $tmp now: it is local to this function
    trap "rm -rf '$tmp'" EXIT
    uv run pybabel extract \
        --project crawl4tools \
        --version "$(project_version)" \
        --copyright-holder xhighhongo41 \
        --msgid-bugs-address https://github.com/xhighhongo41/crawl4tools/issues \
        --no-location \
        -o "$tmp/$DOMAIN.pot" \
        src/crawl4tools
    local lang
    for lang in "${LANGUAGES[@]}"; do
        uv run pybabel update \
            -i "$tmp/$DOMAIN.pot" \
            -d "$LOCALE_DIR" \
            -D "$DOMAIN" \
            -l "$lang" \
            --init-missing \
            --no-fuzzy-matching \
            --ignore-obsolete \
            --ignore-pot-creation-date
    done
}

compile() {
    uv run pybabel compile -d "$LOCALE_DIR" -D "$DOMAIN" --statistics
}

[ $# -eq 1 ] || usage
case "$1" in
    update) update ;;
    compile) compile ;;
    *) usage ;;
esac
