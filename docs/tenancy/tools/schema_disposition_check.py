#!/usr/bin/env python3
"""E4 逐表归属判定门禁：规格、真实 schema、工具假设三方交叉核对

背景
----
`W-02-存储与Schema实施规格_20261001.md` §4 给出了 25 张既有表的归属判定
（`platform` / `tenant` / `sidecar` / `none` / `迁移`）。这是**权威口径**。

但这道判定此前没有任何东西在核对，于是本轮实测发现**工具与规格有两处真实冲突**：

    users              规格 = none（全局账号，不加 tenant_id）
                       但 backfill_migrate 往 users 写 tenant_id，
                       且 backfill_validate 把 users 列进 TENANT_TABLES 并要求填满
    organization_units 规格 = 迁移（迁到 tenant_organizations，不原地改）
                       但 TABLE_MAP 把它改名成 organizations 并加列

**规格是权威，工具是实现。实现与规格冲突时改实现，不改规格。**

四项检查
--------
    SD-1  覆盖面    真实 schema 的每张表都有归属判定（无遗漏）
    SD-2  表名真实  规格里判定的表在真实 schema 中确实存在
    SD-3  工具一致  backfill_migrate / backfill_validate 的假设与规格一致
    SD-4  DDL 可执行 `tenant` 判定的表必须有可执行的 ADD COLUMN 语句

SD-4 当前必然报红：W-02 的逐表判定写在散文表格里，**没有对应的 SQL**。
这是规格的完整性缺口，不是工具的 bug——如实报出，不代为补写。

退出码
------
    0 = 三方一致
    1 = 不一致
    2 = 工具不可运行
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sqlite3
import sys

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# 合法判定值（与 W-02 §4 开头一句的定义一致）
DISPOSITIONS = {"platform", "tenant", "sidecar", "none", "迁移"}

# 不参与租户归属的平台/全局表：出现 tenant_id 假设即为冲突
MUST_NOT_HAVE_TENANT = {"users", "sessions", "email_verification_codes",
                        "mini_program_bindings", "mini_program_bind_tickets"}


def parse_dispositions(spec: pathlib.Path) -> dict[str, str]:
    """解析 §4 的逐表判定表：| 1 | `users` | **none** | ...

    为什么必须限定在 §4 范围内
    --------------------------
    文档里**不止一张**形如 `| 1 | `表名` | 判定 |` 的表格。
    W-02 §3.6.1 的 RLS 覆盖矩阵用的是同一套列格式（`| 1 | `tenants` | platform |`），
    但它描述的是 §3.2/§3.3 **新建**的 13 张表，而 §4 判定的是**现有** 25 张表。
    两者是**不相交的集合**——把 §3.6 一起扫进来，会得到 38 张表的判定，
    判 `tenant` 的从 11 张虚增到 21 张，并把 13 张尚未建表的新表报成
    「规格含代码与库都不存在的表」。

    **症状是「门禁变红」，但真实原因是解析范围错了。**
    判红容易，判红的原因对才重要——否则会为了消红灯去改规格，
    把正确的 RLS 矩阵改坏。

    修法：先按标题切出 §4 到下一个同级标题之间的片段，只在这段里匹配。
    """
    text = spec.read_text(encoding="utf-8")
    m = re.search(r"^##\s*4\.\s*现有.*?$(.*?)^##\s*5\.", text, re.M | re.S)
    if not m:
        # 找不到 §4 边界时**不猜**：返回空，由调用方的覆盖面闸报「工具不可运行」，
        # 而不是退化成全文扫描并报出一堆假阳性。
        return {}
    section = m.group(1)
    out: dict[str, str] = {}
    row = re.compile(
        r"^\|\s*\d+\s*\|\s*`([a-z_]+)`\s*\|\s*\*{0,2}([a-z]+|迁移)\*{0,2}\s*\|", re.M)
    for r in row.finditer(section):
        out[r.group(1)] = r.group(2)
    return out


def real_tables(db: pathlib.Path) -> set[str]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()


def code_tables(db_py: pathlib.Path) -> set[str]:
    """从 backend/app/db.py 抽出全部 CREATE TABLE 名。

    为什么需要它：本地开发库**可能比代码旧**。实测中 `ai_usage`/`ai_quota`
    已在 db.py:192,210 定义，但本地库里没有（DB mtime 2026-09-30 10:36，
    晚于它的代码提交 18:17）。
    把本地库当成唯一真相，会把「库过期」误报成「规格写错了表名」。
    """
    if not db_py.exists():
        return set()
    text = db_py.read_text(encoding="utf-8")
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", text, re.I))


def read_tool_assumptions(tools: pathlib.Path) -> dict:
    """从工具源码里读出它们对「哪些表有 tenant_id」的假设。"""
    out: dict = {}
    bv = (tools / "backfill_validate.py")
    bm = (tools / "backfill_migrate.py")
    text_v = bv.read_text(encoding="utf-8") if bv.exists() else ""
    text_m = bm.read_text(encoding="utf-8") if bm.exists() else ""
    m = re.search(r"TENANT_TABLES\s*=\s*\((.*?)\)", text_v, re.S)
    out["validate_tables"] = set(re.findall(r'"(\w+)"', m.group(1))) if m else set()
    m2 = re.search(r"TABLE_MAP\s*=\s*\{(.*?)\n\}", text_m, re.S)
    out["migrate_map"] = dict(re.findall(r'"(\w+)":\s*"(\w+)"', m2.group(1))) if m2 else {}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="E4 逐表归属判定交叉核对")
    ap.add_argument("--spec", help="W-02 规格路径")
    ap.add_argument("--db", help="真实 SQLite 库（只读）")
    ap.add_argument("--tools-dir", help="门禁工具目录")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    here = pathlib.Path(__file__).resolve()
    root = here.parents[3]
    spec = pathlib.Path(args.spec) if args.spec else root / "docs/product/W-02-存储与Schema实施规格_20261001.md"
    db = pathlib.Path(args.db) if args.db else root / "backend/data/paperguard.db"
    tools = pathlib.Path(args.tools_dir) if args.tools_dir else here.parent
    for p, what in ((spec, "规格"), (db, "数据库"), (tools, "工具目录")):
        if not p.exists():
            sys.stderr.write(f"找不到{what}：{p}\n")
            return EXIT_TOOL_BROKEN

    disp = parse_dispositions(spec)
    tables = real_tables(db)
    assume = read_tool_assumptions(tools)
    findings: list[str] = []

    print("=" * 76)
    print("E4 逐表归属判定 · 三方交叉核对")
    print("=" * 76)
    print(f"  规格判定表      {len(disp)} 张")
    print(f"  真实 schema     {len(tables)} 张")
    print(f"  判定值分布      " + " · ".join(
        f"{k} {sum(1 for v in disp.values() if v == k)}" for k in sorted(set(disp.values()))))

    # SD-1 覆盖面
    print(f"\n  {DIM}SD-1 判定覆盖面{RESET}")
    undecided = sorted(tables - set(disp))
    if undecided:
        for t in undecided:
            findings.append(f"SD-1 真实表 {t} 在 W-02 §4 中没有归属判定")
        print(f"  {RED}✗{RESET} {len(undecided)} 张表未判定：{', '.join(undecided)}")
    else:
        print(f"  {GREEN}✓{RESET} 真实 schema 每一张表都有判定")

    # SD-2 表名真实性：代码是权威，库可能是旧的
    print(f"\n  {DIM}SD-2 规格表名真实性{RESET}")
    code = code_tables(root / "backend/app/db.py")
    stale = sorted(t for t in code if t not in tables)
    if stale:
        print(f"  {YELLOW}!{RESET} 本地开发库比代码旧，缺 {len(stale)} 张已定义表："
              f"{', '.join(stale)}")
        print(f"      {DIM}这不是规格错误，是本地库未重新迁移；"
              f"以 db.py 为准，不计入不一致{RESET}")
    known = set(tables) | code
    ghost = sorted(t for t in disp if t not in known)
    bad_val = sorted(t for t, v in disp.items() if v not in DISPOSITIONS)
    if ghost:
        for t in ghost:
            findings.append(f"SD-2 规格判定了 {t}，但代码 schema 与真实库中都没有这张表")
        print(f"  {RED}✗{RESET} 规格含代码与库都不存在的表：{', '.join(ghost)}")
    else:
        print(f"  {GREEN}✓{RESET} 规格判定的表在代码 schema 或真实库中存在")
    if bad_val:
        for t in bad_val:
            findings.append(f"SD-2 判定值不合法：{t} = {disp[t]}")
        print(f"  {RED}✗{RESET} 非法判定值：{', '.join(f'{t}={disp[t]}' for t in bad_val)}")
    else:
        print(f"  {GREEN}✓{RESET} 判定值均合法（{'/'.join(sorted(DISPOSITIONS))}）")

    # SD-3 工具一致性
    print(f"\n  {DIM}SD-3 工具假设与规格一致{RESET}")
    v_tables = assume["validate_tables"]
    for t in sorted(MUST_NOT_HAVE_TENANT & set(disp)):
        if disp[t] == "none" and t in v_tables:
            findings.append(
                f"SD-3 规格判定 {t} = none（不加 tenant_id），"
                f"但 backfill_validate 把它列入 TENANT_TABLES 并要求 tenant_id 填满")
            print(f"  {RED}✗{RESET} {t}: 规格 none ↔ backfill_validate 视为需回填表")
    mm = assume["migrate_map"]
    for src, dst in mm.items():
        if disp.get(src) == "迁移" and dst != "tenant_organizations":
            findings.append(
                f"SD-3 规格判定 {src} = 迁移（迁到 tenant_organizations，不原地改），"
                f"但 backfill_migrate 的 TABLE_MAP 把它映射成 {dst}")
            print(f"  {RED}✗{RESET} {src}: 规格要求迁到 tenant_organizations，"
                  f"工具映射为 {dst}")
    if not any(f.startswith("SD-3") for f in findings):
        print(f"  {GREEN}✓{RESET} 工具假设与规格判定无冲突")

    # SD-4 DDL 可执行性
    print(f"\n  {DIM}SD-4 tenant 判定的表有可执行 DDL{RESET}")
    sql_text = "".join(re.findall(r"```sql\n(.*?)```",
                                 spec.read_text(encoding="utf-8"), re.S))
    tenant_tables = sorted(t for t, v in disp.items() if v == "tenant")
    missing_ddl = [t for t in tenant_tables
                   if not re.search(rf"ALTER TABLE\s+{t}\b", sql_text, re.I)]
    if missing_ddl:
        print(f"  {YELLOW}✗{RESET} {len(missing_ddl)}/{len(tenant_tables)} 张 tenant 表"
              f"没有对应的 ALTER TABLE 语句")
        print(f"      {', '.join(missing_ddl)}")
        print(f"      {DIM}逐表判定写在散文表格里，没有可执行的 SQL——"
              f"这是规格完整性缺口，不是工具 bug{RESET}")
        findings.append(
            f"SD-4 {len(missing_ddl)} 张 tenant 表只有散文判定、无可执行 DDL："
            f"{', '.join(missing_ddl)}")
    else:
        print(f"  {GREEN}✓{RESET} 全部 tenant 表都有 ALTER TABLE 语句")

    # SD-5 汇总数字与逐表判定一致
    print(f"\n  {DIM}SD-5 汇总数字与逐表判定一致{RESET}")
    spec_text = spec.read_text(encoding="utf-8")
    n_tenant = sum(1 for v in disp.values() if v == "tenant")
    n_side = sum(1 for v in disp.values() if v == "sidecar")
    claims = re.findall(
        r"(\d+)\s*张\s*`?tenant`?\s*表\s*\+\s*(\d+)\s*张\s*`?sidecar`?\s*表", spec_text)
    if not claims:
        print(f"  {GREEN}✓{RESET} 未发现与逐表判定冲突的汇总数字")
    else:
        bad_claims = [(int(a), int(b)) for a, b in claims
                      if (int(a), int(b)) != (n_tenant, n_side)]
        for a, b in dict.fromkeys(bad_claims):
            findings.append(
                f"SD-5 汇总文字称「{a} 张 tenant + {b} 张 sidecar」，"
                f"但 §4 逐表判定是 {n_tenant} + {n_side}——以逐表判定为准")
            print(f"  {RED}✗{RESET} 汇总称 {a}+{b}，逐表判定是 {n_tenant}+{n_side}")
        if not bad_claims:
            print(f"  {GREEN}✓{RESET} 汇总数字与逐表判定一致（{n_tenant} + {n_side}）")

    print("=" * 76)
    findings = list(dict.fromkeys(findings))   # 去重：同一处笔误常被引用多次
    if findings:
        for f in findings:
            print(f"  {RED}✗{RESET} {f}")
        print(f"{RED}三方不一致：{len(findings)} 项{RESET}")
        return EXIT_FINDINGS
    print(f"{GREEN}三方一致{RESET}")
    return EXIT_OK


# ---------------------------------------------------------------------------

def self_test() -> int:
    print("schema_disposition_check 自检")
    print("=" * 64)
    import shutil
    import sqlite3 as sq
    tmp = pathlib.Path("/tmp/sdc_selftest")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "tools").mkdir(parents=True)

    db = tmp / "x.db"
    con = sq.connect(db)
    con.executescript("CREATE TABLE users(id TEXT); CREATE TABLE projects(id TEXT);"
                      "CREATE TABLE templates(id TEXT);")
    con.commit()
    con.close()

    (tmp / "tools" / "backfill_validate.py").write_text(
        'TENANT_TABLES = (\n    "users",\n    "projects",\n)\n', encoding="utf-8")
    (tmp / "tools" / "backfill_migrate.py").write_text(
        'TABLE_MAP = {\n    "organization_units": "organizations",\n}\n',
        encoding="utf-8")

    spec = tmp / "spec.md"

    def run(text: str) -> int:
        spec.write_text(text, encoding="utf-8")
        argv = sys.argv
        sys.argv = ["x", "--spec", str(spec), "--db", str(db),
                    "--tools-dir", str(tmp / "tools")]
        import contextlib
        import io
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = main()
        finally:
            sys.argv = argv
        return rc

    results: list[tuple[str, bool]] = []
    # 必须带 §4 / §5 标题：解析器按章节切分（§3.6.1 的 RLS 矩阵是另一张表）
    head = "## 4. 现有表归属判定\n\n"
    tail = "\n## 5. 任务分解\n"
    base = (head
            + "| 1 | `users` | **none** | 不加 |\n"
              "| 2 | `projects` | **tenant** | 加 |\n"
              "| 3 | `templates` | **tenant** | 加 |\n" + tail)

    rc = run(base)
    results.append(("users=none 却进了 TENANT_TABLES → 被抓", rc == EXIT_FINDINGS))
    rc = run(base.replace(tail, "| 4 | `organization_units` | **迁移** | 迁 |\n" + tail))
    results.append(("迁移表被映射成 organizations → 被抓", rc == EXIT_FINDINGS))
    rc = run(base.replace(tail, "| 4 | `ghost_table` | **tenant** | 加 |\n" + tail))
    results.append(("规格含不存在的表 → 被抓", rc == EXIT_FINDINGS))
    rc = run(head + "| 1 | `users` | **none** | 不加 |\n" + tail)  # projects/templates 未判定
    results.append(("真实表未判定 → 被抓", rc == EXIT_FINDINGS))
    rc = run(base.replace(tail, "| 4 | `organization_units` | **sidecar** | 加 |\n" + tail))
    results.append(("未含 DDL 的 tenant 表 → 被抓", rc == EXIT_FINDINGS))

    # 章节范围隔离：§3.6.1 的 RLS 矩阵不得被当成 §4 判定。
    # 这是 2026-10-01 真实踩到的回归——新增 §3.6 后 SD-2/SD-5 立刻变红，
    # 报 13 张尚未建表的新表为「规格含不存在的表」。
    # 噪声必须放在 §4 **之前**（§3.6 的真实位置），放进 §4 内部会被正确地判为判定。
    noise = ("### 3.6.1 RLS 覆盖矩阵\n\n"
             "| 1 | `tenants` | platform | 无 |\n"
             "| 2 | `storage_objects` | tenant | `NOT NULL` |\n"
             "| 3 | `migration_batches` | platform | 无 |\n\n")

    # 本用例要判「一致的规格」能通过，因此**先**把夹具摆成一致态：
    # 建 organization_units、修正 TABLE_MAP、users 移出 TENANT_TABLES。
    # 顺序很关键——否则 SD-1/SD-3 会先报红，掩盖本用例真正要验的范围隔离。
    (tmp / "tools" / "backfill_validate.py").write_text(
        'TENANT_TABLES = (\n    "projects",\n)\n', encoding="utf-8")
    (tmp / "tools" / "backfill_migrate.py").write_text(
        'TABLE_MAP = {\n    "organization_units": "tenant_organizations",\n}\n',
        encoding="utf-8")
    con = sq.connect(db)
    con.execute("CREATE TABLE organization_units(id TEXT)")
    con.commit()
    con.close()

    consistent = (head
                  + "| 1 | `users` | **none** | 不加 |\n"
                    "| 2 | `projects` | **tenant** | 加 |\n"
                    "| 3 | `templates` | **tenant** | 加 |\n"
                    "| 4 | `organization_units` | **迁移** | 迁 |\n"
                  + "```sql\n"
                    "ALTER TABLE projects ADD COLUMN tenant_id text;\n"
                    "ALTER TABLE templates ADD COLUMN tenant_id text;\n"
                    "```\n" + tail)
    rc = run(noise + consistent)
    results.append(("★ §3.6 RLS 矩阵不得混入 §4 判定 → 通过", rc == EXIT_OK))

    # 反向对照：同样的行放进 §4 内部**必须**被当作判定（否则就是把闸门开太大）
    rc = run(head + noise.split("\n\n", 1)[1].replace("### 3.6.1", "")
             + consistent[len(head):])
    results.append(("同类表格在 §4 内仍按判定处理 → 被抓", rc == EXIT_FINDINGS))

    # 一致场景：users 移出 TENANT_TABLES、迁移表映射正确、tenant 表补 DDL
    ok_spec = consistent
    rc = run(ok_spec)
    results.append(("三方一致 → 通过", rc == EXIT_OK))

    shutil.rmtree(tmp)
    ok = 0
    for label, good in results:
        print(f"  [{'PASS' if good else 'FAIL'}] {label}")
        ok += good
    print("=" * 64)
    if ok == len(results):
        print(f"{GREEN}自检通过：{ok}/{len(results)}{RESET}")
        return EXIT_OK
    print(f"{RED}自检失败：{ok}/{len(results)}{RESET}")
    return EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
