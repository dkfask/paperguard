#!/usr/bin/env python3
"""规格代码引用核对：规格里写的「文件:行号」必须真的指向所说的东西。

背景（INC-26）
--------------
工作包规格（W-01～W-05）大量引用代码位置取证，格式形如
`` `teacher.py:206` ``、`` `backend/app/auth.py:43` ``。

**这些引用从未被机器核对过。** 2026-10-01 首次核对就发现两处错误：

| 规格声称 | 实际内容 |
|---|---|
| `teacher.py:204` 是 `teacher_id` 赋值 | 实际是 `"display_name": display_name` |
| `teacher.py:205` 是 `must_change_password` | 实际是 `"status": "active"` |

真实行号是 206 与 207。**规格里的错误引用比没有引用更危险**——
实施者会照着错误行号去改代码，改到隔壁字段上，
而且**文档看起来是有据可依的**，比「没写」更难被质疑。

为什么值得做这道门禁
--------------------
规格数量在增长（W-01～W-05），引用只会越来越多。
人工核对不可持续——本轮就是人工发现的，下一次未必有人记得去看。
**引用会过期，但过期的引用不会自己报错。**

能查什么、不能查什么
--------------------
**能查**（本门禁范围）：
- 引用的文件是否存在
- 行号是否在文件范围内
- 该行是否**包含规格在旁边声称的关键字**（如 `` `teacher.py:206` `` 旁写
  `teacher_id` ⇒ 第 206 行必须含 `teacher_id`）

**不能查**（如实标注，不假装覆盖）：
- 语义是否正确（行号对、关键字在，但规格对它的**解释**错了）
- 跨文件的调用关系是否仍然成立
- 被引用代码的**行为**是否与规格描述一致

因此本门禁的定位是**行号与关键字的机械核对**，
它是**必要不充分**的：过了不代表规格对，只代表「没有低级引用错误」。

退出码
------
    0 = 全部引用可解析且关键字命中
    1 = 存在失效引用或关键字不匹配（**差距真实且已量化**）
    2 = 工具不可运行（规格目录不存在、扫描到 0 份规格）
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

REPO = pathlib.Path(__file__).resolve().parents[3]
PRODUCT = REPO / "docs/product"
BACKEND = REPO / "backend/app"

# 引用格式：`路径.py:行号` 或 `文件.py:行号`
REF = re.compile(r"`([A-Za-z0-9_/]+\.py):(\d+)(?:-(\d+))?`")
# 在引用附近出现的标识符，作为关键字线索。
#
# 提取规则：反引号片段中**只要含有**标识符即算，
# 而非整段必须就是标识符。
# 原因（2026-10-01 实测）：规格常写
# `` `organization_context: dict[str, str]` `` ——含类型注解，
# 整串匹配会漏掉真正的主标识符 `organization_context`，
# 转而只抓到后面反引号里的 kind 枚举（`candidate` 等），
# 而那些**根本不在被引用的那一行**。
CODE_SPAN = re.compile(r"`([^`\n]+)`")
IDENT = re.compile(r"[a-z_][a-z0-9_]{3,}")


def index_sources() -> dict[str, pathlib.Path]:
    """建立 basename → 真实路径 的索引。

    basename 可能重名（如两个 api/xxx.py），此时**不索引**——
    「猜一个」比「不查」更危险：会核对到错误的文件却报绿。
    """
    idx: dict[str, list[pathlib.Path]] = {}
    for p in BACKEND.rglob("*.py"):
        idx.setdefault(p.name, []).append(p)
    return {k: v[0] for k, v in idx.items() if len(v) == 1}


def check_ref(spec_name: str, raw: str, sources: dict,
              keywords: list[str]) -> tuple[str, str]:
    """核对单条引用。返回 (状态, 说明)，状态 ∈ ok/missing/line/keyword。"""
    m = REF.search(raw)
    if not m:
        return "ok", ""
    fname, start, end = m.group(1), int(m.group(2)), m.group(3)
    base = fname.rsplit("/", 1)[-1]
    # 优先按**全路径**定位：basename 会重名（backend/app/auth.py 与
    # backend/app/api/auth.py 同名），此时按 basename 索引会直接放弃核对。
    # 规格写全路径时信息是充分的，没理由不用。
    if "/" in fname:
        direct = REPO / fname
        if direct.is_file():
            path = direct
        else:
            path = sources.get(base)
    else:
        path = sources.get(base)
    if path is None:
        return "missing", f"文件不存在或 basename 重名：{fname}"
    lines = path.read_text(encoding="utf-8").splitlines()
    hi = int(end) if end else start
    if start < 1 or hi > len(lines):
        return "line", (f"{fname}:{start}{'-' + end if end else ''} "
                        f"超出文件范围（共 {len(lines)} 行）")
    # 收集相邻标识符作为关键字线索。
    #
    # 三类必须排除（2026-10-01 实测修正）：
    #  1. 与文件名同形的标识符——`` `cli/migrate.py:19-27` `` 里的 "migrate"
    #     是**文件名**而非该行代码的标识符
    #  2. 路径成分——`` `backend/app/db.py:148` `` 里的 "backend"/"app"
    #  3. 以 .py 结尾的片段
    # 把它们当关键字会让**正确的引用恒判失效**。
    stem = REF.search(raw).group(1).rsplit("/", 1)[-1].rsplit(".", 1)[0]
    path_parts = {p for p in REF.search(raw).group(1).split("/")[:-1]}
    keywords = [k for k in keywords
                if k != stem and k not in path_parts
                and not k.endswith(".py")]
    # 关键字搜索范围：**引用行本身优先**，其次前后各 1 行。
    #
    # 为什么不是更大窗口（2026-10-01 二次修正）
    # -------------------------------------------
    # 首版只看首行 → 3 处**正确引用**被误报（关键字在表体内/下一行）；
    # 改 3 行窗口 → 变异自检立刻抓到**真实错行被放过**
    #（`mod.py:4` 是 `x = 1`，但 3 行外有 `teacher_id`，仍被判 ok）。
    #
    # **3 行窗口让门禁失去意义**——它抓不到 INC-26 那类错行，
    # 而 INC-26 正是本门禁要抓的东西。
    #
    # 最终判据：先看引用行本身；不中再看前后各 1 行。
    # 收紧到 ±1 行后，`db.py:254`（表体内列在后续行）仍会被误报——
    # 但那属于**「引用行指向表定义起点」这种可解释的情形**，
    # 正确处置是**修正规格的措辞**（写 `db.py:254-258` 明确范围），
    # 而不是把窗口放大到失去检出能力。
    seg_self = lines[start - 1] if start - 1 < len(lines) else ""
    for kw in keywords:
        if kw in seg_self:
            return "ok", ""
    lo = max(0, start - 2)
    seg_near = "\n".join(lines[lo:hi + 1])
    for kw in keywords:
        if kw in seg_near:
            return "ok", ""
    if keywords:
        return "keyword", (f"{fname}:{start} 及其前后各 1 行均不含 "
                           f"相邻标识符 {keywords[:3]}")
    return "ok", ""


def collect(text: str) -> list[tuple[str, str, list[str]]]:
    """产出 (整行原文, 引用, 相邻标识符)。

    **按整行收集，不按表格单元格**（2026-10-01 二次修正）
    ------------------------------------------------
    首版先切表格单元格、再扫表格外的行，导致 W-01 的 10 处引用只抓到 2 处、
    W-02 的 36 处只抓到 21 处——**门禁只核对了六成引用却报绿**，
    这正是本仓库反复出现的「静默假绿」形态：看起来在查，实际漏查。

    整行收集会引入少量噪音（同行里与本引用无关的标识符），
    但**漏查比噪音危险得多**。噪音只会让关键字更容易命中（偏松），
    而漏查是直接不核对。
    """
    out = []
    for line in text.splitlines():
        if not REF.search(line):
            continue
        kws = [k for span in CODE_SPAN.findall(line)
               for k in IDENT.findall(span)]
        out.append((line, REF.search(line).group(0), kws))
    return out


def verdict(n_specs: int, problems: list[str], err: str) -> tuple[int, str]:
    if err:
        return EXIT_TOOL_BROKEN, err
    if n_specs == 0:
        return EXIT_TOOL_BROKEN, "未扫描到任何规格文件，本项无数据可核对"
    if problems:
        return EXIT_FINDINGS, f"未通过：{len(problems)} 处引用失效"
    return EXIT_OK, f"通过：{n_specs} 份规格的引用均可解析"


def self_test() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "app"
        src.mkdir()
        f = src / "mod.py"
        f.write_text(
            "def f():\n"
            "    \"\"\"Create or upgrade the database.\"\"\"\n"
            "    store = Store(db_path)\n"
            "    x = 1\n"
            "teacher_id = 2\n"
            "must_change_password = 3\n", encoding="utf-8")
        sources = {"mod.py": f}
        cases = [
            ("关键字命中 → ok", check_ref("s", "`mod.py:6` `teacher_id`",
                                         sources, ["teacher_id"])[0], "ok"),
            ("行号越界 → line", check_ref("s", "`mod.py:99` `teacher_id`",
                                          sources, ["teacher_id"])[0], "line"),
            ("文件不存在 → missing", check_ref("s", "`nope.py:1` `x`",
                                               sources, ["x"])[0], "missing"),
            # 真实错行（INC-26 原状）：引 `mod.py:1` 说 teacher_id，
            # 实际第 1 行是 `def f():`，teacher_id 在第 5 行——已远出 ±1 窗口
            ("★ 真实错行仍被抓（INC-26 原状）",
             check_ref("s", "`mod.py:1` `teacher_id`", sources,
                       ["teacher_id"])[0], "keyword"),
            # 关键字在**下一行**（migrate.py:19 的 docstring 场景）
            ("★ 关键字在下一行仍算命中",
             check_ref("s", "`mod.py:2` `store`", sources, ["store"])[0], "ok"),
            # 关键字在 3 行外必须**不再**命中——否则门禁失去检出能力
            ("★ 关键字在 3 行外不再算命中",
             check_ref("s", "`mod.py:4` `must_change_password`", sources,
                       ["must_change_password"])[0], "keyword"),
            # 写明范围的引用（`x-y`）应覆盖整个区间
            ("★ 区间引用覆盖区内关键字",
             check_ref("s", "`mod.py:4-6` `must_change_password`", sources,
                       ["must_change_password"])[0], "ok"),
            ("无关键字线索 → ok", check_ref("s", "`mod.py:1`", sources, [])[0], "ok"),
        ]
    # 覆盖面闸
    cases.append(("★ 0 份规格 → 2", verdict(0, [], "")[0], EXIT_TOOL_BROKEN))
    ok = 0
    for label, got, want in cases:
        if got == want:
            ok += 1
            print(f"  {GREEN}✓{RESET} {label}")
        else:
            print(f"  {RED}✗{RESET} {label}  实际 {got}，期望 {want}")
    print(f"\n自检 {ok}/{len(cases)}")
    return EXIT_OK if ok == len(cases) else EXIT_TOOL_BROKEN


def main() -> int:
    ap = argparse.ArgumentParser(description="规格代码引用核对")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        print(f"{CYAN}spec_citation_check 自检{RESET}")
        print("=" * 64)
        return self_test()

    if not PRODUCT.is_dir():
        print(f"{RED}规格目录不存在：{PRODUCT}{RESET}")
        return EXIT_TOOL_BROKEN
    specs = sorted(PRODUCT.glob("W-*.md"))
    if not specs:
        print(f"{RED}未找到任何 W-*.md 规格{RESET}")
        return EXIT_TOOL_BROKEN
    sources = index_sources()
    if not sources:
        print(f"{RED}后端源码索引为空：{BACKEND}{RESET}")
        return EXIT_TOOL_BROKEN

    print(f"{CYAN}规格代码引用核对{RESET}  {DIM}W-01～W-05 的 file:line 取证{RESET}")
    print(f"{DIM}能查行号与关键字；**不能**查语义与调用关系{RESET}")
    print(f"{DIM}规格 {len(specs)} 份 · 可索引源码 {len(sources)} 个文件{RESET}\n")

    problems: list[str] = []
    total = 0
    for spec in specs:
        refs = collect(spec.read_text(encoding="utf-8"))
        if not refs:
            continue
        bad = 0
        for _cell, ref, kws in refs:
            total += 1
            state, msg = check_ref(spec.name, ref, sources, kws)
            if state != "ok":
                bad += 1
                problems.append(f"{spec.name}: {ref} → {msg}")
        mark = f"{GREEN}✓{RESET}" if not bad else f"{RED}✗{RESET}"
        print(f"  {mark} {spec.name:<46} {DIM}{len(refs)} 处引用"
              f"{'' if not bad else f'，{bad} 处失效'}{RESET}")

    print()
    for p in problems:
        print(f"  {RED}·{RESET} {p}")
    if problems:
        print()
    code, msg = verdict(len(specs), problems, "")
    print(msg)
    if code == EXIT_FINDINGS:
        print(f"{DIM}引用错行号会让实施者改到隔壁字段——"
              f"比没有引用更难被质疑。{RESET}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
