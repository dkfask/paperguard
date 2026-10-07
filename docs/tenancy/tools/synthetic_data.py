#!/usr/bin/env python3
"""AT-T-4 隔离环境合成数据构造器。

配套：docs/product/saas-platform-design-20261001.md §24.1（数据与角色准备）
      docs/tenancy/at-traceability-matrix.yaml（45 条 AT 的共同前置）

设计立场
--------
**如实报告造不出什么，比假装能造更有价值。**

§24.1 要求：两个客户租户、每租户至少两个组织范围、7 类角色，
外加 5 类边界成员。当前 schema **无法满足其中大部分**。本脚本不掩盖这一点——
它会显式列出「已构造 / 无法构造（缺什么）」，并把缺口写成机器可读的
`gaps`，让 AT 追溯矩阵能引用，而不是靠人记忆。

两种模式
--------
    --plan      只输出计划与缺口（不碰数据库，默认）
    --emit      导出 schema 无关的 JSON 清单，供迁移后导入
    --apply     向目标库写入（**需目标库具备 tenant 相关列**，否则拒绝执行）

退出码：0 = 按当前 schema 可构造的部分全部构造成功；1 = 有缺口或校验失败；
        2 = 工具自身不可运行。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DB_PY = REPO_ROOT / "backend/app/db.py"

EXIT_OK, EXIT_GAPS, EXIT_TOOL_BROKEN = 0, 1, 2
GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# §24.1 要求的角色 → 代码中实际存在的角色
REQUIRED_ROLES = {
    "owner": "所有者",
    "admin": "管理员",
    "billing": "账务人员",
    "college_admin": "学院管理员",
    "teacher": "教师",
    "student": "学生",
    "support": "支持人员",
}

# §24.1 要求的边界成员
REQUIRED_EDGE_CASES = {
    "dual_tenant_different_roles": "同一账号在两租户角色不同",
    "disabled_member": "已停用成员",
    "no_org_member": "无组织成员",
    "incomplete_email_onboarding": "未完成邮箱引导成员",
    "expired_support_grant": "过期支持授权",
}


# ---------------------------------------------------------------------------
# schema 探测
# ---------------------------------------------------------------------------

def probe_schema() -> dict:
    """只读解析 db.py / auth.py，得出当前 schema 实际具备什么。

    角色识别**不用字符串猜测**。早期版本用正则扫 `"role": "xxx"` 之类的字面量，
    结果把列名 `display_name` 当成角色、把实际存在的 `admin`/`teacher` 漏掉，
    产出了「管理员角色不存在」这种**误报**——误报比不报更糟，它会让人以为
    角色真的缺失而去做无用功。

    改为：直接读 `MANAGE_ROLES` 常量 + `role ... not in (...)` 这类判定式，
    再用 AST 兜底扫描 `db.py` 中 `DEFAULT 'xxx'` 的列默认值。
    """
    if not DB_PY.exists():
        # **退出码必须是 2**（INC-70）。首版写的是 `raise SystemExit(f"找不到 {DB_PY}")`，
        # 而 `SystemExit` 带**字符串**参数时 Python 的退出码是 **1**——
        # 于是「工具找不到输入文件」会被读成「有差距未通过」，
        # 与三态约定完全相反：缺输入属于工具不可运行，不是发现。
        sys.stderr.write(f"找不到 {DB_PY}\n")
        raise SystemExit(EXIT_TOOL_BROKEN)
    s = DB_PY.read_text(encoding="utf-8")
    auth_py = REPO_ROOT / "backend/app/auth.py"
    a = auth_py.read_text(encoding="utf-8") if auth_py.exists() else ""

    def cols(table: str) -> set[str]:
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\s*\);", s, re.S)
        if not m:
            return set()
        # 缩进是 20 个空格。写死 `\s{16}` 会匹配到 0 列——
        # 因为 `\s{16}` 之后紧跟的必须是 \w，而第 17 个字符还是空格。
        # 这个 bug 曾让工具漏报 15 个列并因此对 tenant_id 给出错误结论。
        return set(re.findall(r"^\s{16,}(\w+)\s+(?:TEXT|INTEGER|REAL|BLOB|NUMERIC|BIGINT)",
                              m.group(1), re.M))

    def alt_cols(table: str) -> set[str]:
        return set(re.findall(rf"ALTER TABLE {table} ADD COLUMN (\w+)", s))

    created = cols("users")
    if not created:
        # 静默失效防护：CREATE TABLE 解析一旦因缩进/格式化变化而失配，
        # 这里会得到空集。**不能只看 union**——ALTER 分支仍会返回几列，
        # union 非空就会让守卫漏过，随后所有 `has_tenant_column` 判断
        # 都会给出「该列不存在」的错误结论，而那正是本工具的承重输出。
        sys.stderr.write(
            "synthetic_data: 无法从 db.py 的 CREATE TABLE 解析出 users 表的任何列。\n"
            "  这通常意味着建表语句的缩进或类型关键字变了，正则已失配。\n"
            "  在修复前，本工具的 schema 探测结论不可信。\n"
        )
        raise SystemExit(EXIT_TOOL_BROKEN)
    users = created | alt_cols("users")

    # 角色：① MANAGE_ROLES 常量 ② role IN (...) / role NOT IN (...) 判定式
    roles: set[str] = set()
    m = re.search(r"MANAGE_ROLES\s*=\s*\(([^)]*)\)", a)
    if m:
        roles.update(re.findall(r'"([a-z_]+)"', m.group(1)))
    for m2 in re.finditer(r'role"?\s*(?:not\s+)?in\s*\(([^)]*)\)', a):
        roles.update(re.findall(r'"([a-z_]+)"', m2.group(1)))
    # ③ users.role 的列默认值
    m3 = re.search(r"role\s+TEXT[^,]*DEFAULT\s*'([a-z_]+)'", s)
    if m3:
        roles.add(m3.group(1))
    # ④ 注册接口允许的角色
    m4 = re.search(r'role[^\n]{0,80}?(?:Literal\[[^\]]*\]|in\s*\(([^)]*)\))', a)
    if m4 and m4.group(1):
        roles.update(re.findall(r'"([a-z_]+)"', m4.group(1)))
    roles.discard("")

    return {
        "users_columns": sorted(users),
        "known_roles": sorted(roles),
        "has_tenant_column": "tenant_id" in users,
        "has_org_context": "organization_context" in users,
        "has_requested_org_context": "requested_organization_context" in users,
    }


def build_plan(schema: dict) -> dict:
    """构造数据计划，并如实标注造不出的部分。"""
    gaps: list[dict] = []

    # --- 租户 ---
    tenants = [{"id": "t-alpha", "name": "Alpha 试点租户"},
               {"id": "t-beta", "name": "Beta 试点租户"}]
    if not schema["has_tenant_column"]:
        gaps.append({
            "id": "T-ENABLE-MULTI-TENANT",
            "blocks": "全部 45 条 AT 的多租户前置",
            "missing": "users 表无 tenant_id 列；无 tenant_memberships 表",
            "detail": "§24.1 要求两个客户租户，当前 schema 无任何租户维度，"
                      "无法构造跨租户隔离所需的任何前置数据。",
        })

    # --- 组织范围 ---
    org_scopes = []
    for t in tenants:
        org_scopes += [
            {"id": f"{t['id']}-school", "tenant_id": t["id"], "type": "school",
             "name": f"{t['name']}主校区"},
            {"id": f"{t['id']}-college-1", "tenant_id": t["id"], "type": "college",
             "name": f"{t['name']}计算机学院", "parent_id": f"{t['id']}-school"},
            {"id": f"{t['id']}-college-2", "tenant_id": t["id"], "type": "college",
             "name": f"{t['name']}外国语学院", "parent_id": f"{t['id']}-school"},
        ]
    if not schema["has_org_context"]:
        gaps.append({
            "id": "ORG-CONTEXT",
            "blocks": "AT-01/03/05/12 的组织范围判定",
            "missing": "users 表无 organization_context 列",
            "detail": "组织范围无处存放，'每租户至少两个组织范围' 无法落到用户身上。",
        })

    # --- 角色 ---
    users = []
    existing = set(schema["known_roles"])
    for role, cn in REQUIRED_ROLES.items():
        for t in tenants:
            uid = f"u-{t['id']}-{role}"
            users.append({"id": uid, "username": f"{t['id']}_{role}", "role": role,
                          "display_name": f"{cn}（{t['id']}）", "tenant_id": t["id"],
                          "status": "active"})
        if role not in existing:
            gaps.append({
                "id": f"ROLE-MISSING-{role}",
                "blocks": f"§24.1 要求的「{cn}」角色",
                "missing": f"代码中不存在 role='{role}'",
                "detail": f"当前 schema 仅有 {sorted(existing) or '（未识别）'}；"
                          f"「{cn}」无任何鉴权语义，构造出来也无法验证权限。",
            })

    # --- 边界成员 ---
    edge_cases = []
    for key, desc in REQUIRED_EDGE_CASES.items():
        edge_cases.append({"id": f"u-edge-{key}", "case": key, "desc": desc,
                           "constructible": False, "blocked_by": "T-ENABLE-MULTI-TENANT"
                           if "tenant" in key else "ROLE-MISSING-support"})
        if key == "expired_support_grant":
            gaps.append({
                "id": "SUPPORT-GRANT",
                "blocks": "AT-30（支持授权过期/撤销/超范围）",
                "missing": "无支持授权实体、无过期字段",
                "detail": "「过期支持授权」需要授权表 + expires_at，当前完全没有。"
                          "AT-30 因此无法编写用例。",
            })
        if key == "disabled_member" and "status" in schema["users_columns"]:
            edge_cases[-1]["constructible"] = True
            edge_cases[-1]["blocked_by"] = None
        if key == "incomplete_email_onboarding" and "email_verified" in schema["users_columns"]:
            edge_cases[-1]["constructible"] = True
            edge_cases[-1]["blocked_by"] = None
        if key == "no_org_member" and schema["has_org_context"]:
            edge_cases[-1]["constructible"] = True
            edge_cases[-1]["blocked_by"] = None

    return {"tenants": tenants, "org_scopes": org_scopes, "users": users,
            "edge_cases": edge_cases, "gaps": gaps}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 自检（INC-70）
# ---------------------------------------------------------------------------
# 为什么必须有
# ------------
# §27.3 写着「每道门禁都有 `--self-test`（阳性对照）或变异测试记录，
# 用来证明**它能抓错**——一个抓不住错误的检查器不是检查器」。
# 本工具此前**既没有 `--self-test`，也没有变异记录**，
# 而 `design_doc_check` DC-8 只把它列成一行 △ 软标注，不拦。
# **只被列出、不被拦下的缺口等于没有缺口。**
#
# 本自检覆盖的四类失效
# --------------------
#   ① 阳性对照：schema 齐备时 `gaps` 必须为空（否则工具永远在报红）
#   ② **缺一项必须报一项**：角色逐个点名，缺 3 个就要有 3 条 ROLE-MISSING
#   ③ **静默失效防护**：`db.py` 解析不出 users 列时必须报 2，而不是给空结论
#   ④ **退出码语义**：缺输入文件必须是 2，`--apply` 在无租户列时必须拒绝
#
# ③ 尤其重要：`probe_schema` 里的 CREATE TABLE 正则对缩进敏感，
# 一旦 `db.py` 格式化，`created` 就是空集。此时若只靠 `ALTER` 分支的 union，
# 会给出「该列不存在」的错误结论——**而那正是本工具的承重输出**。
def self_test() -> int:
    import io
    import contextlib
    print("synthetic_data 自检：证明它抓得住「造不出前置数据」")
    print("=" * 72)
    results: list[tuple[str, bool]] = []

    def t(label: str, cond: bool) -> None:
        results.append((label, cond))
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

    full = {
        "users_columns": ["id", "status", "email_verified", "tenant_id",
                          "organization_context", "requested_organization_context"],
        "known_roles": sorted(REQUIRED_ROLES),
        "has_tenant_column": True,
        "has_org_context": True,
        "has_requested_org_context": True,
    }

    # ① 阳性对照
    #
    # **首版这里我写错了预期**：我断言「schema 齐备时 gaps 为空」，
    # 实测不是——`SUPPORT-GRANT` 是**无条件**上报的。
    # 原因是本工具只解析 `db.py` 的 users 表，**无从得知「支持授权实体」是否存在**
    # （它在 W-10 的设计里、当前没有实现）。把它做成「schema 里有就报、没有就不报」
    # 才是错的：那会让「这张表不存在」被读成「没这个需求」。
    # **如实报告造不出什么，比假装能造更有价值**——工具的立身之本，
    # 测试就必须照着这个立身之本写，不能照着我以为的写。
    plan_ok = build_plan(full)
    t("schema 齐备时仅剩 SUPPORT-GRANT 一条（无条件上报，不靠 schema 推断）",
      [g["id"] for g in plan_ok["gaps"]] == ["SUPPORT-GRANT"])
    t("schema 齐备时不得报 T-ENABLE-MULTI-TENANT / ORG-CONTEXT / 任何 ROLE-MISSING",
      not [g for g in plan_ok["gaps"]
           if g["id"] != "SUPPORT-GRANT"
           and (g["id"].startswith("ROLE-MISSING-")
                or g["id"] in ("T-ENABLE-MULTI-TENANT", "ORG-CONTEXT"))])
    t("schema 齐备时仍要造出 2 租户 × 7 角色 = 14 个用户",
      len(plan_ok["users"]) == 14 and len({u["tenant_id"] for u in plan_ok["users"]}) == 2)
    t("schema 齐备时每租户 3 个组织范围（1 校 + 2 院）",
      len(plan_ok["org_scopes"]) == 6)

    # ② 缺一项必须报一项——**不能只报第一个**
    part = dict(full, known_roles=[r for r in sorted(REQUIRED_ROLES)
                                   if r not in ("billing", "support", "college_admin")])
    miss = sorted(g["id"] for g in build_plan(part)["gaps"]
                  if g["id"].startswith("ROLE-MISSING-"))
    t("缺 3 个角色 → 必须逐个点名这 3 个（不多不少）",
      miss == ["ROLE-MISSING-billing", "ROLE-MISSING-college_admin",
               "ROLE-MISSING-support"])
    t("缺失角色数与 ROLE-MISSING 条数必须相等（不能少报）",
      len(miss) == len(set(REQUIRED_ROLES) - set(part["known_roles"])))

    # 无租户列 → T-ENABLE-MULTI-TENANT；无 org 列 → ORG-CONTEXT
    bare = {"users_columns": ["id"], "known_roles": [],
            "has_tenant_column": False, "has_org_context": False,
            "has_requested_org_context": False}
    ids = [g["id"] for g in build_plan(bare)["gaps"]]
    t("无 tenant_id → 必须报 T-ENABLE-MULTI-TENANT", "T-ENABLE-MULTI-TENANT" in ids)
    t("无 organization_context → 必须报 ORG-CONTEXT", "ORG-CONTEXT" in ids)
    t("无任何角色时 7 类角色全部报出", len([i for i in ids if i.startswith("ROLE-MISSING-")]) == 7)
    t("边界成员在无租户列时全部不可构造",
      all(e["constructible"] is False for e in build_plan(bare)["edge_cases"]))

    # 边界成员的 constructible 必须**随 schema 变化**，不是写死的
    ec_ok = {e["case"]: e["constructible"] for e in plan_ok["edge_cases"]}
    ec_bare = {e["case"]: e["constructible"] for e in build_plan(bare)["edge_cases"]}
    t("边界成员可构造性随 schema 变化（有 status/email_verified/org 才为真）",
      ec_ok["disabled_member"] and ec_ok["incomplete_email_onboarding"]
      and ec_ok["no_org_member"]
      and not ec_bare["disabled_member"] and not ec_bare["no_org_member"])

    # ③ 静默失效防护：db.py 解析不出 users 列 → 必须报 2，而不是给空结论
    global DB_PY, REPO_ROOT
    saved_db, saved_root = DB_PY, REPO_ROOT
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="syndata_selftest_"))
    try:
        # ③a 造一份缩进失配的 db.py：正则解析不到任何列
        (tmp / "db_bad.py").write_text(
            'CREATE TABLE IF NOT EXISTS users (\n\tid TEXT PRIMARY KEY\n);\n'
            'ALTER TABLE users ADD COLUMN email TEXT;\n', encoding="utf-8")
        (tmp / "app").mkdir()
        (tmp / "app" / "auth.py").write_text("MANAGE_ROLES = (\"admin\",)\n",
                                             encoding="utf-8")
        DB_PY, REPO_ROOT = tmp / "db_bad.py", tmp
        rc = None
        try:
            probe_schema()
            rc = 0
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
        t("db.py 的 CREATE TABLE 解析不出列 → 报 2（不是给出空结论）",
          rc == EXIT_TOOL_BROKEN)
        t("该状态下**不得**返回 has_tenant_column=False 这种看似正常的结论",
          rc != EXIT_OK)

        # ③b 缺 db.py 本身 → 也必须是 2
        DB_PY = tmp / "nonexistent.py"
        rc2 = None
        try:
            probe_schema()
            rc2 = 0
        except SystemExit as e:
            rc2 = e.code if isinstance(e.code, int) else 1
        t("db.py 不存在 → 报 2（SystemExit 传字符串会让退出码变成 1）",
          rc2 == EXIT_TOOL_BROKEN)
    finally:
        DB_PY, REPO_ROOT = saved_db, saved_root
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    # ④ 真实基线：当前 db.py 的 users 确实没有 tenant_id
    real = probe_schema()
    t("真实 db.py 可被解析出 users 列（非空，防提取层静默失配）",
      len(real["users_columns"]) > 0)
    t("真实基线：users 当前确实无 tenant_id（承重结论不得凭空翻转）",
      real["has_tenant_column"] is False)
    t("真实基线下的计划必然带 T-ENABLE-MULTI-TENANT 缺口",
      "T-ENABLE-MULTI-TENANT"
      in [g["id"] for g in build_plan(real)["gaps"]])

    # ⑤ --apply 在无租户列时必须拒绝执行（不能制造假隔离）
    #    拒绝信息走的是 **stderr**，只捕 stdout 会读成「它没拒绝」——
    #    测试自己抓错了地方，结论就会是假的。
    argv = sys.argv
    buf, ebuf = io.StringIO(), io.StringIO()
    try:
        sys.argv = ["synthetic_data.py", "--apply", "unused.db"]
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
            rc_apply = main()
    finally:
        sys.argv = argv
    t("--apply 在 users 无 tenant_id 时拒绝执行（非 0）",
      rc_apply != EXIT_OK and "拒绝执行" in buf.getvalue() + ebuf.getvalue())
    t("拒绝理由必须说清「假隔离比不写更危险」",
      "假隔离" in buf.getvalue() + ebuf.getvalue())

    print("=" * 72)
    passed = sum(1 for _, ok in results if ok)
    if passed == len(results):
        print(f"{GREEN}自检通过：{passed}/{len(results)}{RESET}")
        return EXIT_OK
    print(f"{RED}自检失败：{passed}/{len(results)}{RESET}")
    for label, ok in results:
        if not ok:
            print(f"  ✗ {label}")
    return EXIT_GAPS


def main() -> int:
    ap = argparse.ArgumentParser(description="AT-T-4 隔离环境合成数据构造器")
    ap.add_argument("--plan", action="store_true", help="只输出计划与缺口（默认）")
    ap.add_argument("--emit", help="导出 JSON 清单到该路径")
    ap.add_argument("--apply", metavar="DB", help="向目标库写入（需 tenant 列）")
    ap.add_argument("--self-test", action="store_true",
                    help="阳性对照与变异自检（证明本工具抓得住「造不出前置数据」）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    try:
        schema = probe_schema()
    except SystemExit as e:
        sys.stderr.write(f"{e}\n")
        return EXIT_TOOL_BROKEN

    plan = build_plan(schema)

    print(f"{DIM}schema 探测（只读解析 backend/app/db.py）{RESET}")
    print(f"  users 列数            : {len(schema['users_columns'])}")
    print(f"  tenant_id 列          : {'有' if schema['has_tenant_column'] else '无 ❌'}")
    print(f"  organization_context  : {'有' if schema['has_org_context'] else '无 ❌'}")
    print(f"  可识别的角色          : {schema['known_roles'] or '（未识别）'}")
    print()
    print(f"{DIM}构造计划{RESET}")
    print(f"  租户   : {len(plan['tenants'])}")
    print(f"  组织范围: {len(plan['org_scopes'])}（每租户 3 个：1 校 + 2 学院）")
    print(f"  用户   : {len(plan['users'])}（7 类角色 × 2 租户）")
    ok_edge = [e for e in plan["edge_cases"] if e["constructible"]]
    print(f"  边界成员: {len(ok_edge)}/{len(plan['edge_cases'])} 可构造")

    print()
    if plan["gaps"]:
        print(f"{RED}{YELLOW}缺口：{len(plan['gaps'])} 项 —— 按当前 schema 无法构造{RESET}")
        print("=" * 72)
        for g in plan["gaps"]:
            print(f"  {RED}✗{RESET} [{g['id']}]")
            print(f"      阻塞: {g['blocks']}")
            print(f"      缺少: {g['missing']}")
            print(f"      说明: {g['detail']}")
        print("=" * 72)
        print(f"{YELLOW}结论：§24.1 的数据准备要求当前**无法满足**。{RESET}")
        print(f"{DIM}在 T-ENABLE-MULTI-TENANT 与 ROLE-MISSING-* 解除前，"
              f"45 条 AT 中约 14 条无法编写前置数据。{RESET}")
    else:
        print(f"{GREEN}无缺口，可直接构造。{RESET}")

    if args.emit:
        out = pathlib.Path(args.emit)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_probe": schema, "plan": plan}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已导出清单：{out.relative_to(REPO_ROOT) if out.is_relative_to(REPO_ROOT) else out}")

    if args.apply:
        if not schema["has_tenant_column"]:
            sys.stderr.write(
                "拒绝执行 --apply：目标库没有 tenant_id 列。\n"
                "  在当前 schema 上写入「租户」数据只会制造无法生效的假隔离，\n"
                "  比不写更危险——它会让 AT 用例看似有前置数据，实际全在同一租户。\n"
            )
            return EXIT_GAPS
        sys.stderr.write("--apply 尚未实现（需先完成 W-02 迁移）\n")
        return EXIT_GAPS

    return EXIT_GAPS if plan["gaps"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
