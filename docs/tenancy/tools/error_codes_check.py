#!/usr/bin/env python3
"""错误码登记一致性校验器（AT-T-7）。

配套：docs/tenancy/error-codes.md

为什么需要它
------------
错误码字典是**手工维护**的，而代码里错误码是**持续新增**的。
没有门禁时，字典会静默过期——新人加了个码没登记，前端照旧走通用分支，
没人发现。这与 AT 追溯矩阵漂移是同一类问题。

它检查什么
----------
EC-01  代码中所有 HTTP 异常码都已登记在字典中
EC-02  字典中标记为「已实现」的码在代码中确实存在（防"登记了但没实现"）
EC-03  裸字符串数量不高于基线（防止新增，而不是要求一次降到 0）
EC-04  错误 message 中不含 SQL 关键字 / 绝对路径 / 堆栈痕迹（AT-29）
EC-05  字典中的 HTTP 状态码与代码一致

关于 EC-03 的设计
-----------------
不要求把 404 处裸字符串一次改完——那不现实且会引入回归。
只要求**不再增长**：新增裸字符串会让门禁失败，迫使作者至少登记一个码。
存量清理按 api-contract-delta.md §7 的 P2～P5 分阶段推进。

退出码：0 = 通过；1 = 存在问题；2 = 工具自身不可运行。
"""

from __future__ import annotations

import ast

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
APP = REPO_ROOT / "backend/app"
DICT = REPO_ROOT / "docs/tenancy/error-codes.md"

EXIT_OK, EXIT_FINDINGS, EXIT_TOOL_BROKEN = 0, 1, 2
GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

# 基线（2026-10-01 实测，backend/app/ 全量口径）。存量不要求清零，只要求不增长。
BASELINE_BARE_APP = 404
BASELINE_BARE_API = 367

# EC-04：禁止出现在面向用户的 message 里的内容
SQL_PATTERNS = (
    r"\bSELECT\b.*\bFROM\b", r"\bINSERT\b.*\bINTO\b", r"\bUPDATE\b.*\bSET\b",
    r"\bDELETE\b.*\bFROM\b", r"sqlite3?\.", r"OperationalError", r"IntegrityError",
)
PATH_PATTERN = r"(/home/|/Users/|/var/|/tmp/)[A-Za-z0-9_./-]+"
TRACEBACK_PATTERN = r"Traceback \(most recent call last\)|File \"[^\"]+\", line \d+"


def iter_saas_error_calls(source: str):
    for call in ast.walk(ast.parse(source)):
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "fail" and len(call.args) >= 2:
            status, code = call.args[:2]
            if isinstance(status, ast.Constant) and isinstance(status.value, int) and isinstance(code, ast.Constant) and isinstance(code.value, str):
                yield code.value, str(status.value), call.lineno


def iter_http_exceptions():
    """产出 (code, status, file, line) —— 只处理含 "code": 的结构化异常。"""
    for f in sorted(APP.rglob("*.py")):
        s = f.read_text(encoding="utf-8")
        # The approved SaaS helper wraps HTTPException. Its constant status/code
        # call sites remain contracts; do not let a wrapper hide new errors.
        if f.name in {"saas_identity.py", "tenant_projects.py"} and f.parent.name == "services":
            for code, status, line in iter_saas_error_calls(s):
                yield code, status, f, line
        for m in re.finditer(r"HTTPException\(", s):
            i = m.end() - 1
            depth, j = 0, i
            while j < len(s):
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            body = s[i + 1:j]
            cm = re.search(r'"code":\s*"([A-Z0-9_]+)"', body)
            if not cm:
                continue
            sm = re.search(r"status_code\s*=\s*(\d+)", body)
            yield cm.group(1), (sm.group(1) if sm else "?"), f, s[:i].count("\n") + 1


def count_bare(scope: pathlib.Path) -> tuple[int, int]:
    """统计 (裸字符串, 其他形式)"""
    bare = other = 0
    for f in sorted(scope.rglob("*.py")):
        s = f.read_text(encoding="utf-8")
        for m in re.finditer(r"HTTPException\(", s):
            i = m.end() - 1
            depth, j = 0, i
            while j < len(s):
                if s[j] == "(":
                    depth += 1
                elif s[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            body = s[i + 1:j]
            if '"code":' in body:
                continue
            if re.search(r'detail\s*=\s*["\']', body):
                bare += 1
            else:
                other += 1
    return bare, other


def dict_codes(text: str) -> tuple[set[str], dict[str, str]]:
    """解析字典中「已实现」码 → 状态码。

    §5 的目标码位（占位，尚未实现）也用 `| CODE | 状态码 |` 的表格格式，
    若不区分，EC-02 会把 16 个占位码全部报成"登记了但未实现"的幽灵码。
    区分办法：只解析 §2～§4 的表格，§5 之后的内容不参与。
    """
    cut = text.find("## 5. 目标码位")
    body = text[:cut] if cut > 0 else text
    implemented: dict[str, str] = {}
    for m in re.finditer(r"^\|\s*`([A-Z][A-Z0-9_]{3,})`\s*\|\s*(\d{3})\s*\|", body, re.M):
        implemented[m.group(1)] = m.group(2)
    return set(implemented), implemented


def placeholder_codes(text: str) -> set[str]:
    """解析 §5「目标码位」区（含其子小节）的占位码。

    为什么需要它（EC-06）
    --------------------
    `dict_codes()` 只解析 §5 **之前**的已实现码，因此占位码在 EC-01/EC-02
    里都是隐形的。后果是：**同一个码可以同时出现在两处而无人发现**——
    一次作为「已实现」（带状态码），一次作为「占位」（语义可能不同）。
    2026-10-01 补 W-03 的 6 个上下文出口码时就撞上这个边界：
    既有 `TENANT_OVERRIDE_FORBIDDEN`（400，请求不合法），
    新增 `TENANT_HEADER_NOT_HONORED`（403，身份够但目标不可达）——
    两者形态相似，若重名会让客户端无法区分「越权尝试」与「合法平台请求」。

    注意：§5 之后新增的 §5.1.1 子小节**仍在**本函数范围内——
    它是占位码区的一部分，不是已实现区。

    **只认表格首列，不认正文提及**（2026-10-01 实测修正）
    ------------------------------------------------------
    首版用 `` `([A-Z][A-Z0-9_]{3,})` `` 全文匹配，把 §5.2 里
    「`QUOTA_EXHAUSTED` 429 三维额度耗尽（**替代 `AI_DAILY_QUOTA_EXCEEDED`
    的通用形态**）」这句**说明文字**里的旧码也算成占位码，
    于是 EC-06 误报 `AI_DAILY_QUOTA_EXCEEDED` 重名。

    判据收紧为**表格首列**：`` | `CODE` | ``。
    真实重名会出现在表格首列；说明文字里的提及不是登记。
    **会误报的检查器等于没有检查器。**
    """
    cut = text.find("## 5. 目标码位")
    if cut < 0:
        return set()
    tail = text[cut:]
    return set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]{3,})`\s*\|", tail, re.M))


def main() -> int:
    if not DICT.exists():
        sys.stderr.write(f"找不到错误码字典：{DICT}\n")
        return EXIT_TOOL_BROKEN

    text = DICT.read_text(encoding="utf-8")
    registered, reg_status = dict_codes(text)

    findings: list[str] = []
    notes: list[str] = []

    # EC-01 代码有但字典未登记
    actual: dict[str, tuple[str, str]] = {}
    for code, status, f, line in iter_http_exceptions():
        actual.setdefault(code, (status, f"{f.relative_to(REPO_ROOT)}:{line}"))
    unregistered = sorted(set(actual) - registered)
    if unregistered:
        findings.append(f"EC-01 代码中有 {len(unregistered)} 个未登记的错误码：{unregistered}")

    # EC-02 字典登记但代码无（排除 §5 目标占位——它们没有 HTTP 状态码列）
    ghost = sorted(registered - set(actual))
    if ghost:
        findings.append(f"EC-02 字典登记为已实现但代码中不存在：{ghost}")

    # EC-06 占位码与已实现码不得重名
    placeholders = placeholder_codes(text)
    dup = sorted(placeholders & registered)
    if dup:
        findings.append(
            f"EC-06 同一码同时出现在已实现区与目标占位区（语义可能不同）：{dup}")
    notes.append(f"目标占位码 {len(placeholders)} 个（§5 起，含子小节）")

    # EC-05 状态码不一致
    mismatch = [
        f"{c}: 字典 {reg_status[c]} vs 代码 {actual[c][0]}"
        for c in sorted(set(actual) & registered)
        if reg_status[c] != actual[c][0]
    ]
    if mismatch:
        findings.append(f"EC-05 状态码不一致：{mismatch}")

    # EC-03 裸字符串不增长
    bare_app, _ = count_bare(APP)
    bare_api, _ = count_bare(APP / "api")
    if bare_app > BASELINE_BARE_APP:
        findings.append(
            f"EC-03 裸字符串增长：app/ 全量 {bare_app} > 基线 {BASELINE_BARE_APP}"
        )
    if bare_api > BASELINE_BARE_API:
        findings.append(
            f"EC-03 裸字符串增长：api/ 顶层 {bare_api} > 基线 {BASELINE_BARE_API}"
        )
    notes.append(f"裸字符串 app/={bare_app}（基线 {BASELINE_BARE_APP}）、"
                 f"api/={bare_api}（基线 {BASELINE_BARE_API}）")

    # EC-04 message 泄漏检查
    leaks: list[str] = []
    for f in sorted(APP.rglob("*.py")):
        s = f.read_text(encoding="utf-8")
        for m in re.finditer(r'HTTPException\([^)]*?"message":\s*([^,)]+)', s):
            expr = m.group(1)
            if not expr.startswith(('"', "f\"")):
                continue
            for pat in SQL_PATTERNS:
                if re.search(pat, expr):
                    leaks.append(f"{f.relative_to(REPO_ROOT)}: {expr[:60]}")
                    break
            else:
                for pat in (PATH_PATTERN, TRACEBACK_PATTERN):
                    if re.search(pat, expr):
                        leaks.append(f"{f.relative_to(REPO_ROOT)}: {expr[:60]}")
                        break
    if leaks:
        findings.append(f"EC-04 message 中疑似泄漏内部信息：{leaks[:3]}")

    # ---- 输出 ----
    print(f"{DIM}字典：{DICT.relative_to(REPO_ROOT)}{RESET}\n")
    print("=" * 72)
    print("错误码登记校验")
    print("=" * 72)

    checks = [
        ("EC-01", not unregistered,
         f"{len(actual)} 个代码中的码全部已登记" if not unregistered
         else f"{len(unregistered)} 个未登记"),
        ("EC-02", not ghost,
         "无「登记了但未实现」的码" if not ghost else f"{len(ghost)} 个幽灵码"),
        ("EC-03", bare_app <= BASELINE_BARE_APP and bare_api <= BASELINE_BARE_API,
         notes[0]),
        ("EC-04", not leaks,
         "message 中无 SQL/路径/堆栈泄漏" if not leaks
         else f"{len(leaks)} 处疑似泄漏"),
        ("EC-05", not mismatch,
         "状态码与字典一致" if not mismatch else f"{len(mismatch)} 处不一致"),
        ("EC-06", not dup,
         f"占位码与已实现码无重名（占位 {len(placeholders)} 个）" if not dup
         else f"{len(dup)} 个重名：{dup}"),
    ]
    for code, ok, msg in checks:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{mark}] {code}  {msg}")

    print(f"\n{DIM}已登记 HTTP 码 {len(registered)} 个；"
          f"代码实际抛出点 {sum(1 for _ in iter_http_exceptions())} 处{RESET}")
    print("=" * 72)
    if findings:
        for f in findings:
            print(f"  {RED}✗{RESET} {f}")
        print(f"{RED}未通过{RESET}")
        return EXIT_FINDINGS
    print(f"{GREEN}全部通过{RESET}")
    return EXIT_OK


def self_test() -> int:
    """自检：证明 EC-06 既能抓真重名，又不会把**说明文字里的提及**误判为占位码。

    2026-10-01 真实踩到：EC-06 首版用全文正则匹配反引号内的码名，
    把 §5.2 说明文字「替代 `AI_DAILY_QUOTA_EXCEEDED` 的通用形态」里的旧码
    算成占位码，于是误报重名。**会误报的检查器等于没有检查器**，
    因此必须同时锁住「能抓」与「不误报」两个方向。
    """
    print(f"{CYAN}error_codes_check 自检{RESET}")
    print("=" * 64)
    base = (
        "## 2. 已实现\n\n"
        "| Code | HTTP | 位置 | 说明 |\n|---|---|---|---|\n"
        "| `AI_DAILY_QUOTA_EXCEEDED` | 429 | `x.py:99` | 超出日配额 |\n"
        "| `NOT_FOUND` | 404 | `y.py:1` | 未找到 |\n\n"
        "## 5. 目标码位（尚未实现，仅占位）\n\n"
        "### 5.2 幂等与并发\n\n"
        "| Code | HTTP | 用途 |\n|---|---|---|\n"
        "| `QUOTA_EXHAUSTED` | 429 | 三维额度耗尽"
        "（替代 `AI_DAILY_QUOTA_EXCEEDED` 的通用形态） |\n"
    )
    dup_dict = base + (
        "\n### 5.9 冲突示例\n\n| Code | HTTP | 用途 |\n|---|---|---|\n"
        "| `NOT_FOUND` | 410 | 与已实现区重名 |\n"
    )
    empty = "## 2. 已实现\n\n| Code | HTTP |\n|---|---|\n| `X` | 400 |\n"

    reg_of = lambda t: set(re.findall(          # noqa: E731
        r"^\|\s*`([A-Z][A-Z0-9_]{3,})`", t.split("## 5.")[0], re.M))
    cases = [
        ("说明文字提及旧码 **不**算占位 → 无重名",
         not (placeholder_codes(base) & reg_of(base))),
        ("★ 表格首列真重名 → 被抓到",
         bool(placeholder_codes(dup_dict) & reg_of(dup_dict))),
        ("★ 子小节 §5.1.1 的码也算占位（不漏）",
         "FAKE_MEMBERSHIP" in placeholder_codes(
             base.replace("### 5.2", "### 5.1.1 上下文\n\n"
                                      "| Code | HTTP |\n|---|---|\n"
                                      "| `FAKE_MEMBERSHIP` | 403 |\n\n### 5.2"))),
        ("★ 无 §5 区 → 占位集为空（不报错）",
         placeholder_codes(empty) == set()),
    ]
    cases.extend([
        ("SaaS helper 的常量码不会逃出契约登记", list(iter_saas_error_calls("fail(403, 'SCOPE_DENIED', 'fixture')")) == [("SCOPE_DENIED", "403", 1)]),
        ("不同 helper 不误算为 HTTP 契约", list(iter_saas_error_calls("other(403, 'NOT_HTTP', 'fixture')")) == []),
    ])
    ok = 0
    for label, good in cases:
        if good:
            ok += 1
            print(f"  {GREEN}✓{RESET} {label}")
        else:
            print(f"  {RED}✗{RESET} {label}")
    print("=" * 64)
    print(f"{GREEN}自检通过：{ok}/{len(cases)}{RESET}" if ok == len(cases)
          else f"{RED}自检失败：{ok}/{len(cases)}{RESET}")
    return EXIT_OK if ok == len(cases) else EXIT_TOOL_BROKEN


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
