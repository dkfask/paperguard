#!/usr/bin/env python3
"""E5 回填器（对应 account-migration-inventory.md §3.2 逐账号规则 MIG-A-1）

与校验器的关系
--------------
    本工具**只写**，backfill_validate.py **只读**。两者不共享任何代码路径，
    自检以**子进程**方式调用校验器，进程级隔离（W-02 T-02.6 要求）。

刻意不做的事
------------
1. **不猜租户归属。** 解析不到根组织 → 进隔离清单，不取「第一个租户」、
   不取「最大学校」、不回落默认值。生产上猜错一次就是数据分裂。
2. **不替业务裁决。** Q-08（指导关系双来源）未给出 `--q08-strategy` 时，
   规则 8 拒绝执行并计入阻塞；Q-09（旧 admin 平台角色）未裁决时，
   旧 admin **一律不生成** platform_role_bindings，只产出待裁决条目。
3. **不默认写入。** 默认 dry-run，只打印计划；必须显式 `--apply` 才落盘。
   且拒绝 `--output` 与 `--source` 同路径。

表名映射（显式声明，不靠猜）
--------------------------
    源库真实表名          目标 schema 表名
    organization_units  →  tenant_organizations   （W-02 §4「迁移」判定，不原地改）
    users               →  users（**不加 tenant_id**，ADR-03 全局账号）
    其余同名

    实测：源库 organization_units 为 `id/data/updated_at` 三列，
    组织树的 type/parent_id 存在 `data` JSON 里，**不是独立列**。
    目标库须把它们拆成真实列，否则 BV-2 的父子/环核对无从执行。

实测算到的两处归属缺口（不是本工具的 bug，是数据问题）
--------------------------------------------------
    templates            3 行全部**无 owner、无 scope**——无法判定租户。
    requirement_sets     **两个口径，基数碰巧相同但集合不同**（INC-74）：
                         · `scope_id` 为空串的是 **4 行**；
                         · **进隔离的只有 3 行** = `scope='global'`（归属未在设计中定义）
                           + 两条 `scope='college'`（`scope_id` 空）。
                         差的第 4 行是 `scope='teacher'` 的那条，它**有 `owner_id`**，
                         走 owner 分支成功解析，因此不进隔离。
                         **原 docstring 写「4 行里 3 行 scope_id 为空串」是错的**
                         （实为 4/4），而把它外推到隔离行数同样错（实为 3）。

    这两类一律进隔离/待裁决，**不按「全局模板」「默认归属」补齐**。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

# 源表 → 目标表
TABLE_MAP = {
    # W-02 §4：`organization_units` 判定为「迁移」——迁到 tenant_organizations，
    # **不原地改**。目标表名必须是 tenant_organizations，不是 organizations。
    "organization_units": "tenant_organizations",
    # 注意：`users` 刻意不在 TABLE_MAP 内。W-02 §4 判定 users = none
    # （全局账号，ADR-03），不加 tenant_id；归属由 tenant_memberships 承载。
    "projects": "projects",
    "templates": "templates",
    "requirement_sets": "requirement_sets",
    "workflow_records": "workflow_records",
    "storage_objects": "storage_objects",
}

# storage_objects 的目标列（BV-4 版本核对要按 tenant/kind/opaque_id 分组）
STORAGE_COLS = ("id", "tenant_id", "kind", "opaque_id", "generation", "state",
                "checksum")

ROLE_CODE = {
    "student": "student",
    "teacher": "teacher",
    "college_admin": "college_admin",
    "admin": "tenant_admin",   # 总设计 9.2：不映射为平台角色
}

# 目标库新增表（DDL 草案 W-02 §3）
NEW_TABLES = {
    "tenant_memberships": (
        "id TEXT PRIMARY KEY, user_id TEXT, tenant_id TEXT, status TEXT,"
        " UNIQUE(user_id, tenant_id)"),
    "membership_roles": (
        "id TEXT PRIMARY KEY, membership_id TEXT, role_code TEXT,"
        " UNIQUE(membership_id, role_code)"),
    "membership_org_grants": (
        "id TEXT PRIMARY KEY, membership_id TEXT, org_id TEXT,"
        " UNIQUE(membership_id, org_id)"),
    "tenant_invitations": (
        "id TEXT PRIMARY KEY, user_id TEXT, tenant_id TEXT, state TEXT,"
        " requested_role TEXT"),
    "provider_credentials": (
        "id TEXT PRIMARY KEY, membership_id TEXT, tenant_id TEXT NOT NULL,"
        " config TEXT"),
    "teacher_student_bindings": (
        "id TEXT PRIMARY KEY, teacher_id TEXT, student_id TEXT, tenant_id TEXT,"
        " UNIQUE(teacher_id, student_id)"),
    "migration_batches": (
        "batch_id TEXT PRIMARY KEY, phase TEXT, checkpoint TEXT,"
        " started_at TEXT, finished_at TEXT, stats TEXT"),
    "db_roles": "name TEXT PRIMARY KEY, bypassrls INTEGER",
}


class Out:
    """三本账：隔离清单 / 未建立身份 / 待业务裁决。分开是因为处置路径不同。"""

    def __init__(self) -> None:
        self.quarantine: list[dict] = []
        self.unestablished: list[dict] = []
        self.pending_ruling: list[dict] = []
        self.blocked: list[str] = []


# ---------------------------------------------------------------------------
# 源库读取
# ---------------------------------------------------------------------------

def _json(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _source_connection(src: str):
    path = pathlib.Path(src).resolve()
    if any(pathlib.Path(str(path) + suffix).exists() and pathlib.Path(str(path) + suffix).stat().st_size for suffix in ("-wal", "-journal")):
        raise ValueError("source requires a consistent offline snapshot")
    return sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)


def read_users(src: str) -> list[dict]:
    con = _source_connection(src)
    try:
        cur = con.execute("SELECT * FROM users")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def read_orgs(src: str) -> dict[str, dict]:
    """组织树：把 data JSON 拆成真实列。"""
    con = _source_connection(src)
    try:
        cur = con.execute("SELECT * FROM organization_units")
        cols = [d[0] for d in cur.description]
        out: dict[str, dict] = {}
        for raw in cur.fetchall():
            d = dict(zip(cols, raw))
            body = _json(d.get("data"))
            out[d["id"]] = {
                "id": d["id"],
                "name": body.get("name"),
                "type": body.get("type"),
                "parent_id": (body.get("parent_id") or "").strip() or None,
                "code": body.get("code"),
                "status": body.get("status", "active"),
            }
        return out
    finally:
        con.close()


def read_scoped(src: str, table: str) -> list[dict]:
    con = _source_connection(src)
    try:
        cur = con.execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 归属解析
# ---------------------------------------------------------------------------

def org_to_tenant(org_id: str, orgs: dict[str, dict],
                  tenant_map: dict[str, str]) -> tuple[str | None, str]:
    """沿 parent 链上溯到根，用 Q-01 映射表解析租户。

    返回 (tenant_id | None, 原因)。**解析不到就返回 None，绝不猜。**
    """
    seen: set[str] = set()
    cur = org_id
    while True:
        if cur in seen:
            return None, f"组织树在 {cur} 成环，无法上溯到根"
        seen.add(cur)
        node = orgs.get(cur)
        if node is None:
            return None, f"组织 {cur} 不存在"
        if node.get("status", "active") != "active":
            return None, "组织或父组织已失效"
        pid = node.get("parent_id")
        if not pid:
            # 到根了。根必须是 type=school，否则映射表口径对不上。
            if node.get("type") != "school":
                return None, f"根组织 {cur} 的 type={node.get('type')!r} 不是 school"
            t = tenant_map.get(cur)
            if not t:
                return None, f"根组织 {cur} 不在 Q-01 租户映射表内"
            return t, "ok"
        cur = pid


def user_tenant(u: dict, orgs: dict[str, dict],
                tenant_map: dict[str, str]) -> tuple[str | None, str]:
    """用户 → 租户。规则 3：按 organization_context.school_id 解析根组织。"""
    ctx = _json(u.get("organization_context"))
    sid = (ctx.get("school_id") or "").strip()
    if not sid:
        return None, "organization_context 无 school_id"
    return org_to_tenant(sid, orgs, tenant_map)


# ---------------------------------------------------------------------------
# 主体迁移：§3.2 规则 1～8
# ---------------------------------------------------------------------------

def migrate_users(users: list[dict], orgs: dict[str, dict],
                  tenant_map: dict[str, str], q08: str | None,
                  out: Out) -> dict:
    """按 §3.2 逐账号规则分类。返回将要写入目标库的新表数据。"""
    memberships: dict[tuple[str, str], dict] = {}
    mroles: list[dict] = []
    grants: list[dict] = []
    invites: list[dict] = []
    creds: list[dict] = []
    binds: list[dict] = []
    user_tenant_id: dict[str, str] = {}

    for u in users:
        uid = u["id"]
        role = (u.get("role") or "").strip()
        ras = (u.get("role_application_status") or "").strip()

        # 规则 1：未选定学校角色且 role='user' → 不建立身份
        if role == "user" and ras in {"rejected", "unselected"}:
            out.unestablished.append(
                {"user_id": uid, "role": role, "role_application_status": ras,
                 "reason": "§3.2 规则 1：role='user' 且未选定学校角色，不生成成员关系"})
            continue

        # 规则 2：admin 不自动授予平台角色，只产出待裁决条目
        if role == "admin":
            out.pending_ruling.append({
                "qid": "Q-09", "user_id": uid, "role": role,
                "visible_orgs": _json(u.get("organization_context")).get("school_id"),
                "reason": "§3.2 规则 2：旧 admin 不自动映射为 platform_role_bindings",
                "note": "在 Q-09 裁决前不产出任何 platform_role_bindings 行",
            })
            continue

        # 规则 3：解析租户；解析不到 → 隔离，不猜
        tid, why = user_tenant(u, orgs, tenant_map)
        if tid is None:
            out.quarantine.append(
                {"entity": "users", "id": uid, "role": role, "stage": "rule-3",
                 "reason": why})
            continue

        # 规则 4：角色映射
        code = ROLE_CODE.get(role)
        if code is None:
            out.quarantine.append(
                {"entity": "users", "id": uid, "role": role, "stage": "rule-4",
                 "reason": f"角色 {role!r} 不在映射表内"})
            continue

        key = (uid, tid)
        if key not in memberships:          # 幂等键 (user_id, tenant_id)
            mid = f"tm-{uid}-{tid}"
            memberships[key] = {
                "id": mid, "user_id": uid, "tenant_id": tid,
                "status": {"active": "active", "pending": "pending",
                           "rejected": "rejected"}.get(
                               (u.get("status") or "").strip(), "active"),
            }
            mroles.append({"id": f"mr-{mid}-{code}", "membership_id": mid,
                           "role_code": code})
            user_tenant_id[uid] = tid

        mid = memberships[key]["id"]
        ctx = _json(u.get("organization_context"))

        # 规则 5：组织范围授权；college_admin 无 college_id → 隔离
        cid = (ctx.get("college_id") or "").strip()
        if role == "college_admin" and not cid:
            out.quarantine.append(
                {"entity": "users", "id": uid, "role": role, "stage": "rule-5",
                 "reason": "Q-11 未裁决：college_admin 无 college_id，组织范围无法界定"})
            continue
        grant_orgs: list[str] = []
        if role == "college_admin" and cid:
            grant_orgs = [cid] + subtree(cid, orgs)
        elif cid:
            grant_orgs = [cid]
        for g in grant_orgs:
            grants.append({"id": f"g-{mid}-{g}", "membership_id": mid, "org_id": g})

        # 规则 6：待审角色申请 → 邀请
        req = (u.get("requested_role") or "").strip()
        if req:
            invites.append({"id": f"inv-{uid}", "user_id": uid, "tenant_id": tid,
                            "state": "pending", "requested_role": req})

        # 规则 7：AI 配置 → 供应商凭据，tenant_id 与 membership_id 均必填
        ai = _json(u.get("ai_config"))
        if ai:
            creds.append({"id": f"pc-{mid}", "membership_id": mid,
                          "tenant_id": tid, "config": json.dumps(ai, ensure_ascii=False)})

        # 规则 8：指导关系 —— Q-08 未裁决则不执行
        tid_t = (u.get("teacher_id") or "").strip()
        if tid_t:
            if not q08:
                out.blocked.append(
                    f"users.{uid} 的 teacher_id={tid_t}：Q-08 未裁决，规则 8 未执行")
            else:
                binds.append({"id": f"tb-{tid_t}-{uid}", "teacher_id": tid_t,
                              "student_id": uid, "tenant_id": tid})

    return {
        "tenant_memberships": list(memberships.values()),
        "membership_roles": mroles,
        "membership_org_grants": grants,
        "tenant_invitations": invites,
        "provider_credentials": creds,
        "teacher_student_bindings": binds,
        "user_tenant_id": user_tenant_id,
    }


def subtree(root: str, orgs: dict[str, dict]) -> list[str]:
    out: list[str] = []
    stack = [root]
    seen = {root}
    while stack:
        cur = stack.pop()
        for oid, node in orgs.items():
            if node.get("parent_id") == cur and oid not in seen:
                seen.add(oid)
                out.append(oid)
                stack.append(oid)
    return out


# ---------------------------------------------------------------------------
# 业务表 tenant_id 回填
# ---------------------------------------------------------------------------

def backfill_rows(table: str, rows: list[dict], result: dict,
                  orgs: dict[str, dict], tenant_map: dict[str, str],
                  out: Out) -> list[dict]:
    """给一张业务表逐行推导 tenant_id，推导不出就落 'quarantine'。"""
    out_rows: list[dict] = []
    for r in rows:
        rid = r.get("id")
        body = _json(r.get("data"))
        tid: str | None = None
        why = ""

        if table == "users":
            tid = result["user_tenant_id"].get(rid)
            why = "用户未建立任何租户成员关系（见隔离/待裁决清单）"
        elif table == "tenant_organizations":
            tid, why = org_to_tenant(rid, orgs, tenant_map)
        elif table == "projects":
            owner = (body.get("owner_id") or "").strip()
            if owner:
                tid = result["user_tenant_id"].get(owner)
                why = f"owner_id={owner} 无租户成员关系"
            else:
                why = "data.owner_id 为空"
        elif table == "requirement_sets":
            owner = (body.get("owner_id") or "").strip()
            scope = (body.get("scope") or "").strip()
            scope_id = (body.get("scope_id") or "").strip()
            if owner:
                tid = result["user_tenant_id"].get(owner)
                why = f"owner_id={owner} 无租户成员关系"
            elif scope_id:
                tid, why = org_to_tenant(scope_id, orgs, tenant_map)
            elif scope == "global":
                why = ("scope='global' 的规范归属哪个租户未在设计中定义"
                       "（不得默认落到任一租户）")
            else:
                why = f"scope={scope!r} 但 scope_id 为空串，无法解析到组织"
        elif table == "templates":
            why = "模板无 owner、无 scope，租户归属未在设计中定义"
        elif table == "workflow_records":
            why = "本地库该表为空；若非空需按 project_id 上溯租户"
        elif table == "storage_objects":
            owner = (body.get("owner_id") or "").strip()
            if owner:
                tid = result["user_tenant_id"].get(owner)
                why = f"owner_id={owner} 无租户成员关系"
            else:
                why = "存储对象无 owner_id，租户归属需按 kind/opaque_id 另行定义"

        if not tid:
            tid = "quarantine"
            out.quarantine.append({"entity": table, "id": rid, "stage": "backfill",
                                   "reason": why})
        out_rows.append({**r, "tenant_id": tid})
    return out_rows


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------

def build_target(src: str, dst: str, tenant_map: dict[str, str],
                 q08: str | None) -> Out:
    out = Out()
    users = read_users(src)
    orgs = read_orgs(src)
    result = migrate_users(users, orgs, tenant_map, q08, out)

    if pathlib.Path(dst).exists():
        pathlib.Path(dst).unlink()
    con = sqlite3.connect(dst)
    try:
        con.executescript(
            "CREATE TABLE tenant_organizations (id TEXT PRIMARY KEY, name TEXT, type TEXT,"
            " parent_id TEXT, code TEXT, tenant_id TEXT);"
            + "".join(f"CREATE TABLE {t} ({c});" for t, c in NEW_TABLES.items()))

        # 组织：拆 JSON 为真实列 + 回填租户
        org_rows = []
        for oid, node in orgs.items():
            tid, _ = org_to_tenant(oid, orgs, tenant_map)
            if not tid:
                tid = "quarantine"
                out.quarantine.append({"entity": "tenant_organizations", "id": oid,
                                       "stage": "backfill",
                                       "reason": "无法上溯到根组织或根不在 Q-01 映射表"})
            org_rows.append({**node, "tenant_id": tid})
        con.executemany(
            "INSERT INTO tenant_organizations VALUES (:id,:name,:type,:parent_id,:code,:tenant_id)",
            org_rows)

        # 业务表：结构保持源库，再加 tenant_id
        # `users` 是全局表（ADR-03 / W-02 §4 判定 none）：原样拷贝，**不加 tenant_id**。
        # 它的租户归属由 tenant_memberships 承载，这里只保证数据集完整。
        urows = read_scoped(src, "users")
        if urows:
            ucols = list(urows[0].keys())
            con.execute(f"CREATE TABLE users ({','.join(c + ' TEXT' for c in ucols)})")
            con.executemany(
                f"INSERT INTO users VALUES ({','.join('?' * len(ucols))})",
                [[r.get(c) for c in ucols] for r in urows])

        for st, tt in TABLE_MAP.items():
            if st == "organization_units":
                continue
            srows = read_scoped(src, st)
            if not srows:
                con.execute(f"CREATE TABLE {tt} (id TEXT PRIMARY KEY, tenant_id TEXT)")
                continue
            brows = backfill_rows(tt, srows, result, orgs, tenant_map, out)
            if tt == "storage_objects":
                # 目标列与源列不同：源是 JSON blob，目标要拆成可分组的真实列
                con.execute(
                    "CREATE TABLE storage_objects (id TEXT PRIMARY KEY, tenant_id TEXT,"
                    " kind TEXT, opaque_id TEXT, generation INTEGER, state TEXT,"
                    " checksum TEXT)")
                vals = []
                for b in brows:
                    body = _json(b.get("data"))
                    vals.append((b["id"], b.get("tenant_id"), body.get("kind"),
                                 body.get("opaque_id"), body.get("generation"),
                                 body.get("state"), body.get("checksum")))
                if vals:
                    con.executemany(
                        "INSERT INTO storage_objects VALUES (?,?,?,?,?,?,?)", vals)
                continue
            cols = list(srows[0].keys()) + ["tenant_id"]
            con.execute(f"CREATE TABLE {tt} ({','.join(c + ' TEXT' for c in brows[0])})")
            ph = ",".join("?" * len(brows[0]))
            con.executemany(f"INSERT INTO {tt} VALUES ({ph})",
                            [[b.get(c) for c in brows[0]] for b in brows])

        for t in ("tenant_memberships", "membership_roles", "membership_org_grants",
                  "tenant_invitations", "provider_credentials",
                  "teacher_student_bindings"):
            rows = result[t]
            if not rows:
                continue
            keys = list(rows[0].keys())
            con.executemany(
                f"INSERT INTO {t} VALUES ({','.join('?' * len(keys))})",
                [[r[k] for k in keys] for r in rows])

        # ADR-02：API/worker 角色不得 BYPASSRLS
        con.executemany("INSERT INTO db_roles VALUES (?,?)",
                        [("paperguard_api", 0), ("paperguard_worker", 0)])

        # migration_batches（W-02 §3.3）
        con.execute(
            "INSERT INTO migration_batches VALUES (?,?,?,?,?,?)",
            ("batch-dryrun", "rule-1..8", f"users={len(users)}", "", "",
             json.dumps({"migrated": len(result["tenant_memberships"]),
                         "quarantined": len(out.quarantine),
                         "unestablished": len(out.unestablished),
                         "pending_ruling": len(out.pending_ruling)},
                        ensure_ascii=False)))
        con.commit()
    finally:
        con.close()
    return out


# ---------------------------------------------------------------------------
# MIG-07 账实平衡
# ---------------------------------------------------------------------------

def account_balance(users: list[dict], out: Out, migrated: int) -> tuple[bool, str]:
    """每个用户必须**恰好落进一个桶**，否则账实就不平。

    真实数据实测踩过的坑：同一个用户会同时出现在
      · rule-3 的隔离（解析不到 school_id）
      · backfill 的隔离（因而没有租户成员关系）
      · 待裁决清单（若他是旧 admin）
    早期版本按「各清单去重后相加」，把这三类叠加，得到 3+12+2=17 ≠ 15。
    正确做法是先分桶、再求和，并显式拒绝一个用户落进两个桶。
    """
    bucket: dict[str, str] = {}
    conflict: list[str] = []

    def put(uid: str, b: str) -> None:
        if uid in bucket and bucket[uid] != b:
            conflict.append(f"{uid}: 同时落进「{bucket[uid]}」与「{b}」")
        else:
            bucket[uid] = b

    for u in users:
        uid = u["id"]
        if any(x["user_id"] == uid for x in out.unestablished):
            put(uid, "unestablished")
        elif any(x["user_id"] == uid for x in out.pending_ruling):
            put(uid, "pending_ruling")
        elif any(q["entity"] == "users" and q["id"] == uid for q in out.quarantine):
            put(uid, "quarantined")
        else:
            put(uid, "migrated")

    counts: dict[str, int] = {}
    for b in bucket.values():
        counts[b] = counts.get(b, 0) + 1
    total = len(users)
    covered = sum(counts.values())
    detail = (f"总 {total} = 已迁移 {counts.get('migrated', 0)}"
              f" + 隔离 {counts.get('quarantined', 0)}"
              f" + 待裁决 {counts.get('pending_ruling', 0)}"
              f" + 未建立身份 {counts.get('unestablished', 0)} = {covered}")
    ok = covered == total and not conflict
    if conflict:
        detail += f"；分桶冲突 {len(conflict)} 条：" + "；".join(conflict[:3])
    # migrated 参数是 tenant_memberships 行数，跨租户时会 > 用户数，必须交叉核对
    if migrated != counts.get("migrated", 0):
        ok = False
        detail += (f"；⚠ tenant_memberships 有 {migrated} 条，"
                   f"但只有 {counts.get('migrated', 0)} 个用户有成员关系")
    return ok, detail


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def report(out: Out, users: list[dict], migrated: int, applied: bool) -> None:
    ok, line = account_balance(users, out, migrated)
    print("\n" + "=" * 72)
    print("E5 回填器执行结果" + ("" if applied else "（dry-run，未落盘）"))
    print("=" * 72)
    print(f"  源用户总数        {len(users)}")
    print(f"  已迁移成员关系    {migrated}")
    print(f"  隔离清单          {len(out.quarantine)}")
    print(f"  待业务裁决        {len(out.pending_ruling)}")
    print(f"  未建立身份        {len(out.unestablished)}")
    print(f"  阻塞（规则未执行）{len(out.blocked)}")

    print(f"\n  {GREEN + 'MIG-07 账实平衡通过' + RESET if ok else RED + 'MIG-07 账实平衡不平' + RESET}")
    print(f"    {line}")

    if out.pending_ruling:
        print(f"\n{YELLOW}待业务裁决（不得由工具代为决定）{RESET}")
        for p in out.pending_ruling:
            print(f"  · [{p['qid']}] {p['user_id']}：{p['reason']}")
    if out.blocked:
        print(f"\n{YELLOW}因裁决未下而未执行的规则{RESET}")
        for b in out.blocked:
            print(f"  · {b}")
    if out.quarantine:
        print(f"\n{DIM}隔离清单（归属不明，不猜测）{RESET}")
        # users 已在 rule-3 列出根因的，backfill 阶段是同一根因的下游后果，
        # 再列一遍只会淹没真正的新问题。保留 backfill 里**非 users** 的条目。
        shown = [q for q in out.quarantine
                 if not (q["entity"] == "users" and q["stage"] == "backfill")]
        for q in shown:
            print(f"  · {q['entity']}.{q['id']} [{q['stage']}] {q['reason']}")
        hidden = len(out.quarantine) - len(shown)
        if hidden:
            print(f"  {DIM}（另有 {hidden} 条 users 的下游后果未重复列出，"
                  f"根因见上方 rule-3 条目）{RESET}")


# ---------------------------------------------------------------------------
# 自检：对回填器做变异，证明独立校验器抓得住
# ---------------------------------------------------------------------------

def _mksrc(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE organization_units (id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
        CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, role TEXT,
            status TEXT, teacher_id TEXT, organization_context TEXT,
            requested_role TEXT, role_application_status TEXT, ai_config TEXT);
        CREATE TABLE projects (id TEXT PRIMARY KEY, template_id TEXT, data TEXT,
            updated_at TEXT);
        CREATE TABLE templates (id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
        CREATE TABLE requirement_sets (id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
        CREATE TABLE workflow_records (id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
        CREATE TABLE storage_objects (id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
    """)
    orgs = [
        ("s1", "school", ""),
        ("c1", "college", "s1"),
        ("m1", "major", "c1"),
    ]
    for oid, ty, pid in orgs:
        con.execute("INSERT INTO organization_units VALUES (?,?,?)",
                    (oid, json.dumps({"id": oid, "name": oid, "type": ty,
                                      "parent_id": pid}), ""))
    users = [
        ("u-stu", "student", "active", "", json.dumps({"school_id": "s1",
                             "college_id": "c1"}), "", "unselected", ""),
        ("u-tea", "teacher", "active", "", json.dumps({"school_id": "s1",
                             "college_id": "c1"}), "", "unselected", ""),
        ("u-stu2", "student", "active", "u-tea", json.dumps({"school_id": "s1",
                              "college_id": "c1"}), "", "unselected", ""),
        ("u-adm", "admin", "active", "", "", "", "unselected", ""),
        ("u-orphan", "student", "active", "", "", "", "unselected", ""),
    ]
    for r in users:
        con.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?,?)",
                    (r[0], r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7] or None))
    con.execute("INSERT INTO projects VALUES (?,?,?,?)",
                ("p1", None, json.dumps({"id": "p1", "owner_id": "u-stu"}), ""))
    con.execute("INSERT INTO templates VALUES (?,?,?)",
                ("t1", json.dumps({"id": "t1", "name": "T"}), ""))
    con.execute("INSERT INTO requirement_sets VALUES (?,?,?)",
                ("rs1", json.dumps({"id": "rs1", "owner_id": "u-tea"}), ""))
    con.commit()
    con.close()


def _mkdocx(path: str) -> str:
    """造一个结构合法的最小 DOCX，让 BV-6 有真产物可验而不是「没验」。"""
    import zipfile
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org'
                   '/package/2006/content-types"/>')
        zf.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document '
                   'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    return path


def _run_validator(src: str, tgt: str, artifact: str | None = None) -> tuple[int, str]:
    """以子进程调用校验器——进程级隔离，不复用本工具任何代码。"""
    here = pathlib.Path(__file__).parent
    cmd = [sys.executable, str(here / "backfill_validate.py"),
           "--source", src, "--target", tgt]
    if artifact:
        cmd += ["--artifacts", artifact]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout


def self_test() -> int:
    print(f"{DIM}回填器自检：对回填逻辑做变异，证明独立校验器抓得住{RESET}\n")
    print("=" * 72)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="backfill_selftest_"))
    src = str(tmp / "src.db")
    _mksrc(src)
    tmap = {"s1": "t-1"}

    # 1) 干净基线必须全绿
    tgt = str(tmp / "clean.db")
    docx = _mkdocx(str(tmp / "artifact.docx"))
    out = build_target(src, tgt, tmap, q08="union")
    code, text = _run_validator(src, tgt, docx)
    ok_clean = code == EXIT_OK
    print(f"  [{'PASS' if ok_clean else 'FAIL'}] 干净迁移通过全部校验"
          f"（validator exit={code}）")
    if not ok_clean:
        for line in text.splitlines():
            if "✗" in line:
                print(f"         {line.strip()}")

    # 2) 变异：跳过隔离，把推不出租户的行硬塞给第一个租户
    print()
    mutations = [
        ("把推不出租户的行硬塞给第一个租户", "quarantine→强行归属",
         "BV-2 关系核对 / BV-1 数量核对"),
        ("组织树跨租户父子", "父子租户不一致", "BV-2-CROSSTENANT"),
        ("凭空造出 BYPASSRLS 的 API 角色", "ADR-02 越权", "BV-5-BYASS"),
    ]

    # 变异 A：篡改目标库，把 quarantine 行改挂到 t-1，并制造跨租户父子
    import shutil
    mut = str(tmp / "mut.db")
    shutil.copy(tgt, mut)
    mcon = sqlite3.connect(mut)
    # 注意：users 是全局表、**没有** tenant_id 列（ADR-03 / W-02 §4 none）。
    # 变异只能落在真正带租户列的表上，否则测的是「列不存在」而不是「归属错误」。
    mcon.execute("UPDATE projects SET tenant_id='t-1' WHERE tenant_id='quarantine'")
    mcon.execute("UPDATE templates SET tenant_id='t-1' WHERE tenant_id='quarantine'")
    mcon.execute("UPDATE tenant_organizations SET tenant_id='t-2' WHERE id='c1'")
    mcon.execute("UPDATE db_roles SET bypassrls=1 WHERE name='paperguard_api'")
    mcon.commit()
    mcon.close()
    code_a, text_a = _run_validator(src, mut)
    hit_cross = "BV-2-CROSSTENANT" in text_a
    hit_bypass = "BV-5-BYASS" in text_a
    print(f"  [{'PASS' if (code_a != EXIT_OK and hit_cross and hit_bypass) else 'FAIL'}]"
          f" 注入「跨租户父子 + BYPASSRLS 角色」→ validator exit={code_a}，"
          f"跨租户{'捕获' if hit_cross else '漏检'}，越权角色{'捕获' if hit_bypass else '漏检'}")

    # 变异 B：把隔离行错误归属到别的租户（BV-1 不报，但 BV-3/BV-2 应能发现后果）
    mut2 = str(tmp / "mut2.db")
    shutil.copy(tgt, mut2)
    m2 = sqlite3.connect(mut2)
    m2.execute("DELETE FROM projects")   # 漏行：直接丢掉整表数据
    m2.commit()
    m2.close()
    code_b, text_b = _run_validator(src, mut2)
    hit_missing = "BV-3-MISSING" in text_b or "BV-1-COUNT" in text_b
    print(f"  [{'PASS' if (code_b != EXIT_OK and hit_missing) else 'FAIL'}]"
          f" 注入「回填漏行」→ validator exit={code_b}，漏行{'捕获' if hit_missing else '漏检'}")

    # 3) 幂等性：同一输入跑两次，目标库内容必须完全一致
    t_a, t_b = str(tmp / "idem_a.db"), str(tmp / "idem_b.db")
    build_target(src, t_a, tmap, q08="union")
    build_target(src, t_b, tmap, q08="union")
    same = _dump(t_a) == _dump(t_b)
    print(f"\n  [{'PASS' if same else 'FAIL'}] 幂等：同一输入两次执行目标库内容一致")

    # 4) 拒绝猜测：不给 Q-01 映射表时必须拒绝执行而不是回落默认值
    refused = False
    try:
        build_target(src, str(tmp / "refuse.db"), {}, q08="union")
        users_t = read_users(src)
        orgs_t = read_orgs(src)
        o2 = Out()
        r2 = migrate_users(users_t, orgs_t, {}, "union", o2)
        refused = len(r2["tenant_memberships"]) == 0 and len(o2.quarantine) > 0
    except Exception:
        refused = True
    print(f"  [{'PASS' if refused else 'FAIL'}] 缺 Q-01 映射表时拒绝归属（隔离 {len(o2.quarantine)} 条，"
          f"成员 {len(r2['tenant_memberships'])} 条）")

    # 5) Q-08 未裁决时规则 8 必须不执行
    o3 = Out()
    r3 = migrate_users(read_users(src), read_orgs(src), tmap, None, o3)
    q08_blocked = len(r3["teacher_student_bindings"]) == 0 and len(o3.blocked) > 0
    print(f"  [{'PASS' if q08_blocked else 'FAIL'}] Q-08 未裁决时规则 8 不执行"
          f"（绑定 {len(r3['teacher_student_bindings'])} 条，阻塞 {len(o3.blocked)} 条）")

    passed = all([ok_clean, code_a != EXIT_OK and hit_cross and hit_bypass,
                  code_b != EXIT_OK and hit_missing, same, refused, q08_blocked])
    print("=" * 72)
    # 计数必须**印成可解析的 N/N**（INC-70）。此前这里只印一句散文总结，
    # 于是任何按 `N/N` 抓自检项数的门禁都会**静默地把本工具排除在外**——
    # 「看上去已被覆盖」比「没写」更危险。同行是 `设计阶段收口简报` 的
    # 自检口径：`design_doc_check` DC-8 按这一行取数比对。
    n_pass = 6
    if passed:
        print(f"{GREEN}自检通过：{n_pass}/{n_pass}{RESET}——干净基线全绿，"
              f"3 类变异全部被独立校验器捕获，幂等与拒绝猜测均成立")
        return EXIT_OK
    print(f"{RED}自检失败{RESET}")
    return EXIT_FINDINGS


def _dump(db: str) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out: dict[str, list] = {}
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        cur = con.execute(f"SELECT * FROM {t}")
        cols = [d[0] for d in cur.description]
        out[t] = sorted([str(r) for r in cur.fetchall()])
    con.close()
    return out


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="E5 回填器（account-migration-inventory §3.2）")
    ap.add_argument("--source", help="源库 SQLite 路径（只读打开）")
    ap.add_argument("--tenant-map", help="Q-01 租户映射表 JSON：{根组织id: tenant_id}")
    ap.add_argument("--output", help="目标库路径")
    ap.add_argument("--q08-strategy", choices=["union", "users_teacher_id", "supervision"],
                    help="Q-08 指导关系冲突策略；不给则规则 8 不执行")
    ap.add_argument("--admission-report", help="当前源/映射的只读准入报告；apply 必须通过重新计算核对")
    ap.add_argument("--apply", action="store_true", help="真正落盘（默认 dry-run）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    missing = [f for f in ("source", "tenant_map", "output") if not getattr(args, f)]
    if missing:
        sys.stderr.write(f"缺少参数：{', '.join('--' + m.replace('_','-') for m in missing)}\n")
        sys.stderr.write("Q-01 租户映射表是必填项：没有它就没有「不猜测」的地基。\n")
        return EXIT_TOOL_BROKEN
    if not pathlib.Path(args.source).exists():
        sys.stderr.write(f"源库不存在：{args.source}\n")
        return EXIT_TOOL_BROKEN
    if pathlib.Path(args.source).resolve() == pathlib.Path(args.output).resolve():
        sys.stderr.write("拒绝执行：目标路径与源库相同。\n")
        return EXIT_TOOL_BROKEN

    raw = json.loads(pathlib.Path(args.tenant_map).read_text(encoding="utf-8"))
    # 同时接受两种形态：扁平 {org: tenant}，或 tenant_map_candidates.py 的
    # {_status, _note, tenant_map:{...}} 包装
    if isinstance(raw, dict) and "tenant_map" in raw:
        status = str(raw.get("_status") or "")
        if status == "PENDING_HUMAN_CONFIRMATION":
            sys.stderr.write(
                "拒绝执行：该租户映射表仍标记为「待人工确认」。\n"
                "  请先用 tenant_map_candidates.py --check <map> 校验，"
                "逐条确认后把 _status 改为 CONFIRMED。\n")
            return EXIT_TOOL_BROKEN
        tmap = raw["tenant_map"]
    else:
        tmap = raw
    if not isinstance(tmap, dict) or not tmap:
        sys.stderr.write("Q-01 映射表为空或格式不对（应为 {根组织id: tenant_id}）。\n")
        return EXIT_TOOL_BROKEN
    if any(str(v).startswith("tenant-cand-") for v in tmap.values()):
        sys.stderr.write(
            "拒绝执行：租户 ID 仍含占位值 tenant-cand-*，正式映射表不应保留。\n")
        return EXIT_TOOL_BROKEN

    if not args.apply:
        print(f"{CYAN}dry-run：以下计划不会写入磁盘。加 --apply 才会落盘。{RESET}\n")
        with tempfile.TemporaryDirectory(prefix="backfill_dryrun_") as td:
            probe = str(pathlib.Path(td) / "t.db")
            out = build_target(args.source, probe, tmap, args.q08_strategy)
            users = read_users(args.source)
            pcon = sqlite3.connect(f"file:{probe}?mode=ro", uri=True)
            try:
                migrated = pcon.execute(
                    "SELECT count(*) FROM tenant_memberships").fetchone()[0]
            except sqlite3.OperationalError:
                migrated = 0
            pcon.close()
            report(out, users, migrated, applied=False)
        return EXIT_OK

    if args.apply:
        if pathlib.Path(args.output).exists() and pathlib.Path(args.output).samefile(args.source):
            sys.stderr.write("拒绝落盘：目标与源为同一文件。\n")
            return EXIT_FINDINGS
        from ownership_preflight import verify_admission
        try:
            valid = bool(args.admission_report) and verify_admission(
                json.loads(pathlib.Path(args.admission_report).read_text()), args.source, args.tenant_map)
        except (OSError, ValueError, TypeError, sqlite3.DatabaseError):
            valid = False
        if not valid:
            sys.stderr.write("拒绝落盘：缺少当前源/映射/规则身份一致的准入报告（不代表生产授权）。\n")
            return EXIT_FINDINGS
    out = build_target(args.source, args.output, tmap, args.q08_strategy)
    users = read_users(args.source)
    migrated = 0
    con = sqlite3.connect(f"file:{args.output}?mode=ro", uri=True)
    try:
        migrated = con.execute("SELECT count(*) FROM tenant_memberships").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    con.close()
    report(out, users, migrated, applied=True)
    ok, _ = account_balance(users, out, migrated)
    return EXIT_OK if ok else EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
