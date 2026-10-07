#!/usr/bin/env bash
#
# ops/uat-verify.sh 的契约自测。
#
# 用回环 mock server 验证脚本自身的判定逻辑，不碰任何真实环境：
#   - 明文 HTTP 默认被拒（除非显式 opt-in）
#   - 口令占位符（过短）被拒
#   - 缺必填环境变量被拒
#   - 未认证访问受保护接口时断言失败
#   - 角色断言按 mock 返回的数据形状判定
#
# 刻意不覆盖：真实凭据、真实网络、任何写操作。

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
script="$repo_root/ops/uat-verify.sh"
temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/paperguard-uat-contract.XXXXXX")"
port_file="$temp_dir/port"
server_log="$temp_dir/server.log"

cleanup() {
  if [[ -n "${server_pid:-}" ]]; then
    kill "$server_pid" >/dev/null 2>&1 || true
    wait "$server_pid" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$temp_dir"
}
trap cleanup EXIT

# 口令每次随机生成，避免在仓库里留下静态凭据
uat_pw="$(python3 -c 'import secrets; print("Uat-" + secrets.token_urlsafe(24))')"

python3 - "$port_file" "$uat_pw" >"$server_log" 2>&1 <<'PY' &
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file, uat_pw = sys.argv[1], sys.argv[2]
open(port_file, "w").close()

TEACHER = {"t" * 64}
STUDENT = {"s" * 64}
# 批次指导来源的学生：可重置密码、不可解绑
STUDENTS = [
    {"id": "s1", "username": "stu_b", "relation_sources": ["cohort"],
     "can_reset_password": True, "can_manage_account": False},
    {"id": "s2", "username": "stu_a", "relation_sources": ["cohort"],
     "can_reset_password": True, "can_manage_account": False},
]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def _send(self, status, body):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?")[0]
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else None
        if token not in TEACHER and token not in STUDENT:
            # 与生产一致：模板等受保护资源未登录一律 401
            self._send(401, {"detail": "未登录"})
            return
        if token in TEACHER:
            if path == "/api/teacher/students":
                self._send(200, {"students": STUDENTS, "total": len(STUDENTS)})
                return
        if path == "/api/auth/me":
            self._send(200, {"username": "uat", "role": "teacher" if token in TEACHER else "student"})
            return
        if path in ("/api/audit", "/api/teacher/students"):
            self._send(403, {"detail": "forbidden"})
            return
        self._send(404, {"detail": "not found"})

    def do_POST(self):
        if self.path == "/api/auth/login":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            username, password = payload.get("username"), payload.get("password")
            if password != uat_pw:
                self._send(401, {"detail": "bad credentials"})
            elif username == "uat_teacher":
                self._send(200, {"token": "t" * 64})
            elif username == "uat_student":
                self._send(200, {"token": "s" * 64})
            else:
                self._send(401, {"detail": "bad credentials"})
            return
        self._send(404, {"detail": "not found"})


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
open(port_file, "w").write(str(server.server_address[1]))
server.serve_forever()
PY

for _ in $(seq 1 100); do
  [[ -s "$port_file" ]] && break
  sleep 0.1
done
port="$(cat "$port_file")"
[[ -n "$port" ]] || { echo "mock server failed to start" >&2; cat "$server_log" >&2; exit 1; }
base="http://127.0.0.1:$port"

failures=0
expect_exit() {
  local label="$1" expected="$2"
  shift 2
  set +e
  ( "$@" ) >"$temp_dir/out" 2>"$temp_dir/err"
  local actual=$?
  set -e
  if [[ "$actual" == "$expected" ]]; then
    printf '  ok   %s (exit %s)\n' "$label" "$actual"
  else
    printf '  FAIL %s (expected exit %s, got %s)\n' "$label" "$expected" "$actual" >&2
    sed -n '1,5p' "$temp_dir/err" >&2 || true
    failures=$((failures + 1))
  fi
}

echo "== 输入校验 =="

expect_exit "缺少 base_url 退出 2" 2 env -u PAPERGUARD_UAT_BASE_URL -u PAPERGUARD_UAT_TEACHER_USERNAME \
  -u PAPERGUARD_UAT_TEACHER_PASSWORD -u PAPERGUARD_UAT_STUDENT_USERNAME -u PAPERGUARD_UAT_STUDENT_PASSWORD \
  "$script"

expect_exit "缺少学生凭据退出 2" 2 env \
  PAPERGUARD_UAT_BASE_URL="$base" \
  PAPERGUARD_UAT_TEACHER_USERNAME=uat_teacher PAPERGUARD_UAT_TEACHER_PASSWORD="$uat_pw" \
  "$script"

expect_exit "口令像占位符退出 2" 2 env \
  PAPERGUARD_UAT_BASE_URL="$base" \
  PAPERGUARD_UAT_TEACHER_USERNAME=uat_teacher PAPERGUARD_UAT_TEACHER_PASSWORD=short \
  PAPERGUARD_UAT_STUDENT_USERNAME=uat_student PAPERGUARD_UAT_STUDENT_PASSWORD="$uat_pw" \
  "$script"

# 明文非回环默认拒绝（这里用 203.0.113.1 文档地址，不会真的发包）
expect_exit "明文 HTTP 默认拒绝退出 2" 2 env \
  PAPERGUARD_UAT_BASE_URL="http://203.0.113.1:8080" \
  PAPERGUARD_UAT_TEACHER_USERNAME=uat_teacher PAPERGUARD_UAT_TEACHER_PASSWORD="$uat_pw" \
  PAPERGUARD_UAT_STUDENT_USERNAME=uat_student PAPERGUARD_UAT_STUDENT_PASSWORD="$uat_pw" \
  "$script"

echo "== 角色断言 =="

# 全通过：证据文件写在临时目录，避免污染仓库
expect_exit "全绿退出 0" 0 env \
  PAPERGUARD_UAT_BASE_URL="$base" \
  PAPERGUARD_UAT_TEACHER_USERNAME=uat_teacher PAPERGUARD_UAT_TEACHER_PASSWORD="$uat_pw" \
  PAPERGUARD_UAT_STUDENT_USERNAME=uat_student PAPERGUARD_UAT_STUDENT_PASSWORD="$uat_pw" \
  PAPERGUARD_UAT_EVIDENCE_PATH="$temp_dir/evidence.json" \
  "$script"

if jq -e '.result == "passed" and .credentials_persisted == false and ([.checks[] | select(.status != "passed")] | length == 0)' \
    "$temp_dir/evidence.json" >/dev/null 2>&1; then
  printf '  ok   证据结构正确且全部 passed\n'
else
  printf '  FAIL 证据结构或结果不正确\n' >&2
  cat "$temp_dir/evidence.json" >&2 || true
  failures=$((failures + 1))
fi

# 未认证必须被判为 blocked：mock 对无 token 一律 401，若 mock 退化为放行，
# anonymous.* 检查应当失败
PAPERGUARD_UAT_BASE_URL="$base" \
PAPERGUARD_UAT_TEACHER_USERNAME=uat_teacher PAPERGUARD_UAT_TEACHER_PASSWORD="$uat_pw" \
PAPERGUARD_UAT_STUDENT_USERNAME=uat_student PAPERGUARD_UAT_STUDENT_PASSWORD="$uat_pw" \
PAPERGUARD_UAT_EVIDENCE_PATH="$temp_dir/evidence2.json" \
  "$script" >/dev/null 2>&1 || true

if jq -e '[.checks[] | select(.name | startswith("anonymous.")) | select(.status == "passed")] | length == 4' \
    "$temp_dir/evidence2.json" >/dev/null 2>&1; then
  printf '  ok   四个匿名访问检查均判为 blocked\n'
else
  printf '  FAIL 匿名访问检查结果不对\n' >&2
  failures=$((failures + 1))
fi

# 错误口令应导致失败退出 1
expect_exit "错误口令退出 1" 1 env \
  PAPERGUARD_UAT_BASE_URL="$base" \
  PAPERGUARD_UAT_TEACHER_USERNAME=uat_teacher PAPERGUARD_UAT_TEACHER_PASSWORD="${uat_pw}-wrong" \
  PAPERGUARD_UAT_STUDENT_USERNAME=uat_student PAPERGUARD_UAT_STUDENT_PASSWORD="$uat_pw" \
  PAPERGUARD_UAT_EVIDENCE_PATH="$temp_dir/evidence3.json" \
  "$script"

if [[ "$failures" -eq 0 ]]; then
  printf '\nuat-verify contract test: all passed\n'
  exit 0
fi
printf '\nuat-verify contract test: %s checks failed\n' "$failures" >&2
exit 1
