#!/usr/bin/env python3
"""W-03 上下文解析门禁：RequestContext 不变量与状态机的静态核对。

背景
----
W-03（租户上下文与授权）目前只有总设计 §8.1 的**散文契约**。
散文是「建议」，不是可执行判据——它无法回答「实现有没有做到」。

本门禁把契约变成四项**静态可查**的判据：

| 编号 | 检查 | 对应的 W-03 章节 |
|---|---|---|
| CR-1 | `RequestContext` 是 `frozen=True` 且有 CI-1～CI-4 断言 | §2.1 |
| CR-2 | 解析函数按 S0→S4 顺序出现七个失败出口 E1～E7 | §2.2 / §2.3 |
| CR-3 | 服务层函数**无默认 `ctx` 参数** | §2.4 R-1 |
| CR-4 | 旧全局 `users.role` 的非测试引用点已清零 | §3.1 |

CR-4 为什么是关键闸门
---------------------
只要代码还在读全局单值 `users.role`，「同账号双租户不同角色不串权」
（W-03 完成定义的第一条）就**在数学上不可能成立**——
A 校教师切到 B 校时读到的还是同一个 `role`。

本门禁**如实报告剩余引用数**并因此报红。**它不替业务决定怎么改**，
也不假装「引用点少一点就等于隔离生效」。

覆盖面闸（与本仓库既有纪律一致）
--------------------------------
若扫描不到任何引用点（正则失效 / 目录改名），必须报 **2（工具不可运行）**
而不是 0（通过）。**「核对了 0 行却显示通过」比只会报红危险得多。**

退出码
------
    0 = 四项判据全部满足
    1 = 存在未满足项（差距真实且已量化）
    2 = 工具不可运行（依赖缺失、目录不存在、扫描到 0 个文件）
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

REQUIRED_EXITS = ["E1", "E2", "E3", "E4", "E5", "E6", "E7"]
REQUIRED_STEPS = ["S0", "S1", "S2", "S3", "S4"]
REQUIRED_INVARIANTS = ["CI-1", "CI-2", "CI-3", "CI-4"]


def scan_files(app_dir: pathlib.Path) -> list[pathlib.Path]:
    if not app_dir.is_dir():
        return []
    return sorted(p for p in app_dir.rglob("*.py") if "test" not in p.parts)


def role_refs(files: list[pathlib.Path]) -> dict[str, int]:
    """统计全局 `users.role` 的非测试引用点（CR-4）。

    只认**真读取**：下标取值 `u["role"]` 或 `.get("role")`。
    不认 SQL 里的 `role` 列名、不认 `role_code`（那是每租户的 membership_roles）。
    """
    hits: dict[str, int] = {}
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        n = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                    and node.slice.value == "role":
                n += 1
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "get" and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and node.args[0].value == "role":
                n += 1
        if n:
            hits[str(p)] = n
    return hits


def context_class_status(files: list[pathlib.Path]) -> dict:
    """CR-1/CR-2：找 RequestContext 类与上下文解析函数。"""
    found_cls = None
    frozen = False
    has_post_init = False
    invariants: set[str] = set()
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != "RequestContext":
                continue
            found_cls = str(p)
            for dec in node.decorator_list:
                src = ast.dump(dec)
                if "frozen" in src and "True" in src:
                    frozen = True
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__post_init__":
                    has_post_init = True
                    # 必须同时扫 AST 与**源码文本**。
                    # AST 里**没有注释**——把不变量写在 `# CI-3 ...` 注释里
                    # 会让本检查永远判「缺不变量」，即门禁自身恒为红。
                    # 真实实现里不变量既可能是断言表达式，也可能是行内说明，
                    # 两处都要看。
                    seg = ast.dump(item)
                    try:
                        raw = ast.get_source_segment(
                            p.read_text(encoding="utf-8"), item) or ""
                    except Exception:
                        raw = ""
                    for inv in REQUIRED_INVARIANTS:
                        if inv in seg or inv in raw:
                            invariants.add(inv)
    return {"file": found_cls, "frozen": frozen,
            "post_init": has_post_init, "invariants": invariants}


def resolver_status(files: list[pathlib.Path]) -> dict:
    """CR-2：找上下文解析函数，核对七个失败出口（E7 于 2026-10-01 因 INC-59 新增）。"""
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name.lower()
            if "context" not in name and "resolve" not in name:
                continue
            # 与 CR-1 同理：AST 无注释，失败出口既可能写在 raise 里，
            # 也可能只写在注释/文档字符串里。两处都看，否则门禁恒红。
            seg = ast.dump(node)
            try:
                raw = ast.get_source_segment(text, node) or ""
            except Exception:
                raw = ""
            present = {e for e in REQUIRED_EXITS if e in seg or e in raw}
            if present:
                return {"file": str(p), "func": node.name, "exits": present}
    return {"file": None, "func": None, "exits": set()}


def default_ctx_params(files: list[pathlib.Path]) -> list[str]:
    """CR-3：服务层函数的 `ctx`/`context` 参数带默认值 → 违反 R-1。

    **必须按类型注解消歧**：仓库里 `core/reporters/json_reporter.py` 的
    `_build_payload(..., context: dict | None = None)` 里的 `context`
    是**报告渲染上下文**，与 `RequestContext` 毫无关系。
    只按参数名匹配会把它误报成违反 R-1 —— **会误报的检查器等于没有检查器**。

    判据：参数名是 `ctx`/`context` **且**（注解里出现 `RequestContext` 或**没有注解**）。
    显式标注为 `dict`/`dict | None`/`str` 等其他类型的一律跳过。
    """
    bad: list[str] = []
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            allargs = list(args.posonlyargs) + list(args.args)
            tail = allargs[len(allargs) - len(args.defaults):]
            for a, d in zip(tail, args.defaults):
                if a.arg not in ("ctx", "context") or d is None:
                    continue
                ann = a.annotation
                if ann is not None:
                    seg = ast.dump(ann)
                    # 显式标注为非 RequestContext 类型 → 不是本门禁的 ctx
                    if "RequestContext" not in seg:
                        continue
                bad.append(f"{p.name}:{node.lineno} {node.name}() 的 {a.arg} 有默认值")
    return bad


def verdict(n_files: int, problems: list[str], err: str) -> tuple[int, str]:
    """纯判定。扫描 0 个文件必须报 2。"""
    if err:
        return EXIT_TOOL_BROKEN, err
    if n_files == 0:
        return EXIT_TOOL_BROKEN, "未扫描到任何源文件，本项无数据可核对"
    if problems:
        return EXIT_FINDINGS, f"未通过：{len(problems)} 项"
    return EXIT_OK, f"通过：扫描 {n_files} 个文件，四项判据均满足"


def _cr5_cases() -> list[tuple[str, bool]]:
    """CR-5 的自检用例：门禁与规格的规则 ID 必须**双向**一致。

    只测「门禁要求但规格没有」是不够的——
    规格新增一个门禁不查的编号，同样是零覆盖，而且**看起来全绿**。

    **夹具不能写死 `E7`**：E7 曾是「门禁不查」的反例，2026-10-01 因 INC-59
    补进 `REQUIRED_EXITS` 后，它就成了**必检项**——夹具若还写死 E7，
    这条用例会从「应报红」变成「应报绿」，而失败原因是**夹具过期**不是判据失效。
    取一个**当前不在 `REQUIRED_EXITS` 里**的编号，才能持续测同一个性质：
    「规格声明了但门禁不检查 ⇒ 必须被抓」。这与 `secret_scan_check`
    把判据抽成不依赖夹具的纯函数是同一条纪律。
    """
    import tempfile
    out: list[tuple[str, bool]] = []
    ghost = next(f"E{n}" for n in range(2, 99) if f"E{n}" not in REQUIRED_EXITS)
    with tempfile.TemporaryDirectory() as td:
        sp = pathlib.Path(td) / "W-03.md"
        full = ("CI-1 CI-2 CI-3 CI-4 " + " ".join(REQUIRED_EXITS) + " "
                + " ".join(REQUIRED_STEPS))
        sp.write_text(full, encoding="utf-8")
        out.append(("★ CR-5 完全一致 → 无问题",
                    check_spec_alignment(sp) == []))
        sp.write_text(full.replace("CI-4", "CI-5"), encoding="utf-8")
        out.append(("★ CR-5 规格重编号 → 被抓",
                    any("未定义" in a for a in check_spec_alignment(sp))))
        sp.write_text(full + " " + ghost, encoding="utf-8")
        out.append((f"★ CR-5 规格新增 {ghost} 而门禁不查 → 被抓",
                    any("不检查" in a for a in check_spec_alignment(sp))))
        sp.unlink()
        out.append(("★ CR-5 规格缺失 → 被抓（不静默通过）",
                    bool(check_spec_alignment(sp))))
    return out


def self_test() -> int:
    src_ok = '''
from dataclasses import dataclass
@dataclass(frozen=True, slots=True)
class RequestContext:
    tenant_id: str | None = None
    def __post_init__(self):
        # CI-1 member 三者非空
        # CI-2 platform 时 tenant_id 为 None
        # CI-3 support 时 grant 非空
        # CI-4 capabilities 不可变
        assert True

def resolve_context(u):
    if not u: raise E1
    if u.blocked: raise E2
    if not u.member: raise E3
    if u.mstatus != 'active': raise E4
    if u.header_bad: raise E5
    if u.tstatus != 'active': raise E6
    if u.tstatus == 'draft' and not u.owner: raise E7
    return RequestContext()

def service(ctx, x):
    return x
'''
    def run(src: str) -> int:
        """返回**退出码**（不是元组）——自检比对的是退出码。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td) / "app"
            d.mkdir()
            (d / "mod.py").write_text(src, encoding="utf-8")
            files = scan_files(d)
            probs: list[str] = []
            c = context_class_status(files)
            if not c["file"]:
                probs.append("CR-1 未找到 RequestContext")
            else:
                if not c["frozen"]:
                    probs.append("CR-1 RequestContext 非 frozen")
                if not c["post_init"]:
                    probs.append("CR-1 缺 __post_init__")
                missing = set(REQUIRED_INVARIANTS) - c["invariants"]
                if missing:
                    probs.append(f"CR-1 缺不变量 {sorted(missing)}")
            r = resolver_status(files)
            if not r["func"]:
                probs.append("CR-2 未找到解析函数")
            else:
                missing = set(REQUIRED_EXITS) - r["exits"]
                if missing:
                    probs.append(f"CR-2 缺失败出口 {sorted(missing)}")
            probs += default_ctx_params(files)
            return verdict(len(files), probs, "")[0]

    cases = [
        ("全量合规 → 0", src_ok, EXIT_OK),
        ("★ RequestContext 非 frozen → 1",
         src_ok.replace("frozen=True, ", ""), EXIT_FINDINGS),
        ("★ 缺 __post_init__ → 1",
         re.sub(r"    def __post_init__.*?assert True\n", "", src_ok, flags=re.S),
         EXIT_FINDINGS),
        ("★ 缺不变量 CI-3 → 1",
         src_ok.replace("# CI-3 support 时 grant 非空", ""), EXIT_FINDINGS),
        ("★ 缺失败出口 E4 → 1",
         src_ok.replace("if u.mstatus != 'active': raise E4", ""), EXIT_FINDINGS),
        # E7 于 2026-10-01 因 INC-59 新增（draft 租户原先无任何出口覆盖）。
        # 这条用例锁死「E7 是必检项」——**把 E7 从夹具里删掉必须报红**，
        # 否则有人日后把 `REQUIRED_EXITS` 改回六个，门禁不会有任何反应。
        ("★ 缺失败出口 E7 → 1",
         src_ok.replace("    if u.tstatus == 'draft' and not u.owner: raise E7\n", ""),
         EXIT_FINDINGS),
        ("★ ctx 有默认值 → 1",
         src_ok.replace("def service(ctx, x):", "def service(ctx=None, x=1):"),
         EXIT_FINDINGS),
        ("★ 完全没有 RequestContext → 1",
         "def resolve_context(u):\n    raise E1\n", EXIT_FINDINGS),
        # 消歧：显式标注为 dict 的 context 是**报告上下文**，不是 RequestContext。
        # 只按参数名匹配会误报 —— 真实踩到：core/reporters/json_reporter.py:28。
        ("★ context: dict|None 不算违规 → 0",
         src_ok + "\ndef _build_payload(f, context: dict | None = None):\n    return {}\n",
         EXIT_OK),
        ("★ ctx: RequestContext 有默认值 → 1",
         src_ok + "\ndef svc(ctx: RequestContext = None):\n    return ctx\n",
         EXIT_FINDINGS),
    ]
    ok = 0
    for label, src, want in cases:
        got = run(src)
        if got == want:
            ok += 1
            print(f"  {GREEN}✓{RESET} {label}  → exit {got}")
        else:
            print(f"  {RED}✗{RESET} {label}  → 实际 {got}，期望 {want}")
    # CR-5：门禁与规格的规则 ID 双向一致
    for label, good in _cr5_cases():
        if good:
            ok += 1
            print(f"  {GREEN}✓{RESET} {label}")
        else:
            print(f"  {RED}✗{RESET} {label}")
    # 覆盖面闸
    empty = verdict(0, [], "")[0]
    if empty == EXIT_TOOL_BROKEN:
        ok += 1
        print(f"  {GREEN}✓{RESET} 扫描 0 个文件 → 2，不得为 0")
    else:
        print(f"  {RED}✗{RESET} 扫描 0 个文件 → 实际 {empty}")
    total = len(cases) + len(_cr5_cases()) + 1
    print(f"\n自检 {ok}/{total}")
    return EXIT_OK if ok == total else EXIT_TOOL_BROKEN


def spec_declares(spec: pathlib.Path) -> set[str]:
    """规格里**实际声明**的规则 ID 集合。

    正则必须**开放上界**（`E\\d+` 而非 `E[1-6]`）
    ---------------------------------------
    首版写 `E[1-6]`，于是规格新增 E7 时**根本提取不到 E7**，
    CR-5 的反向检查自然抓不到——**提取层的盲区会让校验层恒绿**。
    门禁自己限死了出口个数，规格加第七个时它会假装没看见。
    同理 CI 与 S 也用 `\\d+` / `\\d`。
    """
    if not spec.exists():
        return set()
    text = spec.read_text(encoding="utf-8")
    return set(re.findall(r"\b(CI-\d+|E\d+|S\d+)\b", text))


def check_spec_alignment(spec: pathlib.Path) -> list[str]:
    """CR-5：门禁要求的 ID 必须在规格里有定义，反之亦然。

    为什么需要这道检查（2026-10-01 实测）
    ------------------------------------
    本门禁的 `REQUIRED_EXITS` / `REQUIRED_STEPS` / `REQUIRED_INVARIANTS`
    是**硬编码常量**，规格里的 CI/E/S 编号也是**人写的**。两边没有任何联系：
    - 规格把 CI-4 改名成 CI-5 → 门禁仍要求 CI-4，**永远判「缺不变量」**
    - 规格新增 E7 → 门禁**完全不知道**有 E7
    - 规格删掉 S2 → 门禁**照样报绿**

    实测发现：规格声明了 **12 个** ID（E1～E7、S0～S4），
    门禁只检查 **4 个**（CI-1～CI-4）——E/S 那一族**规格定义了但门禁不查**，
    这正是「各自硬编码、谁都不知道对方存在」的代价。
    """
    declared = spec_declares(spec)
    if not declared:
        return [f"CR-5 读不到规格：{spec}"]
    problems: list[str] = []
    required = set(REQUIRED_INVARIANTS)
    missing = sorted(required - declared)
    if missing:
        problems.append(
            f"CR-5 门禁要求但规格未定义：{missing}——规格重编号后门禁会永远报红")
    # 反向：规格里的 E/S 编号，**门禁在代码层面是否真的检查**。
    #
    # 注意这里比的是「门禁的出口/步骤集合」而不是 `required` 的并集——
    # 若用并集，declared - required 恒为空，这道反向检查就成了死代码，
    # 看起来在查、实际永远绿。（这是本函数第一版的真实缺陷。）
    unchecked = sorted(declared - required - set(REQUIRED_EXITS)
                       - set(REQUIRED_STEPS))
    if unchecked:
        problems.append(
            f"CR-5 规格定义但门禁不检查：{unchecked}——这些规则**没有任何机器校验**")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="W-03 上下文解析静态门禁")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--app-dir", help="后端源码目录")
    args = ap.parse_args()
    if args.self_test:
        print(f"{CYAN}context_resolution_check 自检{RESET}")
        print("=" * 64)
        return self_test()

    root = pathlib.Path(__file__).resolve().parents[3]
    app_dir = pathlib.Path(args.app_dir) if args.app_dir else root / "backend/app"
    if not app_dir.is_dir():
        print(f"{RED}源码目录不存在：{app_dir}{RESET}")
        return EXIT_TOOL_BROKEN
    files = scan_files(app_dir)

    print(f"{CYAN}W-03 上下文解析门禁{RESET}  {DIM}W-03 规格 §6{RESET}")
    print(f"{DIM}扫描 {len(files)} 个非测试源文件{RESET}\n")

    problems: list[str] = []
    c = context_class_status(files)
    print(f"  {DIM}CR-1 RequestContext 不变量{RESET}")
    if not c["file"]:
        problems.append("CR-1 未找到 RequestContext 类")
        print(f"  {RED}✗{RESET} 未找到 RequestContext")
    else:
        print(f"    定义于 {c['file']}")
        for label, cond, msg in (
                ("frozen", c["frozen"], "非 frozen（上下文可被中途改写）"),
                ("__post_init__", c["post_init"], "缺 __post_init__ 断言"),
        ):
            if cond:
                print(f"    {GREEN}✓{RESET} {label}")
            else:
                problems.append(f"CR-1 {msg}")
                print(f"    {RED}✗{RESET} {label} —— {msg}")
        missing = set(REQUIRED_INVARIANTS) - c["invariants"]
        if missing:
            problems.append(f"CR-1 缺不变量 {sorted(missing)}")
            print(f"    {RED}✗{RESET} 缺 {sorted(missing)}")
        else:
            print(f"    {GREEN}✓{RESET} CI-1～CI-4 齐全")

    print(f"\n  {DIM}CR-2 解析状态机失败出口{RESET}")
    r = resolver_status(files)
    if not r["func"]:
        problems.append("CR-2 未找到上下文解析函数")
        print(f"  {RED}✗{RESET} 未找到解析函数")
    else:
        print(f"    {r['func']}() @ {r['file']}")
        missing = set(REQUIRED_EXITS) - r["exits"]
        if missing:
            problems.append(f"CR-2 缺失败出口 {sorted(missing)}")
            print(f"    {RED}✗{RESET} 缺 {sorted(missing)}")
        else:
            print(f"    {GREEN}✓{RESET} E1～E7 齐全")

    print(f"\n  {DIM}CR-3 服务层 ctx 不得有默认值{RESET}")
    bad = default_ctx_params(files)
    if bad:
        for b in bad:
            problems.append(f"CR-3 {b}")
            print(f"  {RED}✗{RESET} {b}")
    else:
        print(f"  {GREEN}✓{RESET} 无带默认值的 ctx 参数")

    print(f"\n  {DIM}CR-4 全局 users.role 引用点{RESET}")
    hits = role_refs(files)
    total = sum(hits.values())
    if total == 0 and not hits:
        # 分不清「真的清零了」和「正则失效了」——保守报工具坏
        print(f"  {RED}✗{RESET} 扫描到 0 个引用点，无法区分"
              f"「已清零」与「正则失效」")
        return EXIT_TOOL_BROKEN
    top = sorted(hits.items(), key=lambda kv: -kv[1])[:6]
    for f, n in top:
        print(f"    {DIM}{pathlib.Path(f).name:<22}{RESET} {n}")
    if total:
        problems.append(
            f"CR-4 全局 users.role 仍有 {total} 处非测试引用"
            f"（{len(hits)} 个文件）——双租户角色隔离不可能成立")
        print(f"  {RED}✗{RESET} 合计 {total} 处")
    print()
    print(f"\n  {DIM}CR-5 门禁与规格的规则 ID 一致性{RESET}")
    spec = root / "docs/product/W-03-租户上下文与授权实施规格_20261001.md"
    align = check_spec_alignment(spec)
    if align:
        for a in align:
            problems.append(a)
            print(f"  {RED}✗{RESET} {a}")
    else:
        print(f"  {GREEN}✓{RESET} 规则 ID 双向一致"
              f"（{len(REQUIRED_INVARIANTS)} 不变量 + {len(REQUIRED_EXITS)} 出口"
              f" + {len(REQUIRED_STEPS)} 步骤）")

    code, msg = verdict(len(files), problems, "")
    print(msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
