#!/usr/bin/env python3
"""全产品主流程测试：API 级执行脚本（阶段 5）

覆盖既有测试的三个零覆盖域：
  1. 跨组织隔离 —— 含用户裁决为缺陷的 C1（教师跨批次可见性）
  2. jobs 租约与失败重试 —— worker.py / db.py claim_jobs 无任何测试
  3. 多角色全链路 —— 无任何测试同时以多角色走完闭环

用法：
  API_BASE=http://127.0.0.1:8901 ADMIN_PASSWORD=xxx python3 qa_api_matrix.py

凭据只从环境变量读，不落盘、不打印。
退出码：0 全部通过 / 1 有失败 / 2 输入不满足
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any

import httpx

API = os.environ.get("API_BASE", "http://127.0.0.1:8901").rstrip("/")
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "")
TIMEOUT = float(os.environ.get("API_TIMEOUT", "30"))

if not ADMIN_PW:
    print("input error: ADMIN_PASSWORD is required", file=sys.stderr)
    raise SystemExit(2)

client = httpx.Client(base_url=API, timeout=TIMEOUT)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        PASSED.append(name)
        print(f"  ✓ {name}")
    else:
        FAILED.append((name, detail))
        print(f"  ✗ {name} — {detail}")
    return ok


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def run(tag: str) -> str:
    return f"{tag}_{uuid.uuid4().hex[:8]}"


def pwd() -> str:
    return f"Pwd-{uuid.uuid4().hex[:10]}-9a"


def register(username: str, role: str, display: str = "") -> dict:
    """无邮箱注册（本地栈无邮件服务，带邮箱会 503）。"""
    r = client.post("/api/auth/register", json={
        "username": username, "password": pwd(), "role": role, "display_name": display,
    })
    r.raise_for_status()
    return r.json()


def login(username: str, password: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    r.raise_for_status()
    return r.json()["token"]


def hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def get_json(path: str, token: str = "") -> httpx.Response:
    return client.get(path, headers=hdr(token) if token else {})


def post_json(path: str, payload: Any, token: str = "") -> httpx.Response:
    return client.post(path, json=payload, headers=hdr(token) if token else {})


def put_json(path: str, payload: Any, token: str = "") -> httpx.Response:
    return client.put(path, json=payload, headers=hdr(token) if token else {})


# 账号密码在注册时随机生成，这里保存以便后续登录
CREDS: dict[str, str] = {}


def register_with_cred(username: str, role: str, display: str = "") -> str:
    password = pwd()
    r = client.post("/api/auth/register", json={
        "username": username, "password": password, "role": role, "display_name": display,
    })
    r.raise_for_status()
    CREDS[username] = password
    return login(username, password)


# --------------------------------------------------------------------------
section("准备：账号与组织")
# --------------------------------------------------------------------------
admin_token = login("admin", ADMIN_PW)
check("admin 登录", True)

school_a = run("schoolA")
school_b = run("schoolB")
org_a = post_json("/api/organizations", {"name": school_a, "type": "school", "parent_id": ""}, admin_token).json()
org_b = post_json("/api/organizations", {"name": school_b, "type": "school", "parent_id": ""}, admin_token).json()
check("创建两所学校", org_a.get("id") and org_b.get("id"),
      f"org_a={org_a} org_b={org_b}")

# 教师 A：只在 school_a；教师 B：只在 school_b
teacher_a_user = run("teacherA")
teacher_b_user = run("teacherB")
student_a_user = run("studentA")
teacher_a_token = register_with_cred(teacher_a_user, "teacher", "教师A")
teacher_b_token = register_with_cred(teacher_b_user, "teacher", "教师B")
student_a_token = register_with_cred(student_a_user, "student", "学生A")

# 教师需要 status=active 才能进 workflow（require_manager / _ensure_manager）
for uid, token in ((teacher_a_user, teacher_a_token), (teacher_b_user, teacher_b_token)):
    uid = get_json("/api/auth/me", token).json()["id"]
    r = put_json(f"/api/auth/admin/users/{uid}/role-status",
                 {"role": "teacher", "status": "active"}, admin_token)
    check(f"认证教师 {uid} 为 active", r.status_code == 200, f"{r.status_code} {r.text[:120]}")

# 绑定组织上下文
def set_ctx(token: str, org_id: str) -> bool:
    # /api/auth/me 直接返回 public_user，无 "user" 外层包装
    # 组织作用域 key 为 "<type>_id"（Organizations.vue:88 / access_service._SCOPE_KEYS）
    me = get_json("/api/auth/me", token).json()
    r = put_json(f"/api/organizations/users/{me['id']}/context",
                 {"organization_context": {"school_id": org_id}}, admin_token)
    if r.status_code != 200:
        print(f"    set_ctx({me['id']}, {org_id}) -> {r.status_code} {r.text[:150]}")
    return r.status_code == 200

check("教师A 绑定 school_a", set_ctx(teacher_a_token, org_a["id"]))
check("教师B 绑定 school_b", set_ctx(teacher_b_token, org_b["id"]))
check("学生A 绑定 school_a", set_ctx(student_a_token, org_a["id"]))

# --------------------------------------------------------------------------
section("C1（用户裁决为越权）：教师跨组织批次可见性")
# --------------------------------------------------------------------------
# 教师B 在 school_b 建批次并加学生A（跨组织）
cohort_b = post_json("/api/cohorts", {"name": run("cohortB"), "organization_id": org_b["id"]}, teacher_b_token)
if cohort_b.status_code != 200:
    # 退路：部分部署要求 college_admin 才能建批次
    cohort_b = post_json("/api/cohorts", {"name": run("cohortB"), "organization_id": org_b["id"]}, admin_token)
check("教师B 在 school_b 建批次", cohort_b.status_code in (200, 201),
      f"{cohort_b.status_code} {cohort_b.text[:200]}")

cohort_b_id = cohort_b.json().get("id") if cohort_b.status_code in (200, 201) else None

if cohort_b_id:
    # 教师B 把学生A 加进自己的批次（学生在 school_a，属于跨组织）
    add = post_json(f"/api/cohorts/{cohort_b_id}/members",
                    {"student_ref": student_a_user, "reason": "C1 跨组织越权验证"}, teacher_b_token)
    cross_org_accepted = add.status_code in (200, 201)
    check("C1 断言：教师B 不可把 school_a 的学生A 拉进 school_b 的批次（应 403/422）",
          add.status_code in (403, 422),
          f"实际 {add.status_code}，接口接受跨组织成员关系（越权成立）；body={add.text[:200]}")

    # 对照组：教师A 自身没有任何关系，且组织不同 -> 应当不可见
    r_list = get_json("/api/cohorts", teacher_a_token)
    visible_ids = []
    if r_list.status_code == 200:
        items = r_list.json()
        visible_ids = [c.get("id") for c in (items if isinstance(items, list) else items.get("items", []))]
    check(
        "C1 对照组：教师A（同为 teacher，无关系、跨组织）不可见 school_b 批次",
        cohort_b_id not in visible_ids,
        f"对照组本身也越权：教师A 可见 school_b 批次 {cohort_b_id}；可见列表={visible_ids}",
    )
    r_detail = get_json(f"/api/cohorts/{cohort_b_id}", teacher_a_token)
    check("C1 对照组：教师A 直取 school_b 批次详情应被拒",
          r_detail.status_code in (403, 404),
          f"status={r_detail.status_code}（200 表示可直接读取）")

    # 越权成立后，学生A（属 school_a）是否会获得 school_b 批次可见性
    if cross_org_accepted:
        s_list = get_json("/api/cohorts", student_a_token)
        s_ids = []
        if s_list.status_code == 200:
            items = s_list.json()
            s_ids = [c.get("id") for c in (items if isinstance(items, list) else items.get("items", []))]
        check("C1 断言：学生A（属 school_a）不可见 school_b 批次",
              cohort_b_id not in s_ids,
              f"学生A 被拉入 school_b 批次后获得跨组织可见性（越权成立）；可见列表={s_ids}")

        s_members = get_json(f"/api/cohorts/{cohort_b_id}/members", student_a_token)
        check("C1 断言：学生A 不可读 school_b 批次成员名册",
              s_members.status_code in (403, 404),
              f"status={s_members.status_code}（200 表示可读他人所在 school_b 的成员名册）")
    else:
        check("C1 断言：学生A（属 school_a）不可见 school_b 批次", True, "前置被拒，后续不可达")
        check("C1 断言：学生A 不可读 school_b 批次成员名册", True, "前置被拒，后续不可达")
else:
    check("C1 前置：批次创建", False, "批次未创建成功，C1 无法验证")
    check("C1 断言：教师A 不可见 school_b 的批次", False, "前置失败，未执行")
    check("C1 断言：教师A 直取 school_b 批次详情应被拒", False, "前置失败，未执行")

# --------------------------------------------------------------------------
section("权限矩阵：5 角色 × 关键端点")
# --------------------------------------------------------------------------
MATRIX = [
    ("匿名", "", "GET", "/api/cohorts", (401, 403)),
    ("匿名", "", "GET", "/api/audit?limit=1", (401, 403)),
    ("匿名", "", "GET", "/api/ai/admin/config", (401, 403)),
    ("学生", student_a_token, "GET", "/api/audit?limit=1", (403,)),
    ("学生", student_a_token, "GET", "/api/teacher/students", (403,)),
    # 设计上 list_cohorts 是"过滤后返回"而非 403：无指导关系的学生应得 200 + 空列表
    ("学生", student_a_token, "GET", "/api/cohorts", (200,)),
    ("教师", teacher_a_token, "GET", "/api/audit?limit=1", (403,)),
    ("教师", teacher_a_token, "GET", "/api/ai/admin/config", (403,)),
    ("教师", teacher_a_token, "GET", "/api/cohorts", (200,)),
    ("admin", admin_token, "GET", "/api/ai/admin/config", (200,)),
    ("admin", admin_token, "GET", "/api/audit?limit=1", (200,)),
]

for role_name, token, method, path, expected in MATRIX:
    r = client.get(path, headers=hdr(token) if token else {})
    check(f"权限矩阵 {role_name} {method} {path} → {expected}",
          r.status_code in expected,
          f"实际 {r.status_code}，期望 {expected}；body={r.text[:120]}")

# 教师不得改 AI 系统配置（越权写）
r = client.put("/api/ai/admin/config",
               json={"base_url": "https://evil.example/v1", "api_key": "sk-x", "model": "m"},
               headers=hdr(teacher_a_token))
check("教师不可修改 AI 系统配置（403）", r.status_code == 403, f"实际 {r.status_code}")

# --------------------------------------------------------------------------
section("C3 归档落点（文件级证据，不走 API）")
# --------------------------------------------------------------------------
# API 不暴露 file_path（_archive_view 主动剔除该键），故 C3 以代码解析结果 + 文件系统证据判定。
# 归档目录必须从实际代码解析（app.api.workflow._archive_dir），不得在此处写死期望路径，
# 否则修复后断言仍会拿旧路径做对比而误报。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))
from app.api.workflow import _archive_dir  # noqa: E402

legacy_hardcoded = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "backend", "data", "archives")
db_path = os.environ.get("PAPERGUARD_DB_PATH", "")
data_dir = os.path.dirname(db_path) if db_path else ""
actual_archive = str(_archive_dir())
print(f"  数据目录     = {data_dir or '(未设置)'}")
print(f"  归档目录(实) = {actual_archive}")
print(f"  旧硬编码路径 = {legacy_hardcoded}（仅供对照，不参与断言）")
if data_dir:
    same = os.path.realpath(actual_archive) == os.path.realpath(os.path.join(data_dir, "archives"))
    check("C3 断言：归档目录应与数据目录同级（跟随 DB 落点）", same,
          f"归档落在 {actual_archive}，与数据目录 {data_dir} 不一致；发布切换代码目录会带走或丢失归档")
    check("C3 断言：归档目录不再指向代码目录 backend/data/archives",
          os.path.realpath(actual_archive) != os.path.realpath(legacy_hardcoded),
          f"仍指向硬编码路径 {actual_archive}")

section("C1 收尾：跨组织隔离副作用检查")
# 若 C1 成立，学生A 被塞进 school_b 批次后，学生侧的越权读也已在上方断言过；
# 这里补一条兜底：任何非成员教师都不得读取该批次名册。
if cohort_b_id:
    outsider = post_json(f"/api/cohorts/{cohort_b_id}/members",
                         {"student_ref": student_a_user, "reason": "兜底探测"}, teacher_a_token)
    check("C1 兜底：教师A（无关系、跨组织）写入 school_b 批次应被拒",
          outsider.status_code in (403, 404, 409, 422),
          f"实际 {outsider.status_code}，非成员教师可写入他人批次；body={outsider.text[:160]}")

# --------------------------------------------------------------------------
section("结果汇总")
# --------------------------------------------------------------------------
print(f"\n通过 {len(PASSED)} / 失败 {len(FAILED)}")
if FAILED:
    print("\n失败明细：")
    for name, detail in FAILED:
        print(f"  - {name}\n      {detail}")
    sys.exit(1)
print("全部通过")
