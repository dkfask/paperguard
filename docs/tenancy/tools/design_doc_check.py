#!/usr/bin/env python3
"""设计文档自洽门禁：文档对门禁工具的陈述必须与现实一致

为什么需要它
------------
设计文档里写着「某工具当前 exit=1」。工具改了、文档不跟着改，
就出现**过期陈述**——而过期的设计文档比没有文档更危险，
因为读者会照着它做决策。

实测过一次：本轮靠人工每次都记得同步，文档当时是对的。
但**「当时是对的」不等于「会一直对」**。没有任何机制能保证下一轮还是对的。

标记格式
--------
文档在表格里保持人类可读，另用一行 HTML 注释承载机器可校验的数据：

    <!-- gate:backfill_validate.py --self-test=0 -->
    <!-- gate:ddl_static_check.py =2 -->

注释对渲染不可见，不破坏阅读体验；本工具只认这个格式，不解析散文表格
——散文里「自检 6 项」这种数字会被误读成退出码，**只有结构化标记才可靠**。

七项检查
--------
    DC-1 退出码一致   文档声称的 exit 必须等于实际运行的 exit
    DC-2 工具存在     标记引用的工具文件必须存在
    DC-3 工具全覆盖   tools/ 下每个 .py 都必须在文档里登记（防止新工具被遗忘）
    DC-4 文档卫生     相对链接可达、无乱码字符
    DC-5 §27.3 表格   人读的工具表必须也列出全部工具
    DC-6 简报事实标记 收口简报的关键数字按现实重算比对
    DC-7 §27.5 事实标记 本文 §27.5 表格里的关键数字按现实重算比对

DC-7 与 DC-6 的区别
------------------
DC-6 校验的是**另一份文件**（收口简报），DC-7 校验的是**本文自己的 §27.5 表格**。
两者都用同一种机制：散文保持人类可读，另埋一行 HTML 注释承载可核对的数据。

DC-7 存在的理由是实测踩到的：**DC-3/DC-5 校验的标记与表格，比真实过期的地方更窄**。
标记块齐全、人读表格齐全，读者看到的数字仍然可以是错的——
实测一次就抓到 5 处（错误码占位数、规则 ID 数、门禁套件三态、
`spec_citation` 引用数、`DC-1～DC-5` 的范围声明）。
**门禁只校验了比真实问题更窄的一部分，等于没校验。**

退出码
------
    0 = 文档与现实一致
    1 = 不一致（文档过期 / 链接断裂 / 有工具未登记）
    2 = 工具不可运行（找不到设计文档或 tools 目录）
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

MARKER = re.compile(r"<!--\s*gate:([\w./-]+\.py)((?:\s+--[\w-]+)*)\s*=(-?\d+)\s*-->")
# §27.5 散文表格里的关键数字（DC-7）。前缀与 DC-6 的 `fact:` 区分开，
# 避免两个文件互相串台时把对方的标记读进来。
FACT275 = re.compile(r"<!--\s*fact275:([a-z_0-9]+)=(-?\d+)\s*-->")
LINK = re.compile(r"\]\((\.\.?/[^)#]+)\)")
MOJIBAKE = "\ufffd"


def _norm_entry(name: str, flags: object) -> str:
    """把 run_all_gates 的条目与文档标记归一到同一个字符串。

    GATES 用第二元素 `'plain'` 表示普通调用，文档标记用**无 flag** 表示普通调用。
    两边字面不同但语义相同，不归一会把 31 个条目全部误判成「未登记」——
    这正是 DC-5 首版字符类写错时踩过的坑（详见 self_test 第 11 条）。
    """
    fs = flags if isinstance(flags, str) else " ".join(flags or [])
    is_plain = (not fs) or ("plain" in fs.split())
    return f"{name} --plain" if is_plain else f"{name} --self-test"


def count_err_code_rows(path: pathlib.Path) -> dict[str, int]:
    """按 error-codes.md 的章节统计 `| `CODE` |` 形态的码位行。

    口径必须写死在这里并在文档里说明，否则「16 个占位」和「26 个占位」
    这种分歧会再次出现——数字的含义由上下文决定，散文无法机械校验。

    `§2 体系 A（已实现）` + `§3 体系 B（已实现）` = 已实现码位
    `§5 目标码位（占位，含 5.1～5.5 全部子表）` = 占位码位

    **数的是「不同码位」而不是「表格行数」**（2026-10-01 因 INC-64 修正）
    ------------------------------------------------------------------
    §5.1.1 补了 E7 那一行之后，表格行数从 26 变 27，
    但 E7 **复用 `TENANT_NOT_ACTIVE`**，**码位一个都没多**。
    首版数行数，于是把「多了一行」报成「多了一个码位」——
    **口径含糊会让门禁在无关改动上报假警**，
    而假警的代价和漏报一样大：门禁第一次报假警，第二次就会有人把它关掉。
    """
    out = {"implemented": 0, "placeholder": 0}
    if not path.is_file():
        return out
    sec = ""
    seen_impl: set[str] = set()
    seen_ph: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^##\s+(\d+)\.", line)
        if m:
            sec = m.group(1)
        cm = re.match(r"^\|\s*`([A-Z][A-Z0-9_]+)`", line)
        if not cm:
            continue
        if sec in ("2", "3"):
            seen_impl.add(cm.group(1))
        elif sec == "5":
            seen_ph.add(cm.group(1))
    out["implemented"] = len(seen_impl)
    out["placeholder"] = len(seen_ph)
    return out

# 不要求在设计文档里登记状态的工具。**必须保持为空（INC-69）**。
#
#   这里曾经写着 `EXEMPT = {"run_all_gates.py"}`，理由写的是
#   「门禁的基础设施本身，状态由其自检保证」。实测这条豁免有两个问题：
#     ① 它的理由**没有任何东西去验证**——`run_all_gates.py --self-test`
#        当时**根本不在 GATES 里**，那 10 项判据只有我手工敲过一次才跑过。
#        「由其自检保证」是一句没人核对的话。
#     ② 豁免名单本身**没有任何判据**。把它加回去，DC-3/DC-5 会立刻变绿，
#        没有一处会报红——**一条免检通道比没有判据更危险**，
#        因为它让「漏登记」看起来像「本来就该豁免」。
#   现在 `run_all_gates.py --self-test` 已纳入套件（GATES 末项），
#   其状态由 `<!-- gate:run_all_gates.py --self-test=0 -->` 标记与 DC-1 核对，
#   与其余 18 个工具同等对待，豁免没有存在的理由。
EXEMPT: set[str] = set()


def parse_markers(text: str) -> list[tuple[str, list[str], int]]:
    out = []
    for m in MARKER.finditer(text):
        name = m.group(1)
        flags = m.group(2).split()
        out.append((name, flags, int(m.group(3))))
    return out


DEPTH_VAR = "DESIGN_DOC_CHECK_DEPTH"
SELF_NAME = pathlib.Path(__file__).name


def count_rule_ids(root: pathlib.Path) -> int:
    """规则 ID 数量——**引用** `spec_rule_id_check` 的判据，不复制提取逻辑。

    为什么要改成引用（2026-10-02 INC-78 暴露）
    ------------------------------------------
    本文件原本在 **DC-6 与 DC-7 两处**各自复制了一份
    「`^\\|\\s*\\*{0,2}([A-Z]{2,4})-(\\d{1,2})\\*{0,2}\\s*\\|`」的提取逻辑。
    于是 INC-78 把 `spec_rule_id_check` 的「定义」形态从一种扩到三种时，
    **本文件的两份副本毫无察觉**，DC-6/DC-7 齐声报
    「声明 444 ≠ 现实 343」——**门禁在用一份过期的世界模型核对现实**。

    这正是本文件 DC-7 注释里自己写下的那句话：
    「复制 `spec_citation_check` 的提取逻辑（**口径漂移风险**）」。
    当时正因为这个风险**才没给「引用总处数」埋标记**，
    而 `rule_ids` 埋了标记——**标记要求机器核对，就不能各写各的**。

    修法沿用本文件 DC-7 既有的 `importlib` 加载 `run_all_gates` 的做法：
    **单一判据来源，副本归零**。
    口径与 `spec_rule_id_check.main()` 严格一致：按 `SPEC_NAME` 过滤、
    逐行计数（**同规格内重复也计数**，与该工具的 `total += 1` 对齐）。
    """
    import importlib.util as _ilu
    sp = _ilu.spec_from_file_location(
        "_sric", root / "docs/tenancy/tools/spec_rule_id_check.py")
    mod = _ilu.module_from_spec(sp)
    sp.loader.exec_module(mod)
    total = 0
    for f in sorted((root / "docs/product").glob("W-*.md")):
        if not mod.SPEC_NAME.match(f.name):
            continue
        total += len(mod.rule_defs(f.read_text(encoding="utf-8")))
    return total


SPAWN_BUDGET = 80
_spawn_used = 0


def run_tool(tools_dir: pathlib.Path, name: str,
             flags: list[str]) -> tuple[int | None, str]:
    """跑一个门禁工具，返回 (退出码, 合并后的输出)。

    **输出必须一起带回来（DC-8）**：DC-1 已经在 spawn 每个工具了，
    自检项数就印在这些输出里。此前 `run_tool` 只取 `returncode`，
    于是「自检 23/23」这种数字明明已经算出来了却被丢掉——
    INC-57 当时把这个缺口记成「要给自检 N/N 埋标记就得再 spawn 自己，递归且慢」，
    **那个理由站不住**：进程本来就在跑，代价只是多接一个返回值。

    两道保险（INC-71）
    ------------------
    ① `DEPTH_VAR` 递归守卫。**每一处会 spawn 门禁的判据都必须自己判一次**：
       DC-8 首版没判，于是 `design_doc_check --self-test` → main() → DC-1 按
       守卫跳过自身 → DC-8 看不到「自己已经被跑过」，又 spawn 一个自己 → 无限递归。
       实测在 3 分 40 秒内 fork 出 170+ 个进程，**这是守卫加在一处就以为万事大吉的典型**。
    ② 全局 spawn 预算。守卫是按「谁 spawn 自己」写的，**只防得住已知的自指路径**；
       预算防的是任何未预见的自指——超预算后不再 spawn，直接报「预算耗尽」。
       **一个能把机器 fork 爆的检查器，比没有检查器更糟。**
    """
    global _spawn_used
    if _spawn_used >= SPAWN_BUDGET:
        return None, f"spawn 预算耗尽（{SPAWN_BUDGET} 次），已停止再启动子进程"
    p = tools_dir / name
    if not p.exists():
        return None, ""
    env = dict(os.environ)
    env[DEPTH_VAR] = str(int(os.environ.get(DEPTH_VAR, "0")) + 1)
    _spawn_used += 1
    try:
        r = subprocess.run([sys.executable, str(p)] + flags,
                           capture_output=True, text=True, timeout=300, env=env)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, str(exc)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# 自检输出里的 `N/M` 计数行。必须锚在「自检」/「阳性对照」这类词上，
# **不能全文取最后一个 `N/M`**——工具正文里出现 `18/18` 之类的字样会误取。
SELFTEST_COUNT = re.compile(
    r"(?:自检|阳性对照)[^\n]{0,24}?(\d+)\s*/\s*(\d+)")


def selftest_count(output: str) -> tuple[int, int] | None:
    """从自检输出里取 (通过数, 总数)。取不到就返回 None——**不猜、不填默认值**。"""
    hits = SELFTEST_COUNT.findall(output)
    if not hits:
        return None
    n, m = hits[-1]
    return int(n), int(m)


def main() -> int:
    ap = argparse.ArgumentParser(description="设计文档自洽门禁")
    ap.add_argument("--doc", help="设计文档路径（自检传**副本**，不碰真实文件）")
    ap.add_argument("--brief", help="收口简报路径（同上，默认真实文件）")
    ap.add_argument("--tools-dir", help="门禁工具目录")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    here = pathlib.Path(__file__).resolve()
    root = here.parents[3]
    doc_path = (pathlib.Path(args.doc) if args.doc
                else root / "docs/product/saas-platform-design-20261001.md")
    tools_dir = (pathlib.Path(args.tools_dir) if args.tools_dir
                 else root / "docs/tenancy/tools")
    if not doc_path.exists() or not tools_dir.is_dir():
        sys.stderr.write(f"找不到设计文档或工具目录：{doc_path} / {tools_dir}\n")
        return EXIT_TOOL_BROKEN

    brief_path = (pathlib.Path(args.brief) if args.brief
                  else root / "docs/tenancy/设计阶段收口简报_20261001.md")
    text = doc_path.read_text(encoding="utf-8")
    findings: list[str] = []
    # DC-1 跑过的 (工具, flags) → (退出码, 输出)。DC-8 直接复用，
    # 不为「取自检项数」再开一轮进程。
    ran: dict[tuple[str, tuple[str, ...]], tuple[int | None, str]] = {}
    # §27.5 的事实标记。DC-7 与 DC-8 都要用，提到 main() 作用域。
    declared275 = dict(FACT275.findall(text))
    # spawn 预算**按本次 main() 计**（INC-71）。
    # 首版把它做成模块级全局，于是自检里连着 4 次 `run_real()` 全流程
    # （每次约 34 次 spawn）累计超预算，后两次直接被判「工具无法运行」——
    # **兜底机制自己成了新的假红来源**。一次 main() 是一次完整的判定，
    # 预算也该是它的预算。
    global _spawn_used
    _spawn_used = 0

    print("=" * 76)
    print("设计文档自洽核对")
    print("=" * 76)

    # DC-1 / DC-2
    markers = parse_markers(text)
    if not markers:
        findings.append(
            "DC-0 文档里没有任何 <!-- gate:TOOL FLAGS=EXIT --> 标记——"
            "文档对门禁的陈述无法被机器校验，只能靠人记得同步")
        print(f"  {RED}✗ 无 gate 标记{RESET}")
    else:
        print(f"\n  {DIM}DC-1/DC-2 退出码与工具存在性{RESET}")
        for name, flags, claimed in markers:
            # 递归守卫：文档必须登记本工具自己的状态，但校验它会再 spawn 一个
            # 自己 → 无限递归。子进程跳过自身这一条，由父进程的判定负责。
            if name == SELF_NAME and int(os.environ.get(DEPTH_VAR, "0")) >= 1:
                print(f"  {DIM}—{RESET} {name:30} 自身状态由父进程判定（递归守卫）")
                continue
            actual, out = run_tool(tools_dir, name, flags)
            ran[(name, tuple(flags))] = (actual, out)
            if actual is None:
                findings.append(f"DC-2 标记引用的工具不存在或无法运行：{name}")
                print(f"  {RED}✗{RESET} {name:30} 工具不存在/无法运行")
                continue
            if actual != claimed:
                findings.append(
                    f"DC-1 文档声称 {name} {' '.join(flags)} 的 exit={claimed}，"
                    f"实际 exit={actual} —— 文档已过期")
                print(f"  {RED}✗{RESET} {name:30} {' '.join(flags) or '(默认)':12}"
                      f" 文档={claimed} 实际={actual}")
            else:
                print(f"  {GREEN}✓{RESET} {name:30} {' '.join(flags) or '(默认)':12}"
                      f" exit={actual}")

    # DC-3 工具全覆盖
    print(f"\n  {DIM}DC-3 工具登记覆盖率{RESET}")
    on_disk = {p.name for p in tools_dir.glob("*.py")} - {"__init__.py"}
    documented = {n for n, _, _ in markers}
    for missing in sorted(on_disk - documented - EXEMPT):
        findings.append(f"DC-3 工具 {missing} 存在于 tools/ 但未在设计文档登记状态")
        print(f"  {RED}✗{RESET} {missing:30} 未登记")
    covered = len(on_disk & documented)
    print(f"  {GREEN}✓{RESET} 已登记 {covered}/{len(on_disk - EXEMPT)}"
          f"（豁免：{', '.join(sorted(EXEMPT)) or '无'}）")

    # DC-5 §27.3 人读表格的登记完整性
    #
    # 为什么 DC-3 不够（2026-10-01 真实踩到）
    # ---------------------------------------
    # DC-3 只校验**结构化标记块**里有没有登记，而 §27.3 的「门禁工具」表
    # 是给人读的散文表格。实测中 `rls_policy_check` / `contract_readiness_check`
    # 等 5 个工具在标记块里齐全，DC-3 全绿，**但 §27.3 表格里一个都没有**——
    # 读者看那张表会以为只有 8 个门禁。
    #
    # **两处登记必须同时存在**：标记块给机器看，表格给人看。
    # 只校验其中一处，等于允许另一处静默过期。
    print(f"\n  {DIM}DC-5 §27.3 工具表登记完整性{RESET}")
    # 抓取 §27.3 区间内的所有 `xxx.py` 链接
    # 区间必须锚到 §28 而不是下一个 `####`：「门禁工具」是 §27.3 下的 #### 子标题，
    # 锚到下一个 #### 会在那里截断，只抓到 3 个工具却以为覆盖全章（实测 1397 字符）。
    sec = re.search(r"####\s*27\.3.*?(?=\n#+\s*28\.|\Z)", text, re.S)
    if not sec:
        findings.append("DC-5 找不到 §27.3 章节")
        print(f"  {RED}✗{RESET} 找不到 §27.3 章节")
    else:
        # §27.3 里工具名出现在 Markdown 链接语法中：| [`g0.py`](../tenancy/tools/g0.py) |
        # 因此**不能**要求反引号紧贴文件名（`\`g0.py\``），要允许前后有 [ ] ( ) 等符号。
        # 字符类必须含数字：夹具工具名形如 `g0.py`，漏掉 0-9 会让整套 DC-5 恒为「未列」。
        in_table = set(re.findall(r"[A-Za-z0-9_]+\.py", sec.group(0)))
        absent = sorted((on_disk - EXEMPT) - in_table)
        for a in absent:
            findings.append(
                f"DC-5 工具 {a} 在 §27.3 工具表中缺行（读者看不到这道门禁）")
            print(f"  {RED}✗{RESET} {a:30} §27.3 表格未列")
        if not absent:
            print(f"  {GREEN}✓{RESET} §27.3 表格已列全部 {len(on_disk - EXEMPT)} 个工具")

    # DC-6 收口简报的机器可校验事实
    # -----------------------------------------------------------
    # 为什么需要（2026-10-01 真实踩到）
    # ---------------------------------
    # §27.5 把 `设计阶段收口简报` 标为「**先读这份**」的入口。
    # 它写于 13 份工作包规格之前，当时称「产出清单 17 份」「五项高危发现」
    # 「代码不一致 INC 2 项」——而现实已是 **INC-49、13 份规格、18 个门禁工具**。
    # **过期的入口文档比没有入口文档更危险**：读者会照着它做决策，
    # 而它看起来是权威且最新的。
    #
    # 修法不是「记得更新」，而是**让数字可被机器核对**：
    # 简报里埋 `<!-- fact:KEY=VALUE -->`，DC-6 按现实重算并比对。
    # 散文里的数字**无法被机械校验**——数字的含义由上下文决定。
    print(f"\n  {DIM}DC-6 收口简报事实标记核对{RESET}")
    real_design = root / "docs/product/saas-platform-design-20261001.md"
    # **判据是「文件名」而不是「绝对路径」**（2026-10-01 修正）
    # 原来比 `resolve()`，导致自检**只能**去改真实设计文档——
    # 于是产生两次真实事故：并发编辑被 `finally` 静默覆盖；
    # 自检被取消时 `finally` 不执行，真实文档**留在变异态**
    # （实测留下缺失的门禁标记，DC-7 立刻报 `gate_all_total 30 ≠ 31`）。
    # 改用文件名后自检可传**同名副本**，真实文件全程只读；
    # 夹具文档名为 `d.md`，与本仓设计文档不同，**仍被正确跳过**。
    brief = brief_path
    if doc_path.name != real_design.name:
        # **适用边界（不是偷懒）**：简报里 gates_* 与 inc_max 断言的是
        # **真实设计文档**。拿夹具文档来核对，派生值必然与真实仓库错配，
        # 产生 3 条纯误报——那正是「校验器输入边界比被校验对象更窄/错配」，
        # 会让门禁在自检里恒红，最终被人关掉。
        print(f"  {DIM}· 跳过：被检文档不是本仓设计文档，DC-6 的跨文件断言不适用{RESET}")
    elif not brief.is_file():
        findings.append("DC-6 找不到收口简报 docs/tenancy/设计阶段收口简报_20261001.md")
        print(f"  {RED}✗{RED} 收口简报不存在")
    else:
        btext = brief.read_text(encoding="utf-8")
        declared = dict(re.findall(r"<!--\s*fact:([a-z_]+)=(-?\d+)\s*-->", btext))
        if not declared:
            findings.append("DC-6 收口简报未埋任何 <!-- fact:KEY=VALUE --> 标记"
                            "（散文数字无法被机器校验，简报必然静默过期）")
            print(f"  {RED}✗{RED} 无 fact 标记")
        else:
            # 按现实重算
            prod = root / "docs/product"
            specs = sorted(prod.glob("W-*.md"))
            spec_lines = sum(len(p.read_text(encoding="utf-8").splitlines())
                             for p in specs)
            ten_docs = [p for p in (root / "docs/tenancy").glob("*") if p.is_file()]
            tools = sorted((root / "docs/tenancy/tools").glob("*.py"))
            inc_max = 0
            # `\d{1,3}` 而不是 `\d{2}`（INC-85 顺带修）：两位数写法在 INC-100 处
            # 会**静默截断**成 10 —— `INC-100` 里 `\d{2}` 只吃下 "10"，
            # 剩下的 0 被忽略，max 反而变**小**。一个只会算小的 max 比没有 max 更坏，
            # 因为它看起来仍然是个数。
            for m in re.finditer(r"INC-(\d{1,3})", text):
                inc_max = max(inc_max, int(m.group(1)))
            # `inc_rows` = 发现表里**实际登记的条数**（2026-10-02 INC-85 顺带新增）
            # ------------------------------------------------------------------
            # 简报 §1 结论句写着「N 条发现全部登记」，而那个 N **没有任何判据守着**，
            # 实测长期停在 **64**——而发现表是 `| **INC-NN** |` 行，**INC-03～INC-85
            # 共 83 行、无缺号**。也就是说它不是「慢慢过期」，而是**从来没对过**。
            #
            # **口径必须写死**：`inc_max`（最大编号）与 `inc_rows`（登记条数）是
            # **两个不同的数**。有缺号时二者不等；**无缺号时 `rows = max - 2`**。
            # 只登记 `inc_max` 的话，删掉中间某一行 `inc_max` 纹丝不动 ⇒ 缺口无告警。
            inc_rows = len(re.findall(r"^\| \*\*INC-\d+\*\* \|", text, re.M))
            # 规则 ID：**引用** `spec_rule_id_check`（见 `count_rule_ids` 的理由），
            # 不在本文件另写一份提取逻辑。
            rule_ids = count_rule_ids(root)
            # 门禁口径（2026-10-01 修正）
            # ------------------------------
            # `gates_total` = `run_all_gates.GATES` 的**条目数**（plain + self-test）
            # `gates_pass/gap/broken` = **只数 plain 调用**的退出码三态
            #
            # **两者口径不同，混用会得出「22 通过 + 6 + 1 ≠ 29」这种自相矛盾的数字。**
            # 首版简报正是这么写的，被 DC-6 当场抓出。
            #
            # GATES 只做**静态 import**，不执行任何门禁——
            # 执行会造成门禁套件自递归（design_doc_check 自己就在 GATES 里）。
            try:
                import importlib.util
                _spec = importlib.util.spec_from_file_location(
                    "_rag", root / "docs/tenancy/tools/run_all_gates.py")
                _mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                n_gates = len(_mod.GATES)
            except Exception as exc:  # noqa: BLE001
                findings.append(f"DC-6 无法静态读取 run_all_gates.GATES：{exc}")
                n_gates = -1
            # 三态：从设计文档的 gate 标记推导，准确性由 **DC-2 保证**
            #（DC-2 逐个实跑工具并比对声明值），因此是**传递可信**。
            marks = parse_markers(text)
            plain = [m for m in marks if not m[1]]
            tri = {0: 0, 1: 0, 2: 0}
            for _n, _f, exp in plain:
                if exp in tri:
                    tri[exp] += 1
            actual = {
                "specs": len(specs),
                "spec_lines": spec_lines,
                "tenancy_docs": len(ten_docs),
                "tools": len(tools),
                "inc_max": inc_max,
                "inc_rows": inc_rows,
                "rule_ids": rule_ids,
                "gates_total": n_gates,
                "gates_pass": tri[0],
                "gates_gap": tri[1],
                "gates_broken": tri[2],
            }
            for key, val in sorted(declared.items()):
                if key not in actual:
                    findings.append(f"DC-6 简报 fact 标记 {key} 无对应现实口径"
                                    "（无法核对 = 等于没有校验）")
                    print(f"  {RED}✗{RESET} {key:14} 声明 {val}，但门禁不知道该怎么算它")
                    continue
                if actual[key] != int(val):
                    findings.append(
                        f"DC-6 简报过期：{key} 声明 {val}，现实 {actual[key]}")
                    print(f"  {RED}✗{RESET} {key:14} 声明 {val} ≠ 现实 {actual[key]}")
                else:
                    print(f"  {GREEN}✓{RESET} {key:14} {val}")

    # DC-7 §27.5 表格里的关键数字
    # -----------------------------------------------------------
    # 为什么需要（2026-10-01 真实踩到）
    # ---------------------------------
    # §27.5 的人读表格里有 5 处数字已过期，而 DC-3/DC-5 全绿：
    #   · `error-codes.md`「16 个目标占位」→ 现实 26
    #   · `spec_rule_id_check`「13 份规格 316 个 ID」→ 现实 318
    #   · `run_all_gates`「通过 14 · 差距 4 · 不可运行 1」→ 现实 24/6/1
    #   · `spec_citation_check`「44 处引用」→ 现实 132
    #   · 本表自己写「DC-1～DC-5」→ 现实已是 DC-1～DC-6
    #
    # **标记块齐全、表格齐全，读者看到的数字仍然是错的。**
    # 这与 INC-56（决定不建表名门禁）是同一类问题的两面：
    # 那里是提取层比真实问题更窄，这里是**校验层比真实问题更窄**。
    #
    # 修法与 DC-6 同构：表格保持人类可读，另埋一行 HTML 注释，
    # 本检查按现实重算比对。标记前缀 `fact275:` 与简报的 `fact:` 区分。
    #
    # **口径边界（必须说清，否则又是一个「看起来被校验了」的数字）**：
    #   · `gate_all_*` 数的是 **31 次调用**（plain + self-test）的三态，
    #     与 DC-6 的 `gates_*`（**只数 plain** 的 15 次）口径不同，
    #     两者不可互相换算或相加。
    #   · `gate_all_*` 由**文档的 gate 标记**推导，不是实跑出来的；
    #     它的准确性由 **DC-1 保证**（DC-1 逐个实跑工具比对声明值），
    #     因此是**传递可信**。实跑整套门禁要 ~60s，不适合放进每次检查。
    #   · 本文**刻意不给「自检 N/N」和「引用总处数」埋标记**：
    #     前者要 spawn 自己（递归且慢），后者要复制 `spec_citation_check`
    #     的提取逻辑（口径漂移风险）。这两个数字改为指向工具自身输出。
    print(f"\n  {DIM}DC-7 §27.5 事实标记核对{RESET}")
    if doc_path.name != real_design.name:
        print(f"  {DIM}· 跳过：被检文档不是本仓设计文档，DC-7 的跨文件断言不适用{RESET}")
    else:
        if not declared275:
            findings.append("DC-7 §27.5 未埋任何 <!-- fact275:KEY=VALUE --> 标记"
                            "（§27.5 的关键数字无法被机器校验，必然静默过期）")
            print(f"  {RED}✗{RED} 无 fact275 标记")
        else:
            prod = root / "docs/product"
            specs = sorted(prod.glob("W-*.md"))
            # 与 DC-6 同一来源（`count_rule_ids`），**两处各写一份就是 INC-78 的成因**
            rule_ids = count_rule_ids(root)
            errs = count_err_code_rows(root / "docs/tenancy/error-codes.md")
            registry = root / "docs/tenancy/resource-registry.yaml"
            all_marks = parse_markers(text)

            # 套件条目 ← → 文档标记 的**双向覆盖**。
            # --------------------------------------------------
            # DC-3 只要求「每个工具文件至少出现一次」，
            # 因此**某个套件条目漏登记时 DC-3 依然绿**。
            # 实测就是这样漏的：`design_doc_check.py --self-test`
            # 从未在本文登记，而 31 次调用里少一次没有任何机制报警。
            #
            # 首版 DC-7 是靠 `gate_all_pass` 的算术不平**碰巧**发现的
            # （我手写 24、现实 23）。**靠算术不平发现覆盖缺口是运气，不是设计。**
            # 这里把覆盖做成显式判据。
            try:
                import importlib.util as _ilu
                _sp = _ilu.spec_from_file_location(
                    "_rag7", root / "docs/tenancy/tools/run_all_gates.py")
                _mo = _ilu.module_from_spec(_sp)
                _sp.loader.exec_module(_mo)
                need = {_norm_entry(e[0], e[1]) for e in _mo.GATES}
                n_gates = len(_mo.GATES)
            except Exception as exc:  # noqa: BLE001
                findings.append(f"DC-7 无法静态读取 run_all_gates.GATES：{exc}")
                need, n_gates = None, -1
            have = {_norm_entry(n, f) for n, f, _e in all_marks}
            if need is not None:
                missing = sorted(need - have)
                extra = sorted(have - need)
                if missing:
                    findings.append(
                        "DC-7 套件条目未在本文门禁标记中登记（读者看不到它）："
                        + "、".join(missing))
                    for it in missing:
                        print(f"  {RED}✗{RESET} 未登记的套件条目 {it}")
                if extra:
                    findings.append(
                        "DC-7 本文登记了套件里不存在的门禁条目（可能已删除或改名）："
                        + "、".join(extra))
                    for it in extra:
                        print(f"  {RED}✗{RED} 多余的套件条目 {it}")
                if not missing and not extra:
                    print(f"  {GREEN}✓{RESET} {'suite_covered':22} "
                          f"{len(have)}/{n_gates}")

            tri_all = {0: 0, 1: 0, 2: 0}
            for _n, _f, exp in all_marks:
                if exp in tri_all:
                    tri_all[exp] += 1
            actual275 = {
                "err_codes_implemented": errs["implemented"],
                "err_codes_placeholder": errs["placeholder"],
                "rule_ids": rule_ids,
                "registry_lines": (len(registry.read_text(encoding="utf-8").splitlines())
                                   if registry.is_file() else -1),
                "gate_all_total": len(all_marks),
                "gate_all_pass": tri_all[0],
                "gate_all_gap": tri_all[1],
                "gate_all_broken": tri_all[2],
            }
            for key, val in sorted(declared275.items()):
                # `selftest_*` 由 DC-8 核对（它要实跑 `--self-test` 才能取到 N/M，
                # DC-7 手里没有那个数）。**不能因为「本层算不了」就报成
                # 「无对应现实口径」**——那是 DC-7 对自己职责边界的误判，
                # 会把一份完全正确的文档报成过期。
                # 委派是安全的：DC-6/DC-7 与 DC-8 共用同一个「是否本仓文档」守卫，
                # DC-7 跑到这里时，DC-8 一定也会跑。
                if key.startswith("selftest_"):
                    print(f"  {DIM}·{RESET} {key:22} {val}（由 DC-8 核对）")
                    continue
                if key not in actual275:
                    findings.append(f"DC-7 §27.5 标记 {key} 无对应现实口径"
                                    "（无法核对 = 等于没有校验）")
                    print(f"  {RED}✗{RED} {key:22} 声明 {val}，但门禁不知道该怎么算它")
                    continue
                if actual275[key] != int(val):
                    findings.append(
                        f"DC-7 §27.5 过期：{key} 声明 {val}，现实 {actual275[key]}")
                    print(f"  {RED}✗{RED} {key:22} 声明 {val} ≠ 现实 {actual275[key]}")
                else:
                    print(f"  {GREEN}✓{RESET} {key:22} {val}")

    # DC-8 自检能力与自检项数（INC-70）
    # -----------------------------------------------------------
    # 为什么需要（2026-10-02 实测踩到）
    # ----------------------------------
    # DC-7 的双向判据只保证「套件里有的 ⟺ 文档登记的」，
    # **它不回答「有自检能力的工具，是不是真的被套件跑了」**。
    # 实测 `api_contract_check.py` 实现了 9/9 阳性对照，
    # 却只有 plain 条目在 GATES 里——那 9 项从未被任何机器执行过。
    # 这与 INC-69（`run_all_gates --self-test` 曾被漏登记）是同一种缺口：
    # **工具写好了自检，却没有任何东西会去敲它**。
    #
    # 边界必须说清（否则就是 INC-56 那一族：提取层比真实问题窄）
    # -----------------------------------------------------------
    # ① 「支不支持 --self-test」只能**实跑一次**判定，不解析源码——
    #    源码里有 `argparse` 不等于接得住 `--self-test`。
    # ② 取不到 `N/M` 的工具**单独列出**，不静默跳过：
    #    `backfill_migrate` 此前只印一句散文总结，任何按 `N/M` 取数的判据
    #    都会把它悄悄排除在覆盖之外，而报告看上去是全的。
    # ③ 本判据覆盖的是「**实现了自检的工具**」。
    #    对「压根没写自检的工具」，DC-8 只如实列出，不代替业务判断它该不该有。
    print(f"\n  {DIM}DC-8 自检能力覆盖与自检项数{RESET}")
    try:
        import importlib.util as _ilu8
        _sp8 = _ilu8.spec_from_file_location(
            "_rag8", root / "docs/tenancy/tools/run_all_gates.py")
        _mo8 = _ilu8.module_from_spec(_sp8)
        _sp8.loader.exec_module(_mo8)
        in_suite = {(e[0], e[1]) for e in _mo8.GATES}
    except Exception as exc:  # noqa: BLE001
        findings.append(f"DC-8 无法静态读取 run_all_gates.GATES：{exc}")
        print(f"  {RED}✗{RESET} 无法读取 GATES：{exc}")
        in_suite = None

    if in_suite is not None:
        depth = int(os.environ.get(DEPTH_VAR, "0"))
        # 夹具守卫：自检传入的是**同名副本或夹具文档**，此时 `tools_dir` 里躺着的是
        # `g0.py` 之类的桩，它们当然没有 `--self-test`、也没有 `N/M` 输出。
        # 若不在这里跳过，DC-8 会把每个桩都报成「自检输出取不到 N/M」，
        # **把自检里所有「应当通过」的断言全部打翻**。
        # 判据用**文件名**而不是绝对路径（与 DC-6/DC-7 一致，见 run_real 的说明）。
        if doc_path.name != (root / "docs/product/saas-platform-design-20261001.md").name:
            print(f"\n  {DIM}DC-8 自检能力覆盖与自检项数{RESET}")
            print(f"  {DIM}· 跳过：被检文档不是本仓设计文档，"
                  f"DC-8 的跨文件断言不适用{RESET}")
            in_suite = None
    if in_suite is not None:
        depth = int(os.environ.get(DEPTH_VAR, "0"))
        real_counts: dict[str, tuple[int, int]] = {}
        no_self_test: list[str] = []
        unparsed: list[str] = []
        for tool in sorted(on_disk):
            # 递归守卫（INC-71）。DC-1 在深度 ≥1 时跳过了本工具的 self-test 条目，
            # 所以 `(SELF_NAME, "--self-test")` **不在 ran 里**——DC-8 若不判深度，
            # 会把「没被 DC-1 跑过」误读成「需要我跑」，于是自己 spawn 自己。
            if tool == SELF_NAME and depth >= 1:
                print(f"  {DIM}—{RESET} {tool:30} 自检项数由父进程判定（递归守卫）")
                continue
            if (tool, "self-test") in ran:
                _code, out = ran[(tool, "self-test")]
            else:
                # DC-1 没跑过这一条（文档没登记），这里补跑一次——
                # 「文档没登记」正是要抓的缺口，不能因为没登记就不去发现。
                _code, out = run_tool(tools_dir, tool, ["--self-test"])
            # argparse 拒绝该参数 → 工具没有自检能力
            if _code is not None and "unrecognized arguments" in out:
                no_self_test.append(tool)
                continue
            if _code is None:
                unparsed.append(f"{tool}（无法运行）")
                continue
            c = selftest_count(out)
            if c is None:
                unparsed.append(tool)
                continue
            real_counts[tool] = c

        missing_st = sorted(t for t in real_counts
                            if (t, "self-test") not in in_suite)
        for t in missing_st:
            findings.append(
                f"DC-8 {t} 实现了自检（{real_counts[t][0]}/{real_counts[t][1]}）"
                f"却不在门禁套件的 self-test 条目里——没有任何机器会执行它")
            print(f"  {RED}✗{RESET} {t:30} 有自检但套件未跑")
        if not missing_st:
            print(f"  {GREEN}✓{RESET} {'selftest_in_suite':22} "
                  f"{len(real_counts) - len(missing_st)}/{len(real_counts)}")

        # 自检项数：文档登记的 fact275:selftest_* 必须与现实一致
        for tool, (n, m) in sorted(real_counts.items()):
            key = f"selftest_{pathlib.Path(tool).stem}"
            if key not in declared275:
                continue
            if int(declared275[key]) != m:
                findings.append(
                    f"DC-8 {key} 声明 {declared275[key]}，现实 {m}"
                    f"（{tool} 自检 {n}/{m}）")
                print(f"  {RED}✗{RED} {key:22} 声明 {declared275[key]} ≠ 现实 {m}")
            else:
                print(f"  {GREEN}✓{RESET} {key:22} {m}")

        if no_self_test:
            # **升为硬判据（INC-70）**。首版这里只打一行 △ 软标注，
            # 于是「`synthetic_data.py` 压根没写自检」被如实列了出来、
            # 却没有任何东西拦它——**只被列出、不被拦下的缺口等于没有缺口**，
            # 而 §27.3 恰恰写着「每道门禁都有 --self-test」。
            print(f"  {RED}✗{RESET} 无 --self-test 能力的工具："
                  + "、".join(no_self_test))
            for tool in no_self_test:
                findings.append(
                    f"DC-8 {tool} 没有 --self-test 能力：无法证明它抓得住错误，"
                    f"而 §27.3 声明「每道门禁都有 --self-test」")
        if unparsed:
            print(f"  {YELLOW}△{RESET} 自检输出里取不到 N/M，**未计入覆盖**："
                  + "、".join(unparsed))
            findings.append(
                "DC-8 有工具的自检输出里取不到可解析的 N/M，其自检项数"
                "无法被机器核对：" + "、".join(unparsed))

    # DC-4 文档卫生
    print(f"\n  {DIM}DC-4 文档卫生{RESET}")
    # **链接基准**：自检传入的可能是**同名副本**（见 run_real 的说明），
    # 副本的 `parent` 是临时目录，于是全部相对链接都会被判为不可达——
    # 实测 57 条纯误报。所以**同名副本一律按真实文档的目录解析**。
    # 夹具文档 `d.md` 名字不同，仍用自己的 `parent`。
    link_base = doc_path.parent
    if doc_path.name == (root / "docs/product/saas-platform-design-20261001.md").name:
        link_base = root / "docs/product"
    bad_links = []
    for link in set(LINK.findall(text)):
        if not (link_base / link).exists():
            bad_links.append(link)
    if bad_links:
        for b in sorted(bad_links):
            findings.append(f"DC-4 相对链接不可达：{b}")
        print(f"  {RED}✗{RESET} 不可达链接 {len(bad_links)} 个")
    else:
        print(f"  {GREEN}✓{RESET} 相对链接全部可达")
    n_moji = text.count(MOJIBAKE)
    if n_moji:
        findings.append(f"DC-4 文档含 {n_moji} 个乱码字符（U+FFFD）")
        print(f"  {RED}✗{RESET} 乱码字符 {n_moji} 个")
    else:
        print(f"  {GREEN}✓{RESET} 无乱码字符")

    print("=" * 76)
    if findings:
        for f in findings:
            print(f"  {RED}✗{RESET} {f}")
        print(f"{RED}文档与现实不一致：{len(findings)} 项{RESET}")
        return EXIT_FINDINGS
    print(f"{GREEN}文档与现实一致{RESET}")
    return EXIT_OK


# ---------------------------------------------------------------------------

def self_test() -> int:
    print("design_doc_check 自检：证明它抓得住文档过期")
    print("=" * 68)
    import shutil
    import tempfile
    # **必须是进程独占目录，不能用固定路径。**
    # 真实踩到：`/tmp/ddoc_selftest` 是硬编码的，而本自检会调用 `main()`，
    # `main()` 的 DC-1 会 spawn `design_doc_check.py --self-test` 子进程，
    # **那个子进程也跑一遍自检**，开头 `rmtree(tmp)` 把父进程的夹具目录删掉，
    # 父进程跑到末尾再 `rmtree` 就 FileNotFoundError 崩掉。
    # 用 `mkdtemp` 让每个进程拿自己的目录，冲突从根上消失。
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ddoc_selftest_"))
    tools = tmp / "tools"
    tools.mkdir(parents=True)

    (tools / "g0.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    (tools / "g1.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    (tools / "g2.py").write_text("import sys\nsys.exit(2)\n", encoding="utf-8")

    doc = tmp / "d.md"
    (tmp / "sub").mkdir()
    (tmp / "sub" / "ok.md").write_text("ok", encoding="utf-8")

    results: list[tuple[str, bool]] = []

    def run(doc_text: str) -> int:
        doc.write_text(doc_text, encoding="utf-8")
        argv = sys.argv
        sys.argv = ["design_doc_check.py", "--doc", str(doc), "--tools-dir", str(tools)]
        import contextlib
        import io
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = main()
        finally:
            sys.argv = argv
        return rc

    # 1) 退出码写错 → 必须被抓
    rc = run("<!-- gate:g1.py =0 -->\n[链接](sub/ok.md)\n#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n| `g0.py` | a |\n| `g1.py` | b |\n| `g2.py` | c |\n")
    results.append(("文档把 exit=1 写成 0 → 被抓", rc == EXIT_FINDINGS))

    # 2) 写对 → 通过
    rc = run("<!-- gate:g1.py =1 -->\n<!-- gate:g0.py =0 -->\n"
             "<!-- gate:g2.py =2 -->\n[链接](sub/ok.md)\n#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n| `g0.py` | a |\n| `g1.py` | b |\n| `g2.py` | c |\n")
    results.append(("退出码全部写对 → 通过", rc == EXIT_OK))

    # 3) 链接断裂 → 被抓
    rc = run("<!-- gate:g0.py =0 -->\n[断链](sub/nope.md)\n#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n| `g0.py` | a |\n| `g1.py` | b |\n| `g2.py` | c |\n")
    results.append(("相对链接断裂 → 被抓", rc == EXIT_FINDINGS))

    # 4) 未登记的工具 → 被抓（新增工具被遗忘）
    rc = run("<!-- gate:g0.py =0 -->\n[链接](sub/ok.md)\n#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n| `g0.py` | a |\n| `g1.py` | b |\n| `g2.py` | c |\n")
    results.append(("存在未登记工具 → 被抓", rc == EXIT_FINDINGS))

    # 5) 全部登记后通过
    rc = run("<!-- gate:g0.py =0 -->\n<!-- gate:g1.py =1 -->\n"
             "<!-- gate:g2.py =2 -->\n[链接](sub/ok.md)\n#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n| `g0.py` | a |\n| `g1.py` | b |\n| `g2.py` | c |\n")
    results.append(("全部登记且一致 → 通过", rc == EXIT_OK))

    # 5b) ★ `run_all_gates.py` **不得享有豁免**（INC-69）
    #     这条夹具点名它，而不是用一个泛化的 `g3.py`。理由：EXEMPT 曾写着
    #     `{"run_all_gates.py"}`，理由是「状态由其自检保证」——而那句保证
    #     当时根本没有任何机器去核对。加一条泛化夹具（任意未登记工具被抓）
    #     **挡不住把豁免加回去**：豁免里的工具本来就不在文档里，
    #     泛化夹具照样会因 g3 未登记而报红，两条判据互相掩盖。
    #     只有点名这个具体文件，才能在豁免被加回去时立刻报红。
    (tools / "run_all_gates.py").write_text("import sys\nsys.exit(0)\n",
                                            encoding="utf-8")
    rc = run("<!-- gate:g0.py =0 -->\n<!-- gate:g1.py =1 -->\n"
             "<!-- gate:g2.py =2 -->\n[链接](sub/ok.md)\n"
             "#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n"
             "| `g0.py` | a |\n| `g1.py` | b |\n| `g2.py` | c |\n")
    results.append(("★ run_all_gates.py 未被豁免：漏登记照样被抓", rc == EXIT_FINDINGS))
    (tools / "run_all_gates.py").unlink()

    # 6) 无标记 → 被抓（文档根本没被纳入校验）
    rc = run("只是一段没有任何标记的文档\n")
    results.append(("无 gate 标记 → 被抓", rc == EXIT_FINDINGS))

    # 7) 乱码 → 被抓
    rc = run("<!-- gate:g0.py =0 -->\n<!-- gate:g1.py =1 -->\n"
             "<!-- gate:g2.py =2 -->\n[链接](sub/ok.md)\n#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n| `g0.py` | a |\n| `g1.py` | b |\n| `g2.py` | c |\n乱\ufffd码\n")
    results.append(("含乱码 → 被抓", rc == EXIT_FINDINGS))

    # 8) 引用不存在的工具 → 被抓
    rc = run("<!-- gate:ghost.py =0 -->\n<!-- gate:g0.py =0 -->\n"
             "<!-- gate:g1.py =1 -->\n<!-- gate:g2.py =2 -->\n[链接](sub/ok.md)\n#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n| `g0.py` | a |\n| `g1.py` | b |\n| `g2.py` | c |\n")
    results.append(("引用不存在的工具 → 被抓", rc == EXIT_FINDINGS))

    # 9) DC-5：§27.3 表格漏登记工具 → 必须被抓
    #    这是 2026-10-01 真实踩到的：标记块齐全（DC-3 全绿），
    #    但 §27.3 人读表格里 5 个工具一个都没有，读者完全看不到。
    rc = run("<!-- gate:g0.py =0 -->\n<!-- gate:g1.py =1 -->\n"
             "<!-- gate:g2.py =2 -->\n[链接](sub/ok.md)\n"
             "#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n"
             "| `g0.py` | a |\n| `g1.py` | b |\n")
    results.append(("★ DC-5 §27.3 表格漏登记工具 → 被抓", rc == EXIT_FINDINGS))

    # 10) DC-5 反向：§27.3 完全缺失该章节 → 不得静默通过
    rc = run("<!-- gate:g0.py =0 -->\n<!-- gate:g1.py =1 -->\n"
             "<!-- gate:g2.py =2 -->\n[链接](sub/ok.md)\n")
    results.append(("★ DC-5 §27.3 章节缺失 → 被抓", rc == EXIT_FINDINGS))

    # 11) DC-5 字符类必须含数字
    #     真实踩到：写成 `[a-z_]+\.py` 时，夹具工具名 `g0.py` 里的数字不在字符类内，
    #     in_table 恒为空 → DC-5 对**所有**夹具恒报「未列」，
    #     两个「应通过」用例全挂，而两个「应报红」用例却「因为错误原因而通过」——
    #     变异测试自证不了自己。本例锁死数字工具名可被识别。
    rc = run("<!-- gate:g0.py =0 -->\n<!-- gate:g1.py =1 -->\n"
             "<!-- gate:g2.py =2 -->\n[链接](sub/ok.md)\n"
             "#### 27.3 文档包对应关系\n\n| 工具 | 作用 |\n|---|---|\n"
             "| `g0.py` | a |\n| `g1.py` | b |\n| `g2.py` | c |\n")
    results.append(("★ DC-5 数字工具名 g0.py 可被识别 → 通过", rc == EXIT_OK))

    # DC-6 专项：简报过期必须能被抓到。
    # 变异真实简报的 fact 标记（tools 18 → 0），DC-6 应当报红。
    # **不能只测「标记存在时通过」**——那样的门禁不会过期。
    root = pathlib.Path(__file__).resolve().parents[3]
    real_design = root / "docs/product/saas-platform-design-20261001.md"
    brief = root / "docs/tenancy/设计阶段收口简报_20261001.md"
    real_text = real_design.read_text(encoding="utf-8") if real_design.is_file() else ""

    # **自检专用副本**：真实设计文档与收口简报**全程只读**。
    # 副本沿用**同名文件**，这样 DC-6/DC-7 的「是否本仓文档」守卫（按文件名判定）
    # 依然成立；而夹具 `d.md` 名字不同，仍会被正确跳过。
    # 详见 run_real() 上方的说明：改真实文件曾导致并发编辑被覆盖、
    # 以及自检被取消后真实文档留在变异态。
    hermetic_doc = tmp / real_design.name
    hermetic_doc.write_text(real_text, encoding="utf-8")
    hermetic_brief = tmp / brief.name
    if brief.is_file():
        hermetic_brief.write_text(brief.read_text(encoding="utf-8"), encoding="utf-8")

    saved_argv = sys.argv

    def run_real() -> int:
        """在**本进程内**跑一次完整核对。

        为什么必须把 DEPTH_VAR 抬起来
        ------------------------------
        `main()` 的 DC-1 会**逐个实跑**文档里登记的每个门禁。
        本自检自身也登记在文档里（`design_doc_check.py --self-test`），
        所以不抬深度时，每次 `run_real()` 都会 spawn 一个
        **跑满全流程的自检子进程**，而那个子进程内部又会再跑若干次
        `run_real()`——实测单次 `run_real()` 因此膨胀到 ~2 分钟，
        四次就够跑八分钟。

        `DEPTH_VAR >= 1` 时 DC-1 会跳过 `design_doc_check` 自身的标记
        （见 DC-1 的 `name == SELF_NAME` 分支），子进程就此消失。
        **代价要写清楚**：这意味着自检里的 `run_real()` **不实跑本工具自己**。
        该跳过的正是「自己实跑自己」这条无意义的路径——
        它的结论只能由外层门禁套件（`run_all_gates`）来保证。

        为什么用**副本**而不是真实设计文档（2026-10-01 真实踩到）
        ------------------------------------------------------
        最初这几条用例直接改写真实设计文档、跑完再改回去。两次真实事故：
          ① 自检运行期间**并发编辑**真实文档 → 编辑被 `finally` 的还原**静默覆盖**
          ② 自检**被取消/被杀** → `finally` 没执行，真实文档**留在变异态**
             （实测留下一个缺失的门禁标记，DC-7 立刻报 `gate_all_total 30 ≠ 31`）
        两者的共同根因是：**自检在改一个别人也在用的真实文件**。
        现在改为**复制到临时目录再改**，真实文档全程只读——
        进程被 kill 也只会留下一个 `/tmp` 下的碎片。
        """
        argv = sys.argv
        sys.argv = ["design_doc_check.py", "--doc", str(hermetic_doc),
                    "--brief", str(hermetic_brief)]
        saved_depth = os.environ.get(DEPTH_VAR, "0")
        os.environ[DEPTH_VAR] = str(int(saved_depth) + 1)
        import contextlib
        import io
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc0 = main()
        finally:
            sys.argv = argv
            os.environ[DEPTH_VAR] = saved_depth
        return rc0

    dc6_ok = False
    dc6_back = False
    if brief.is_file() and real_text:
        # **只改副本**（见 run_real 的说明：改真实文件会导致并发编辑被覆盖、
        # 进程被取消时留下变异态）。副本沿用**同名**才能通过 DC-6 的文件名守卫。
        saved_b = brief.read_text(encoding="utf-8")
        hermetic_brief.write_text(
            saved_b.replace("<!-- fact:tools=", "<!-- fact:tools=0 -->tools="),
            encoding="utf-8")
        try:
            dc6_ok = run_real() == EXIT_FINDINGS
        finally:
            hermetic_brief.write_text(saved_b, encoding="utf-8")
        # 反向：恢复后必须回到通过（证明上一步不是恒红）
        dc6_back = run_real() == EXIT_OK
    results.append(("★ DC-6 简报 fact 标记过期 → 报红", dc6_ok))
    results.append(("★ DC-6 简报恢复后回到通过（非恒红）", dc6_back))

    # `inc_rows` 专项（INC-85）：**删掉发现表里的一行，`inc_max` 纹丝不动**，
    # 只有 `inc_rows` 会报红。这条用例证明的不是「标记能过期」，
    # 而是「**这两个口径回答的是不同的问题，缺了其中一个就抓不到一类缺口**」。
    # 只测 `fact:tools` 那种通用变异，对 `inc_rows` 是**零证明力**。
    dc6_rows_ok = dc6_rows_back = False
    if brief.is_file() and real_text:
        saved_b = brief.read_text(encoding="utf-8")
        m = re.search(r"<!-- fact:inc_rows=(\d+) -->", saved_b)
        if m:
            hermetic_brief.write_text(
                saved_b.replace(m.group(0), f"<!-- fact:inc_rows={int(m.group(1)) + 1} -->"),
                encoding="utf-8")
            try:
                dc6_rows_ok = run_real() == EXIT_FINDINGS
            finally:
                hermetic_brief.write_text(saved_b, encoding="utf-8")
            dc6_rows_back = run_real() == EXIT_OK
    results.append(("★ DC-6 `inc_rows`（登记条数，与 `inc_max` 不同口径）→ 报红",
                    dc6_rows_ok))
    results.append(("★ DC-6 `inc_rows` 恢复后回到通过（非恒红）", dc6_rows_back))
    # DC-6 的**适用边界**由本文件既有的「全部登记且一致 → 通过」夹具用例承担：
    # 该夹具的退出码全对，若 DC-6 在夹具上误报，那条用例会变红。
    # **不要为此再写一条 `or True` 的空转断言**——那等于把门禁的
    # 「看起来测了」当成「测了」，是本仓库反复出现的假绿形态。

    # DC-7 专项：§27.5 表格里的数字过期必须能被抓到。
    # 与 DC-6 同构的两条（报红 + 恢复后回到通过）之外，
    # 额外加一条**「标记缺失」**用例——DC-7 若在无标记时静默通过，
    # 它就是一道只在数字写对时才存在的检查，而数字恰恰是它该抓的东西。
    dc7_stale = False
    dc7_back = False
    dc7_absent = False
    dc7_uncovered = False
    if real_text and FACT275.search(real_text):
        saved_d = real_design.read_text(encoding="utf-8")
        hermetic_doc.write_text(
            saved_d.replace("<!-- fact275:rule_ids=",
                            "<!-- fact275:rule_ids=0 -->rule_ids=", 1),
            encoding="utf-8")
        dc7_stale = run_real() == EXIT_FINDINGS
        hermetic_doc.write_text(saved_d, encoding="utf-8")
        dc7_back = run_real() == EXIT_OK
        # 去掉全部标记 → 必须报红（不得静默通过）
        hermetic_doc.write_text(FACT275.sub("", saved_d), encoding="utf-8")
        dc7_absent = run_real() == EXIT_FINDINGS
        hermetic_doc.write_text(saved_d, encoding="utf-8")
        # 删掉**一个** gate 标记 → 套件覆盖必须报红。
        # 这条对应真实缺口：`design_doc_check.py --self-test` 长期漏登记，
        # 而 DC-3 全绿（工具文件本身已登记）。只测「标记齐全时通过」
        # 的门禁，抓不到这一类——**覆盖判据必须自己证明能发现缺失**。
        hermetic_doc.write_text(
            saved_d.replace("<!-- gate:lease_guard_check.py --self-test=0 -->\n", "", 1),
            encoding="utf-8")
        dc7_uncovered = run_real() == EXIT_FINDINGS
        hermetic_doc.write_text(saved_d, encoding="utf-8")
    results.append(("★ DC-7 §27.5 标记过期 → 报红", dc7_stale))
    results.append(("★ DC-7 §27.5 标记全部缺失 → 报红（不静默通过）", dc7_absent))
    results.append(("★ DC-7 单个套件条目漏登记 → 报红（覆盖判据能发现缺失）",
                    dc7_uncovered))
    results.append(("★ DC-7 §27.5 恢复后回到通过（非恒红）", dc7_back))

    # ---- INC-78：证明 `count_rule_ids` 真的在**引用**，不是在复述 ----
    # --------------------------------------------------------------
    # 「两处各写一份提取逻辑」正是 INC-78 的成因：真判据从一种形态扩到三种，
    # 两份副本毫无察觉，DC-6/DC-7 齐声拿 343 去核对 444。
    # 只测「数字对得上」**证明不了没有副本**——今天碰巧一致，
    # 明天真判据一改就又是一次静默漂移。
    # 所以要测**行为本身**：旧逻辑（只认两段窄形态、不过滤外部）的两个特征
    # 必须在引用后消失。
    # 探针刻意构造成**旧逻辑与新判据答案必然不同**的形状。
    # ⚠ 第一版探针只有 2 条两段形态 + 2 条外部首列，结果**旧逻辑也算出 2**，
    #   于是「退回副本」的变异照样全绿——**夹具是装饰性的，等于没有夹具**。
    #   判据对自己的病无效时，唯一的证据是**变异测试**，不是夹具数量。
    # 现在外部首列 3 条、旧逻辑只认两段 ⇒ 旧=3 / 新=2，**两侧不可能相等**。
    import importlib.util as _ilu2
    probe_root = pathlib.Path(tmp) / "probe_root"
    (probe_root / "docs/product").mkdir(parents=True, exist_ok=True)
    (probe_root / "docs/tenancy/tools").mkdir(parents=True, exist_ok=True)
    shutil.copy2(pathlib.Path(__file__).resolve().parent / "spec_rule_id_check.py",
                 probe_root / "docs/tenancy/tools/spec_rule_id_check.py")
    probe_text = ("| AC-02-1 | E1 |\n| AC-02-2 | E2 |\n"
                  "| **CR-1** | 门禁判据镜像 |\n| **BQ-02** | 外部待裁决项 |\n"
                  "| **CR-2** | 门禁判据镜像 |\n")
    (probe_root / "docs/product/W-99-探针_20261001.md").write_text(
        probe_text, encoding="utf-8")
    probed = count_rule_ids(probe_root)
    # 旧副本逻辑在这段文本上的答案：只认 `| XX-N |` 且不过滤 ⇒ 3 条
    old_copy_count = sum(
        1 for line in probe_text.splitlines()
        if re.match(r"^\|\s*\*{0,2}([A-Z]{2,4})-(\d{1,2})\*{0,2}\s*\|", line))
    results.append(("★ INC-78 count_rule_ids 跟随真判据而非旧副本"
                    f"（新判据 {probed} ≠ 旧副本 {old_copy_count}）",
                    probed == 2 and old_copy_count != probed,
                    f"探针算成 {probed}（旧副本 {old_copy_count}），期望 2 且两者不等"))
    # 反向：副本若与真判据脱钩，这一条在真判据改动后立刻失败
    _sp3 = _ilu2.spec_from_file_location(
        "_sric_probe", probe_root / "docs/tenancy/tools/spec_rule_id_check.py")
    _m3 = _ilu2.module_from_spec(_sp3)
    _sp3.loader.exec_module(_m3)
    results.append(("★ INC-78 探针口径与 spec_rule_id_check 同一函数"
                    "（rule_defs 而非 collect_defs）",
                    probed == len(_m3.rule_defs(probe_text))
                    and probed != len(_m3.collect_defs(probe_text)),
                    f"两侧口径不一致：{probed} / "
                    f"{len(_m3.rule_defs(probe_text))} / "
                    f"{len(_m3.collect_defs(probe_text))}"))
    sys.argv = saved_argv

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    ok = 0
    for item in results:
        # 兼容 (label, ok) 与 (label, ok, why)：带原因的三元组在失败时能直接
        # 指出是哪个数算错，而只印「FAIL」等于让人自己回去猜。
        label, good = item[0], item[1]
        why = item[2] if len(item) > 2 else ""
        print(f"  [{'PASS' if good else 'FAIL'}] {label}"
              + (f"  —— {why}" if (why and not good) else ""))
        ok += bool(good)
    print("=" * 68)
    if ok == len(results):
        print(f"{GREEN}自检通过：{ok}/{len(results)}{RESET}")
        return EXIT_OK
    print(f"{RED}自检失败：{ok}/{len(results)}{RESET}")
    return EXIT_FINDINGS


if __name__ == "__main__":
    raise SystemExit(main())
