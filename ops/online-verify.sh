#!/usr/bin/env bash

set -euo pipefail

base_url="${PAPERGUARD_ONLINE_BASE_URL:-}"
admin_username="${PAPERGUARD_ONLINE_ADMIN_USERNAME:-}"
admin_password="${PAPERGUARD_ONLINE_ADMIN_PASSWORD:-}"
timeout_seconds="${PAPERGUARD_ONLINE_TIMEOUT_SECONDS:-15}"
evidence_path="${PAPERGUARD_ONLINE_EVIDENCE_PATH:-online-verification-evidence.json}"
checks='[]'
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/paperguard-online-verify.XXXXXX")"

cleanup() {
  rm -rf -- "$temp_dir"
}

trap cleanup EXIT

record_check() {
  local name="$1"
  local status="$2"
  checks="$(jq -c --arg name "$name" --arg status "$status" '. + [{name: $name, status: $status}]' <<< "$checks")"
}

write_evidence() {
  local result="$1"
  jq -n \
    --arg result "$result" \
    --arg target_base_url "$base_url" \
    --arg completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg source_revision "${PAPERGUARD_ONLINE_SOURCE_REVISION:-unknown}" \
    --argjson checks "$checks" \
    '{format:"paperguard-online-verification", format_version:1, result:$result, read_only:true, target_base_url:$target_base_url, completed_at:$completed_at, source_revision:$source_revision, checks:$checks}' \
    > "$evidence_path"
}

fail_now() {
  local message="$1"
  record_check "verification" "failed"
  write_evidence "failed"
  printf 'online verification failed: %s\n' "$message" >&2
  exit 1
}

if [[ -z "$base_url" ]]; then
  fail_now "PAPERGUARD_ONLINE_BASE_URL is required"
fi

base_url="${base_url%/}"
if [[ "$base_url" == *"@"* || "$base_url" == *"?"* || "$base_url" == *"#"* ]]; then
  fail_now "the base URL must not contain credentials, a query, or a fragment"
fi

case "$base_url" in
  https://*) ;;
  http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*) ;;
  *) fail_now "the target must use HTTPS, except for a local loopback rehearsal" ;;
esac

if ! [[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
  fail_now "PAPERGUARD_ONLINE_TIMEOUT_SECONDS must be a positive integer"
fi

if [[ -z "$admin_username" || -z "$admin_password" ]]; then
  fail_now "online admin credentials are required"
fi

request_public() {
  local name="$1"
  local path="$2"
  local output="$3"
  if ! curl --fail --silent --show-error --max-time "$timeout_seconds" \
    -H 'Accept: application/json' \
    -o "$output" "$base_url$path"; then
    fail_now "$name request failed"
  fi
  record_check "$name" "passed"
}

request_json() {
  local name="$1"
  local path="$2"
  local filter="$3"
  local output="$4"
  request_public "$name" "$path" "$output"
  if ! jq -e "$filter" "$output" >/dev/null; then
    fail_now "$name response shape was unexpected"
  fi
}

request_auth_json() {
  local name="$1"
  local path="$2"
  local filter="$3"
  local output="$4"
  if ! curl --fail --silent --show-error --max-time "$timeout_seconds" \
    --config "$auth_config" \
    -o "$output" "$base_url$path"; then
    fail_now "$name request failed"
  fi
  if ! jq -e "$filter" "$output" >/dev/null; then
    fail_now "$name response shape was unexpected"
  fi
  record_check "$name" "passed"
}

request_public "frontend.logo" "/logo.png" "$temp_dir/logo.png"
request_public "frontend.avatar" "/avatar.png" "$temp_dir/avatar.png"
request_public "health.live" "/api/health/live" "$temp_dir/live.json"
request_public "health.ready" "/api/health/ready" "$temp_dir/ready.json"

login_payload="$(PAPERGUARD_ONLINE_ADMIN_PASSWORD="$admin_password" jq -nc \
  --arg username "$admin_username" \
  '{username:$username,password:$ENV.PAPERGUARD_ONLINE_ADMIN_PASSWORD}')"
if ! printf '%s' "$login_payload" | curl --fail --silent --show-error --max-time "$timeout_seconds" \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  --data-binary @- \
  -o "$temp_dir/login.json" "$base_url/api/auth/login"; then
  fail_now "auth.login request failed"
fi
token="$(jq -r '.token // empty' "$temp_dir/login.json")"
if [[ -z "$token" ]]; then
  fail_now "auth.login response did not contain a token"
fi
# Keep the bearer token out of the curl process arguments and command output.
auth_config="$temp_dir/auth.conf"
printf 'header = "Accept: application/json"\nheader = "Authorization: Bearer %s"\n' "$token" > "$auth_config"
record_check "auth.login" "passed"

request_auth_json "auth.me" "/api/auth/me" 'type == "object" and (.username | type == "string")' "$temp_dir/me.json"
request_auth_json "templates.list" "/api/templates" 'type == "array"' "$temp_dir/templates.json"
request_auth_json "document-packages.list" "/api/document-packages" 'type == "array"' "$temp_dir/packages.json"
request_auth_json "audit.list" "/api/audit?limit=1" 'type == "array"' "$temp_dir/audit.json"

write_evidence "passed"
printf 'online verification passed: %s checks; evidence written to %s\n' "$(jq 'length' <<< "$checks")" "$evidence_path"
