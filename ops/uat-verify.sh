#!/usr/bin/env bash
#
# PaperGuard UAT 自动登录验证（多角色）
#
# 为什么需要它：逐屏审查与部署复验原本每次都要人工接管浏览器登录，
# 往返耗时且元素引用会在等待期间失效。这里把「登录 + 断言」固化成脚本，
# 凭据只从环境变量读取、只留在进程内存中，不落盘、不进版本库。
#
# 与 ops/online-verify.sh 的分工：
#   - online-verify.sh 面向**发布前**的线上冒烟，只用 admin，口令受控、
#     目标是 HTTPS 或本地回环；
#   - 本脚本面向 **UAT/测试环境的角色级验证**，用多个普通角色账号，
#     断言的是权限与数据形状（例如「未登录读不到模板」「教师能重置
#     自己指导学生的密码」），不是可用性冒烟。
#
# 用法：
#   export PAPERGUARD_UAT_BASE_URL=http://43.167.211.145:8080
#   export PAPERGUARD_UAT_TEACHER_USERNAME=...
#   export PAPERGUARD_UAT_TEACHER_PASSWORD=...
#   export PAPERGUARD_UAT_STUDENT_USERNAME=...
#   export PAPERGUARD_UAT_STUDENT_PASSWORD=...
#   ./ops/uat-verify.sh
#
# 可选：
#   PAPERGUARD_UAT_ALLOW_PLAINTEXT=1   允许明文 HTTP（默认仅允许回环）
#   PAPERGUARD_UAT_EVIDENCE_PATH=./uat-verification-evidence.json
#
# 退出码：0 全部通过；1 有断言失败；2 输入/环境不满足。

set -euo pipefail

umask 077

base_url="${PAPERGUARD_UAT_BASE_URL:-}"
teacher_username="${PAPERGUARD_UAT_TEACHER_USERNAME:-}"
teacher_password="${PAPERGUARD_UAT_TEACHER_PASSWORD:-}"
student_username="${PAPERGUARD_UAT_STUDENT_USERNAME:-}"
student_password="${PAPERGUARD_UAT_STUDENT_PASSWORD:-}"
allow_httplain="${PAPERGUARD_UAT_ALLOW_PLAINTEXT:-0}"
timeout_seconds="${PAPERGUARD_UAT_TIMEOUT_SECONDS:-15}"
evidence_path="${PAPERGUARD_UAT_EVIDENCE_PATH:-uat-verification-evidence.json}"

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/paperguard-uat-verify.XXXXXX")"
checks='[]'
failures=0

cleanup() {
  rm -rf -- "$temp_dir"
}
trap cleanup EXIT

record_check() {
  local name="$1" status="$2" detail="${3:-}"
  checks="$(jq -c --arg name "$name" --arg status "$status" --arg detail "$detail" \
    '. + [{name: $name, status: $status, detail: $detail}]' <<< "$checks")"
  if [[ "$status" != "passed" ]]; then
    failures=$((failures + 1))
  fi
}

write_evidence() {
  local result="$1"
  jq -n \
    --arg result "$result" \
    --arg target_base_url "$base_url" \
    --arg completed_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg source_revision "${PAPERGUARD_UAT_SOURCE_REVISION:-unknown}" \
    --argjson checks "$checks" \
    '{format:"paperguard-uat-verification", format_version:1, result:$result,
      non_destructive:true, credentials_persisted:false,
      target_base_url:$target_base_url, completed_at:$completed_at,
      source_revision:$source_revision, checks:$checks}' > "$evidence_path"
}

die() {
  printf 'uat verification input error (%s): %s\n' "$1" "$2" >&2
  exit 2
}

# ---------- 输入校验 ----------

[[ -n "$base_url" ]] || die "input.base_url" "PAPERGUARD_UAT_BASE_URL is required"
[[ -n "$teacher_username" && -n "$teacher_password" ]] || \
  die "input.teacher" "PAPERGUARD_UAT_TEACHER_USERNAME/PASSWORD are required"
[[ -n "$student_username" && -n "$student_password" ]] || \
  die "input.student" "PAPERGUARD_UAT_STUDENT_USERNAME/PASSWORD are required"
[[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || \
  die "input.timeout" "PAPERGUARD_UAT_TIMEOUT_SECONDS must be a positive integer"

base_url="${base_url%/}"
[[ "$base_url" != *"@"* && "$base_url" != *"?"* && "$base_url" != *"#"* ]] || \
  die "input.base_url" "the base URL must not contain credentials, a query, or a fragment"

# 口令强度：太短的凭据几乎可以断定是被误填成占位符
for pair in "teacher:$teacher_password" "student:$student_password"; do
  role="${pair%%:*}"; pw="${pair#*:}"
  [[ ${#pw} -ge 8 ]] || die "input.$role" "the $role password looks like a placeholder (shorter than 8 chars)"
done

case "$base_url" in
  https://*) ;;
  http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*) ;;
  http://*)
    if [[ "$allow_httplain" != "1" ]]; then
      die "input.base_url" "refusing a plaintext HTTP target; set PAPERGUARD_UAT_ALLOW_PLAINTEXT=1 to override (credentials would cross the network in clear)"
    fi
    printf 'warning: running against a plaintext HTTP target; the session token crosses the network unencrypted\n' >&2
    ;;
  *) die "input.base_url" "unsupported scheme in $base_url" ;;
esac

# ---------- 工具函数 ----------

# 登录并把 token 写入 $temp_dir/<role>.conf（curl 配置，避免 token 出现在进程参数里）
login_role() {
  local role="$1" username="$2" password="$3"
  local payload
  # 用环境变量传给 jq，避免口令出现在进程参数与命令行历史里
  payload="$(UAT_PW="$password" jq -nc --arg username "$username" \
    '{username:$username,password:$ENV.UAT_PW}')"
  if ! printf '%s' "$payload" | curl --fail --silent --show-error --max-time "$timeout_seconds" \
      -H 'Accept: application/json' -H 'Content-Type: application/json' \
      --data-binary @- -o "$temp_dir/$role.login.json" "$base_url/api/auth/login"; then
    record_check "auth.login.$role" "failed" "login request failed"
    return 1
  fi
  local token
  if ! token="$(jq -er 'if (.token|type)=="string" and (.token|length)>0 then .token else error("missing token") end' \
      "$temp_dir/$role.login.json" 2>/dev/null)"; then
    record_check "auth.login.$role" "failed" "response did not contain a valid token"
    return 1
  fi
  printf 'header = "Accept: application/json"\nheader = "Authorization: Bearer %s"\n' "$token" \
    > "$temp_dir/$role.conf"
  record_check "auth.login.$role" "passed" ""
  return 0
}

# assert_status <name> <role> <expected_status> <path>
assert_status() {
  local name="$1" role="$2" expected="$3" path="$4"
  local actual
  actual="$(curl --silent --show-error --max-time "$timeout_seconds" --output "$temp_dir/$name.body" \
    --write-out '%{http_code}' --config "$temp_dir/$role.conf" "$base_url$path" || printf '000')"
  if [[ "$actual" == "$expected" ]]; then
    record_check "$name" "passed" "HTTP $actual"
  else
    record_check "$name" "failed" "expected HTTP $expected, got $actual"
  fi
}

# assert_json <name> <role> <jq-filter> <path>
assert_json() {
  local name="$1" role="$2" filter="$3" path="$4"
  local out="$temp_dir/$name.json"
  if ! curl --fail --silent --show-error --max-time "$timeout_seconds" \
      --config "$temp_dir/$role.conf" -o "$out" "$base_url$path"; then
    record_check "$name" "failed" "request failed"
    return
  fi
  if jq -e "$filter" "$out" >/dev/null 2>&1; then
    record_check "$name" "passed" ""
  else
    record_check "$name" "failed" "response shape did not match the expectation"
  fi
}

# ---------- 执行 ----------

printf '== 1/3 未认证不得读到受保护资源 ==\n'
for path in /api/templates /api/policies /api/organizations /api/cohorts; do
  status="$(curl --silent --show-error --max-time "$timeout_seconds" --output /dev/null \
    --write-out '%{http_code}' "$base_url$path" || printf '000')"
  if [[ "$status" == "401" || "$status" == "403" ]]; then
    record_check "anonymous.${path#/api/}.blocked" "passed" "HTTP $status"
  else
    # /api/packs 与 health 是有意公开的，这里不涉及
    record_check "anonymous.${path#/api/}.blocked" "failed" "expected 401/403, got $status"
  fi
done

printf '== 2/3 角色登录 ==\n'
login_role teacher "$teacher_username" "$teacher_password" || true
login_role student "$student_username" "$student_password" || true

printf '== 3/3 角色级断言 ==\n'
if [[ -f "$temp_dir/teacher.conf" ]]; then
  # 教师能看到自己的指导学生，并且「重置密码」按两种指导来源授权
  assert_json "teacher.students.list" teacher \
    'type == "object" and (.students | type == "array")' "/api/teacher/students"
  assert_json "teacher.students.reset_permission" teacher \
    '(.students | length > 0) and all(.students[]; .can_reset_password == true)' "/api/teacher/students"
  # 解绑仍只认账号绑定
  assert_json "teacher.students.unbind_strict" teacher \
    'all(.students[]?; .can_manage_account != true or (.relation_sources | index("account") != null))' \
    "/api/teacher/students"
fi

if [[ -f "$temp_dir/student.conf" ]]; then
  # 学生不得触碰管理类接口
  assert_status "student.audit.forbidden" student 403 "/api/audit?limit=1"
  assert_status "student.teacher_students.forbidden" student 403 "/api/teacher/students"
  # 学生自己的数据可读
  assert_json "student.me" student 'type == "object" and (.username | type == "string")' "/api/auth/me"
fi

# ---------- 汇总 ----------

total="$(jq 'length' <<< "$checks")"
if [[ "$failures" -eq 0 ]]; then
  write_evidence "passed"
  printf 'uat verification passed: %s checks; evidence written to %s\n' "$total" "$evidence_path"
  exit 0
fi

write_evidence "failed"
printf 'uat verification FAILED: %s of %s checks failed; evidence written to %s\n' \
  "$failures" "$total" "$evidence_path" >&2
jq -r '.[] | select(.status != "passed") | "  - \(.name): \(.detail)"' "$evidence_path" >&2 || true
exit 1
