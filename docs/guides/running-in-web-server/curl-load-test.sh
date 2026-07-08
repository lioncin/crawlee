#!/usr/bin/env bash
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:3000}"
FETCH_URL="${FETCH_URL:-https://httpbin.org/delay/2}"
BURST="${BURST:-12}"
CURL_TIMEOUT_SECS="${CURL_TIMEOUT_SECS:-40}"
JSON_MODE=0

usage() {
    cat <<'USAGE'
Usage: curl-load-test.sh [--json]

Options:
  --json    Print one-line JSON summary (for CI)
  -h,--help Show help
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --json)
            JSON_MODE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    printf '%s' "$s"
}

payload() {
    printf '{"url":"%s"}' "$(json_escape "$FETCH_URL")"
}

request_once() {
    local out
    out="$(curl -sS -m "$CURL_TIMEOUT_SECS" \
        -H 'content-type: application/json' \
        -X POST "$API_BASE/fetch" \
        -d "$(payload)" \
        -w $'\n%{http_code}')"
    local code body
    code="$(printf '%s\n' "$out" | tail -n 1)"
    body="$(printf '%s\n' "$out" | sed '$d')"
    printf '%s\n%s' "$code" "$body"
}

first="$(request_once)"
first_code="$(printf '%s\n' "$first" | head -n 1)"
first_body="$(printf '%s\n' "$first" | tail -n +2)"

second="$(request_once)"
second_code="$(printf '%s\n' "$second" | head -n 1)"
second_body="$(printf '%s\n' "$second" | tail -n +2)"

cache_pass=false
if printf '%s' "$second_body" | grep -Eq '"cached"[[:space:]]*:[[:space:]]*true'; then
    cache_pass=true
fi

burst_file="$(mktemp)"
cleanup() {
    rm -f "$burst_file"
}
trap cleanup EXIT

for i in $(seq 1 "$BURST"); do
    (
        res="$(request_once)"
        code="$(printf '%s\n' "$res" | head -n 1)"
        body="$(printf '%s\n' "$res" | tail -n +2)"
        printf '%s\t%s\n' "$code" "$body" >> "$burst_file"
    ) &
done
wait

total="$(wc -l < "$burst_file" | tr -d ' ')"
cnt_200="$(awk -F'\t' '$1=="200"{n++} END{print n+0}' "$burst_file")"
cnt_429="$(awk -F'\t' '$1=="429"{n++} END{print n+0}' "$burst_file")"
cnt_other="$(awk -F'\t' '$1!="200" && $1!="429"{n++} END{print n+0}' "$burst_file")"

pass_429=false
if [ "$cnt_429" -gt 0 ]; then
    pass_429=true
fi

if [ "$JSON_MODE" -eq 1 ]; then
    printf '{"api_base":"%s","endpoint":"%s/fetch","target_url":"%s","burst":%s,"cache":{"first_status":%s,"second_status":%s,"hit":%s},"rate_limit":{"total":%s,"status_200":%s,"status_429":%s,"status_other":%s,"pass":%s}}\n' \
        "$(json_escape "$API_BASE")" \
        "$(json_escape "$API_BASE")" \
        "$(json_escape "$FETCH_URL")" \
        "$BURST" \
        "$first_code" \
        "$second_code" \
        "$cache_pass" \
        "$total" \
        "$cnt_200" \
        "$cnt_429" \
        "$cnt_other" \
        "$pass_429"
    exit 0
fi

printf '== Endpoint ==\n%s/fetch\n\n' "$API_BASE"
printf '== Target URL ==\n%s\n\n' "$FETCH_URL"

printf '== 1) Cache check (same URL twice) ==\n'
printf 'First status: %s\n' "$first_code"
printf 'First body: %s\n' "$first_body"
printf 'Second status: %s\n' "$second_code"
printf 'Second body: %s\n' "$second_body"

if [ "$cache_pass" = true ]; then
    printf 'Cache verification: PASS (second response is cached)\n\n'
else
    printf 'Cache verification: WARN (second response not marked cached)\n\n'
fi

printf '== 2) 429 check (burst=%s) ==\n' "$BURST"
printf 'Total: %s, 200: %s, 429: %s, other: %s\n' "$total" "$cnt_200" "$cnt_429" "$cnt_other"
if [ "$pass_429" = true ]; then
    printf '429 verification: PASS\n'
else
    printf '429 verification: WARN (no 429 observed, try larger BURST or slower FETCH_URL)\n'
fi

printf '\nSample responses:\n'
sed -n '1,5p' "$burst_file"
