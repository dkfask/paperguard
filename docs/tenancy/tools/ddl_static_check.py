#!/usr/bin/env python3
"""DDL 静态检查：语法 + 外键目标可解析性。

背景
----
W-02 规格中的租户 DDL 草案在产出时**从未被任何 PostgreSQL 执行过**。
本脚本提供无服务端的两级检查：

第 1 层（**不依赖 pglast，每台机器都能跑**）
    ① ADD COLUMN / 索引引用的表与列是否存在、是否重复加列
    ② **SC-5** 每个 `status`/`state` 文本列必须有 `CHECK ... IN` 取值约束
    ③ **SC-6** 外键的目标表与**被引用列**是否可解析（行内与表级两种写法）
    ④ 不重复加列

第 2 层（需要 `pglast`）
    1. **语法** —— 用 `pglast`（绑定 libpg_query，即 PostgreSQL 官方解析器）逐条解析。
    2. **外键目标（AST 版）** —— 检查被引用列是否被目标表的**主键或唯一约束**覆盖。

> **为什么 ③ 要在第 1 层重复做一遍**：外键目标「在不在」是纯文本问题，
> 不该跟着 `pglast` 一起被阻塞——原先它挂在第 2 层，结果是
> **DDL 里最有价值的那条检查在本机从未执行过**，而报告只显示「工具不可运行」。
> 第 1 层的 ③ 比第 2 层的 2 **弱**（看不到带引号标识符、schema 限定名、
> `ALTER TABLE ... ADD CONSTRAINT` 形式），**两层互补，不互相替代**。

第 2 级仍不可替代：PostgreSQL 官方文档 5.5.5 规定
"A foreign key must reference columns that either are a primary key or form a
unique constraint" —— **语法解析器不会发现这类错误**，只有建表时才会报
`there is no unique constraint matching given keys for referenced table`。
文本层能确认「列存在」，**不能确认「这组列被唯一约束覆盖」**。

> **本工具第 1 层通过 ≠ DDL 正确**。它查不出 CHECK 取值集合与散文规格是否一致
> （INC-59 `tenants_status_chk`、INC-60 四个无约束状态列都是**人工审计**发现的，
> 不是任何一层自动抓到的）。报告里会显式列出这一条。

用法
----
推荐（用项目自身的开发依赖，不要另建临时 venv）：

    backend/.venv/bin/pip install -r backend/requirements-dev.txt
    backend/.venv/bin/python docs/tenancy/tools/ddl_static_check.py

退出码
------
    0 = 全部通过
    1 = 存在语法错误或外键目标缺失（**检查确实跑了，且未通过**）
    2 = 工具自身无法运行（缺少依赖等）

为什么要区分 1 和 2
------------------
早期版本在缺 pglast 时用 `sys.exit("需要 pglast")`——`sys.exit` 传字符串时
退出码是 **1**，与「检查未通过」完全相同。于是 CI 里看到红灯无法判断
是「DDL 有缺陷」还是「工具根本没跑起来」，后者会被误判成前者，
进而把一个环境问题当成设计缺陷去排查。
**一个无法区分「检查失败」和「工具坏了」的门禁，不是门禁。**
"""

from __future__ import annotations

import pathlib
import re
import sys

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

# 缺 pglast 时**不立即退出**：改为记录原因，由 main() 判定。
# 这样 --self-test 可以在没有 pglast 的机器上验证判定逻辑——
# 否则「判定逻辑」和「依赖是否装好」会绑死，修复无法被验证。
PGLAST_MISSING = None
try:
    import pglast
    from pglast import ast, parse_sql
    from pglast.parser import ParseError
except ImportError:  # pragma: no cover
    PGLAST_MISSING = (
        "ddl_static_check: 缺少依赖 pglast，无法执行 DDL 检查。\n"
        "  安装：backend/.venv/bin/pip install -r backend/requirements-dev.txt\n"
        "  注意：本退出码为 2（工具不可运行），与 1（检查未通过）含义不同。\n"
    )

ROOT = pathlib.Path(__file__).resolve().parents[3]  # tools→tenancy→docs→仓库根
DOCS = [
    "docs/product/W-02-存储与Schema实施规格_20261001.md",
    "docs/tenancy/system-settings-scope-inventory.md",
    "docs/tenancy/account-migration-inventory.md",
]
# libpg_query ConstrType 枚举值
CONSTR_PRIMARY, CONSTR_UNIQUE, CONSTR_FOREIGN = 6, 7, 9
# 引用本批 DDL 之外、但目标列已知被主键覆盖的遗留表
EXTERNAL_TARGETS = {"users": {"id"}}  # 依据 backend/app/db.py:111 id TEXT PRIMARY KEY


def _keys(node) -> set[str]:
    return {k.sval for k in (node or [])}


#: 同一张表在规格里被声明了不止一次的表名。第 2 层只按**去重后的表**核对，
#: 第二次声明的约束不会进入核对面 —— 记下来并在结论处报出，避免两个数字无法互相解释。
DUPLICATE_TABLES: set[str] = set()


def collect_sql(sql_text: str, label: str, tables: dict, fks: list,
                 syntax_errors: list) -> None:
    """把一段 DDL 文本的解析结果并入 `tables` / `fks` / `syntax_errors`。

    ★ 从 `collect()` 里拆出来的唯一目的：**让第 2 层的收集逻辑可被自检直接测**。
    原来它只从磁盘读 `DOCS`，自检无法注入样本，于是「列级内联 REFERENCES
    被静默丢弃」这个缺陷（INC-115）在四十多轮里一直无人能证伪。
    """
    for block in re.findall(r"```sql\n(.*?)```", sql_text, re.S):
        for stmt in (s.strip() for s in block.split(";")):
            if not stmt or all(c in " \t\n-" for c in stmt):
                continue
            try:
                parsed = parse_sql(stmt)
            except ParseError as exc:
                syntax_errors.append(
                    f"{label}: {' '.join(stmt.split())[:70]} → {str(exc).splitlines()[0]}"
                )
                continue
            for raw in parsed:
                node = getattr(raw, "stmt", raw)
                if not isinstance(node, ast.CreateStmt):
                    continue
                name = node.relation.relname
                if name in tables:
                    DUPLICATE_TABLES.add(name)  # ALTER 场景不重复登记
                    continue
                pk: set[str] = set()
                unique: list[set[str]] = []
                for elt in node.tableElts or []:
                    if isinstance(elt, ast.ColumnDef):
                        for con in elt.constraints or []:
                            if con.contype == CONSTR_PRIMARY:
                                pk.add(elt.colname)
                            elif con.contype == CONSTR_UNIQUE:
                                unique.append({elt.colname})
                            elif con.contype == CONSTR_FOREIGN:
                                # ★ INC-115：列级内联 `col text REFERENCES t(id)`
                                # 在 PostgreSQL 里是**真正的外键约束**，与表级
                                # `FOREIGN KEY (col) REFERENCES t(id)` 等价。
                                # 原实现只在这一分支收表级外键，**19/30 条声明
                                # （含全部 `tenant_id REFERENCES tenants(id)`）
                                # 被静默丢弃** —— 第 2 层却照样报「外键目标均可解析」。
                                fks.append(
                                    (
                                        name,
                                        con.pktable.relname,
                                        [k.sval for k in (con.pk_attrs or [])],
                                        [elt.colname],
                                    )
                                )
                    elif isinstance(elt, ast.Constraint):
                        if elt.contype == CONSTR_PRIMARY:
                            pk = _keys(elt.keys)
                        elif elt.contype == CONSTR_UNIQUE:
                            unique.append(_keys(elt.keys))
                        elif elt.contype == CONSTR_FOREIGN:
                            fks.append(
                                (
                                    name,
                                    elt.pktable.relname,
                                    [k.sval for k in (elt.pk_attrs or [])],
                                    [k.sval for k in (elt.fk_attrs or [])],
                                )
                            )
                tables[name] = {"pk": pk, "unique": unique}


def collect():
    """返回 (tables, fks, syntax_errors)。tables: name -> {'pk','unique'}。"""
    tables: dict[str, dict] = {}
    fks: list[tuple[str, str, list[str], list[str]]] = []
    syntax_errors: list[str] = []
    for rel_doc in DOCS:
        doc = ROOT / rel_doc
        if not doc.exists():
            syntax_errors.append(f"{rel_doc}: 文件不存在")
            continue
        collect_sql(doc.read_text(encoding="utf-8"), rel_doc, tables, fks,
                    syntax_errors)
    return tables, fks, syntax_errors


def verdict(n_tables: int, syntax_errors: list, fk_errors: list) -> tuple[int, str]:
    """纯判定函数：不做任何 I/O，因此可在没有 pglast 的机器上被自检覆盖。"""
    if n_tables == 0:
        return EXIT_FINDINGS, "未解析到任何 DDL 声明，本项无数据可核对"
    if syntax_errors or fk_errors:
        return EXIT_FINDINGS, "未通过"
    return EXIT_OK, "通过：语法合法且外键目标均可解析"


# ---------------------------------------------------------------------------
# 结构预检：**不依赖 pglast**，因此本地与 CI 都能跑
# ---------------------------------------------------------------------------

def dbpy_tables(db_py: pathlib.Path) -> dict[str, set[str]]:
    """从 backend/app/db.py 抽出 {表名: {列名}}。这是既有 schema 的权威来源。"""
    if not db_py.exists():
        return {}
    text = db_py.read_text(encoding="utf-8")
    out: dict[str, set[str]] = {}
    for m in re.finditer(
            r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\s*\)", text, re.S):
        out[m.group(1)] = {mm.group(1) for mm in
                           re.finditer(r"^\s*(\w+)\s+\w+", m.group(2), re.M)}
    return out


def sc10_object_ddl_coverage(design_text: str, ddl_text: str,
                             known_tables: set[str]) -> list[str]:
    """SC-10：§9.2/§9.3/§9.4 声明的对象数，必须与「已建 / 缺建」的陈述一致。

    为什么需要（2026-10-02 实测，INC-80）
    --------------------------------------
    那一节写着「列了 **23 个**对象，**12 个**已有 DDL，**C1/C2 11 张**仍无 DDL」。
    逐表实测：**29 个对象、20 个有 DDL、9 张 C1/C2 缺**——**三个数字全错**，
    而且 `provider_credentials` 的出处「W-02 §3.3.1」也标错（§3.3.1 里没有它）。

    危害不是「数字不好看」
    ------------------------
    这一节的**全部职责**是回答「哪些存储规格还缺位」。
    §14.2 八态靠状态机兜「结果未知」，而 `state` 的取值约束来自 DDL——
    **说少了会让人以为账务表快好了，说多了会让人以为要返工**。
    **一个自身数字就错的承载现状表，比没有这张表更糟。**

    判据形态
    --------
    · 解析 §9.2/§9.3/§9.4 三节表格首列的对象名（**逐节切分**，不跨节累计——
      首版正则一路吃到 §10，把 `active/closed` 这类**状态取值**也当成了对象名）
    · 声明口径 = 该节说明里出现的「共列 N 个对象」/「M 个已有 `CREATE TABLE`」
    · 现实口径 = 在**全部 DDL 来源**（13 份规格 + `docs/tenancy/` 两份盘点
      + `backend/app/db.py`）里能搜到 `CREATE TABLE <名>` 的对象数
    · 两侧不等即报红；**C1/C2 延后本身不判红**（PV-7 明写，那是有意的）
    """
    problems: list[str] = []
    # ⚠ `known_tables` 传进来的是 `dbpy_tables()` 的返回，即
    #   `dict[str, set[str]]`（表名 → 列名集合）。我第一版直接 `| known_tables`，
    #   于是 `set | dict` 抛 **TypeError**——
    #   **而 TypeError 让 `ddl_static_check` 的退出码从 2 变成 1**：
    #   「工具坏了，CI 必须拦」降级成「有差距，CI 不拦」，
    #   **CI 从此看不见工具崩溃**。
    #   类型错了却不是 TypeError 而是静默算错，是更坏的情况。
    #   **判据的入参类型必须显式归一**，不能靠调用方「传对」。
    created: set[str] = set(re.findall(
        r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)", ddl_text, re.I)) | set(
            known_tables if isinstance(known_tables, dict) else known_tables)

    # 逐节切分：**必须按标题边界**，否则 §10 的状态取值会被算成对象
    heads = list(re.finditer(r"^#{2,4}\s*(9\.[234])\s", design_text, re.M))
    if len(heads) != 3:
        return [f"SC-10 §9.2/§9.3/§9.4 解析到 {len(heads)} 节（期望 3），"
                f"无法核对对象数——判据静默失效比误报更危险"]
    objects: list[str] = []
    for i, h in enumerate(heads):
        if i + 1 < len(heads):
            end = heads[i + 1].start()
        else:
            nxt = re.search(r"^##\s", design_text[h.end():], re.M)
            end = h.end() + nxt.start() if nxt else len(design_text)
        objects += re.findall(r"^\|\s*`(\w+)`\s*\|", design_text[h.start():end], re.M)
    if not objects:
        return ["SC-10 §9.2/§9.3/§9.4 解析到 0 个对象，不得为 0"]

    m_total = re.search(r"共列\s*\*\*([0-9]+)\s*个对象", design_text)
    m_have = re.search(r"\*\*([0-9]+)\s*个已有\s*`CREATE TABLE`\*\*", design_text)
    if not m_total or not m_have:
        problems.append(
            "SC-10 §9.3 承载现状没有「共列 N 个对象」与「M 个已有 DDL」两个可核对数字"
            "——该节的核心职责是回答「哪些存储规格还缺位」，"
            "没有数字就没有任何东西能发现它过期")
        return problems
    decl_total = int(m_total.group(1))
    decl_have = int(m_have.group(1))
    real_total = len(objects)
    real_have = len([o for o in objects if o in created])
    if decl_total != real_total:
        problems.append(
            f"SC-10 §9.2/§9.3/§9.4 声明 {decl_total} 个对象，"
            f"实测 {real_total} 个（§9.2 10 + §9.3 8 + §9.4 11）")
    if decl_have != real_have:
        problems.append(
            f"SC-10 声明 {decl_have} 个对象已有 `CREATE TABLE`，"
            f"实测 {real_have} 个（仍缺："
            f"{', '.join(o for o in objects if o not in created) or '无'}）")

    # ---- 双向核对：§9.3 的「阶段表」与 §9.3/§9.4 的对象清单必须一致 ----
    # ------------------------------------------------------------------
    # **2026-10-02 普查发现（INC-84）**：同一节里还有**第二张表**
    # （`| 阶段 | 对象 | 承载规格 | 依赖的未决项 |`），
    # 它逐个列出每个对象归 C0/C1/C2 与由哪份规格承载——
    # **也就是说它才是「哪些缺 DDL」这个问题最直接的答案**。
    # 实测 `provider_credentials` **在这张表里完全没有登记**，
    # 而它在 §9.3 对象清单里、在 W-02 §3.3.0b 有 DDL、有 RLS 策略。
    # 读者查这张表只会得到「不在表里」，而**不在表里**历史上意味着「缺 DDL」。
    #
    # **为什么上一版的 SC-10 抓不到**：它只核对「声明的两个数字 vs 实算」，
    # **不核对两张表之间的一致性**——那是**另一个维度**。
    # 这与「集合比对发现不了两侧同时缺失」同源：
    # **每个判据只回答自己能回答的问题。**
    sec93 = re.search(r"###\s*9\.3.*?(?=\n###\s*9\.4)", design_text, re.S)
    if sec93:
        t93 = sec93.group(0)
        i = t93.find("| 阶段 |")
        if i != -1:
            table_rows = t93[i:].split("\n\n")[0]
            stage_objs: set[str] = set()
            for line in table_rows.splitlines():
                if line.startswith("|"):
                    stage_objs |= set(re.findall(r"`(\w+)`", line))
            sec93_objs = set(re.findall(r"^\|\s*`(\w+)`\s*\|", t93, re.M))
            missing = sorted(sec93_objs - stage_objs)
            if missing:
                problems.append(
                    f"SC-10 §9.3 阶段表漏登记 {len(missing)} 个对象："
                    f"{', '.join(missing)}——**这张表是「哪些缺 DDL」最直接的答案**，"
                    f"对象不在表里，历史上就等于「读者以为它缺 DDL」")
    return problems


def structure_check(sql: str, known: dict[str, set[str]]) -> list[str]:
    """纯结构核对：ALTER/索引引用的表与列是否真实存在、是否重复加列。

    为什么需要它
    ------------
    `pglast` 只在装了依赖的机器上才有。手写 27 条 `ALTER TABLE` 时最容易犯的错
    是**列名打错**——那会是一条语法合法、执行时才炸的语句。
    这一层不需要任何依赖，因此**在每台机器、每次运行都能给信号**。
    """
    problems: list[str] = []
    # 本次 DDL 里 CREATE TABLE 出来的**新表**同样合法——§3.2 的
    # `CREATE INDEX ... ON tenant_organizations` 指向的是新表，不是既有表。
    # 不把它们算进「已知表」就会把正确 DDL 误报成「表不存在」。
    known = {**known}
    for m in re.finditer(
            r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)\s*\((.*?)\n\s*\)",
            sql, re.S | re.I):
        known[m.group(1)] = {mm.group(1) for mm in
                             re.finditer(r"^\s*(\w+)\s+\w+", m.group(2), re.M)}
    added: dict[str, set[str]] = {}
    for m in re.finditer(r"ALTER TABLE\s+(\w+)\s+ADD COLUMN\s+(\w+)\s+\w+", sql, re.I):
        added.setdefault(m.group(1), set()).add(m.group(2))
    for table, cols in added.items():
        if table not in known:
            problems.append(f"ALTER 目标表 {table} 不在既有 schema 中")
            continue
        for c in cols:
            if c in known[table]:
                problems.append(f"重复加列 {table}.{c}——该列在既有 schema 中已存在")

    # SC-5：状态列必须有取值约束（2026-10-01 因 INC-60 新增）
    # ---------------------------------------------------------
    # 实测踩到：`tenant_organizations.status`、`teacher_student_bindings.state`、
    # `platform_role_bindings.status` 三列**连 CHECK 都没有**。
    # 最刺眼的是第二条——W-05 **TM-3 明确要求写入 `legacy`**，
    # 而该要求在 DDL 层**完全不受约束**，一个拼写错误就能静默违反它。
    # 第三条是**平台级授权表**，风险最高。
    #
    # 为什么放第 1 层而不是第 2 层
    # -----------------------------
    # 语法完全合法，`pglast` **查不出来**——它只保证「这段 SQL 能被解析」，
    # 不管取值集合对不对、够不够。而这一层**不需要任何依赖**，
    # 因此在每台机器、每次运行都能给信号。
    #
    # **为什么不做成通用规则**：不是所有状态列都该有 CHECK。
    # 本判据只作用于**本设计新建的表**（`CREATE TABLE` 块），
    # 既有 `jobs` / `workflow_runs` 等表的状态语义不由本设计决定，
    # 强加约束会误报。
    for m in re.finditer(
            r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)\s*\((.*?)\n\s*\);", sql,
            re.S | re.I):
        tname, tbody = m.group(1), _strip_sql_comments(m.group(2))
        for cm in re.finditer(r"^\s{2,}(status|state)\s+text\b[^\n]*$", tbody, re.M):
            col = cm.group(1)
            if not re.search(r"\b" + col + r"\s+IN\s*\(", tbody):
                problems.append(
                    f"SC-5 状态列 {tname}.{col} 没有任何取值约束——"
                    f"拼错的状态值会被静默接受（本设计新建的表必须有 CHECK ... IN）")

    # SC-6：外键目标表与被引用列可解析（2026-10-01 补）
    # ---------------------------------------------------------
    # 为什么必须搬到第 1 层
    # ------------------
    # 「外键目标可解析」原先挂在第 2 层，跟着 `pglast` 一起被阻塞——
    # 而它是**纯文本分析**：被引用的表在不在、被引用的列在不在，
    # 用 `known`（db.py 的表 + 本次 DDL 建的表）就能回答。
    # 结果是：DDL 里**最有价值的那条检查在本机从未执行过**，
    # 而报告只显示「工具不可运行」，**看不出它本来能查什么**。
    #
    # **这一层比 pglast 的 AST 检查弱，弱在哪必须写清**
    # --------------------------------------------
    # 文本层看不到：带引号的标识符、`public.` 等 schema 限定名、
    # 外键列里的表达式，以及 `CREATE TABLE` 块之外的 `ALTER TABLE ... ADD CONSTRAINT`。
    # **pglast 那层仍然保留；两层互补，不互相替代。**
    for m in re.finditer(
            r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)\s*\((.*?)\n\s*\);", sql,
            re.S | re.I):
        src = m.group(1)
        body = _strip_sql_comments(m.group(2))
        for cm in re.finditer(
                r"^\s{2,}(\w+)\s+text[^,\n]*?REFERENCES\s+(\w+)\s*\(([^)]*)\)",
                body, re.M | re.I):
            tgt = cm.group(2)
            tcols = [c.strip() for c in cm.group(3).replace("\n", " ").split(",")]
            if tgt not in known:
                problems.append(
                    f"SC-6 行内外键 {src}.{cm.group(1)} → {tgt}：目标表不在既有 schema 中")
                continue
            # **列名也必须查**。首版只验了行内外键的表名，
            # 变异测试（`REFERENCES projects (idd)`）立刻暴露那是个半截检查——
            # **只查一半的检查会给出虚假的安全感**，比不查更难发现。
            imissing = [c for c in tcols if c not in known[tgt]]
            if imissing:
                problems.append(
                    f"SC-6 行内外键 {src}.{cm.group(1)} → {tgt}："
                    f"被引用列 {imissing} 不存在")
        for fm in re.finditer(
                r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)",
                body, re.S | re.I):
            tgt = fm.group(2)
            tcols = [c.strip() for c in fm.group(3).replace("\n", " ").split(",")]
            if tgt not in known:
                problems.append(
                    f"SC-6 外键 {src} → {tgt}：目标表不在既有 schema 中")
                continue
            missing = [c for c in tcols if c not in known[tgt]]
            if missing:
                problems.append(
                    f"SC-6 外键 {src} → {tgt}：被引用列 {missing} 不存在"
                    f"（{tgt} 实际有 {sorted(known[tgt])[:6]}）")

    for m in re.finditer(
            r"CREATE (?:UNIQUE )?INDEX\s+\w+\s+ON\s+(\w+)\s*\(([^)]*)\)", sql, re.I):
        table, raw = m.group(1), m.group(2)
        have = known.get(table, set()) | added.get(table, set())
        if table not in known:
            problems.append(f"索引目标表 {table} 不在既有 schema 中")
            continue
        for c in (x.strip() for x in raw.replace("\n", " ").split(",")):
            if c and c not in have:
                problems.append(
                    f"索引 {table} 引用了不存在的列 {c}"
                    f"（既有列 + 本次新增列中均无）")
    return problems


def sc7_count_consistency(prose: str, sql: str) -> list[str]:
    """SC-7：§3.6.4 声明的「现有表张数」必须等于 §4.1 实际 ALTER 的表数。

    为什么需要（2026-10-02 实测踩到，INC-73）
    ------------------------------------------
    §3.6.4 三处写「11 张现有表」，而 §4.1 的 `ALTER TABLE ... ADD COLUMN`
    实际涉及 **18 张表 / 27 列**。差 7 张，且**没有任何门禁发现**——
    DC-7 只核对总设计 §27.5 的事实标记，管不到 W-02 内部的数字。

    后果不是「数字不好看」：§3.6.4 的**全部职责**就是
    「别让人以为加完列就安全了，contract 前这些表一直没有数据库级隔离」。
    **一个自身数字就错的警告，比没有警告更糟。**

    口径与判据
    ----------
    · 现实口径 = 本 SQL 中 `ALTER TABLE X ADD COLUMN ...` 里的**不同 X 的个数**。
      **必须按表去重**：`workflow_records` 有两条 ADD COLUMN，
      按语句数会把 27 条语句当成 27 张表。
    · 声明口径 = §3.6.4 那一节里出现的 `N 张`。
      **只取本节**，否则 §4.1 行 8～24 表格里的其他「张数」会被误取。
    """
    problems: list[str] = []
    sec = re.search(r"####\s*3\.6\.4.*?(?=\n#{1,4}\s|\Z)", prose, re.S)
    if not sec:
        problems.append("SC-7 找不到 §3.6.4 章节")
        return problems
    claimed = re.findall(r"(\d+)\s*张", sec.group(0))
    if not claimed:
        return problems  # 已改为不复述数字 —— 这是正确状态，不报
    real = len({m.group(1) for m in re.finditer(
        r"ALTER TABLE\s+(\w+)\s+ADD COLUMN", sql, re.I)})
    for c in claimed:
        if int(c) != real:
            problems.append(
                f"SC-7 §3.6.4 声明「{c} 张现有表」，而 §4.1 实际 ALTER 了 {real} 张"
                f"——该节的职责正是提醒「加完列不等于有隔离」，数字错了警告就失效")
    return problems


# 出处注释里抽规则 ID。**必须带第三段**，否则 `AC-11-1` 被抽成 `AC-11`——
# 那是一条**根本不存在的 ID**，而 `later` 里不会有它，于是 SC-9 **零报告**。
#
# 这是「什么算规则 ID」的**第四份窄副本**，且位置更靠里：
# 上一份（`DEF_ROW`）管的是「规格里定义了什么」，
# 这一份管的是「注释里引用了什么」——**两侧窄的维度不同，但病相同**。
# 抽取与还原都改为引用真判据，副本归零。
PROV_COMMENT = re.compile(r"\b([A-Z]{2,4}-\d{1,2}(?:-\d{1,2})?)\b")
# ⚠ 这里**曾**有一份 `DEF_ROW = r"^\|\s*([A-Z]{2,4}-\d+)\s*\|"`，
#   是「什么算规则定义」的第三份副本，已被 `rule_defs_of()` 取代并**删除**。
#   留着它 = 留一个「看起来还在管事、实际无人引用」的僵尸正则，
#   下一个人很可能直接用它，于是漂移重新长出来。**删掉比标注更可靠。**
C1C2_SPECS = ("W-11", "W-12")


def is_family_anchor(rid: str, defined: set[str]) -> bool:
    """`AC-11` / `AC-11-x` 是否只是**族锚点或占位符**，而非具体规则引用。

    判法：把已定义集合里所有**三段形态**的族取出来，
    `rid` 等于某个族、或等于「族 + 占位符」时为真。
    **只有两段、且族内确有定义时才算**——
    否则 `AC-11` 这种**根本不存在**的引用会被当成族锚点放过，
    那就是把「没定义」和「定义了但我宽泛一点」混成一件事。
    """
    if not re.fullmatch(r"[A-Z]{2,4}-\d{1,2}", rid):
        return False
    return any(d.startswith(rid + "-") for d in defined)


def rule_defs_of(spec_text: str):
    """一份规格**定义**的业务规则 ID——**引用** `spec_rule_id_check`，不自己写。

    为什么必须消除这份副本（2026-10-02 INC-78 收口时实测）
    ---------------------------------------------------
    SC-9 原先内联了 `DEF_ROW = r"^\\|\\s*([A-Z]{2,4}-\\d+)\\s*\\|"`，
    这是**同一判据的第三份副本**（前两份在 `design_doc_check` 的 DC-6/DC-7，
    已被消除）。实测它在 W-11/W-12 上**少收 8 条**（`AC-11-1…5`、`AC-12-1…3`）。

    **当前无实际影响**（实测 C0 的 CHECK 注释不引用 `AC-`），
    **但「当前无影响」不是「没有缺口」**——这正是 INC-57 家族：
    副本的窄形态假设不会报警，等哪天有人给 C0 的 CHECK 挂一条
    `AC-11-x` 的出处，SC-9 就会**静默放过**。

    更要紧的是**形态假设已经漂移过一次**：`spec_rule_id_check` 从一种形态
    扩到三种时，这个副本毫无察觉。**副本本身就是病。**
    修法与 `design_doc_check.count_rule_ids` 同一套：`importlib` 引用真判据。
    """
    import importlib.util as _ilu
    sp = _ilu.spec_from_file_location(
        "_sric_for_sc9", pathlib.Path(__file__).resolve().parent
        / "spec_rule_id_check.py")
    mod = _ilu.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod.rule_defs(spec_text)


def sc9_no_cross_phase_citation(prose: str) -> list[str]:
    """SC-9：C0 的表不得由 C1/C2 包的规则来约束。

    为什么需要（2026-10-02 实测踩到，INC-77）
    --------------------------------------
    `budget_policies`（C0 表）的 CHECK 注释引用了 **CM-2**，
    而 **CM-2 是 W-11 的规则**——W-11 是 **C1 账务包**。
    更糟的是那条规则**根本没在 W-06 `QT-4` 里**，是**我倒推出来的归属**。

    为什么「跨阶段」是个可判据的原则
    ------------------------------
    **C0 阶段不建账务表**（W-06 `PV-7` 明写），所以 C1 规则的前提
    （账务模式已定、`quantity ledger` 已存在）**在 C0 根本不存在**。
    引用一条前提不成立的规则去约束 C0 的表，等于**拿未来的结论约束今天的数据**——
    而 D-02（合同/计费主体/币种/会计规则）尚未裁决，
    那些规则的**内容本身还可能变**。

    **与 SC-8 的分工**（这一族很难，判据要各管各的）
    ----------------------------------------------
    · **SC-8** 查「有没有声明出处」——抓的是**一声不吭**。
    · **SC-9** 查「出处是不是跨了阶段」——抓的是**拿不存在的前提当依据**。
    · 两者都**查不了**「出处规则的内容与约束语义是否相符」。
      `CM-2` 真实存在、W-06 的 `QT-4` 也真实存在，
      **把它们混淆的那一步没有任何正则能判断**——只能靠人核对。
      **如实写出这一条，比假装判据覆盖了它更重要。**
    """
    problems: list[str] = []
    sec = re.search(r"###\s*3\.3\.1.*?(?=\n###\s*3\.4|\Z)", prose, re.S)
    if not sec:
        problems.append("SC-9 找不到 §3.3.1 章节")
        return problems
    # 动态收集 C1/C2 包**定义**的规则 ID —— 不硬编码前缀，规格改名也不失效。
    #
    # **只认定义不认引用**（与 `spec_rule_id_check` 同一条规矩）：
    # 首版用 `PROV_COMMENT.findall(全文)`，把 W-12 里对 `PB-1`（W-06 的规则）
    # 的**跨包引用**也当成了定义，于是报出「`PB-1` 属 W-11/W-12」——
    # **假报的代价和漏报一样大**。定义形态是表格首列 `| XX-N |`。
    later: set[str] = set()
    for name in C1C2_SPECS:
        f = pathlib.Path(ROOT / "docs/product") / name
        cand = sorted(f.parent.glob(f"{name}-*.md"))
        for path in cand:
            later.update(rule_defs_of(path.read_text(encoding="utf-8")))
    body = sec.group(0)
    for m in re.finditer(r"^\s*--([^\n]*)", body, re.M):
        # **披露 ≠ 引用**（2026-10-02 实测踩到）：⚠ 注释里写
        # 「原注释错在 CM-2」是**诚实登记**，不是拿它当依据。
        # 若不区分，判据会把作者对自己错误的**说明**当成**引用**——
        # **一个只会报红的检查器不是检查器。**
        # 判法：向上回溯**连续注释行**，整块被 ⚠ 标记则跳过。
        block = [m.group(1)]
        pos = m.start()
        for ln in reversed(body[:pos].splitlines()):
            if "--" not in ln or not ln.strip().startswith("--"):
                break
            block.append(ln.split("--", 1)[1])
        if any(("\u26a0" in x) or ("无规则出处" in x) for x in block):
            continue
        for rid in PROV_COMMENT.findall(m.group(1)):
            # 注释里可能出现**族锚点**（`AC-11` 指 `AC-11-1…5`）与
            # **子编号占位符**（`AC-11-x` 表示「族里任意一条」）。
            # 两者都不是「引用了某一条具体规则」——SC-9 抓的是**具体引用**。
            #
            # 单靠 `rid in later` 不够：它对族锚点是**碰运气**的
            # （族名通常不在定义集里，于是碰巧不报；哪天族内有一条同名
            # 就会变成误报）。**判据的精度不够时，报红本身在指错方向。**
            if is_family_anchor(rid, later):
                continue
            if rid in later:
                problems.append(
                    f"SC-9 C0 表的约束引用了 C1/C2 包的规则 {rid}"
                    f"（属 {'/'.join(C1C2_SPECS)}）——C0 阶段不建账务表，"
                    f"那条规则的前提此刻不存在，且内容还可能随 D-02 变更")
    return sorted(set(problems))



def sc8_constraint_provenance(prose: str) -> list[str]:
    """SC-8：每条 CHECK 约束上方必须有规则出处注释（`-- XX-N`）或显式 ⚠。

    为什么需要（2026-10-02 实测踩到，INC-77）
    --------------------------------------
    `budget_policies` 的 `budget_metric_currency_chk` 注释写着「CM-2」，
    而 **CM-2 是 W-11 的 C1 账务规则**（「非货币额度用 quantity ledger…」），
    注释引用的「不混用」其实是 **QT-4**（W-06）的原文——
    **两条规则被合并后归属给了其中一条**。同表的 `metric` 列则**根本没有取值 CHECK**，
    `QT-4` 的「三者之一」完全没落地。

    SC-5 只查 `status`/`state` 列，**`metric` 是枚举列不是状态列**，
    所以它落在判据外——这是 SC-5「刻意不做成通用规则」的已知代价，不是意外。

    **本判据的能力边界必须写清**（否则就是 INC-56 那一族）
    ------------------------------------------------------
    · 能查：**这条约束有没有声明出处**。
    · **查不了**：「声明的出处对不对」。`CM-2` 真实存在，只是讲的不是这件事
      ——**语义相符性无法机械校验**，只能靠 SC-5 报告里已写明的
      「本层查不出 CHECK 取值集合与散文规格是否一致」。
    · 允许 `⚠` 显式标注「无规则出处」：**如实标注比沉默好**，
      本判据要抓的是「一声不吭」，不是「诚实登记的待定项」。
    """
    problems: list[str] = []
    sec = re.search(r"###\s*3\.3\.1.*?(?=\n###\s*3\.4|\Z)", prose, re.S)
    if not sec:
        problems.append("SC-8 找不到 §3.3.1 章节")
        return problems
    body = sec.group(0)
    for m in re.finditer(r"(CONSTRAINT\s+\w+)\s*\n?\s*CHECK\s*\(", body):
        lines = [ln for ln in body[:m.start()].splitlines() if ln.strip()][-6:]
        cited = set()
        marked = False
        for ln in lines:
            if "--" not in ln:
                continue
            comment = ln.split("--", 1)[1]
            cited.update(PROV_COMMENT.findall(comment))
            if "\u26a0" in comment or "待" in comment:
                marked = True
        if not cited and not marked:
            problems.append(
                f"SC-8 {m.group(1)} 上方没有任何规则出处注释（`-- XX-N`），"
                f"也没有显式标注「无规则出处」——约束的来源无法被追溯")
    return problems


def _strip_sql_comments(body: str) -> str:
    """去掉 `--` 行注释后再判断约束是否存在。

    为什么必须做（2026-10-01 真实踩到，INC-60 修复当场制造的盲区）
    ---------------------------------------------------------
    修 `tenants_status_chk` 时我在 DDL 里留了一行**注释形式的旧约束**：
        --   CHECK (status IN ('provisioning','active','suspended','closed'))
    而 SC-5 的原判据是「表体里能不能搜到 `state IN (`」——
    **注释里那一行照样搜得到**，于是判据会认为该列有约束。
    同理 `ddl_static_check` 的取值提取若不剥注释，会读出**被注释掉的旧值集**。
    **判据必须只看会被执行的那部分**，否则「写下旧约束」本身就能消掉一道门禁。
    """
    return "\n".join(re.sub(r"--.*$", "", ln) for ln in body.splitlines())


def _sc5(sql: str) -> list[str]:
    """只跑 SC-5 一条判据，供自检夹具使用。

    为什么要单独抽出来：SC-5 的范围是「本设计新建的表」，
    若把整段 `structure_check` 拿去跑夹具，既有的「表必须存在」
    「索引列必须存在」等判据会一起触发，**夹具就测不出 SC-5 本身**。
    判据混在一起时，测试失败会指向错误的原因。
    """
    out: list[str] = []
    for m in re.finditer(
            r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)\s*\((.*?)\n\s*\);", sql,
            re.S | re.I):
        tname, tbody = m.group(1), _strip_sql_comments(m.group(2))
        for cm in re.finditer(r"^\s{2,}(status|state)\s+text\b[^\n]*$", tbody, re.M):
            col = cm.group(1)
            if not re.search(r"\b" + col + r"\s+IN\s*\(", tbody):
                out.append(f"SC-5 状态列 {tname}.{col} 没有任何取值约束")
    return out


def self_test() -> int:
    """证明覆盖面闸存在：0 张表 ≠ 通过；并覆盖结构预检。"""
    print("ddl_static_check 自检：判定逻辑 + 结构预检（均不依赖 pglast）")
    print("=" * 68)
    cases = [
        ("0 张表、0 语法错、0 外键错 → 必须判未通过（防空转假绿）",
         verdict(0, [], []), EXIT_FINDINGS),
        ("3 张表、0 错误 → 通过", verdict(3, [], []), EXIT_OK),
        ("3 张表、2 条语法错 → 未通过", verdict(3, ["e1", "e2"], []), EXIT_FINDINGS),
        ("3 张表、1 条外键错 → 未通过", verdict(3, [], ["fk1"]), EXIT_FINDINGS),
    ]
    # 族锚点容错的**直接单元断言**
    # --------------------------------------------------
    # 端到端夹具测不出它的边界：`AC-99` 在任何实现下都不会进 `later`，
    # 于是「族锚点放过」与「真悬空报红」**两种行为都表现为安静**——
    # 夹具照不到差别，等于没夹具。这里直接断言纯函数本身。
    # **一个测不出两种实现差别的夹具，比没有夹具更糟：它给人虚假的安全感。**
    # （第一版把它塞进 `cases`，而 `cases` 的形状是 `(code, _)` 元组 ⇒ 直接崩。）
    fam_cases = [
        ("is_family_anchor：AC-11 是已定义族的锚点 → True",
         is_family_anchor("AC-11", {"AC-11-1", "AC-11-2"}), True),
        ("is_family_anchor：AC-99 族内无任何定义 → False（不得无脑放过）",
         is_family_anchor("AC-99", {"AC-11-1", "AC-11-2"}), False),
        ("is_family_anchor：三段具体 ID 本身不是锚点 → False",
         is_family_anchor("AC-11-1", {"AC-11-1", "AC-11-2"}), False),
    ]

    # ---- INC-115：列级内联 `REFERENCES` 与表级 `FOREIGN KEY` 等价，不得只收一半 ----
    # ------------------------------------------------------------------
    # 第 2 层此前只收 `ast.Constraint` 分支的表级外键，`ColumnDef` 分支只看
    # PRIMARY/UNIQUE ⇒ W-02 的 30 条外键声明里，**19 条列级内联的被静默丢弃**，
    # 而结论句照样打印「外键目标均可解析」。**其中包括全部
    # `tenant_id text NOT NULL REFERENCES tenants(id)`**。
    #
    # 这两条用例**直接断言收集器**，而不是断言某个结论句——
    # 断言结论句会在「总数变了」时给出假象，收集器才是判据的入口。
    if PGLAST_MISSING:
        print("  [SKIP] pglast 未安装，列级外键收集的两条用例无法运行")
    else:
        _t: dict = {}
        _f: list = []
        _e: list = []
        collect_sql(
            "```sql\n"
            "CREATE TABLE tenants (id text PRIMARY KEY);\n"
            "CREATE TABLE support_grants (\n"
            "  id text PRIMARY KEY,\n"
            "  tenant_id text NOT NULL REFERENCES tenants(id)\n"
            ");\n"
            "```\n", "列级样本", _t, _f, _e)
        fam_cases.append(
            ("★ INC-115 列级内联 REFERENCES 必须被收集为外键（与表级等价）",
             len(_f) == 1 and _f[0][0] == "support_grants"
             and _f[0][1] == "tenants" and _f[0][3] == ["tenant_id"], True))

        _t2: dict = {}
        _f2: list = []
        _e2: list = []
        collect_sql(
            "```sql\n"
            "CREATE TABLE tenants (id text PRIMARY KEY);\n"
            "CREATE TABLE child (\n"
            "  id text PRIMARY KEY,\n"
            "  tenant_id text NOT NULL,\n"
            "  FOREIGN KEY (tenant_id) REFERENCES tenants(id)\n"
            ");\n"
            "```\n", "表级样本", _t2, _f2, _e2)
        fam_cases.append(
            ("★ INC-115 表级 FOREIGN KEY 仍被收集（不得因修列级而回退）",
             len(_f2) == 1 and _f2[0][0] == "child", True))

        # 「两种写法都必须收」是本条的全部要害，必须分别断言
        _t3: dict = {}
        _f3: list = []
        _e3: list = []
        collect_sql(
            "```sql\n"
            "CREATE TABLE tenants (id text PRIMARY KEY);\n"
            "CREATE TABLE a (id text PRIMARY KEY,"
            " tenant_id text REFERENCES tenants(id));\n"
            "CREATE TABLE b (id text PRIMARY KEY, tenant_id text,"
            " FOREIGN KEY (tenant_id) REFERENCES tenants(id));\n"
            "```\n", "两种写法", _t3, _f3, _e3)
        fam_cases.append(
            ("★ INC-115 同一条外键写成列级或表级，收到的条数必须相同（2 条）",
             len(_f3) == 2, True))
    ok = 0
    for label, (code, _), want in cases:
        good = code == want
        ok += good
        print(f"  [{'PASS' if good else 'FAIL'}] {label}"
              f"  实际 exit={code} 期望={want}")
    for label, got, want in fam_cases:
        good = got == want
        ok += good
        print(f"  [{'PASS' if good else 'FAIL'}] {label}"
              f"  实际 {got} 期望={want}")

    known = {"projects": {"id", "template_id", "data", "updated_at"},
             "workflow_idempotency": {"user_id", "action", "idem_key"}}
    scases = [
        ("结构正确 → 无问题",
         structure_check("ALTER TABLE projects ADD COLUMN tenant_id text;"
                         "ALTER TABLE workflow_idempotency ADD COLUMN tenant_id text;"
                         "CREATE UNIQUE INDEX u ON workflow_idempotency"
                         " (tenant_id, user_id, action, idem_key);", known), 0),
        ("新增列打错但未被索引引用 → 抓不到（语法上合法，如实承认）",
         structure_check("ALTER TABLE projects ADD COLUMN tennant_id text;", known), 0),
        ("新增列与索引一致地打错 → 抓不到（结构自洽，只能靠语义核对）",
         structure_check("ALTER TABLE projects ADD COLUMN tennant_id text;"
                         "CREATE UNIQUE INDEX u ON projects (tennant_id, id);",
                         known), 0),
        # **2026-10-01 新增（INC-59）第三类抓不到的情形**：
        # CHECK 约束的**取值集合**与散文规格不一致。
        # 实测：总设计 §12.1 定义 6 个租户状态，W-02 的 tenants_status_chk
        # 却是另一个 4 值集合——`draft` 缺失导致「新建 draft 客户」被直接拒绝，
        # 还多出一个设计里根本不存在的 `provisioning`。
        # 语法完全合法、CHECK 自洽、外键可解析，**本门禁两层都查不出**。
        # 与上面「列名打错」同类：**结构自洽的语义错误，只能靠语义核对**。
        ("CHECK 取值集合与散文规格不符 → 抓不到（结构自洽，只能靠语义核对）",
         structure_check("ALTER TABLE projects ADD COLUMN status text "
                         "CHECK (status IN ('provisioning','active','suspended','closed'));",
                         known), 0),
        ("索引指向本次 CREATE TABLE 的新表 → 不算问题",
         structure_check("CREATE TABLE tenant_organizations (\n"
                         "    id text,\n"
                         "    tenant_id text\n"
                         ");\n"
                         "CREATE INDEX i ON tenant_organizations (tenant_id, id);",
                         known), 0),
        ("重复加列 → 被抓",
         structure_check("ALTER TABLE projects ADD COLUMN data text;", known), 1),
        ("表不存在 → 被抓",
         structure_check("ALTER TABLE no_such_table ADD COLUMN tenant_id text;",
                         known), 1),
        ("索引引用不存在的列 → 被抓",
         structure_check("CREATE UNIQUE INDEX u ON projects (tenant_id, ghost);",
                         known), 1),
        ("索引引用本次新增的列 → 不算问题",
         structure_check("ALTER TABLE projects ADD COLUMN tenant_id text;"
                         "CREATE UNIQUE INDEX u ON projects (tenant_id, id);",
                         known), 0),
        # ---- SC-5 状态列必须有取值约束（INC-60）----
        # 阳性：新建表的状态列没有 CHECK → 必须报红。
        # **夹具必须自己 CREATE TABLE**，因为 SC-5 只作用于本设计新建的表；
        # 给既有表加列属于**判据范围外**，不该被 SC-5 判。
        ("★ SC-5 新建表 state 列无取值约束 → 被抓",
         _sc5("CREATE TABLE t1 (\n    id text PRIMARY KEY,\n"
              "    state text NOT NULL\n);"), 1),
        ("★ SC-5 新建表 status 列无取值约束 → 被抓",
         _sc5("CREATE TABLE t2 (\n    id text PRIMARY KEY,\n"
              "    status text NOT NULL DEFAULT 'active'\n);"), 1),
        # 阴性：有 CHECK → 不得误报。
        ("SC-5 状态列有 CHECK → 不报",
         _sc5("CREATE TABLE t3 (\n    id text PRIMARY KEY,\n"
              "    state text NOT NULL DEFAULT 'active',\n"
              "    CONSTRAINT t3_chk CHECK (state IN ('active','ended','legacy'))\n);"), 0),
        # 阴性：非状态列（kind / reason_code）不受 SC-5 约束。
        # **误报的代价和漏报一样大**——把 `kind` 也要求 CHECK 会让本门禁恒红。
        ("SC-5 非状态列不受约束 → 不报",
         _sc5("CREATE TABLE t4 (\n    id text PRIMARY KEY,\n"
              "    kind text NOT NULL,\n"
              "    reason_code text NOT NULL\n);"), 0),
        # **回归锁**：约束只存在于 `--` 注释里 → 必须仍然报红。
        # 真实来源：修 `tenants_status_chk` 时把旧约束以注释形式留在 DDL 里，
        # 而判据原样只搜「有没有 `IN (`」，注释里那行照样搜得到 ⇒ 判据失效。
        # **写下旧约束」不该有消掉门禁的能力。**
        ("★ SC-5 约束只写在注释里 → 仍然报红（防注释骗过判据）",
         _sc5("CREATE TABLE t5 (\n    id text PRIMARY KEY,\n"
              "    status text NOT NULL DEFAULT 'active',\n"
              "    -- CONSTRAINT t5_chk CHECK (status IN ('active','revoked'))\n);"), 1),
        # ---- SC-6 外键目标可解析 ----
        # **变异测试发现首版是半截检查**：行内 `REFERENCES` 只验了表名、没验列名，
        # 注入 `REFERENCES projects (idd)` 竟然全绿。
        # **只查一半的检查会给出虚假的安全感**，比不查更难发现——所以两类都补。
        ("SC-6 表级外键指向不存在的表 → 被抓",
         structure_check("CREATE TABLE u1 (\n    project_id text NOT NULL,\n"
                         "    FOREIGN KEY (project_id) REFERENCES projectz (id)\n);",
                         known), 1),
        ("SC-6 表级外键指向不存在的列 → 被抓",
         structure_check("CREATE TABLE u2 (\n    project_id text NOT NULL,\n"
                         "    FOREIGN KEY (project_id) REFERENCES projects (idd)\n);",
                         known), 1),
        ("SC-6 行内外键指向不存在的表 → 被抓",
         structure_check("CREATE TABLE u3 (\n    project_id text "
                         "REFERENCES projectz (id)\n);", known), 1),
        ("SC-6 行内外键指向不存在的列 → 被抓（首版半截检查的回归锁）",
         structure_check("CREATE TABLE u4 (\n    project_id text "
                         "REFERENCES projects (idd)\n);", known), 1),
        ("SC-6 外键正确 → 不报",
         structure_check("CREATE TABLE u5 (\n    project_id text NOT NULL,\n"
                         "    FOREIGN KEY (project_id) REFERENCES projects (id)\n);",
                         known), 0),
        # ---- SC-7 §3.6.4 声明张数 vs §4.1 实际 ALTER 张数（INC-73）----
        # 阳性：声明 11 张而实际 2 张 → 必须报红（这正是 W-02 的真实形态）。
        ("★ SC-7 §3.6.4 声明张数与 §4.1 不符 → 被抓",
         sc7_count_consistency(
             "#### 3.6.4 与 §4 现有表的关系\n\n§4 判定的 11 张现有表在本节不建策略。\n",
             "ALTER TABLE templates ADD COLUMN tenant_id text;\n"
             "ALTER TABLE projects ADD COLUMN tenant_id text;\n"), 1),
        # 阳性：**必须按表去重**。`workflow_records` 有两条 ADD COLUMN，
        # 按语句数会把「2 条语句」读成「2 张表」，恰好蒙对；
        # 但若同表加 3 列而只有 1 张表，按语句数就会误报。
        ("★ SC-7 同表多条 ADD COLUMN 只算一张表（去重口径）",
         sc7_count_consistency(
             "#### 3.6.4 与 §4 现有表的关系\n\n§4 判定的 1 张现有表在本节不建策略。\n",
             "ALTER TABLE workflow_records ADD COLUMN tenant_id text;\n"
             "ALTER TABLE workflow_records ADD COLUMN owner_ref text;\n"
             "ALTER TABLE workflow_records ADD COLUMN scope text;\n"), 0),
        # **正向对照**：改成不复述数字后，本判据必须安静——
        # **一个只会报红的检查器不是检查器**。这条锁死「引用而非复述」的修法。
        ("SC-7 §3.6.4 不复述张数（引用 §4.1）→ 不报",
         sc7_count_consistency(
             "#### 3.6.4 与 §4 现有表的关系\n\n"
             "张数以 §4.1 的 `ALTER TABLE ... ADD COLUMN` 语句为准，"
             "本节不复述数字。\n",
             "ALTER TABLE templates ADD COLUMN tenant_id text;\n"), 0),
        # ---- SC-8 CHECK 约束的规则出处（INC-77）----
        # 阳性：约束上方没有任何出处注释、也没有 ⚠ → 必须报红。
        ("★ SC-8 CHECK 无规则出处注释且未标 ⚠ → 被抓",
         sc8_constraint_provenance(
             "### 3.3.1 X\n\nCREATE TABLE t (\n    id text,\n"
             "    CONSTRAINT t_chk CHECK (id <> '')\n);\n"), 1),
        # 阴性：上方有 `-- XX-N` 出处 → 不报。
        ("SC-8 CHECK 有 `-- XX-N` 出处 → 不报",
         sc8_constraint_provenance(
             "### 3.3.1 X\n\nCREATE TABLE t (\n    id text,\n"
             "    -- TT-1：id 不得为空串\n"
             "    CONSTRAINT t_chk CHECK (id <> '')\n);\n"), 0),
        # **正向对照**：显式 ⚠ 标注「无规则出处」必须**安静**。
        # **如实登记的待定项不是缺口**——SC-8 要抓的是「一声不吭」。
        ("SC-8 CHECK 显式标注 ⚠ 无出处 → 不报（诚实登记不算缺口）",
         sc8_constraint_provenance(
             "### 3.3.1 X\n\nCREATE TABLE t (\n    id text,\n"
             "    -- ⚠ 这 3 个取值是 DDL 提案，尚无规则出处\n"
             "    CONSTRAINT t_chk CHECK (id <> '')\n);\n"), 0),
        # ---- SC-9 C0 表不得引用 C1/C2 包的规则（INC-77）----
        # 阳性：C0 表引用 C1 账务规则 → 必须报红（这正是 budget_policies 的真实形态）。
        ("★ SC-9 C0 表引用 C1 包规则 → 被抓",
         sc9_no_cross_phase_citation(
             "### 3.3.1 X\n\nCREATE TABLE t (\n    id text,\n"
             "    -- CM-2：不混用\n"
             "    CONSTRAINT t_chk CHECK (id <> '')\n);\n"), 1),
        # 阴性：引用 C0 包自己的规则 → 不报。
        ("SC-9 引用 C0 包规则 → 不报",
         sc9_no_cross_phase_citation(
             "### 3.3.1 X\n\nCREATE TABLE t (\n    id text,\n"
             "    -- QT-4：三者之一\n"
             "    CONSTRAINT t_chk CHECK (id <> '')\n);\n"), 0),
        # **正向对照**：⚠ 注释里**说明**那个 C1 规则是错的，必须安静。
        # 首版不区分「披露」与「引用」，结果把作者对自己错误的说明当成了引用——
        # **一个只会报红的检查器不是检查器。**
        ("SC-9 ⚠ 披露块里提到 C1 规则 → 不报（披露≠引用）",
         sc9_no_cross_phase_citation(
             "### 3.3.1 X\n\nCREATE TABLE t (\n    id text,\n"
             "    -- ⚠ 原注释错在 CM-2，那条规则讲的是别的事\n"
             "    -- 真正管辖本表的是 QT-4\n"
             "    CONSTRAINT t_chk CHECK (id <> '')\n);\n"), 0),
        # 阴性：数字对得上 → 不报。
        ("SC-7 声明张数与现实一致 → 不报",
         sc7_count_consistency(
             "#### 3.6.4 与 §4 现有表的关系\n\n§4 判定的 2 张现有表不建策略。\n",
             "ALTER TABLE templates ADD COLUMN tenant_id text;\n"
             "ALTER TABLE projects ADD COLUMN tenant_id text;\n"), 0),
        # ---- INC-78 收口：SC-9 必须**看见三段形态**的 C1 规则 ----
        # ----------------------------------------------------------
        # 变异测试发现：把 SC-9 退回本地窄副本后，**原有 33 条夹具全绿**。
        # 原因很直接：它们全部用 `CM-2` / `QT-4` 这种**两段**形态，
        # 而副本与真判据**在两段形态上完全一致**——夹具照不到那个窄的维度。
        #
        # 实测的真实触发样本：C0 的 CHECK 注释若引用 `AC-11-1`
        # （W-11 账务包里真实存在的三段验收判据），
        # **旧副本看不见它 ⇒ SC-9 静默放过**。
        # 这条夹具必须用**副本看不见的那个形态**，否则它和没加一样。
        ("★ SC-9 引用三段形态的 C1 规则（AC-11-1）→ 被抓"
         "（窄副本看不见这条，会静默放过）",
         sc9_no_cross_phase_citation(
             "### 3.3.1 X\n\nCREATE TABLE t (\n    id text,\n"
             "    -- 预算取值须与 W-11 的 AC-11-1 一致\n"
             "    CONSTRAINT t_chk CHECK (id <> '')\n);\n"), 1),
        # 同一形态的**阴性对照**：C0 包自己的三段判据不得被误报，
        # 否则修法会退化成「见 AC- 就报」——**只抓阳性不做对照，判据必然越修越宽**
        ("SC-9 ★ 三段形态的 C0 规则 → 不报（不是见 AC- 就报）",
         sc9_no_cross_phase_citation(
             "### 3.3.1 X\n\nCREATE TABLE t (\n    id text,\n"
             "    -- 依据 W-03 的 AC-03-2\n"
             "    CONSTRAINT t_chk CHECK (id <> '')\n);\n"), 0),
        # 族锚点容错的**两侧边界**（2026-10-02）
        # · 族锚点 `AC-11`（指 AC-11-1…5 那一族）→ 不是具体引用，必须安静
        # · 同一个 `AC-11` 若族内**一条定义都没有** → 它就是不存在，必须报
        #   （否则「放宽」会退化成「两段一律放过」，真悬空被吃掉）
        ("SC-9 ★ 族锚点 AC-11 → 不报（不是具体引用）",
         sc9_no_cross_phase_citation(
             "### 3.3.1 X\n\nCREATE TABLE t (\n    id text,\n"
             "    -- 与 W-11 的 AC-11 族保持一致\n"
             "    CONSTRAINT t_chk CHECK (id <> '')\n);\n"), 0),
    ]

    # ---- SC-10 §9.2/§9.3/§9.4 对象数与 DDL 承载（INC-80）----
    # ------------------------------------------------------------
    # **夹具必须覆盖三种失败形态**，只测一种等于没测：
    # ① 对象总数错 ②「已有 DDL」数错 ③ 数字被撤掉（无从核对）
    # 另加两条**判据自身的陷阱**：跨节累计、把 0 当通过。
    _sc10_doc = ("### 9.2 A\n\n| 对象 | 字段 |\n|---|---|\n| `t_one` | a |\n"
                 "### 9.3 B\n\n| 对象 | 字段 |\n|---|---|\n| `t_two` | a |\n"
                 "### 9.4 C\n\n| 对象 | 字段 |\n|---|---|\n| `t_three` | a |\n"
                 "## 10 别节\n\n| 值 |\n|---|\n| `active` |\n")
    _sc10_note = "\n> 共列 **3 个对象**，**2 个已有 `CREATE TABLE`**。\n"
    _sc10_ddl = "CREATE TABLE t_one (a text);\nCREATE TABLE t_two (a text);\n"
    # 阶段表夹具：`_sc10_stage` **刻意漏登记** `provider_credentials`（真实缺陷形态）
    #
    # 三条形状要求，缺一条夹具就会「红，但红的原因不是它想测的」：
    # ① §9.3 **清单里必须有** `provider_credentials`，否则「漏登记」无从谈起
    # ② 必须构造**完整三节**（§9.2/§9.3/§9.4），否则先撞「解析到 2 节」的防空转分支
    # ③ 阶段表必须落在 **§9.3 切片范围内**（真实文件就是如此），否则判据静默跳过
    _stage_hdr = "| 阶段 | 对象 | 承载规格 | 依赖的未决项 |\n|---|---|---|---|\n"
    _stage = _stage_hdr + "| C0 | `t_one` | W-08 | — |\n| C0 | `t_two` | W-06 | — |\n"
    _stage_full = _stage + "| C0 | `provider_credentials` | W-07 | — |\n"
    _doc_stage = ("### 9.2 A\n\n| 对象 | 字段 |\n|---|---|\n| `t_one` | a |\n"
                  "### 9.3 B\n\n| 对象 | 字段 |\n|---|---|\n| `t_two` | a |\n"
                  "| `provider_credentials` | a |\n\n"
                  + _stage
                  + "### 9.4 C\n\n| 对象 | 字段 |\n|---|---|\n| `t_three` | a |\n"
                  "## 10 别节\n\n| 值 |\n|---|\n| `active` |\n")
    _note3 = "\n> 共列 **4 个对象**，**2 个已有 `CREATE TABLE`**。\n"
    _sc10_stage = _doc_stage + _note3
    _doc_stage_full = _doc_stage.replace(_stage, _stage_full)
    _sc10_stage_full = _doc_stage_full + _note3.replace("**2 个已有", "**3 个已有")
    _sc10_cases = [
        ("SC-10 声明与现实一致 → 不报",
         sc10_object_ddl_coverage(_sc10_doc + _sc10_note, _sc10_ddl, set()) == []),
        ("SC-10 对象总数错（3 写成 5）→ 报红",
         bool(sc10_object_ddl_coverage(
             _sc10_doc + _sc10_note.replace("**3 个对象**", "**5 个对象**"),
             _sc10_ddl, set()))),
        ("SC-10 「已有 DDL」数错（2 写成 3）→ 报红",
         bool(sc10_object_ddl_coverage(
             _sc10_doc + _sc10_note.replace("**2 个已有", "**3 个已有"),
             _sc10_ddl, set()))),
        ("SC-10 可核对数字被撤掉 → 报红（不得静默通过）",
         bool(sc10_object_ddl_coverage(
             _sc10_doc + "\n> 对象若干个，已建的也有若干个。\n", _sc10_ddl, set()))),
        # **§10 的状态取值不得被算成对象**。
        # ⚠ 判据怎么写才算测到它：若跨节累计，`active` 会进现实侧，
        #   现实侧变成 4，此时**声明 4 反而会通过**——
        #   所以断言必须是「声明 4 时仍报红（现实侧仍是 3）」，
        #   而不是「声明 4 时安静」。我第一版写反了，于是夹具自己红了。
        ("SC-10 §10 的 `active` 不进现实侧（声明 4 仍报红）",
         bool(sc10_object_ddl_coverage(
             _sc10_doc + _sc10_note.replace("**3 个对象**", "**4 个对象**"),
             _sc10_ddl, set()))),
        ("SC-10 解析不到 §9.2/§9.3/§9.4 → 报红（防空转假绿）",
         bool(sc10_object_ddl_coverage("## 别处\n\n无对象表。\n", _sc10_ddl, set()))),
        # ★ 阶段表与对象清单**双向**一致（2026-10-02 普查，INC-84）
        #   真实样本就是 `provider_credentials`：它在 §9.3 清单里、阶段表里没有。
        #   **上一版 SC-10 抓不到它**——那版只核对「两个声明数字 vs 实算」，
        #   不核对两张表之间的一致性，**那是另一个维度**。
        ("SC-10 ★ 阶段表漏登记对象（provider_credentials）→ 报红",
         any("provider_credentials" in p for p in
             sc10_object_ddl_coverage(_sc10_stage, _sc10_ddl, set()))),
        # 阴性：登记齐了**这条判据**必须安静（否则它恒红，第一次用就会被关掉）。
        # ⚠ 不能断言「整个函数零问题」——样本里 `t_three` 故意没有 DDL，
        #   会命中「已有数」那条判据。**断言必须只针对本条判据关心的事**，
        #   否则夹具会因别的原因红，而红的原因不是你想测的。
        ("SC-10 ★ 阶段表登记齐全 → 本条判据不报（非恒红）",
         not any("阶段表" in p for p in
                 sc10_object_ddl_coverage(_sc10_stage_full, _sc10_ddl, set()))),
    ]
    for label, probs, want in scases:
        good = (len(probs) > 0) == bool(want)
        ok += good
        print(f"  [{'PASS' if good else 'FAIL'}] {label}"
              + (f"  → {probs[0]}" if probs else ""))
    for label, good in _sc10_cases:
        ok += bool(good)
        print(f"  [{'PASS' if good else 'FAIL'}] {label}")
    print("=" * 68)
    total = len(cases) + len(fam_cases) + len(_sc10_cases) + len(scases)
    if ok == total:
        print(f"自检通过：{ok}/{total}")
        return EXIT_OK
    print(f"自检失败：{ok}/{total}")
    return EXIT_FINDINGS


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    # ---- 三态退出码的最后一道防线（2026-10-02 实测，INC-80 附带事故）----
    # 任何判据抛异常，Python 都会以**退出码 1** 结束——而 1 的约定是
    # 「差距真实且已登记，ci 模式**不拦**」。于是**工具崩溃被伪装成有差距**，
    # CI 从此看不见它。实测：SC-10 里一句 `set(...) | known_tables`
    # （`dbpy_tables` 返回的是 dict）抛 TypeError，`ddl_static_check`
    # 的退出码就从 2（工具坏了，CI 必须拦）降成 1。
    #
    # **「未捕获异常」绝不能等价于「有差距」。** 这里统一兜住并返回 2。
    try:
        return _main()
    except SystemExit:
        raise
    except BaseException as exc:                      # noqa: BLE001
        import traceback
        sys.stderr.write(
            f"\n工具自身异常：{type(exc).__name__}: {exc}\n"
            f"这不是「有差距」，是工具坏了——按三态约定必须报 2，\n"
            f"否则 ci 模式（只拦 2）会放过一次崩溃。\n")
        traceback.print_exc()
        return EXIT_TOOL_BROKEN


def _main() -> int:

    all_sql = []
    for rel in DOCS:
        d = ROOT / rel
        if d.exists():
            all_sql.append(d.read_text(encoding="utf-8"))
    joined = "\n".join(all_sql)
    sql_text = "\n".join(re.findall(r"```sql\n(.*?)```", joined, re.S))

    # ---- 第 1 层：结构预检。**不依赖 pglast**，永远会跑 ----
    known = dbpy_tables(ROOT / "backend/app/db.py")
    struct_problems = structure_check(sql_text, known)
    # SC-7 需要散文（本节在 ```sql 块之外），单独算
    struct_problems += sc7_count_consistency(joined, sql_text)
    struct_problems += sc8_constraint_provenance(joined)
    struct_problems += sc9_no_cross_phase_citation(joined)
    # SC-10 需要总设计正文 + 全部 DDL 来源（§9.x 的对象清单不在 DOCS 里）
    _design = ROOT / "docs/product/saas-platform-design-20261001.md"
    if _design.is_file():
        _ddl_all = joined
        for _extra in (ROOT / "docs/tenancy").glob("*.md"):
            _ddl_all += "\n" + _extra.read_text(encoding="utf-8")
        struct_problems += sc10_object_ddl_coverage(
            _design.read_text(encoding="utf-8"), _ddl_all, known)
    n_alters = len(re.findall(r"ALTER TABLE\s+\w+\s+ADD COLUMN", sql_text, re.I))
    n_idx = len(re.findall(r"CREATE (?:UNIQUE )?INDEX", sql_text, re.I))
    n_tables = len(re.findall(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?\w+\s*\(",
                               sql_text, re.I))
    n_fk = len(re.findall(r"FOREIGN KEY\s*\(", sql_text, re.I)) + len(
        re.findall(r"^\s{2,}\w+\s+text[^,\n]*?REFERENCES\s+\w+",
                   sql_text, re.M | re.I))
    n_state = len(re.findall(r"^\s{2,}(?:status|state)\s+text\b",
                             sql_text, re.M | re.I))
    # ★ INC-112：原输出把「N 张新建表」与「db.py 的 M 张表」并置，
    # 读起来像两者已被比对过。实测两者的交集是 **0**——规格的目标表
    # 一张都没建（db.py 里全是租户化之前的旧表）。这个比值必须打出来，
    # 否则「✓ 七项判据全过」会被读成「目标 schema 已核对」。
    spec_tables = {m.group(1) for m in re.finditer(
        r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)\s*\(", sql_text, re.I)}
    implemented = spec_tables & set(known)
    print("=" * 66)
    print(f"第 1 层 · 结构预检（不依赖 pglast）"
          f"：{n_alters} 条 ADD COLUMN、{n_idx} 条索引、"
          f"{n_tables} 张新建表的 {n_state} 个状态列、{n_fk} 条外键，"
          f"对照 backend/app/db.py 的 {len(known)} 张表")
    print(f"  · 其中**已在 db.py 建出**的目标表：{len(implemented)} / {len(spec_tables)}"
          + (f"（{', '.join(sorted(implemented))}）" if implemented else "——**规格的目标 schema 尚未实施**"))
    if struct_problems:
        for p in struct_problems:
            print(f"  ✗ {p}")
    else:
        print("  ✓ 七项判据全过："
              "① ADD COLUMN / 索引引用的表与列均存在、无重复加列  "
              "② 状态列均有取值约束（SC-5）"
              "③ 外键目标表与被引用列均可解析（SC-6）"
              "④ §3.6.4 声明的现有表张数 == §4.1 实际 ALTER 的表数（SC-7）"
              "  ⑤ 每条 CHECK 都有规则出处注释或显式 ⚠（SC-8）"
              "  ⑥ C0 表不引用 C1/C2 包的规则（SC-9）")
        print("  · 本层**查不到**的（不要把「本层通过」读成「DDL 正确」）："
              "语法合法性、CHECK 取值集合与散文规格是否一致（INC-59/60 那一类）"
              "、**以及目标 schema 是否已实施**——SC-6 只验「按规格建库时外键指向"
              "能否解析」，目标表可由规格自身的 CREATE TABLE 满足（INC-112："
              "30 条外键里 28 条的判定与 db.py 零耦合）")

    if struct_problems:
        # 结构问题是真发现，不能被「缺 pglast」吞成退出码 2
        print(f"\n结论: ✗ 未通过：{len(struct_problems)} 项结构问题"
              f"（未进入第 2 层语法检查）")
        return EXIT_FINDINGS

    if PGLAST_MISSING:
        sys.stderr.write(PGLAST_MISSING)
        print(f"\n{'-' * 66}\n第 1 层通过；第 2 层（语法 + 外键）未执行：缺 pglast。")
        return EXIT_TOOL_BROKEN

    tables, fks, syntax_errors = collect()
    print(f"pglast / libpg_query {pglast.__version__}")
    print(f"解析到 CREATE TABLE {len(tables)} 张，外键 {len(fks)} 条"
          f"（按**去重后的表**计）\n")
    # ★ 同一张表被两份文档各声明一次时，`collect()` 的 `name in tables: continue`
    # 会整块跳过第二处 ⇒ 它的外键**不进本层核对**。不报出来就成了
    # 「第 1 层说 30、第 2 层说 28」这种没人能解释的两个数（INC-112/INC-114 同族）。
    if DUPLICATE_TABLES:
        print(f"⚠ 有 {len(DUPLICATE_TABLES)} 张表被**重复声明**，"
              f"其第二次声明的约束**未进入本层核对**："
              f"{', '.join(sorted(DUPLICATE_TABLES))}\n")

    # 覆盖面闸：一条 CREATE TABLE 都没解析到时，下面所有「✓」都是空话——
    # 语法检查核对了 0 条语句却报告「语法合法」，这和
    # backfill_validate 的「0 行 == 0 行」是同一类假绿。
    coverage_gap = not tables
    if coverage_gap:
        print("✗ 未解析到任何 CREATE TABLE 声明——本项从未核对，不能报告为通过。")
        print("  可能原因：DDL 草案被移出 ```sql 代码块、文档路径失效，"
              "或解析器与草本文法不兼容。\n")

    if syntax_errors:
        print(f"✗ 语法错误 {len(syntax_errors)} 条：")
        for e in syntax_errors:
            print(f"    {e}")
    else:
        print("✓ 语法：全部语句通过 PostgreSQL 官方解析器")

    print()
    fk_errors: list[str] = []
    external: list[str] = []
    for ref, tgt, cols, refcols in fks:
        target = tables.get(tgt)
        shown = f"{ref} ({','.join(refcols)}) → {tgt} ({','.join(cols)})"
        if target is None:
            covered = EXTERNAL_TARGETS.get(tgt, set())
            (external if set(cols) <= covered else fk_errors).append(
                shown if set(cols) <= covered
                else shown + f"  目标表不在本批且列未被已知主键覆盖"
            )
            continue
        if set(cols) == target["pk"] or any(set(cols) == u for u in target["unique"]):
            print(f"  ✓ {shown}")
        else:
            fk_errors.append(
                f"{shown}  目标 PK={sorted(target['pk'])} "
                f"UNIQUE={[sorted(u) for u in target['unique']]}"
            )
            print(f"  ✗ {shown}")

    for e in external:
        print(f"  ? {e}  （跨批引用，已知主键覆盖）")
    if fk_errors:
        print(f"\n✗ 外键目标缺失 {len(fk_errors)} 条，建表会失败：")
        print('  ERROR: there is no unique constraint matching given keys for referenced table')
        for e in fk_errors:
            print(f"    {e}")

    failed, conclusion = verdict(len(tables), syntax_errors, fk_errors)
    print("\n" + "=" * 66)
    print(("结论: ✗ " if failed else "结论: ✓ ") + conclusion)
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
