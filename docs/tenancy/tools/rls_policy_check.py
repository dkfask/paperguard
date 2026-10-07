#!/usr/bin/env python3
"""RLS 策略矩阵门禁：核对每张租户表是否真的被策略保护。

背景（INC-15）
--------------
W-02 §3.2/§3.3 定义了 13 张新表，其中 10 张带 `tenant_id`。
初版 §3.5 的 RLS 骨架**只对 `tenant_memberships` 一张表**定义了策略——
另外 9 张表的 `tenant_id` 列在数据库层**没有任何隔离保护**。

**加列不等于隔离。** `tenant_id` 只是一个普通列；没有 `CREATE POLICY`，
RLS 机制对这张表根本不生效，ADR-02 的核心隔离机制在这些表上是空的。

总设计 §8.2 行第 321 行的原文是「需验证的表采用适当的 `FORCE ROW LEVEL
SECURITY`」——「表」是**复数**。§3.5 只做一张表，与总设计不一致。**以总设计为准。**

本门禁做什么
------------
按 W-02 §3.6.1 矩阵逐表核对三件事，缺一即报红：

1. `ALTER TABLE <t> ENABLE ROW LEVEL SECURITY`
2. `ALTER TABLE <t> FORCE  ROW LEVEL SECURITY`（否则表所有者绕过，策略形同虚设）
3. `CREATE POLICY ... ON <t>`，且策略体含 `WITH CHECK`（只写 `USING` 时
   PostgreSQL 会把 `USING` 兼作 `WITH CHECK`，属可接受但需显式确认的情形）

三类表分别核对：
- **租户隔离表**（10 张）：必须有策略，且谓词必须引用 `current_setting('app.tenant_id'`
- **平台默认拒绝表**（2 张：`platform_role_bindings`/`migration_batches`）：
  必须 `ENABLE`+`FORCE` 且**不得有**任何 API 策略——默认拒绝是唯一正确选择
- **双主体表**（`tenants`）：必须有平台分支与租户分支两条策略

为什么必须有覆盖面闸
--------------------
`load_policies()` 对**解析不到任何内容**的文档返回空集合。若照 INC-06 的老教训
直接比「期望 13 张 vs 实际 0 张」，会得到 `0 == 0` 的假绿——
**核对了 0 行却报通过，比只会报红危险得多**。
因此本工具先断言「确实解析到 13 张表」，解析到 0 张时返回 **2（工具不可运行）**，
而不是 0（通过）。

退出码
------
    0 = 全部核对通过
    1 = 存在缺策略 / 缺 FORCE / 策略类型错误（**检查确实跑了，且未通过**）
    2 = 工具自身无法运行（文档缺失、解析到 0 张表）
"""

from __future__ import annotations

import pathlib
import re
import sys

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

ROOT = pathlib.Path(__file__).resolve().parents[3]
SPEC = "docs/product/W-02-存储与Schema实施规格_20261001.md"

# ---------------------------------------------------------------------------
# §3.6.1 覆盖矩阵（权威来源：W-02 §3.6.1）
# kind: isolation = 标准租户隔离；deny = 平台默认拒绝（不得有 API 策略）
#       dual = 双主体（平台 ∪ 本租户）
# ---------------------------------------------------------------------------
MATRIX: dict[str, str] = {
    "tenants":                 "dual",
    "tenant_organizations":    "isolation",
    "tenant_memberships":      "isolation",
    "membership_roles":        "isolation",
    "membership_org_grants":   "isolation",
    "membership_certifications": "isolation",
    "teacher_student_bindings": "isolation",
    "member_external_ids":     "isolation",
    "tenant_invitations":      "isolation",
    "platform_role_bindings":  "deny",
    "support_grants":          "isolation",
    "storage_objects":         "isolation",
    "migration_batches":       "deny",
    # ↓ 以下 12 张是 INC-15 之后逐轮补进规格 §3.6.1 的，
    #   **而本常量从未同步**（实测：规格 25 张 / 常量 13 张），
    #   且**没有任何东西核对**——与 INC-83 记录的是同一个病。
    #   补齐后由 `matrix_consistency()` 负责此后不再漂移。
    "plan_versions":           "global",      # 全局目录数据，无租户维度，不建 RLS
    "subscriptions":           "isolation",
    "entitlement_grants":      "isolation",
    "quota_periods":           "isolation",
    "budget_policies":         "isolation",
    "tenant_settings":         "isolation",
    "ai_requests":             "isolation",
    "usage_events":            "isolation",
    "usage_aggregates":        "isolation",
    "support_tickets":         "isolation",
    "exit_requests":           "isolation",
    "provider_credentials":    "isolation",   # 2026-10-02 补（INC-82）
}
EXPECTED_TABLES = 25
# ⚠ **这个常量与上面的 `MATRIX` 没有任何关联**（2026-10-02 实测，INC-83）
# ------------------------------------------------------------------
# `MATRIX` 是一张**手写的**表→类别映射，而 `EXPECTED_TABLES` 是**另一个数字**。
# 两者本该恒等，却**没有任何东西核对**：
# 矩阵加了 `provider_credentials` 之后，`MATRIX` 与规格矩阵都是 **25 张**，
# 而本常量仍是 24 ⇒ 门禁输出「通过：24 张表全部核对」，
# **它报的不是「我核对了多少张」，而是「我预设应该有 24 张」。**
#
# 危害比 INC-57 那一族更具体：**结论句里的数字本身是常量**，
# 读者会以为那就是核对覆盖面，于是**新增表被自动排除在结论之外而无任何提示**。
#
# 修法：`matrix_consistency()` 把两者与规格实算值三方比对，
# 不等即报红。**常量可以留着当「文档声称的数」，但必须有人核对它。**
MATRIX_CATEGORY_DOC = ("isolation", "deny", "both", "global")


def matrix_consistency(spec_matrix: set[str]) -> list[str]:
    """`MATRIX` / `EXPECTED_TABLES` / 规格实算 三方必须一致。"""
    out: list[str] = []
    real = len(spec_matrix)
    if len(MATRIX) != real:
        missing = sorted(spec_matrix - set(MATRIX))
        extra = sorted(set(MATRIX) - spec_matrix)
        out.append(
            f"RL-2 工具内 `MATRIX` 常量 {len(MATRIX)} 张，规格 §3.6.1 实算 {real} 张"
            + (f"；规格有而常量缺：{', '.join(missing)}" if missing else "")
            + (f"；常量有而规格无：{', '.join(extra)}" if extra else ""))
    if EXPECTED_TABLES != real:
        out.append(
            f"RL-2 `EXPECTED_TABLES = {EXPECTED_TABLES}`，规格 §3.6.1 实算 {real} 张"
            f"——结论句会报「通过 {EXPECTED_TABLES} 张全部核对」，"
            f"而真实覆盖面是 {real} 张，**多出来的表被静默排除在结论之外**")
    return out

TENANT_TABLES = sum(1 for k in MATRIX.values() if k == "isolation")
DENY_TABLES = sum(1 for k in MATRIX.values() if k == "deny")

# §4 判定的 11 张**现有**表：expand 阶段加 nullable 列，**contract 阶段才建 RLS**。
# 不得因为它们没有策略就报红——那是阶段不同，不是缺陷（§3.6.4）。
CONTRACT_PHASE_TABLES = {
    "templates", "projects", "requirement_sets", "document_packages",
    "document_projects", "validation_runs", "check_runs", "workflow_records",
    "workflow_idempotency", "workflow_events", "jobs",
}


def load_policies(spec: pathlib.Path) -> tuple[dict, list[str]]:
    """解析 W-02 规格，返回 ({表名: {...}}, 结构性错误列表)。"""
    if not spec.exists():
        return {}, [f"{SPEC}: 文件不存在"]
    text = spec.read_text(encoding="utf-8")

    enabled: set[str] = set()
    forced: set[str] = set()
    policies: dict[str, list[dict]] = {}
    errs: list[str] = []

    for m in re.finditer(
            r"ALTER\s+TABLE\s+(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", text, re.I):
        enabled.add(m.group(1))
    for m in re.finditer(
            r"ALTER\s+TABLE\s+(\w+)\s+FORCE\s+ROW\s+LEVEL\s+SECURITY", text, re.I):
        forced.add(m.group(1))

    # CREATE POLICY <name> ON <table> [FOR ...] [TO ...] USING (...) [WITH CHECK (...)]
    for m in re.finditer(
            r"CREATE\s+POLICY\s+(\w+)\s+ON\s+(\w+)(.*?);", text, re.I | re.S):
        name, table, rest = m.group(1), m.group(2), m.group(3)
        policies.setdefault(table, []).append({
            "name": name,
            "using": bool(re.search(r"\bUSING\b\s*\(", rest, re.I)),
            "with_check": bool(re.search(r"\bWITH\s+CHECK\b\s*\(", rest, re.I)),
            "body": rest,
        })

    if not enabled and not policies:
        errs.append("未从规格中解析到任何 RLS 声明——无法核对")
    return {"enabled": enabled, "forced": forced, "policies": policies}, errs


def check(spec_state: dict, matrix: dict[str, str]) -> list[str]:
    """纯核对逻辑：不做 I/O，因此可被 --self-test 用构造数据覆盖。"""
    problems: list[str] = []
    enabled = spec_state.get("enabled", set())
    forced = spec_state.get("forced", set())
    policies = spec_state.get("policies", {})

    for table, kind in sorted(matrix.items()):
        # `global`（2026-10-02 因 plan_versions 新增）：
        # **全平台共享的目录数据，无租户维度，因而不建 RLS**。
        # 不写这个分支的后果是实测的：门禁把 `plan_versions` 报成
        # 「缺 ENABLE / 缺 FORCE / 无 POLICY」——**判据读不懂合法类别，
        # 就等于强制它建 RLS，或者逼着人把它伪装成 tenant 表**。两者都更坏。
        if kind == "global":
            if table in enabled or table in forced or policies.get(table):
                problems.append(
                    f"{table}: 判定为 `global`（无租户维度、不建 RLS），"
                    f"却有 ENABLE/FORCE/POLICY——**要么改判定，要么删策略**，"
                    f"两者不一致会让读者以为它受隔离保护")
            continue
        if table not in enabled:
            problems.append(f"{table}: 缺 ALTER TABLE ... ENABLE ROW LEVEL SECURITY")
        if table not in forced:
            problems.append(
                f"{table}: 缺 FORCE ROW LEVEL SECURITY（表所有者会绕过策略）")

        mine = policies.get(table, [])
        if kind == "deny":
            # 平台默认拒绝：不得为 API 角色建任何策略
            if mine:
                problems.append(
                    f"{table}: 平台默认拒绝表不应有 CREATE POLICY（现有 "
                    f"{len(mine)} 条：{', '.join(p['name'] for p in mine)}）")
            continue

        if not mine:
            problems.append(f"{table}: 无 CREATE POLICY——tenant_id 列不受任何隔离保护")
            continue

        if kind == "dual":
            # tenants：必须同时有平台分支与租户分支
            names = [p["name"] for p in mine]
            has_platform = any(
                "platform" in n or "pg_read_all_data" in p["body"]
                for n, p in zip(names, mine))
            has_tenant = any(
                "self" in n or "app.tenant_id" in p["body"]
                for n, p in zip(names, mine))
            if not has_platform:
                problems.append(f"{table}: 缺平台主体策略（平台运维需读全量）")
            if not has_tenant:
                problems.append(f"{table}: 缺租户主体策略（成员需读本租户行）")
            continue

        # isolation：至少一条策略，且必须引用租户上下文
        if not any("app.tenant_id" in p["body"] for p in mine):
            problems.append(
                f"{table}: 策略未引用 current_setting('app.tenant_id')——"
                f"不构成租户隔离")
        if not any(p["with_check"] for p in mine):
            problems.append(
                f"{table}: 无 WITH CHECK 子句——写策略未限制 tenant_id，"
                f"可改写为其他租户")
    return problems


def check_built_tables(text: str) -> list[str]:
    """RL-1：§3.6.1 矩阵的表名必须与 §3.2/§3.3 **实际 CREATE TABLE** 一一对应。

    为什么需要它（2026-10-01 补）
    --------------------------
    本门禁原先只核对「矩阵里的表有没有策略」——
    **漏登记一张新表，它就完全不出现，也就不会报红**。
    新表是从 §3.2/§3.3 的 `CREATE TABLE` 来的，权威来源在那里。

    方向都要查：
    - 建了但矩阵没有 → 该表**没有任何 RLS 保护且没人知道**（最危险）
    - 矩阵有但没建   → 矩阵写了不存在的表，规格与现实脱节
    """
    built: set[str] = set()
    for m in re.finditer(r"^\s*CREATE TABLE\s+(\w+)\s*\(", text, re.M):
        built.add(m.group(1))
    if not built:
        return ["RL-1 未从规格解析到任何 CREATE TABLE，无法核对矩阵完整性"]
    # 矩阵行：| N | `name` | platform|tenant | ... |
    #
    # **必须限定在 §3.6.1 区间内**（2026-10-01 实测修正）
    # ------------------------------------------------
    # §4 逐表判定表的列格式高度相似（`| N | \`name\` | tenant | ...`），
    # 直接全文匹配会把 25 张**现有表**当成矩阵行，
    # 于是 RL-1 报「矩阵登记了 12 张未建表的表」——**纯误报**。
    # §3.6.1 与下一个 #### 之间就是矩阵全貌。
    mat: set[str] = set()
    sec = re.search(r"####\s*3\.6\.1.*?(?=\n#+\s|\Z)", text, re.S)
    problems_early: list[str] = []
    if sec:
        # `global` 是第三类（2026-10-01 因 plan_versions 新增）：
        # **全平台共享的目录数据，无租户维度，因而不建 RLS**。
        # 不把它写进正则的后果是实测的：门禁把 `plan_versions` 报成
        # 「已建表但矩阵未登记」——**判据读不懂合法类别，就等于强制它走 RLS，
        # 或者逼着人把它伪装成 tenant 表**。两者都更坏。
        mat = set(re.findall(
            r"^\|\s*\d+\s*\|\s*`([a-z_]+)`\s*\|\s*(?:platform|tenant|\*\*全局\*\*)\s*\|",
            sec.group(0), re.M))
    else:
        return ["RL-1 找不到 §3.6.1 覆盖矩阵，无法核对登记完整性"]
    # **矩阵声明 ⇒ DDL 里必须有对应语句**（2026-10-01 因 INC-66 新增）
    # ----------------------------------------------------------------
    # 实测缺口：矩阵扩到 24 张之后，`rls_policy_check` 依然报
    # 「通过：13 张表全部核对」——它只核对**已经有 ALTER 语句的那 13 张**，
    # **完全没发现矩阵里另外 11 张是空头声明**。
    # 换句话说：**矩阵可以声称一张表受保护，而 DDL 里什么都没写**，
    # 门禁照样全绿。**这正是「看起来已被覆盖」的教科书形态。**
    #
    # 因此本判据**反向**核对：矩阵里凡要求 `ENABLE`+`FORCE` 的行，
    # 其 ALTER 语句必须在规格里真实存在。`global` 类**显式豁免**——
    # 它按定义就不建 RLS（且另有「带 tenant_id 即失败」的前置条件兜底）。
    covered: set[str] = set()
    for m2 in re.finditer(
            r"ALTER\s+TABLE\s+(\w+)\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
            text, re.I):
        covered.add(m2.group(1))
    forced: set[str] = set()
    for m2 in re.finditer(
            r"ALTER\s+TABLE\s+(\w+)\s+FORCE\s+ROW\s+LEVEL\s+SECURITY",
            text, re.I):
        forced.add(m2.group(1))
    for m2 in re.finditer(
            r"^\|\s*\d+\s*\|\s*`([a-z_]+)`\s*\|\s*(?:platform|tenant)\s*\|",
            sec.group(0), re.M):
        name = m2.group(1)
        if name not in covered:
            problems_early.append(
                f"RL-1 矩阵声明 `{name}` 受保护，但规格里没有 "
                f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY——**空头声明**")
        elif name not in forced:
            problems_early.append(
                f"RL-1 `{name}` 有 ENABLE 但缺 FORCE（表所有者会绕过策略）")

    # **`global` 类别的前置条件**（2026-10-01 新增）
    # --------------------------------------------
    # 「不建 RLS」是一个**有代价的选择**，不能成为逃生口：
    # 一张 `global` 表若带 `tenant_id`，它就是**租户数据却没有行级隔离**——
    # 任何 API 角色都能读到全平台数据。所以这个组合**必须**硬失败，
    # 而不是「合法例外」。判据：矩阵里标 `**全局**` 的表，
    # 其 CREATE TABLE 体内**不得出现** `tenant_id` 列。
    problems: list[str] = list(problems_early)
    global_rows = set(re.findall(
        r"^\|\s*\d+\s*\|\s*`([a-z_]+)`\s*\|\s*\*\*全局\*\*\s*\|",
        sec.group(0), re.M))
    if global_rows:
        bodies = {m.group(1): m.group(0) for m in re.finditer(
            r"CREATE TABLE\s+(\w+)\s*\((.*?)\n\s*\);", text, re.S | re.I)}
        for g in sorted(global_rows):
            body = bodies.get(g, "")
            # **`re.I` 不是可有可无**：SQL 关键字不区分大小写，
            # 而自检夹具用的是大写 `TEXT`。首版漏了 `re.I`，
            # 这条判据在大写 DDL 上会**静默失效**——判据「没报」会被
            # 读成「没这回事」，而真实风险是租户数据没有行级隔离。
            if re.search(r"^\s+tenant_id\s+text\b", body, re.M | re.I):
                problems.append(
                    f"RL-1 `{g}` 标为「全局/不建 RLS」却带 tenant_id 列——"
                    f"这是**租户数据没有行级隔离**，必须改为 tenant 类并建策略")

    missing = sorted(built - mat)
    if missing:
        problems.append(
            f"RL-1 已建表但 RLS 矩阵未登记：{missing}"
            f"——该表既无策略保护，也不在门禁视野内")
    extra = sorted(mat - built)
    if extra:
        problems.append(f"RL-1 矩阵登记了未建表的表：{extra}")
    return problems


def verdict(n_tables: int, problems: list[str], errs: list[str]
            ) -> tuple[int, str]:
    """纯判定函数。**0 张表必须报 2 而不是 0**——这是本工具最重要的不变量。"""
    if errs:
        return EXIT_TOOL_BROKEN, errs[0]
    if n_tables == 0:
        return EXIT_TOOL_BROKEN, "未解析到任何带策略的表，本项无数据可核对"
    if problems:
        return EXIT_FINDINGS, f"未通过：{len(problems)} 项"
    return EXIT_OK, (f"通过：{n_tables} 张表全部核对"
                      f"｜★ 本工具校验的是 W-02 **规格文本**里的 RLS 策略定义"
                      f"（E/F/P 齐全性），**不读 backend/、不查任何运行库**——"
                      f"这些表是否已建出由 ddl_static_check 报告，见 INC-113")


# ---------------------------------------------------------------------------
# 自检：阳性对照 + 阴性对照 + 变异（证明能抓错，不是只会报红）
# ---------------------------------------------------------------------------

def _state(enable=(), force=(), policies=None):
    return {"enabled": set(enable), "forced": set(force),
            "policies": policies or {}}


def _iso(table):
    """构造一条合规的隔离策略。"""
    return {table: [{
        "name": f"{table}_tenant_isolation",
        "using": True, "with_check": True,
        "body": (f"USING (tenant_id = nullif("
                 f"current_setting('app.tenant_id', true), '')) "
                 f"WITH CHECK (tenant_id = nullif("
                 f"current_setting('app.tenant_id', true), ''))"),
    }]}


def _full_good():
    """构造一份「`MATRIX` 里的表全部合规」的状态（张数随矩阵走，不写死）。

    ⚠ **不要在这里写死张数**：本函数首版注释写「13 张表全部合规」，
    而 `MATRIX` 早已扩到 25 张——**注释里的数字与代码实际遍历的集合脱节**，
    于是夹具在 `MATRIX` 扩容后静默变红，看起来像「判据坏了」。
    **夹具的正确形状是「遍历被测对象」，不是「复述它的规模」。**
    """
    en, fo, po = set(), set(), {}
    for t, kind in MATRIX.items():
        if kind == "global":
            continue          # 不建 RLS：ENABLE/FORCE/POLICY 三样都**不应有**
        en.add(t); fo.add(t)
        if kind == "deny":
            continue
        if kind == "dual":
            po[t] = [
                {"name": "tenants_platform_read", "using": True,
                 "with_check": False, "body": "FOR SELECT TO pg_read_all_data USING (true)"},
                {"name": "tenants_self_read", "using": True,
                 "with_check": False,
                 "body": "FOR SELECT USING (id = nullif(current_setting('app.tenant_id', true), ''))"},
            ]
        else:
            po.update(_iso(t))
    return _state(en, fo, po)


def self_test() -> int:
    cases: list[tuple[str, tuple[int, str], tuple[int, str]]] = []

    def expect_findings(label, spec_state, matrix=None, n=EXPECTED_TABLES):
        """登记一个「应当报红（1）」的用例。"""
        m = matrix if matrix is not None else MATRIX
        cases.append((label, verdict(n, check(spec_state, m), []),
                      (EXIT_FINDINGS, "")))

    def add(label, spec_state, matrix=None, n=EXPECTED_TABLES):
        """登记一个「应当变绿（0）」的用例。"""
        m = matrix if matrix is not None else MATRIX
        cases.append((label, verdict(n, check(spec_state, m), []),
                      (EXIT_OK, "")))

    # --- 阳性对照：完整合规状态必须变绿 ---
    add("全量合规（13 表）→ 0", _full_good())

    # --- 覆盖面闸：0 张表必须报 2，绝不报 0 ---
    empty_problems = check(_state(), MATRIX)
    cases.append((
        "★ 0 张表（覆盖面闸）→ 2，不得为 0",
        verdict(0, empty_problems, []), (EXIT_TOOL_BROKEN, ""),
    ))
    cases.append((
        "★ 文档缺失 → 2",
        verdict(0, [], [f"{SPEC}: 文件不存在"]),
        (EXIT_TOOL_BROKEN, ""),
    ))

    # --- RL-1：矩阵与实际建表必须一一对应 ---
    # 漏登记一张新表是**最危险的漏法**：
    # 该表既没有任何 RLS 保护，也不在门禁视野内 —— 永远不会报红。
    # 夹具必须带 §3.6.1 标题（RL-1 只在该区间内取矩阵行），
    # 并**额外放一张 §4 风格的干扰表**——它不得被当成矩阵行。
    def _spec(created, matrix_rows, extra_s4=""):
        return ("#### 3.6.1 覆盖矩阵\n\n" + created + "\n" + matrix_rows
                + "\n## 4. 现有表判定\n\n" + extra_s4)

    created2 = ("CREATE TABLE alpha (\n  id TEXT\n);\n"
                "CREATE TABLE beta (\n  id TEXT\n);\n")
    # **必须带 ALTER**：2026-10-01 起矩阵声明 ENABLE+FORCE 就**必须**有对应语句
    # （空头声明判据）。这两条夹具写于该判据之前，补齐后语义才一致——
    # **补夹具不是放宽判据**。
    created1 = ("CREATE TABLE alpha (\n  id TEXT\n);\n"
                "ALTER TABLE alpha ENABLE ROW LEVEL SECURITY;\n"
                "ALTER TABLE alpha FORCE  ROW LEVEL SECURITY;\n")
    row_alpha = "| 1 | `alpha` | tenant | `NOT NULL` | x | y |"
    s4_noise = ("| 1 | `projects` | tenant | 加 |\n"
                "| 2 | `jobs` | tenant | 加 |\n")
    cases.append(("★ RL-1 建了 beta 但矩阵未登记 → 被抓",
                  verdict(2, check_built_tables(
                      _spec(created2, row_alpha)), []), (EXIT_FINDINGS, "")))
    cases.append(("RL-1 矩阵与建表一致 → 通过",
                  verdict(1, check_built_tables(
                      _spec(created1, row_alpha, s4_noise)), []), (EXIT_OK, "")))
    cases.append(("★ RL-1 矩阵多出未建表 → 被抓",
                  verdict(2, check_built_tables(
                      _spec(created2, row_alpha + "\n| 2 | `ghost` | tenant | a | b | c |")),
                      []), (EXIT_FINDINGS, "")))
    # 解析不到任何 CREATE TABLE 是**工具不可运行**（2）而非「未通过」（1）——
    # 与覆盖面闸同源：区分不了「规格里没建表」和「正则失效了」。
    cases.append(("★ RL-1 解析不到 CREATE TABLE → 2（工具不可运行）",
                  verdict(0, check_built_tables("没有任何建表语句"), []),
                  (EXIT_TOOL_BROKEN, "")))
    cases.append(("★ RL-1 §4 判定表不得被当成矩阵行",
                  verdict(1, check_built_tables(
                      _spec(created1, row_alpha, s4_noise)), []), (EXIT_OK, "")))

    # ---- `global` 类别的两条判据（2026-10-01 新增）----
    # 「不建 RLS」是合法选择，但**不是无代价的**：一张 global 表若带 tenant_id，
    # 它就是租户数据却没有任何行级隔离。**必须硬失败**，不能当例外放过。
    row_global_ok = "| 1 | `alpha` | **全局** | 无 | **不建 RLS** | 目录数据 |"
    created_global = "CREATE TABLE alpha (\n  id TEXT,\n  label TEXT\n);\n"
    cases.append(("RL-1 `global` 表且无 tenant_id → 通过",
                  verdict(1, check_built_tables(
                      _spec(created_global, row_global_ok)), []), (EXIT_OK, "")))
    created_global_bad = ("CREATE TABLE alpha (\n  id TEXT,\n"
                          "  tenant_id TEXT NOT NULL\n);\n")
    cases.append(("★ RL-1 `global` 表却带 tenant_id → 被抓（不得当例外放过）",
                  verdict(1, check_built_tables(
                      _spec(created_global_bad, row_global_ok)), []),
                  (EXIT_FINDINGS, "")))

    # ---- 空头声明：矩阵声称受保护、DDL 里没有 ALTER ----
    # 真实来源（INC-66）：矩阵扩到 24 张后，门禁仍报「通过：13 张全部核对」——
    # 它只核对**已有 ALTER 的表**，矩阵多出来的 11 张**完全没进入视野**。
    # 这类缺口**只测「有语句时不报」是抓不到的**，必须有「矩阵有、DDL 无」的夹具。
    created_claim = "CREATE TABLE alpha (\n  id TEXT,\n  tenant_id TEXT NOT NULL\n);\n"
    no_alter = _spec(created_claim, "| 1 | `alpha` | tenant | `NOT NULL` | ENABLE+FORCE | x |")
    cases.append(("★ RL-1 矩阵声称受保护但无 ALTER 语句 → 被抓（空头声明）",
                  verdict(1, check_built_tables(no_alter), []), (EXIT_FINDINGS, "")))
    with_alter = _spec(
        created_claim + "ALTER TABLE alpha ENABLE ROW LEVEL SECURITY;\n"
        "ALTER TABLE alpha FORCE  ROW LEVEL SECURITY;\n",
        "| 1 | `alpha` | tenant | `NOT NULL` | ENABLE+FORCE | x |")
    cases.append(("RL-1 矩阵声明且有 ENABLE+FORCE → 通过",
                  verdict(1, check_built_tables(with_alter), []), (EXIT_OK, "")))
    force_missing = _spec(
        created_claim + "ALTER TABLE alpha ENABLE ROW LEVEL SECURITY;\n",
        "| 1 | `alpha` | tenant | `NOT NULL` | ENABLE+FORCE | x |")
    cases.append(("★ RL-1 有 ENABLE 但缺 FORCE → 被抓（表所有者会绕过）",
                  verdict(1, check_built_tables(force_missing), []), (EXIT_FINDINGS, "")))
    # `global` 行**豁免**这条反向核对：它按定义不建 RLS
    global_no_alter = _spec(created_global, row_global_ok)
    cases.append(("RL-1 `global` 行豁免反向核对（按定义不建 RLS）→ 通过",
                  verdict(1, check_built_tables(global_no_alter), []), (EXIT_OK, "")))

    # --- RL-2：工具内常量 / 期望值 / 规格实算 三方一致（INC-83）---
    # ------------------------------------------------------------
    # **夹具必须造出「规格变了而常量没变」这个真实场景**，
    # 只测「三者相等时不报」等于没测。
    #
    # ⚠ `cases` 的形状是 `(label, (code, msg), (want_code, want_msg))`——
    #   我第一版把第二项写成裸字符串，解包直接崩。
    def cons_ok(spec_set: set[str]) -> tuple[int, str]:
        probs = matrix_consistency(spec_set)
        return (EXIT_FINDINGS, "；".join(probs)) if probs else (EXIT_OK, "")

    _spec25 = set(MATRIX)                       # 规格与常量同步的基线
    cases.append(("RL-2 常量与规格一致 → 不报",
                  cons_ok(_spec25), (EXIT_OK, "")))
    # ① 规格多了一张表（INC-82 加 provider_credentials 时就是这个形态）
    cases.append(("★ RL-2 规格新增表而常量未同步 → 报红",
                  cons_ok(_spec25 | {"brand_new_table"}), (EXIT_FINDINGS, "")))
    # ② `EXPECTED_TABLES` 过期：结论句会报一个比真实覆盖面小的数字
    _saved_exp = EXPECTED_TABLES
    try:
        globals()["EXPECTED_TABLES"] = _saved_exp - 1
        _stale = cons_ok(_spec25)
    finally:
        globals()["EXPECTED_TABLES"] = _saved_exp
    cases.append(("★ RL-2 `EXPECTED_TABLES` 过期 → 报红（结论句不得少报覆盖面）",
                  _stale, (EXIT_FINDINGS, "")))
    # ③ 阴性对照：还原后必须安静——**否则这条判据恒红，第一次用就会被关掉**
    cases.append(("RL-2 还原后 → 不报（非恒红）",
                  cons_ok(_spec25), (EXIT_OK, "")))

    # --- 阴性对照：真实缺口必须报红 ---
    # INC-15 原状：只有 tenant_memberships 有策略
    inc15 = _state({"tenant_memberships"}, {"tenant_memberships"}, _iso("tenant_memberships"))
    expect_findings("★ INC-15 原状（仅 1/13 有策略）→ 1", inc15)

    # 逐项变异：每次破坏一项都必须被抓到
    def mutated(fn):
        st = _full_good()
        fn(st)
        return st

    expect_findings("抽掉一张表的 ENABLE → 1", mutated(
        lambda s: s["enabled"].discard("storage_objects")))
    expect_findings("抽掉一张表的 FORCE → 1", mutated(
        lambda s: s["forced"].discard("member_external_ids")))
    expect_findings("抽掉一张表的整条策略 → 1", mutated(
        lambda s: s["policies"].pop("membership_roles")))
    expect_findings("策略不引用 app.tenant_id → 1", mutated(
        lambda s: s["policies"].__setitem__("storage_objects", [{
            "name": "bogus", "using": True, "with_check": True,
            "body": "USING (true) WITH CHECK (true)"}])))
    expect_findings("策略缺 WITH CHECK → 1", mutated(
        lambda s: s["policies"].__setitem__("membership_org_grants", [{
            "name": "no_check", "using": True, "with_check": False,
            "body": "USING (tenant_id = nullif(current_setting('app.tenant_id', true), ''))"}])))
    expect_findings("平台默认拒绝表被加了策略 → 1", mutated(
        lambda s: s["policies"].__setitem__("migration_batches", [{
            "name": "leak", "using": True, "with_check": True,
            "body": "USING (true) WITH CHECK (true)"}])))
    expect_findings("tenants 缺平台分支 → 1", mutated(
        lambda s: s["policies"].__setitem__("tenants", [s["policies"]["tenants"][1]])))
    expect_findings("tenants 缺租户分支 → 1", mutated(
        lambda s: s["policies"].__setitem__("tenants", [s["policies"]["tenants"][0]])))

    ok = 0
    for label, got, want in cases:
        if got[0] == want[0]:
            ok += 1
            print(f"  {GREEN}✓{RESET} {label}  → exit {got[0]}")
        else:
            print(f"  {RED}✗{RESET} {label}  → 实际 exit {got[0]}，期望 {want[0]}")
            if got[1]:
                print(f"      {DIM}{got[1][:150]}{RESET}")

    print(f"\n自检 {ok}/{len(cases)}")
    if ok != len(cases):
        return EXIT_TOOL_BROKEN
    print(f"{GREEN}判定逻辑可验证：能报红（9 项变异）、不假绿（覆盖面闸）{RESET}")
    return EXIT_OK


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    state, errs = load_policies(ROOT / SPEC)
    if state.get("policies") or state.get("enabled"):
        # 解析到的「声明过 ENABLE 的表数」即覆盖面
        n = len(state.get("enabled", set()) | set(state.get("policies", {})))
    else:
        n = 0

    print(f"{CYAN}RLS 策略矩阵门禁{RESET}  {DIM}{SPEC} §3.6{RESET}")
    print(f"{DIM}矩阵：{EXPECTED_TABLES} 张"
          f"（租户隔离 {TENANT_TABLES} / 平台默认拒绝 {DENY_TABLES} / "
          f"双主体 1）{RESET}")
    print(f"{DIM}contract 阶段才建 RLS 的现有表（不参与本门禁）："
          f"{len(CONTRACT_PHASE_TABLES)} 张{RESET}\n")

    problems = check(state, MATRIX)
    _spec_text = (ROOT / SPEC).read_text(encoding="utf-8")
    problems += check_built_tables(_spec_text)
    # RL-2：工具内常量 / 期望值 / 规格实算 三方一致（INC-83）
    _sec = re.search(r"####\s*3\.6\.1.*?(?=\n#+\s|\Z)", _spec_text, re.S)
    if _sec:
        _spec_matrix = set(re.findall(
            r"^\|\s*\d+\s*\|\s*`([a-z_]+)`\s*\|\s*"
            r"(?:platform|tenant|\*\*全局\*\*)\s*\|", _sec.group(0), re.M))
        problems += matrix_consistency(_spec_matrix)
    else:
        problems.append("RL-2 找不到规格 §3.6.1 矩阵，无法核对工具内常量是否过期")
    code, msg = verdict(n, problems, errs)

    have_enable = state.get("enabled", set())
    have_force = state.get("forced", set())
    have_policy = set(state.get("policies", {}))
    for t, kind in sorted(MATRIX.items()):
        marks = [
            ("E" if t in have_enable else "-"),
            ("F" if t in have_force else "-"),
            ("P" if t in have_policy else "-"),
        ]
        flag = f"{GREEN}✓{RESET}" if "".join(marks) != "---" else f"{YELLOW}·{RESET}"
        if kind == "deny" and t in have_enable and t in have_force and t not in have_policy:
            flag = f"{GREEN}✓{RESET}"
        print(f"  {flag} {t:<30} {DIM}[{'/'.join(marks)}] E=ENABLE F=FORCE P=POLICY{RESET}")

    print()
    if problems:
        print(f"{RED}发现 {len(problems)} 项问题：{RESET}")
        for p in problems:
            print(f"  {RED}·{RESET} {p}")
        print()
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
