"""spec_xref_check：规格文档的 `§N` 交叉引用是否指向真实存在的章节。

为什么需要它（INC-98）
----------------------
`spec_citation_check` 只核对 `` `file.py:N` `` 形式的**代码行引用**，
`spec_rule_id_check` 只核对**规则 ID**。
**没有任何判据看章节号**——于是「见 §3.3 只读诊断」这种指向空气的写法畅通无阻。

首次运行（2026-10-03）立刻找到 2 处真实断链：
  · W-01 §2.4 写「见 §3.3 只读诊断」，而该文件 §3 用的是
    `T-01.1 / T-01.2 / T-01.3` 命名，**根本没有 §3.3**（U-09 诊断在 **T-01.3**）
  · W-07 写「安全是隐式的（见 §5.3）」，而它讲这件事的是 **§4.3**

★ 判据设计上的两个要点（都是踩过的坑）
--------------------------------------
**① 跨规格引用必须被认出来。** 首版正则要求「见」后紧跟「§」，
于是 `见 W-02 §3.3` 完全不被匹配——而那是**合法**引用。
漏掉它意味着「查了但没查全」，本工具就会变成
**「0 条 == 全部覆盖」的空洞通过**。
现在显式识别 `W-NN` 前缀，并把**总设计**的章节计入所有规格的合法目标
（W-08 §14.2「请求生命周期」、W-11 §16.4「退款与冲正」即属此类）。

**② 扫到 0 处引用必须报红（防空转）。** 本工具的「通过」含义是
「所有章节引用都能落地」。若正则因文档格式变化而失配，
它会扫到 0 条、报 0 断链、**然后宣布通过**——
而真相是「一条都没查」。**这正是 INC-92「0 规则 ID 判绿」的同族。**
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
DOCS = REPO / "docs/product"
DESIGN = DOCS / "saas-platform-design-20261001.md"

EXIT_OK, EXIT_FINDINGS, EXIT_TOOL_BROKEN = 0, 1, 2

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# 标题行里的小节号：数字层级（3 / 3.1 / 14.2）或 T-01.3 这类命名
SEC_RE = re.compile(r"(?m)^#{2,4}\s*((?:\d+(?:\.\d+)*)|(?:[A-Z]{1,3}-\d\d\.\d))[ .　]")
# 「（可跨文档的）§N」：前面允许出现 `W-NN`
REF_RE = re.compile(
    r"(?:走|见|详见|按|参见)\s*"
    r"(?:(?P<doc>W-\d\d)\s*)?"
    r"§\s*(?P<sec>\d+(?:\.\d+)*)")


def sections_of(text: str) -> set[str]:
    return set(SEC_RE.findall(text))


def scan(root: pathlib.Path) -> tuple[int, list[str]]:
    """返回 (扫到的引用数, 断链清单)。

    `root` 是**假仓库根**用的：所有路径都从它推导，
    因此自检可以用临时目录造场景，**不碰真实文件**。
    """
    docs = root / "docs/product"
    design = docs / "saas-platform-design-20261001.md"
    specs = sorted(docs.glob("W-*.md"))
    if not design.is_file() or not specs:
        raise FileNotFoundError(f"缺少设计文档或规格：{design} / {len(specs)} 份 W-*.md")

    legal: dict[str, set[str]] = {design.name: sections_of(design.read_text("utf-8"))}
    for f in specs:
        legal[f.name] = sections_of(f.read_text(encoding="utf-8"))

    broken: list[str] = []
    n_refs = 0
    for f in specs:
        text = f.read_text(encoding="utf-8")
        own = legal[f.name]
        for m in REF_RE.finditer(text):
            n_refs += 1
            sec, doc = m.group("sec"), m.group("doc")
            if sec in own:
                continue
            if doc and any(sec in v for k, v in legal.items() if k.startswith(doc)):
                continue
            if sec in legal[design.name]:
                continue
            ln = text.count("\n", 0, m.start()) + 1
            broken.append(f"{f.name}:{ln}  §{sec}"
                          + (f"（指向 {doc}）" if doc else "") + " 在任何文档里都不存在")
    return n_refs, broken


def verdict(n_refs: int, broken: list[str], err: str = "") -> int:
    if err:
        print(f"工具不可运行：{err}", file=sys.stderr)
        return EXIT_TOOL_BROKEN
    # ★ 防空转：扫到 0 处引用 ⇒ 一条都没查，不得据此判绿（INC-92/INC-93 同族）
    if n_refs == 0:
        print("✗ 扫到 0 处章节引用 → 报 2（提取层空输入不得判绿）")
        return EXIT_TOOL_BROKEN
    for b in broken:
        print(f"  {RED}✗{RESET} {b}")
    if broken:
        print(f"{RED}章节引用断链 {len(broken)} 处 / 共 {n_refs} 处{RESET}")
        return EXIT_FINDINGS
    print(f"{GREEN}✓ 通过：{n_refs} 处章节引用全部指向真实章节{RESET}")
    return EXIT_OK


def self_test() -> int:
    import shutil
    import tempfile
    print("spec_xref_check 自检：证明它抓得住断链，且不误报合法引用")
    print("=" * 68)
    root = pathlib.Path(tempfile.mkdtemp(prefix="xref_selftest_"))
    docs = root / "docs/product"
    docs.mkdir(parents=True)
    (docs / "saas-platform-design-20261001.md").write_text(
        "### 14.2 请求生命周期\n### 16.4 退款与冲正\n", encoding="utf-8")
    # 靶规格：§1 / §2.1 存在，§3.3 不存在；另有一处指向总设计的合法引用
    (docs / "W-01-甲_20261001.md").write_text(
        # 每处都写成「见 + §」紧邻，让条数断言**真的在数引用**——
        # 首版写「见 §2.1 与 §1」时 §1 不会被匹配，条数断言当场是错的。
        # **夹具自己写错、而它在报失败**，比工具出错更难发现。
        "## 1. 目标\n### 2.1 子节\n见 §2.1；另见 §1（均合法）\n"
        "见 §14.2（总设计，合法）\n见 W-02 §1（跨规格，合法）\n"
        "见 §3.3（**断链**）\n", encoding="utf-8")
    (docs / "W-02-乙_20261001.md").write_text("## 1. 目标\n", encoding="utf-8")

    results: list[tuple[str, bool]] = []

    def t(label: str, cond: bool) -> None:
        results.append((label, cond))
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

    n, broken = scan(root)
    t("扫到 5 处引用", n == 5)
    t("恰好报出 1 处断链（§3.3）", len(broken) == 1 and "§3.3" in broken[0])
    t("断链行号可定位", "W-01-甲_20261001.md:6" in broken[0])
    t("同文件内引用不算断链（§2.1 / §1）",
      not any("§2.1" in b or b.endswith("§1  在任何文档里都不存在") for b in broken))
    t("指向总设计的引用不算断链（§14.2）", not any("§14.2" in b for b in broken))
    t("跨规格引用不算断链（W-02 §1）", not any("W-02" in b for b in broken))
    t("有断链 → 报 1", verdict(n, broken) == EXIT_FINDINGS)
    t("无断链 → 报 0（证明不是恒红）", verdict(n, []) == EXIT_OK)
    # ★ 防空转：0 条引用必须报 2
    t("★ 0 处引用 → 报 2（不得据此判绿）", verdict(0, []) == EXIT_TOOL_BROKEN)
    t("输入缺失 → 报 2", verdict(0, [], "目录不存在") == EXIT_TOOL_BROKEN)

    shutil.rmtree(root, ignore_errors=True)
    ok = sum(1 for _, c in results if c)
    print(f"\n自检 {ok}/{len(results)}")
    return EXIT_OK if ok == len(results) else EXIT_TOOL_BROKEN


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    try:
        n_refs, broken = scan(REPO)
    except Exception as exc:  # noqa: BLE001
        return verdict(0, [], f"{type(exc).__name__}: {exc}")
    print(f"{DIM}规格章节引用核对（{n_refs} 处引用）{RESET}")
    return verdict(n_refs, broken)


if __name__ == "__main__":
    raise SystemExit(main())
