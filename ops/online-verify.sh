#!/usr/bin/env bash

set -euo pipefail

umask 077

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base_url="${PAPERGUARD_ONLINE_BASE_URL:-}"
admin_username="${PAPERGUARD_ONLINE_ADMIN_USERNAME:-}"
admin_password="${PAPERGUARD_ONLINE_ADMIN_PASSWORD:-}"
timeout_seconds="${PAPERGUARD_ONLINE_TIMEOUT_SECONDS:-15}"
evidence_path="${PAPERGUARD_ONLINE_EVIDENCE_PATH:-online-verification-evidence.json}"
expected_backend_revision="${PAPERGUARD_ONLINE_EXPECTED_BACKEND_REVISION:-}"
deployed_backend='null'
session_metadata_written=false
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
  if ! jq -n \
      --arg result "$result" \
      --arg target_base_url "$base_url" \
      --arg completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --arg verifier_source_revision "${PAPERGUARD_ONLINE_SOURCE_REVISION:-unknown}" \
      --arg expected_backend_revision "$expected_backend_revision" \
      --argjson deployed_backend "$deployed_backend" \
      --argjson session_metadata_written "$session_metadata_written" \
      --argjson checks "$checks" \
      '{format:"paperguard-online-verification", format_version:3, result:$result, non_destructive:true, session_metadata_written:$session_metadata_written, target_base_url:$target_base_url, completed_at:$completed_at, verifier_source_revision:$verifier_source_revision, expected_backend_revision:(if $expected_backend_revision == "" then null else $expected_backend_revision end), deployed_backend:$deployed_backend, checks:$checks}' \
      > "$evidence_path"; then
    printf 'unable to write verification evidence to %s\n' "$evidence_path" >&2
  fi
}

fail_now() {
  local name="$1"
  local message="$2"
  record_check "$name" "failed"
  write_evidence "failed"
  printf 'online verification failed (%s): %s\n' "$name" "$message" >&2
  exit 1
}

if [[ -z "$base_url" ]]; then
  fail_now "input.base_url" "PAPERGUARD_ONLINE_BASE_URL is required"
fi

base_url="${base_url%/}"
if [[ "$base_url" == *"@"* || "$base_url" == *"?"* || "$base_url" == *"#"* ]]; then
  base_url=""
  fail_now "input.base_url" "the base URL must not contain credentials, a query, or a fragment"
fi

case "$base_url" in
  https://*) ;;
  http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*) ;;
  *) base_url=""; fail_now "input.base_url" "the target must use HTTPS, except for a local loopback rehearsal" ;;
esac

if ! [[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
  fail_now "input.timeout" "PAPERGUARD_ONLINE_TIMEOUT_SECONDS must be a positive integer"
fi

if [[ -z "$admin_username" || -z "$admin_password" ]]; then
  fail_now "input.credentials" "online admin credentials are required"
fi
if [[ -n "$expected_backend_revision" && ! "$expected_backend_revision" =~ ^[0-9a-f]{40}$ ]]; then
  expected_backend_revision=""
  fail_now "input.expected_backend_revision" "the expected backend revision must be a full lowercase SHA"
fi

request_body() {
  local name="$1"
  local path="$2"
  local output="$3"
  if ! curl --fail --silent --show-error --max-time "$timeout_seconds" \
    -H 'Accept: application/json' \
    -o "$output" "$base_url$path"; then
    fail_now "$name" "request failed"
  fi
  if [[ ! -s "$output" ]]; then
    fail_now "$name" "response body was empty"
  fi
}

request_json() {
  local name="$1"
  local path="$2"
  local filter="$3"
  local output="$4"
  request_body "$name" "$path" "$output"
  if ! jq -e "$filter" "$output" >/dev/null 2>&1; then
    fail_now "$name" "response shape was unexpected"
  fi
  record_check "$name" "passed"
}

request_auth_json() {
  local name="$1"
  local path="$2"
  local filter="$3"
  local output="$4"
  if ! curl --fail --silent --show-error --max-time "$timeout_seconds" \
    --config "$auth_config" \
    -o "$output" "$base_url$path"; then
    fail_now "$name" "request failed"
  fi
  if [[ ! -s "$output" ]]; then
    fail_now "$name" "response body was empty"
  fi
  if ! jq -e "$filter" "$output" >/dev/null 2>&1; then
    fail_now "$name" "response shape was unexpected"
  fi
  record_check "$name" "passed"
}

request_body "frontend.logo" "/logo.png" "$temp_dir/logo.png"
record_check "frontend.logo" "passed"
request_body "frontend.avatar" "/avatar.png" "$temp_dir/avatar.png"
record_check "frontend.avatar" "passed"
request_json "health.live" "/api/health/live" \
  'type == "object" and .status == "ok" and .service == "paperguard" and .probe == "live"' \
  "$temp_dir/live.json"
request_json "health.ready" "/api/health/ready" \
  'type == "object" and .status == "ok" and .service == "paperguard" and .probe == "ready"' \
  "$temp_dir/ready.json"

request_body "version.backend" "/api/version" "$temp_dir/version.json"
if ! PYTHONPATH="$repo_root/backend" python3 -m app.build_identity \
  --validate-public "$temp_dir/version.json" >/dev/null 2>&1; then
  fail_now "version.backend" "backend build identity was missing, invalid, or inconsistent"
fi
deployed_backend="$(jq -c '.' "$temp_dir/version.json")"
record_check "version.backend" "passed"
if [[ -n "$expected_backend_revision" ]]; then
  if [[ "$(jq -r '.source_revision' "$temp_dir/version.json")" != "$expected_backend_revision" ]]; then
    fail_now "version.expected_revision" "deployed backend differs from the deployment record"
  fi
  record_check "version.expected_revision" "passed"
fi

login_payload="$(PAPERGUARD_ONLINE_ADMIN_PASSWORD="$admin_password" jq -nc \
  --arg username "$admin_username" \
  '{username:$username,password:$ENV.PAPERGUARD_ONLINE_ADMIN_PASSWORD}')"
session_metadata_written=true
if ! printf '%s' "$login_payload" | curl --fail --silent --show-error --max-time "$timeout_seconds" \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/json' \
  --data-binary @- \
  -o "$temp_dir/login.json" "$base_url/api/auth/login"; then
  fail_now "auth.login" "request failed"
fi
if ! token="$(jq -er 'if (.token | type) == "string" and (.token | length) > 0 then .token else error("missing token") end' "$temp_dir/login.json" 2>/dev/null)"; then
  fail_now "auth.login" "response did not contain a valid token"
fi
if ! [[ "$token" =~ ^[0-9a-fA-F]{64}$ ]]; then
  fail_now "auth.login" "response token format was unexpected"
fi
# Keep the bearer token out of the curl process arguments and command output.
auth_config="$temp_dir/auth.conf"
printf 'header = "Accept: application/json"\nheader = "Authorization: Bearer %s"\n' "$token" > "$auth_config"
record_check "auth.login" "passed"

request_auth_json "auth.me" "/api/auth/me" 'type == "object" and (.username | type == "string")' "$temp_dir/me.json"
request_auth_json "document-packages.list" "/api/document-packages" 'type == "array"' "$temp_dir/packages.json"
request_auth_json "audit.list" "/api/audit?limit=1" 'type == "array"' "$temp_dir/audit.json"

write_evidence "passed"
printf 'online verification passed: %s checks; evidence written to %s\n' "$(jq 'length' <<< "$checks")" "$evidence_path"
