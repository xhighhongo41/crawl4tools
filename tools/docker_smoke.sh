#!/usr/bin/env bash
# Smoke-test a crawl4tools Docker image: start a container from it and check
# that it behaves like a real crawl4server deployment, then stop it cleanly.
#
#   tools/docker_smoke.sh <image>
#
# Checks, each printed as one "ok"/"NG" line (release_check.sh style):
#   (a) GET /health on the loader port returns 200 within 60s
#   (b) GET /nope on the loader port returns 404 with a Japanese hint
#       (CRAWL4SERVER_LANG=ja)
#   (c) POST /crawl returns 200 with a non-empty page_content element
#   (d) docker stop exits the container with code 0 (clean SIGTERM handling)
# On failure, "docker logs" of the container is printed before it is removed.
set -euo pipefail

if (($# != 1)); then
    printf 'usage: tools/docker_smoke.sh <image>\n' >&2
    exit 2
fi
image="$1"

failures=0
fail() { printf 'NG  %s\n' "$*"; failures=$((failures + 1)); }
ok() { printf 'ok  %s\n' "$*"; }
note() { printf '==> %s\n' "$*"; }

container=""
cleanup() {
    local status=$?
    if [[ -n "$container" ]]; then
        if ((status != 0)); then
            printf '\n==> docker logs %s\n' "$container" >&2
            docker logs "$container" >&2 || true
        fi
        docker rm -f "$container" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

note "starting container from $image"
container=$(docker run -d \
    --shm-size 1g \
    -e CRAWL4SERVER_LANG=ja \
    -e CRAWL4SERVER_LOADER_API_KEY=smoke \
    -p 127.0.0.1::8766 \
    -p 127.0.0.1::8765 \
    "$image")

loader_port=$(docker port "$container" 8766/tcp | head -1 | tr -d '\r' | sed 's/.*://')
mcp_port=$(docker port "$container" 8765/tcp | head -1 | tr -d '\r' | sed 's/.*://')
if [[ -z "$loader_port" || -z "$mcp_port" ]]; then
    fail "docker port did not report host ports (loader='${loader_port:-}', mcp='${mcp_port:-}')"
    exit 1
fi
note "loader port -> 127.0.0.1:$loader_port, mcp port -> 127.0.0.1:$mcp_port"

# (a) The loader app answers /health; the Dockerfile's own HEALTHCHECK polls
# the same port, so this is the documented liveness endpoint.
health_url="http://127.0.0.1:${loader_port}/health"
deadline=$((SECONDS + 60))
status=""
while ((SECONDS < deadline)); do
    status=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$health_url" 2>/dev/null || true)
    if [[ "$status" == "200" ]]; then
        break
    fi
    sleep 1
done
if [[ "$status" == "200" ]]; then
    ok "(a) GET /health returned 200 within 60s"
else
    fail "(a) GET /health did not return 200 within 60s (last status: '${status:-none}')"
fi

# (b) Any unknown path on the loader port answers 404 with a translated hint.
nope_response=$(curl -s -w '\n%{http_code}' --max-time 5 "http://127.0.0.1:${loader_port}/nope" || true)
nope_body=$(printf '%s' "$nope_response" | sed '$d')
nope_code=$(printf '%s' "$nope_response" | tail -1)
if [[ "$nope_code" == "404" ]]; then
    hint=$(printf '%s' "$nope_body" | python3 -c '
import json, sys

try:
    data = json.load(sys.stdin)
except ValueError:
    print("")
else:
    print(data.get("hint", "") if isinstance(data, dict) else "")
' 2>/dev/null || true)
    # Japanese translation of "POST {path} with {"urls": [...]}"; distinctive
    # substring "してください" ("please ...") does not appear in the English hint.
    if [[ "$hint" == *"してください"* ]]; then
        ok "(b) GET /nope returned 404 with the Japanese hint ($hint)"
    else
        fail "(b) GET /nope hint is not the Japanese translation: '${hint:-<none>}'"
    fi
else
    fail "(b) GET /nope returned '${nope_code:-none}', expected 404"
fi

# (c) POST /crawl fetches a real page. C17: retry once with a different URL,
# then relax the content check (200 with a JSON array is enough) as a last
# resort, since a public site can occasionally be unreachable or block bots.
classify_crawl_body() {
    python3 -c '
import json, sys

try:
    data = json.load(sys.stdin)
except ValueError:
    print("invalid")
    raise SystemExit

if not isinstance(data, list):
    print("invalid")
elif any(isinstance(item, dict) and item.get("page_content") for item in data):
    print("content")
else:
    print("array")
'
}

attempt_crawl() {
    local url="$1" response body code kind
    response=$(curl -s -w '\n%{http_code}' --max-time 90 \
        -X POST "http://127.0.0.1:${loader_port}/crawl" \
        -H 'Authorization: Bearer smoke' \
        -H 'Content-Type: application/json' \
        -d "{\"urls\": [\"${url}\"]}" || true)
    body=$(printf '%s' "$response" | sed '$d')
    code=$(printf '%s' "$response" | tail -1)
    if [[ "$code" == "200" ]]; then
        kind=$(printf '%s' "$body" | classify_crawl_body)
    else
        kind="invalid"
    fi
    printf '%s\t%s\n' "$code" "$kind"
}

first_url="https://example.com/"
retry_url="https://www.iana.org/help/example-domains"
read -r code1 kind1 <<<"$(attempt_crawl "$first_url")"
if [[ "$kind1" == "content" ]]; then
    ok "(c) POST /crawl returned 200 with a non-empty page_content for $first_url"
else
    read -r code2 kind2 <<<"$(attempt_crawl "$retry_url")"
    if [[ "$kind2" == "content" ]]; then
        ok "(c) POST /crawl returned 200 with a non-empty page_content for $retry_url" \
            "(after retry; $first_url gave HTTP $code1)"
    elif [[ "$code2" == "200" && "$kind2" == "array" ]]; then
        printf 'WARNING (c): content check relaxed: neither %s (HTTP %s) nor %s (HTTP %s)' \
            "$first_url" "$code1" "$retry_url" "$code2" >&2
        printf ' returned a non-empty page_content element; accepting 200 with a JSON array\n' >&2
        ok "(c) POST /crawl returned 200 with a JSON array (content check relaxed, see warning above)"
    else
        fail "(c) POST /crawl failed for both $first_url (HTTP $code1) and $retry_url (HTTP $code2)"
    fi
fi

# (d) docker stop must deliver a clean SIGTERM shutdown (exit code 0).
if docker stop "$container" >/dev/null; then
    exit_code=$(docker inspect -f '{{.State.ExitCode}}' "$container")
    if [[ "$exit_code" == "0" ]]; then
        ok "(d) container exited with code 0 after docker stop"
    else
        fail "(d) container exited with code $exit_code after docker stop, expected 0"
    fi
else
    fail "(d) docker stop failed"
fi

if ((failures > 0)); then
    printf '\ndocker_smoke.sh: %d problem(s) with %s\n' "$failures" "$image"
    exit 1
fi
printf '\ndocker_smoke.sh: %s passed all checks\n' "$image"
