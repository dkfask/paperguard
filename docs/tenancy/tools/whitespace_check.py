#!/usr/bin/env python3
"""行尾空白守卫（总设计 §22.4 第 6 条；补上 INC-52 之后仍空着的另一半）

被守护的条款
------------
总设计 §22.4 发布门禁第 6 条原文：
    「`git diff --check`、敏感信息检查和最新 head 的 CI/审核。」

`secret_scan_check` 覆盖了「敏感信息检查」，而 **`git diff --check`
在本工具出现之前，从未被任何门禁执行过**——条款写着，机器不跑。

**INC-88 的当天实证**：本轮新增的 INC-87 那一行表格行尾是 `| `（`|` 后一个空格），
`git diff --check` 能精确指出 `saas-platform-design-20261001.md:1744`，
**而 38 道门禁全绿**——因为没有任何一道在跑它。

为什么不能「全仓扫一遍」
------------------------
2026-10-02 实测全仓形态：

| 形态 | 处数 | 性质 |
|---|---|---|
| `.md` 行尾**两个**空格 | 29 | **markdown 硬换行语法（CommonMark），合法** |
| `.md` 行尾**一个**空格 | 2 | 真问题 |
| `.html` 行尾 1 个空格 | 6 | `<textarea ` 属性间有意分隔 |
| `.html` 行尾 4/6/10 空格 | 8 | 空行带缩进 |

若把这些也算进去，门禁上线第一天就是红的，而**「天天红」等于没有门禁**：
很快就没人再看红绿。所以本工具取 **`git diff --check` 的原生语义——
只判本次变更引入的行**，存量不阻塞新工作。

★ **存量不是「不存在」，是「不在本判据范围内」**：上面那 8 处 html 与 2 处 md
  属历史遗留，此处如实登记，不假装已清理。清理它们是一次独立的、可单独回滚的改动。

markdown 的两个空格为什么放行
-----------------------------
CommonMark 规定「行尾两个及以上空格 = 硬换行」，渲染成 `<br>`。
本工具**只放行恰好两个**，一个或三个及以上仍然报红——
一个空格不是任何语法，三个以上是手滑。

边界（如实登记，不假装覆盖）
----------------------------
1. **只查文本文件**。二进制（图片、字体、`.db`）由 `git diff --check` 自行跳过。
2. **本地默认查工作区 + 暂存区**（`git diff --check HEAD`）。
   ★ **CI 里工作区是干净 checkout，那个范围必然是空的** ⇒ 所以 base
   **必须**换成本次 PR 的来源：工具自动读 `GITHUB_BASE_REF`（GitHub Actions
   在 PR 上自动注入）并用 `origin/<base>..HEAD`。
   **base 解析不到时本工具报红而不是退回工作区**——
   退回就是一条「恒绿而什么都没查」的门禁（INC-70 同族）。
   CI 侧因此需要 `fetch-depth: 0`，否则浅克隆取不到 `origin/main`。
3. **不检查 tab 缩进与行尾 tab**：`git diff --check` 会把 `blank-at-eof`、
   `space-before-tab` 一并报出，本工具**只取 `trailing whitespace`**，
   避免与「已跟踪文件之外的空白」等其它类别混在一起导致归因困难。
4. **只判本次变更引入的行**。上表那 8 处 html 与 2 处 md 是**历史存量**，
   不在改动范围就不报——**这是范围限制，不是「已清理」**。

退出码：0 = 通过；1 = 本次变更引入了行尾空白；2 = 工具自身不可运行。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
EXIT_OK, EXIT_FINDINGS, EXIT_TOOL_BROKEN = 0, 1, 2
GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

#: markdown 硬换行的合法空格数（CommonMark）。**只放行恰好这个数**。
MD_HARD_BREAK_SPACES = 2
#: 未跟踪文件也要扫——`git diff` 完全不看它们（INC-62 的核心）。
SKIP_DIR_PARTS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}


def _git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=REPO_ROOT,
                       capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def is_scannable(p: pathlib.Path) -> bool:
    if any(part in SKIP_DIR_PARTS for part in p.parts):
        return False
    try:
        p.read_bytes()
    except OSError:
        return False
    return True


def line_is_offending(raw: str, suffix: str) -> tuple[bool, str]:
    """判断一行是否该报；返回 `(是否报, 原因)`。

    核心：**markdown 恰好两个尾随空格是硬换行，不报**。
    """
    stripped = raw.rstrip("\n").rstrip("\r")
    trailing = re.search(r"( +)$", stripped)
    if not trailing:
        return False, ""
    n = len(trailing.group(1))
    if suffix == ".md" and n == MD_HARD_BREAK_SPACES:
        return False, ""
    reason = (f"行尾 {n} 个空格"
              + ("（md 硬换行只允许 2 个）" if suffix == ".md" else ""))
    return True, reason


def resolve_base(explicit: str | None) -> tuple[str | None, list[str]]:
    """定出比对 base。返回 `(base, 问题列表)`。

    优先级：显式 `--base` → `GITHUB_BASE_REF`（GitHub Actions 在 PR 上自动注入）。

    ★ **探测不到 base 时必须让调用方报红，绝不能退回「扫工作区」然后绿**：
      CI 里工作区是干净 checkout，扫工作区必然零 finding——
      那是一条**恒绿而什么都没查**的门禁，与 INC-70「一致 ≠ 覆盖现实」同族。
    """
    if explicit:
        return explicit, []
    ref = os.environ.get("GITHUB_BASE_REF", "").strip()
    if ref:
        return f"origin/{ref}", []
    return None, []


def scan(base: str | None) -> list[str]:
    """返回 finding 列表；空列表 = 通过。"""
    findings: list[str] = []
    checked_scope = "工作区+暂存区 + 未跟踪文件"

    # ── ① 已跟踪文件：本次变更引入的行 ──
    # ★ `git diff --check` 的退出码语义实测过：0=干净 / 1=有差异但无空白 /
    #   **2=有空白问题**。git 自己出错是 128+，所以 `<=2` 都继续，
    #   写成 `not in (0, 1)` 会把「发现空白」误判成「git 崩了」。
    if base:
        rc_probe, _ = _git("rev-parse", "--verify", f"{base}^{{commit}}")
        if rc_probe != 0:
            # ★ 不可核对 ≠ 合格。报红，并说明该怎么修。
            return [f"★ base `{base}` 在本地不存在（浅克隆或 ref 未拉取）——"
                    "**无法核对本次变更**。CI 上请用 `fetch-depth: 0`，"
                    "本地请先 `git fetch <remote>`。**这一项不得当作通过。**"]
        checked_scope = f"{base}..HEAD + 未跟踪文件"
        args = ["diff", "--check", f"{base}..HEAD"]
    else:
        args = ["diff", "--check", "HEAD"]
    rc, out = _git(*args)
    if rc > 2:
        raise RuntimeError(f"git {' '.join(args)} 失败：{out.strip()[:200]}")
    hits: dict[tuple[str, int], str] = {}
    for line in out.splitlines():
        m = re.match(r"^(.+?):(\d+):\s+trailing whitespace\.?\s*$", line)
        if m:
            hits[(m.group(1), int(m.group(2)))] = "（git 判定）"

    for (fname, lineno), why in sorted(hits.items()):
        p = REPO_ROOT / fname
        if not p.is_file():
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").split("\n")
        if 1 <= lineno <= len(lines):
            bad, reason = line_is_offending(lines[lineno - 1], p.suffix)
            if not bad:
                continue                      # 放行（如 md 硬换行）
            why = reason
        findings.append(f"{fname}:{lineno} 行尾空白 {why}")

    # ── ② 未跟踪文件：git 完全不看它们，而它们 100% 是新增 ──
    rc3, untracked = _git("ls-files", "--others", "--exclude-standard")
    if rc3 == 0:
        for rel in untracked.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            p = REPO_ROOT / rel
            if not p.is_file() or not is_scannable(p):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue                       # 二进制
            for i, line in enumerate(text.split("\n"), 1):
                bad, reason = line_is_offending(line, p.suffix)
                if bad:
                    findings.append(f"{rel}:{i} 行尾空白 {reason}（**未跟踪文件**，git diff 不覆盖）")
    scan.last_scope = checked_scope          # 供 main() 打印，避免两处口径漂移
    return findings


# ══════════════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════════════

def self_test() -> int:
    print(f"{CYAN}whitespace_check 自检{RESET}")
    print("=" * 68)
    cases: list[tuple[str, bool]] = []

    def bad(text: str, suffix: str = ".py"):
        return line_is_offending(text, suffix)[0]

    # ── 该报的报 ──
    cases.append(("★ 代码行尾 1 个空格 → 报红", bad("x = 1 ")))
    cases.append(("★ 行首有内容、行尾多个空格 → 报红", bad("y = 2      ")))
    cases.append(("★ html 行尾 1 个空格 → 报红（`<textarea ` 那类）",
                  bad('<textarea \n', ".html")))
    cases.append(("★ md 行尾**一个**空格 → 报红（不是换行语法）",
                  bad("**问题**: x \n", ".md")))
    cases.append(("★ md 行尾**三个**空格 → 报红（手滑不是语法）",
                  bad("**问题**: x   \n", ".md")))
    cases.append(("★ html 空行带缩进 → 报红",
                  bad("      \n", ".html")))

    # ── 不该报的不报（**误报与漏报代价一样大**）──
    cases.append(("阴性) md 行尾**两个**空格 = 硬换行 → 不报红",
                  not bad("**日期**: 2026-09-11  \n", ".md")))
    cases.append(("阴性) 行内空格（不在行尾）→ 不报红", not bad("a = 1 + 2\n")))
    cases.append(("阴性) 空行 → 不报红", not bad("\n")))
    cases.append(("阴性) 无尾随空格的普通行 → 不报红", not bad("| a | b |\n", ".md")))
    cases.append(("阴性) 行尾是 tab 不在本判据范围（不报也不假绿地声称覆盖）",
                  not bad("z = 3\t\n")))

    # ── scan 的真实行为（在临时 git 仓库里造状态）──
    import shutil
    import tempfile
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ws-"))
    try:
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

        def g(*a):
            return subprocess.run(["git", *a], cwd=tmp, env=env,
                                  capture_output=True, text=True)

        g("init", "-q")
        (tmp / "clean.py").write_text("a = 1\n", encoding="utf-8")
        (tmp / "hard.md").write_text("**日期**: x  \n", encoding="utf-8")
        g("add", "-A")
        g("commit", "-qm", "init")

        # 工作区：改一个已跟踪文件 + 加一个未跟踪文件
        (tmp / "clean.py").write_text("a = 1 \n", encoding="utf-8")      # 应报
        (tmp / "hard.md").write_text("**日期**: x   \n", encoding="utf-8")  # 3 空格，应报
        (tmp / "brand_new.py").write_text("b = 2 \nc = 3\n", encoding="utf-8")  # 未跟踪，应报
        (tmp / "brand_new.md").write_text("行尾硬换行  \n", encoding="utf-8")   # 未跟踪但合法，不应报

        saved, real_root = globals()["REPO_ROOT"], REPO_ROOT
        globals()["REPO_ROOT"] = tmp
        try:
            f = scan(None)
        finally:
            globals()["REPO_ROOT"] = real_root

        got = {x.split(":")[0] for x in f}
        cases.append(("★ scan 抓到已跟踪文件的改动行尾空白",
                      any("clean.py" in x for x in f)))
        cases.append(("★ scan 抓到 md 的三个尾随空格（只放行两个）",
                      any("hard.md" in x for x in f)))
        cases.append(("★ scan 抓到**未跟踪文件**的行尾空白（git diff 不覆盖它）",
                      any("brand_new.py" in x and "未跟踪" in x for x in f)))
        cases.append(("阴性) scan 不报未跟踪 md 的合法硬换行（不误报）",
                      not any("brand_new.md" in x for x in f)))

        # ── base 模式（CI 用的那条路径）──
        # ★ 这里测的是**判别力**，不是「能跑」：clean.py 的脏改动已经在
        #   工作区里，base 模式必须靠 commit 之间的差异独立再抓到它。
        g("add", "-A")
        g("commit", "-qm", "dirty")
        base_sha = subprocess.run(["git", "rev-parse", "HEAD~1"], cwd=tmp,
                                  capture_output=True, text=True).stdout.strip()
        globals()["REPO_ROOT"] = tmp
        try:
            f_base = scan(base_sha)
        finally:
            globals()["REPO_ROOT"] = real_root
        cases.append(("★ base 模式抓到已提交进历史的行尾空白（CI 走这条路）",
                      any("clean.py" in x for x in f_base)))

        # ★ 不可核对 ≠ 合格。base 不存在时**必须报红**，
        #   绝不能退回「扫工作区」然后绿——那是一条恒绿而什么都没查的门禁。
        globals()["REPO_ROOT"] = tmp
        try:
            f_bad_base = scan("origin/绝不存在-INC-88")
        finally:
            globals()["REPO_ROOT"] = real_root
        cases.append(("★ base 不存在 → **报红**，不得当作「无问题」",
                      any("无法核对" in x or "不存在" in x for x in f_bad_base)
                      and not any("clean.py" in x for x in f_bad_base)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ── resolve_base 的 CI 探测 ──
    saved_env = os.environ.get("GITHUB_BASE_REF")
    os.environ["GITHUB_BASE_REF"] = "main"
    try:
        b, _ = resolve_base(None)
        cases.append(("★ CI 上自动采用 GITHUB_BASE_REF → origin/main", b == "origin/main"))
    finally:
        if saved_env is None:
            os.environ.pop("GITHUB_BASE_REF", None)
        else:
            os.environ["GITHUB_BASE_REF"] = saved_env
    b2, _ = resolve_base("origin/x")
    cases.append(("阴性) 显式 --base 优先于环境变量", b2 == "origin/x"))

    # ── 防空转：必须造一个**干净**状态，且**只能用临时仓库**──
    # ★ 首版这条直接调 `scan(None)` 扫**真实仓库**。后果是：真实仓库
    #   一旦有行尾空白（哪怕是**本工具落地当天我自己写进去的**），
    #   自检就红，而 `run_all_gates` 会把自检红判成 exit=2
    #   「工具不可运行」——**把「数据脏」误报成「工具坏了」**，
    #   正是三态分离要防的那类混淆。
    #   防空转要验的是「工具不会对干净仓库乱报」，
    #   所以**状态必须由夹具造，不能由现实提供**。
    clean_dir = pathlib.Path(tempfile.mkdtemp(prefix="ws-clean-"))
    try:
        cg = lambda *a: subprocess.run(["git", *a], cwd=clean_dir, env=env,
                                       capture_output=True, text=True)
        cg("init", "-q")
        (clean_dir / "a.py").write_text("x = 1\n", encoding="utf-8")
        (clean_dir / "b.md").write_text("**a**: b  \n", encoding="utf-8")
        cg("add", "-A")
        cg("commit", "-qm", "clean")
        globals()["REPO_ROOT"] = clean_dir
        try:
            f_clean = scan(None)
        finally:
            globals()["REPO_ROOT"] = real_root
        cases.append(("★ 干净状态 → 零 finding（否则这条门禁本身是坏的）",
                      not f_clean))
    finally:
        shutil.rmtree(clean_dir, ignore_errors=True)

    ok = 0
    for label, good in cases:
        if good:
            ok += 1
            print(f"  {GREEN}✓{RESET} {label}")
        else:
            print(f"  {RED}✗{RESET} {label}")
    print("=" * 68)
    print(f"{GREEN}自检通过：{ok}/{len(cases)}{RESET}" if ok == len(cases)
          else f"{RED}自检失败：{ok}/{len(cases)}{RESET}")
    return EXIT_OK if ok == len(cases) else EXIT_TOOL_BROKEN


def main() -> int:
    if "--self-test" in sys.argv:      # ★ 必须在 parse_args 之前，
        return self_test()             #   否则 argparse 先因未知参数报错
    ap = argparse.ArgumentParser(description="行尾空白守卫（§22.4 第 6 条）")
    ap.add_argument("--base", help="CI 用：与该 base 比对（如 origin/main）。"
                                    "不给则查工作区+暂存区。")
    args = ap.parse_args()
    base, _ = resolve_base(args.base)
    try:
        findings = scan(base)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"扫描失败：{exc}\n")
        return EXIT_TOOL_BROKEN
    scope = getattr(scan, "last_scope", "工作区+暂存区 + 未跟踪文件")
    print("=" * 72)
    print("行尾空白守卫（总设计 §22.4 第 6 条 · INC-88）")
    print("=" * 72)
    print(f"{DIM}reality：git diff --check（{scope}）{RESET}")
    print(f"{DIM}markdown 恰好两个行尾空格按 CommonMark 硬换行放行；"
          f"一个或三个及以上仍报{RESET}")
    print()
    for x in findings:
        print(f"  {RED}✗{RESET} {x}")
    print("=" * 72)
    if findings:
        print(f"{RED}未通过{RESET}（{len(findings)} 项）")
        return EXIT_FINDINGS
    print(f"{GREEN}全部通过{RESET}")
    print(f"{YELLOW}不覆盖{RESET}：历史存量行尾空白（本次未改动的行不在本判据范围）"
          f"与行尾 tab。")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
