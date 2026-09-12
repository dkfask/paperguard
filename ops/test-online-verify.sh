#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/paperguard-online-contract.XXXXXX")"
port_file="$temp_dir/port"
evidence_path="$temp_dir/evidence.json"
server_log="$temp_dir/server.log"
# 回环 mock 的口令每次随机生成, 避免在仓库中留下静态凭据
contract_password="$(python3 -c 'import secrets; print("contract-" + secrets.token_urlsafe(24))')"
: "${PAPERGUARD_ONLINE_ADMIN_PASSWORD:=$contract_password}"
export PAPERGUARD_ONLINE_ADMIN_PASSWORD

cleanup() {
  if [[ -n "${server_pid:-}" ]]; then
    kill "$server_pid" >/dev/null 2>&1 || true
    wait "$server_pid" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$temp_dir"
}

trap cleanup EXIT

python3 - "$port_file" "$contract_password" >"$server_log" 2>&1 <<'PY' &
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, contract_password = sys.argv[1], sys.argv[2]
token = "a" * 64


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _send(self, status, body, content_type):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path in {"/logo.png", "/avatar.png"}:
            self._send(200, b"asset", "image/png")
        elif self.path == "/api/health/live":
            self._send(200, {"status": "ok", "service": "paperguard", "probe": "live"}, "application/json")
        elif self.path == "/api/health/ready":
            self._send(200, {"status": "ok", "service": "paperguard", "probe": "ready"}, "application/json")
        elif self.path == "/api/auth/me":
            self._send(200, {"username": "contract-admin"}, "application/json")
        elif self.path == "/api/document-packages":
            self._send(200, [], "application/json")
        elif self.path == "/api/audit?limit=1":
            self._send(200, [], "application/json")
        elif self.path == "/api/templates":
            self._send(500, {"error": "legacy template list must not be called"}, "application/json")
        else:
            self._send(404, {"error": "not found"}, "application/json")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/auth/login" and payload.get("password") == contract_password:
            self._send(200, {"token": token, "user": {"username": "contract-admin"}}, "application/json")
        else:
            self._send(401, {"error": "unauthorized"}, "application/json")


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="ascii") as handle:
    handle.write(str(server.server_address[1]))
server.serve_forever()
PY
server_pid=$!

for _ in {1..50}; do
  if [[ -s "$port_file" ]]; then
    break
  fi
  sleep 0.1
done

if [[ ! -s "$port_file" ]]; then
  printf 'loopback mock did not start\n' >&2
  exit 1
fi

port="$(<"$port_file")"
PAPERGUARD_ONLINE_BASE_URL="http://127.0.0.1:$port" \
PAPERGUARD_ONLINE_ADMIN_USERNAME="contract-admin" \
PAPERGUARD_ONLINE_EVIDENCE_PATH="$evidence_path" \
PAPERGUARD_ONLINE_SOURCE_REVISION="contract-test" \
bash "$repo_root/ops/online-verify.sh"

jq -e '
  .result == "passed" and
  .format_version == 2 and
  .non_destructive == true and
  .session_metadata_written == true and
  (.checks | length) == 8 and
  all(.checks[]; .status == "passed")
' "$evidence_path" >/dev/null

failure_evidence_path="$temp_dir/failure-evidence.json"
if PAPERGUARD_ONLINE_BASE_URL="http://example.invalid" \
  PAPERGUARD_ONLINE_ADMIN_USERNAME="contract-admin" \
  PAPERGUARD_ONLINE_EVIDENCE_PATH="$failure_evidence_path" \
  bash "$repo_root/ops/online-verify.sh" >/dev/null 2>"$temp_dir/failure.stderr"; then
  printf 'invalid public HTTP target was accepted\n' >&2
  exit 1
fi

jq -e '
  .result == "failed" and
  .checks[-1].name == "input.base_url" and
  .checks[-1].status == "failed"
' "$failure_evidence_path" >/dev/null

printf 'online verifier contract passed\n'
