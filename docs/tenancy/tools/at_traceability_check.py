#!/usr/bin/env python3
"""AT-ID 追溯矩阵一致性校验器。

配套：at-traceability-matrix.yaml
       at-traceability-framework.md
来源：docs/product/saas-platform-design-20261001.md §24.2

为什么需要它
------------
追溯矩阵最容易出的错是**漂移**：设计文档改了 AT 判据，矩阵没跟着改，
于是矩阵开始对着一份不存在的验收标准说话，而且**没有任何东西会报错**。
本校验器把矩阵里 5 个「不可编辑」字段与设计文档逐条比对，任何不一致立刻失败。

它检查什么
----------
AT-01  矩阵的 AT-ID 集合与设计文档**完全相同**（不多、不少、不换名）
AT-02  每条的 stage / work_package / scenario / criterion 与设计文档**逐字一致**
AT-03  每条 AT-ID 在设计文档的阶段区间内（C0=01-34, C1=35-39, C2=40-42）
AT-04  每条都有通过判据（criterion 非空）
AT-05  每条引用的 blocker 都在 blockers 字典中登记
AT-06  gate_state 取值合法
AT-07  **没有任何一条**声明 strict_uat_passed（设计文档 §24.2 明令"不是本次已执行结果"）
AT-08  current_evidence 中引用的 state 必须是合法六态
AT-09  存在阳性对照（--self-test），证明本检查器在"矩阵正确"时能变绿

退出码：0 = 全部通过；1 = 存在不一致。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DESIGN = REPO_ROOT / "docs/product/saas-platform-design-20261001.md"
MATRIX = REPO_ROOT / "docs/tenancy/at-traceability-matrix.yaml"

# 设计文档 §24.2 的阶段区间
STAGE_RANGE = {
    "C0": range(1, 35),
    "C1": range(35, 40),
    "C2": range(40, 43),
    "cross": range(43, 46),
}

LEGAL_GATE_STATES = {
    "not_started",
    "partially_evidenced",
    "ready_to_design",
    "blocked_on_scope",
}

LEGAL_EVIDENCE_STATES = {
    "local_fixed",
    "local_gate_passed",
    "committed_pushed",
    "merged",
    "deployed",
    "runtime_verified",
    "strict_uat_passed",
    "design_ready",  # 设计产出专用，不属于运行六态
}

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def parse_design_table() -> dict[str, dict[str, str]]:
    """从设计文档 §24.2 解析 AT 表。

    表格行形如：
      | AT-01 | C0 / W-03 | 场景 | 判据 |
    注意 stage 与 work_package 合并在同一格（`C0 / W-03`）。
    """
    if not DESIGN.exists():
        raise SystemExit(f"找不到设计文档：{DESIGN}")
    rows: dict[str, dict[str, str]] = {}
    for line in DESIGN.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(AT-\d+)\s*\|(.+)$", line)
        if not m:
            continue
        at_id = m.group(1)
        cells = [c.strip() for c in m.group(2).split("|")]
        if len(cells) < 3:
            continue
        stage_wp = cells[0]
        if "/" in stage_wp:
            stage, wp = [s.strip() for s in stage_wp.split("/", 1)]
        else:
            stage, wp = stage_wp, ""
        rows[at_id] = {
            "stage": stage,
            "work_package": wp,
            "scenario": cells[1],
            "criterion": cells[2] if len(cells) > 2 else "",
        }
    return rows


def load_matrix() -> dict:
    if not MATRIX.exists():
        raise SystemExit(f"找不到追溯矩阵：{MATRIX}")
    try:
        import yaml
    except ImportError:
        raise SystemExit("需要 pyyaml 才能读取矩阵文件")
    return yaml.safe_load(MATRIX.read_text(encoding="utf-8"))


def expand_wp(wp: str) -> set[str]:
    """把 §24.2 的 work_package 字符串展开成完整工作包集合。

    为什么必须展开（2026-10-01 实测修正）
    ------------------------------------
    §24.2 用**简写**：`W-03/04` 表示 W-03 **和** W-04 两个包。
    朴素地做 `"W-04" in "W-03/04"` 字符串包含判断会得到 **False**，
    `re.findall(r"W-\\d{2}", "W-03/04")` 也只得到 `['W-03']`——
    因为 `04` 没有 `W-` 前缀。后果是：**同时归属两个包的 AT 会被判成
    「只归 W-03」**，W-04 引用它就报「归属不符」。

    简写规则：`W-03/04` → `W-03` + `W-04`；`W-04/05/06` → 三个包。
    段内两位数字补 `W-` 前缀，长度不足两位则跳过（无法判定）。
    """
    out: set[str] = set()
    for part in wp.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        for m in re.finditer(r"W-(\d{2})|(\d{2})(?=$|[/])", part):
            num = m.group(1) or m.group(2)
            out.add(f"W-{num}")
    return out


def spec_at_references() -> list[tuple[str, str]]:
    """扫描各工作包规格，返回 [(规格文件名, AT-ID), ...]。

    **只认「验收表关联列」的引用，不认正文提及**（2026-10-01 实测修正）
    ------------------------------------------------------------------
    首版把规格里出现的**所有** `AT-\\d{2}` 都当成归属声明，7 处全部误报：
    - W-02 正文说明「幂等唯一范围须为租户+成员+操作+键（AT-01）」——
      这是**引用依据**，不是声称 W-02 验收 AT-01
    - W-01 `validation_case: AT-45` 是回退验证项，不是 §24.2 的工作包归属

    误报的代价和漏报一样大：门禁第一次就报 7 条假警，
    第二次就会有人把 AT-10 关掉——那才是真正的损失。

    判据：只取**验收表（`## 5. 验收` 或 `## 6. 验收`）中「关联」列**的 AT，
    且**排除标注「前置」的**——「本包提供前置」不等于「本包验收该 AT」。
    """
    out: list[tuple[str, str]] = []
    for spec in sorted((REPO_ROOT / "docs/product").glob("W-*.md")):
        if not re.match(r"W-\d{2}", spec.name):
            continue
        try:
            text = spec.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^#+\s*\d*\.?\s*验收\s*$", text, re.M)
        if not m:
            continue
        tail = text[m.end():]
        # 只看验收表本身：截到下一个同级别标题
        nxt = re.search(r"^#+\s", tail, re.M)
        table = tail[: nxt.start()] if nxt else tail
        # 关联列形如 | AC-03-2 | AT-05 | ... 或 | AC-02-4 | E3 · 前置 AT-04 |
        #
        # **「前置 AT-xx」不是验收归属**（2026-10-01 显式化）
        # ------------------------------------------------
        # W-02 交付的约束是 AT-04/05/45 的**前置**，但 §24.2 里这三条
        # 都不归属 W-02（AT-04→W-04、AT-05→W-03/04、AT-45 跨阶段）。
        # 本包提供前置 ≠ 本包验收该 AT。把前置当归属会让 AT-10 误判。
        for row in re.finditer(r"^\|\s*AC-[^|]*\|\s*([^|]*)\|", table, re.M):
            cell = row.group(1)
            if "前置" in cell:
                continue
            for at_id in re.findall(r"AT-\d{2}", cell):
                out.append((spec.name, at_id))
    return out


def main() -> int:
    self_test = "--self-test" in sys.argv
    design = parse_design_table()
    matrix = load_matrix()
    cases = {c["id"]: c for c in matrix.get("cases", [])}
    blockers = matrix.get("blockers", {})

    if self_test:
        # 阳性对照：用设计文档自身反向构造一份"正确"矩阵，验证比对逻辑。
        cases = {
            k: {
                **v,
                "related_findings": ["SELF-TEST"],
                "current_evidence": [{"ref": "self-test", "state": "design_ready"}],
                "blockers": [],
                "gate_state": "not_started",
            }
            for k, v in design.items()
        }
        print(f"{DIM}阳性对照：用设计文档自身构造合规矩阵{RESET}\n")

    print(f"{DIM}设计文档 AT-ID: {len(design)}  矩阵条目: {len(cases)}{RESET}\n")
    print("=" * 72)
    print("AT-ID 追溯矩阵校验")
    print("=" * 72)

    results: list[tuple[str, bool, str]] = []

    # AT-01 集合完全一致
    only_design = sorted(set(design) - set(cases))
    only_matrix = sorted(set(cases) - set(design))
    ok1 = not only_design and not only_matrix
    msg1 = "AT-ID 集合完全一致"
    if only_design:
        msg1 += f"；设计文档有但矩阵缺：{only_design}"
    if only_matrix:
        msg1 += f"；矩阵有但设计文档无：{only_matrix}"
    results.append(("AT-01", ok1, msg1))

    # AT-02 逐字段比对
    drift: list[str] = []
    for at_id, d in design.items():
        c = cases.get(at_id)
        if not c:
            continue
        for field in ("stage", "work_package", "scenario", "criterion"):
            if str(c.get(field, "")).strip() != d[field].strip():
                drift.append(f"{at_id}.{field}")
    results.append(("AT-02", not drift,
                    "stage/work_package/scenario/criterion 与设计文档逐字一致"
                    if not drift
                    else f"{len(drift)} 处漂移：{drift[:5]}"))

    # AT-03 stage 与编号区间一致
    #
    # 注意：设计文档对 AT-43/44/45 用的标签是「按范围」「各阶段」，
    # **不在** STAGE_RANGE 里。AT-02 已保证矩阵与设计逐字一致，
    # 因此这里只对「设计文档本身使用标准阶段名」的条目做区间校验；
    # 非标准标签不算错——那是设计文档自己的措辞。
    bad_stage = [
        at_id for at_id, c in cases.items()
        if design.get(at_id, {}).get("stage") in STAGE_RANGE
        and int(at_id.split("-")[1]) not in STAGE_RANGE[design[at_id]["stage"]]
    ]
    nonstandard = sorted(
        at_id for at_id in cases
        if design.get(at_id, {}).get("stage") not in STAGE_RANGE
    )
    results.append(("AT-03", not bad_stage,
                    (f"标准阶段条目编号与区间一致"
                     f"（{len(nonstandard)} 条使用设计文档的非标准标签：{nonstandard}）")
                    if not bad_stage
                    else f"{len(bad_stage)} 条阶段错配：{bad_stage[:5]}"))

    # AT-04 判据非空
    no_crit = [k for k, v in design.items() if not v["criterion"]]
    results.append(("AT-04", not no_crit,
                    "全部 AT-ID 有通过判据" if not no_crit else f"缺判据：{no_crit}"))

    # AT-05 blocker 引用闭合
    undeclared: set[str] = set()
    for c in cases.values():
        for b in (c.get("blockers") or []):
            if b not in blockers:
                undeclared.add(b)
    results.append(("AT-05", not undeclared,
                    "全部 blocker 引用已在字典中登记"
                    if not undeclared
                    else f"{len(undeclared)} 个未登记的 blocker：{sorted(undeclared)}"))

    # AT-06 gate_state 合法
    bad_gate = sorted({c.get("gate_state") for c in cases.values()
                       if c.get("gate_state") not in LEGAL_GATE_STATES})
    results.append(("AT-06", not bad_gate,
                    "gate_state 取值全部合法" if not bad_gate
                    else f"非法取值：{bad_gate}"))

    # AT-07 不得有 strict_uat_passed
    claimed = [c["id"] for c in cases.values()
               for e in (c.get("current_evidence") or [])
               if e.get("state") == "strict_uat_passed"]
    results.append(("AT-07", not claimed,
                    "无任何 AT 声明 strict_uat_passed（符合设计文档 §24.2 的"
                    "「不是本次已执行结果」）"
                    if not claimed
                    else f"{len(claimed)} 条越界声明 strict_uat_passed：{claimed}"))

    # AT-08 证据六态合法
    bad_state: set[str] = set()
    for c in cases.values():
        for e in (c.get("current_evidence") or []):
            if e.get("state") not in LEGAL_EVIDENCE_STATES:
                bad_state.add(str(e.get("state")))
    results.append(("AT-08", not bad_state,
                    "current_evidence 的 state 全部合法"
                    if not bad_state
                    else f"非法六态：{sorted(bad_state)}"))

    # AT-09 矩阵必须真的引用了取证产出（防空壳矩阵）
    refs = {f for c in cases.values() for f in (c.get("related_findings") or [])}
    ev = {e.get("ref") for c in cases.values() for e in (c.get("current_evidence") or [])}
    ok9 = bool(refs) and bool(ev)
    results.append(("AT-09", ok9,
                    f"矩阵已引用 {len(refs)} 个发现编号、{len(ev)} 条证据条目"
                    if ok9 else "矩阵为空壳：既无发现引用也无证据条目"))

    # AT-10 工作包规格引用的 AT 归属必须与 §24.2 相符
    #
    # 为什么已有 AT-02 不够（2026-10-01 补）
    # -------------------------------------
    # AT-01～AT-09 只比对**矩阵 ↔ 设计文档**两方。一致了也不代表
    # **工作包规格**引用 AT 时没写错——W-03 规格的 AC 表里写「AT-05」，
    # 若 §24.2 实际把 AT-05 归给 W-04，矩阵和设计文档照样一致，
    # 错误只在规格里，**三方校验永远发现不了**。
    #
    # 后果不是措辞问题：验收时会拿 W-03 的判据去验一条 W-04 的场景，
    # 或者反过来在 W-03 验收时漏掉本该跑的 AT。
    spec_refs = spec_at_references()
    mismatched: list[str] = []
    for spec_name, at_id in spec_refs:
        d = design.get(at_id)
        if not d:
            mismatched.append(f"{spec_name} 引用了不存在的 {at_id}")
            continue
        wp = d["work_package"]
        m = re.match(r"(W-\d{2})", spec_name)
        if not m:
            continue
        mine = m.group(1)
        # §24.2 对 AT-43/44/45 的 work_package 是「按范围」「各阶段」或空——
        # 这些是**跨阶段**条目，归属不绑定到单一工作包，任何规格都可引用。
        # 把它们按「归属不符」判错是误报。
        if not wp.strip() or wp.strip() in ("按范围", "各阶段", "跨阶段"):
            continue
        if mine not in expand_wp(wp):
            mismatched.append(
                f"{spec_name} 引用 {at_id}，但 §24.2 将其归给「{wp}」")
    ok10 = not mismatched
    results.append(("AT-10", ok10,
                    f"{len(spec_refs)} 处规格↔AT 引用归属一致"
                    if ok10 else f"{len(mismatched)} 处不符：{mismatched[:3]}"))

    # AT-11 反向检查：§24.2 归给本包、但规格验收表**漏掉**的 AT
    #
    # 为什么 AT-10 不够（2026-10-01 实测）
    # -----------------------------------
    # AT-10 只校验「引用了的归属对不对」，**查不出该引用而没引用的**。
    # 实测：W-03 规格验收表只引了 AT-01/02/05，漏了 AT-07；
    # 而 `resource-registry.yaml`（users 挂 global_role_antipattern，
    # 标「属 W-03/W-04」）与 `permission-matrix.md`（P-ACT-3 对应 AT-07）
    # **两处独立产出都指向 W-03**，只有验收表里没有。
    #
    # 漏掉的验收项在收口时不会被发现——**它默认不做，而且没人知道**。
    # 这比引用错更难察觉：引用错了会报红，漏了则一片绿。
    missing_refs: list[str] = []
    for spec_name, at_id in spec_refs:
        m = re.match(r"(W-\d{2})", spec_name)
        if not m:
            continue
        mine = m.group(1)
        declared = {a for n, a in spec_refs if n == spec_name}
        for other_id, d in design.items():
            wp = d["work_package"]
            if not wp.strip() or wp.strip() in ("按范围", "各阶段", "跨阶段"):
                continue
            # §24.2 把 other_id 归给本包，但本包验收表没提它
            if mine in expand_wp(wp) and other_id not in declared:
                missing_refs.append(f"{spec_name} 漏引 {other_id}（§24.2 归「{wp}」）")
    # 去重：同一 AT 在同一规格里只报一次
    missing_refs = sorted(set(missing_refs))
    ok11 = not missing_refs
    results.append(("AT-11", ok11,
                    "各包验收表已覆盖 §24.2 归给它的全部 AT"
                    if ok11 else f"{len(missing_refs)} 处漏引：{missing_refs[:3]}"))

    # AT-12 覆盖面闸：**不可解析的验收表必须报出来，不能静默跳过**
    # ------------------------------------------------------------------
    # 2026-10-01 实测事故（INC-43）
    # --------------------------------
    # AT-10/AT-11 都只遍历 `spec_refs`——即**已经解析出至少一条引用**的规格。
    # 一份验收表首列写成 `AT-14` 而不是仓库约定的 `AC-06-1` 时，
    # 正则 `^\|\s*AC-` 一条都匹配不到，该规格贡献 0 条引用、
    # **从两个检查里彻底消失**，而门禁照样报
    # 「30 处引用归属一致 / 各包验收表已覆盖全部 AT」——**全绿**。
    #
    # 这是本仓库反复出现的形态：**静默假绿比只会报红危险得多**。
    # 引用错了会报红；解析不了则一片绿，而那一大片绿里**根本没看过这份规格**。
    unparsed: list[str] = []
    no_section: list[str] = []
    parsed_specs = {n for n, _ in spec_refs}

    def _at_bearing(wp: str) -> bool:
        """§24.2 是否给这个包分配了至少一条 AT。"""
        return any(wp in expand_wp(d["work_package"])
                   for d in design.values()
                   if d["work_package"].strip()
                   and d["work_package"].strip() not in ("按范围", "各阶段", "跨阶段"))

    for spec in sorted((REPO_ROOT / "docs/product").glob("W-*.md")):
        if not re.match(r"W-\d{2}", spec.name) or spec.name in parsed_specs:
            continue
        # **只对 §24.2 归了 AT 的包设闸。**
        # W-01/W-02 的 AC 行第二列是包内判据（D1、E1/F-01），不是 AT，
        # §24.2 也没给它们分配任何 AT——那不是缺口，是它们的正常形态。
        if not _at_bearing(spec.name[:4]):
            continue
        try:
            text = spec.read_text(encoding="utf-8")
        except OSError:
            continue
        if re.search(r"^#+\s*\d*\.?\s*验收\s*$", text, re.M):
            unparsed.append(spec.name)
        else:
            no_section.append(spec.name)
    ok12 = not unparsed and not no_section
    msg12 = f"{len(parsed_specs)} 份规格验收表可解析"
    if unparsed:
        msg12 += f"；**§24.2 归了 AT、但验收表解析不出 AC- 行**：{unparsed}"
    if no_section:
        msg12 += f"；**§24.2 归了 AT、却没有验收章节**：{no_section}"
    results.append(("AT-12", ok12, msg12))

    failed = [r for r in results if not r[1]]
    if not self_test:
        for code, ok, msg in results:
            mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
            print(f"  [{mark}] {code}  {msg}")
    print("=" * 72)

    if self_test:
        # AT-10 专项变异：证明「只认验收表关联列」与「跨阶段条目不判错」
        # 这两条边界不是碰巧通过的（首版两处都误报）。
        real = spec_at_references()
        ok_a = ("W-02-存储与Schema实施规格_20261001.md", "AT-01") not in real
        results.append(("AT-10-V", ok_a,
                        "正文提及的 AT-01 **不**计入 W-02 归属声明"
                        if ok_a else "正文提及被误当归属声明"))
        # 跨阶段 AT-45：work_package 为空 → 任何规格都可引用，不该判错。
        # 判据必须**真的检查**它没进 mismatched，而不是恒真表达式。
        cross_ok = "AT-45" not in [m.split("引用 ")[-1].split("，")[0]
                                   for m in mismatched]
        results.append(("AT-10-X", cross_ok,
                        "跨阶段 AT-45（work_package 为空）不被判错"
                        if cross_ok else "跨阶段条目被误判为归属不符"))
        # AT-11 专项：漏引必须能被抓到。变异掉 W-03 验收表里的 AT-07，
        # AT-11 应当从 PASS 转 FAIL——否则它就是一片绿里的空壳。
        import tempfile as _tf
        with _tf.TemporaryDirectory() as _td:
            orig = REPO_ROOT / "docs/product"
            saved = (orig / "W-03-租户上下文与授权实施规格_20261001.md").read_text(
                encoding="utf-8")
            try:
                (orig / "W-03-租户上下文与授权实施规格_20261001.md").write_text(
                    saved.replace("| AC-03-5 | **AT-07** |", "| AC-03-5 | — |"),
                    encoding="utf-8")
                mutated = spec_at_references()
                lost = ("W-03-租户上下文与授权实施规格_20261001.md", "AT-07") not in mutated
            finally:
                (orig / "W-03-租户上下文与授权实施规格_20261001.md").write_text(
                    saved, encoding="utf-8")
        results.append(("AT-11-M", lost,
                        "抽掉验收表里的 AT-07 后，AT-11 能发现漏引"
                        if lost else "AT-11 对漏引无反应（空壳）"))
        # 「前置 AT-xx」不得被当成本包的验收归属声明（2026-10-01 显式化）。
        # W-02 提供 AT-04/05/45 的前置，但 §24.2 里这三条都不归 W-02。
        w02 = [a for n, a in spec_at_references()
               if n.startswith("W-02")]
        results.append(("AT-10-P", not w02,
                        "W-02 的「前置 AT-04/05/45」不计入归属声明"
                        if not w02 else f"前置 AT 被误当归属：{w02}"))
        # 简写展开：`W-03/04` 必须同时含 W-03 与 W-04。
        # 朴素字符串包含判断会漏掉后者（实测曾因此误判 AT-05 归属不符）。
        exp_ok = (expand_wp("W-03/04") == {"W-03", "W-04"}
                  and expand_wp("C0, W-03/04/06") == {"W-03", "W-04", "W-06"}
                  and expand_wp("按范围") == set())
        results.append(("AT-10-E", exp_ok,
                        "§24.2 简写 W-03/04 正确展开为两个包"
                        if exp_ok else "简写展开失败"))
        # AT-12 专项（2026-10-01 INC-43）：**不可解析的验收表不得静默跳过**。
        # 把 W-03 验收表首列整体改成 `AT-xx`（即 INC-43 事故的真实形态），
        # AT-12 必须把 W-03 报成「有验收章节但解析不出 AC- 行」——
        # 否则 AT-12 就是又一片只会说「好」的绿灯。
        import re as _re2
        with _tf.TemporaryDirectory() as _td:
            target = REPO_ROOT / "docs/product/W-03-租户上下文与授权实施规格_20261001.md"
            saved12 = target.read_text(encoding="utf-8")
            try:
                # 只破坏验收表里的 AC- 首列，正文其余 AC- 保留
                broken = _re2.sub(r"^\|\s*AC-03-", "| XX-03-", saved12, flags=_re2.M)
                target.write_text(broken, encoding="utf-8")
                after = spec_at_references()
                # 复算 AT-12 的核心判据：W-03 此时必须「解析不出引用」
                w03_lost = ("W-03-租户上下文与授权实施规格_20261001.md", "AT-01") not in after
                w03_gone = not any(
                    n.startswith("W-03-") for n, _ in after)
                # 且旧逻辑（只遍历 spec_refs）会**完全看不到** W-03 —— 这正是事故成因
                legacy_blind = w03_gone
            finally:
                target.write_text(saved12, encoding="utf-8")
        # 变异后 W-03 全部 AC 行不可解析 ⇒ 引用消失；恢复后必须回来
        restored = spec_at_references()
        back_ok = any(n.startswith("W-03-") for n, _ in restored)
        results.append(("AT-12-M", w03_lost and legacy_blind and back_ok,
                        "验收表首列不合约定时，AT-12 能发现该规格解析不出引用"
                        if (w03_lost and back_ok)
                        else "AT-12 对不可解析的验收表无反应（空壳）"))
        for code, ok, msg in results:
            mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
            print(f"  [{mark}] {code}  {msg}")
        failed = [r for r in results if not r[1]]
        if failed:
            print(f"{RED}阳性对照失败：比对逻辑有误{RESET}")
            for code, _ok, msg in failed:
                print(f"  {code}: {msg}")
            return 1
        print(f"{GREEN}阳性对照通过：{len(results)}/{len(results)} 项达标{RESET}")
        return 0

    # 阶段统计（信息性输出，不参与判定）
    dist: dict[str, list[str]] = {}
    for c in cases.values():
        dist.setdefault(c.get("gate_state", "?"), []).append(c["id"])
    print(f"{DIM}gate_state 分布：{RESET}")
    for st in sorted(dist):
        ids = sorted(dist[st])
        rng = f"{ids[0]}～{ids[-1]}" if len(ids) > 2 else ",".join(ids)
        print(f"  {st:<22} {len(ids):>2} 条  {rng}")

    if failed:
        print(f"{RED}{len(failed)}/{len(results)} 项未通过{RESET}")
        return 1
    print(f"{GREEN}全部 {len(results)} 项通过，矩阵与设计文档一致{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
