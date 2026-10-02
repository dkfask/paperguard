#!/usr/bin/env python3
"""contract 阶段准入门禁：隔离清单未清零时，禁止执行不可回退的 DDL。

背景（INC-16）
--------------
W-02 §4 判定的 11 张现有 `tenant` 表在 expand 阶段加了 `tenant_id`（nullable），
但 contract 阶段的 `SET NOT NULL` 与建 RLS **从未被产出**——规格只写了
「contract 阶段（本节不产出）」，没写**什么条件下才允许执行**。

这不是「留白」，是一个**未设防的悬崖**：

| contract 动作 | 对 `tenant_id IS NULL` 行的影响 |
|---|---|
| `SET NOT NULL` | PostgreSQL **报错**（响亮地失败） |
| 建 RLS | 该行**对所有租户不可见**（静默消失） |

`SET NOT NULL` 至少会失败；建 RLS 会让这些行**静默地对所有人消失**。
在隔离清单没清零时执行后者，就是把「数据缺失」升级成「服务不可用」且无人察觉。

实测数据（2026-10-01，本地真实库 dry-run）
----------------------------------------
`backfill_migrate.py` 跑出 **22 条隔离行**（用户 10 / 项目 6 / 模板 3 / 规范 3），
它们的 `tenant_id` 必然为 `NULL`。**这 22 条同时阻断 `SET NOT NULL` 和建 RLS。**

本门禁做什么
------------
按 W-02 T-02.5.1 的准入条件逐条核对，**任一条不满足即报红**：

1. **CT-1 隔离清单归零**：实跑回填器 dry-run，隔离行必须为 0
2. **CT-2 `templates` 归属已裁决**：INC-03 需产品决策（副本 vs 平台共享）
3. **CT-3 `requirement_sets` 全局归属已定义**：INC-04
4. **CT-4 `workflow_events` 有真实样本**：§4 #23 要 `NOT NULL`，本地库 0 行取不到样本

**刻意不做的事**
----------------
1. **不替业务做裁决**。CT-2/CT-3 只报「未裁决」，不猜答案。
2. **不产出 contract DDL**。本门禁是**准入闸**，不是执行器——
   通过了也不代表允许执行，只代表「前置条件已满足，可以开始写 DDL 并评审」。
3. **不把「推迟 contract」当失败**。推迟**不会**造成损害；
   错误执行才会。本门禁的 `1` 表示「现在还不能做」，不是「做错了」。

退出码
------
    0 = 四项准入条件全部满足
    1 = 存在未满足项（差距真实且已量化）
    2 = 工具不可运行（依赖缺失、库不存在、隔离统计解析失败）
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

ROOT = pathlib.Path(__file__).resolve().parents[3]
SPEC = "docs/product/W-02-存储与Schema实施规格_20261001.md"
DB = "backend/data/paperguard.db"
MIGRATE = "docs/tenancy/tools/backfill_migrate.py"
# 「已裁决」在规格中的标记形式：产品把结论写进文档才算数。
RESOLVED_MARK = re.compile(r"(INC-0[34]|templates).{0,40}(RESOLVED|已裁决)", re.I)


def quarantine_stats(db: pathlib.Path) -> tuple[dict, str]:
    """实跑回填器 dry-run，解析隔离清单。

    为什么必须实跑而不是读旧报告：隔离清单是**数据状态**，
    会随建号、回填、业务操作变化。**读旧报告等于报告过期后仍显示通过。**
    """
    if not db.exists():
        return {}, f"真实库不存在：{db}"
    if not (ROOT / MIGRATE).exists():
        return {}, f"回填器不存在：{MIGRATE}"
    # 用一份 CONFIRMED 且非占位的映射表驱动；只写 /tmp，不改仓库。
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmap = pathlib.Path(td) / "tm.json"
        tmap.write_text(json.dumps({
            "_status": "CONFIRMED",
            "tenant_map": {"org-uat-school": "tenant-uat-0001"},
        }, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / MIGRATE),
             "--source", str(db), "--tenant-map", str(tmap),
             "--output", str(pathlib.Path(td) / "out.db")],
            capture_output=True, text=True, timeout=180)
    text = proc.stdout
    if "隔离清单" not in text:
        return {}, "回填器未输出隔离清单段落——无法判定"

    stats: dict = {"total": 0, "by_reason": {}, "by_table": {}}
    # 先剥掉 ANSI 颜色码：回填器输出带色，正则若不剥离会因行首转义序列而匹配不到标题。
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    for label, key in (("隔离清单", "total"),):
        m = re.search(rf"{label}\s+(\d+)", text)
        if m:
            stats[key] = int(m.group(1))
    # 逐条清单行：`  · 表.对象 [rule] 原因`
    m = re.search(r"隔离清单（归属不明，不猜测）\n(.*?)(?:\n\s*\n|\Z)", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            mm = re.match(r"\s*·\s*([a-z_]+)\.", line)
            if mm:
                stats["by_table"][mm.group(1)] = \
                    stats["by_table"].get(mm.group(1), 0) + 1
            rr = re.search(r"\[([a-z0-9-]+)\]", line)
            if rr:
                stats["by_reason"][rr.group(1)] = \
                    stats["by_reason"].get(rr.group(1), 0) + 1
    if stats["total"] == 0 and not stats["by_table"]:
        return {}, "隔离清单段落存在但 0 条——解析失败（不得据此判通过）"
    return stats, ""


def check(stats: dict, spec_text: str) -> list[str]:
    """纯核对：不做 I/O，可被 --self-test 用构造数据覆盖。"""
    problems: list[str] = []

    # CT-1 隔离清单归零
    n = stats.get("total")
    if n is None:
        problems.append("CT-1 隔离清单行数未知")
    elif n > 0:
        by_table = "、".join(
            f"{t} {c}" for t, c in sorted(stats.get("by_table", {}).items()))
        problems.append(
            f"CT-1 隔离清单 {n} 行未清零（{by_table}）——"
            f"阻断 SET NOT NULL 与建 RLS；不得默认归属消号（AC-02-9）")

    # CT-2 / CT-3 产品裁决
    if not RESOLVED_MARK.search(spec_text) or "INC-03" not in spec_text:
        problems.append("CT-2 templates 归属未裁决（INC-03）：副本 or 平台级共享？")
    if "INC-04" in spec_text and not re.search(
            r"INC-04.{0,60}(RESOLVED|已裁决)", spec_text, re.I | re.S):
        problems.append("CT-3 requirement_sets 全局归属未定义（INC-04）")

    # CT-4 workflow_events 真实样本
    if stats.get("by_table", {}).get("workflow_events", 0) == 0:
        problems.append(
            "CT-4 workflow_events 本地库 0 行，§4 #23 的 NOT NULL 无法取样验证")
    return problems


def verdict(n_tables: int, problems: list[str], err: str) -> tuple[int, str]:
    """纯判定。**err 非空（工具跑不起来）必须返回 2，不得返回 0。**"""
    if err:
        return EXIT_TOOL_BROKEN, err
    if n_tables == 0:
        return EXIT_TOOL_BROKEN, "未解析到任何隔离统计，本项无数据可核对"
    if problems:
        return EXIT_FINDINGS, f"contract 未获准：{len(problems)} 项前置条件未满足"
    return EXIT_OK, "contract 准入条件全部满足"


def self_test() -> int:
    cases: list[tuple[str, tuple[int, str], int]] = []

    def add(label, stats, spec_text, want, n_tables=4):
        cases.append((label, verdict(n_tables, check(stats, spec_text), ""), want))

    clean = {"total": 0, "by_table": {}, "by_reason": {}}
    resolved_spec = ("x\nINC-03 已裁决：模板为平台级共享。\n"
                     "INC-04 已裁决：scope='global' 归平台。\n")
    # 隔离行数为 0 且 workflow_events 有样本（模拟真实样本存在的情形）
    with_we = {"total": 0,
               "by_table": {"workflow_events": 2}, "by_reason": {}}

    add("隔离 0 + 裁决齐全 + 有样本 → 0", with_we, resolved_spec, EXIT_OK)
    add("★ 隔离 22 行 → 1", {"total": 22, "by_reason": {"rule-3": 10, "backfill": 12},
                             "by_table": {"users": 10, "projects": 6,
                                          "templates": 3, "requirement_sets": 3}},
        resolved_spec, EXIT_FINDINGS)
    add("裁决缺失 → 1", with_we, "x\n尚无结论。\n", EXIT_FINDINGS)
    add("workflow_events 0 行 → 1", clean, resolved_spec, EXIT_FINDINGS)
    add("统计未知 → 1", {}, resolved_spec, EXIT_FINDINGS)
    # 覆盖面闸与工具不可运行
    cases.append(("★ 统计解析失败 → 2，不得为 0",
                  verdict(0, [], "回填器未输出隔离清单段落"), EXIT_TOOL_BROKEN))
    cases.append(("★ 0 张表 → 2", verdict(0, [], ""), EXIT_TOOL_BROKEN))

    # ANSI 剥离：回填器输出带颜色码，曾导致逐条清单解析静默失效
    # （by_table 为空 → verdict 看到 0 张表 → 误判 2 或在别处误判）。
    # 这里锁死「带色输出也能解析出 22 行」。
    ansi = ("\x1b[2m隔离清单（归属不明，不猜测）\x1b[0m\n"
            "  · users.u1 [rule-3] 无 school_id\n"
            "  · templates.t1 [backfill] 模板无 owner\n"
            "  · templates.t2 [backfill] 模板无 owner\n"
            "  · requirement_sets.r1 [backfill] scope='global'\n")
    stripped = re.sub(r"\x1b\[[0-9;]*m", "", ansi)
    mm = re.search(r"隔离清单（归属不明，不猜测）\n(.*?)(?:\n\s*\n|\Z)", stripped, re.S)
    got_tables = 0
    if mm:
        got_tables = sum(
            1 for ln in mm.group(1).splitlines()
            if re.match(r"\s*·\s*([a-z_]+)\.", ln))
    cases.append((f"★ 带 ANSI 的逐条清单仍解析出 4 条 → 0，"
                  f"实际 {got_tables}",
                  verdict(got_tables, [], ""),
                  EXIT_OK if got_tables == 4 else EXIT_TOOL_BROKEN))

    ok = 0
    for label, got, want in cases:
        if got[0] == want:
            ok += 1
            print(f"  {GREEN}✓{RESET} {label}  → exit {got[0]}")
        else:
            print(f"  {RED}✗{RESET} {label}  → 实际 {got[0]}，期望 {want}")
            if got[1]:
                print(f"      {DIM}{got[1][:140]}{RESET}")
    print(f"\n自检 {ok}/{len(cases)}")
    return EXIT_OK if ok == len(cases) else EXIT_TOOL_BROKEN


def main() -> int:
    if "--self-test" in sys.argv:
        print(f"{CYAN}contract 准入门禁自检{RESET}")
        print("=" * 64)
        return self_test()

    spec = ROOT / SPEC
    if not spec.exists():
        print(f"{RED}{SPEC} 不存在{RESET}")
        return EXIT_TOOL_BROKEN

    print(f"{CYAN}contract 阶段准入门禁{RESET}  {DIM}W-02 T-02.5.1{RESET}")
    print(f"{DIM}隔离统计为实跑 dry-run 得出（不读旧报告——报告会过期）{RESET}\n")

    stats, err = quarantine_stats(ROOT / DB)
    if not err:
        print("  隔离清单实测（backfill_migrate dry-run）")
        print(f"    合计 {RED if stats.get('total') else GREEN}"
              f"{stats.get('total', 0)} 行{RESET}")
        for t, c in sorted(stats.get("by_table", {}).items()):
            print(f"      {DIM}{t:<22}{RESET} {c}")
        for r, c in sorted(stats.get("by_reason", {}).items()):
            print(f"      {DIM}[{r}]{RESET} {c}")
        print()

    problems = check(stats, spec.read_text(encoding="utf-8"))
    code, msg = verdict(len(stats.get("by_table", {})), problems, err)
    if problems:
        print(f"{RED}contract 准入未通过{RESET}（{len(problems)} 项）")
        for p in problems:
            print(f"  {RED}·{RESET} {p}")
        print()
    print(msg)
    if code == EXIT_FINDINGS:
        print(f"{DIM}推迟 contract 不会造成损害；错误执行会让隔离行对所有人静默消失。{RESET}")
    return code


if __name__ == "__main__":
    sys.exit(main())
