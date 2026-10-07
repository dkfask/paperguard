#!/usr/bin/env python3
"""AI 出站计量出口守卫（INC-86 残留风险）。

配套：docs/tenancy/command-contracts.md §4.3
被守护的对象：backend/app/api/ai.py 的计量出口 `_metered_chat`

它守的是什么
------------
INC-86 实测结论：6 条外部 AI 写操作**全部**经 `_metered_chat`（`api/ai.py:357-416`），
该函数是**唯一计量出口**（内含 `begin_call` 配额门禁 + `record_usage` 用量记录）。
`_chat_completion`（322-354）的 docstring 自述它就是为修「5 条路径不计量」而建的收敛点。

**但没有任何机制阻止下一个开发者直接调用 `_chat_completion(...)` 绕过计量**，
也没有机制阻止新模块自己发一次 `httpx.post(…/chat/completions)`。
**这正是修复后真正的头号待办**——本工具就是它的机器实现。

六条判据
--------
AE-1  **出站原语收敛**：任何把请求发往 AI 上游的代码
      （`/chat/completions`、`/v1/messages`、Anthropic 版本头、
      或 `httpx` 调用出现在含 AI 协议字面量的函数里）
      必须落在 `EGRESS_ALLOWED` 名单内。
      **不局限于 `api/ai.py`**——新建一个文件自己发 AI 请求也必须被报出来。
AE-2  **计量出口收敛**：`_chat_completion` 的**每一个**调用点（跨模块也算）
      必须落在 `METERING_EXITS` 内。
AE-3  **记账完整性**：每个计量出口必须同时含 `begin_call` 与 `record_usage`；
      且 `record_usage` 出现 **≥2** 次——**异常路径不记账是最容易静默发生的漏记**，
      成功路径记一次就足以通过「有没有记账」的检查。
AE-4  **跨模块不得绕过**：其他模块若引用 `_chat_completion` /
      `_dispatch_completion` / `_anthropic_completion`，
      同一个函数里**必须**出现 `_metered_chat`。
      这是真正的绕过向量——`ai_api._chat_completion(...)` 不经过任何门禁。
AE-5  **运行时用例覆盖每一个派发点**：静态扫出「经计量出口的写操作处理器」
      与 `test_ai_metering_coverage.py` 的 `_SCENARIOS` 做**路径级双向差集**。
      AE-1~AE-4 全是静态的——它们**看不见**「这个入口的 HTTP 用例没写」。
      ★ 首版这条只比「条数相等」，实测有**两个数字基数相同就互相抵消**的假绿：
      「新增一条派发点 + 复制一条已有场景」与「删掉一条场景 + 新增一条派发点」
      都是 6 == 6，新派发点静默失覆盖。对照实验（把实现退回首版后跑同两条变异）
      **两条都假绿**；现实现两条都报红。

AE-0  **防空转**：解析到 0 个出站点、0 个计量调用点、0 个派发点或 0 个场景
      ⇒ 报红。**「0 == 全部合规」是空洞通过**，那不是合规，是解析器坏了。

三条刻意不查的边界（如实登记，不假装覆盖）
------------------------------------------
1. **运行时是否真的计量**：`_metered_chat` 是否被执行、配额是否真被扣、
   上游账单是否对得上——**全部未验证**，需接口层 UAT 或账单对账。
2. **重试路径**：`record_usage` 的次数不等于上游请求次数（`base_url` 兜底会重试）。
   本工具**不判定重试是否漏计**。
3. **其他出站**（邮件、微信内容安全、CLI 联网）**不属于 AI 计量面**，
   本工具**不查也不应查**——把它们纳入只会产出纯误报。

退出码：0 = 通过；1 = 存在问题（差距已记录）；2 = 工具自身不可运行。
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
APP = REPO_ROOT / "backend/app"
AI_API = REPO_ROOT / "backend/app/api/ai.py"
# AE-5 的运行时侧：静态派发点数必须与这里的场景数一致
METERING_TEST = REPO_ROOT / "backend/tests/test_ai_metering_coverage.py"

EXIT_OK, EXIT_FINDINGS, EXIT_TOOL_BROKEN = 0, 1, 2
GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

# ── 白名单 ──────────────────────────────────────────────────────
# 两者都刻意**很短**：白名单是「允许绕过收敛点的唯一入口」，
# 每加一行等于开一个口子，所以必须逐条有理由。
EGRESS_ALLOWED = {
    "_anthropic_completion",   # api/ai.py:236-264  Anthropic /v1/messages 出口
    "_dispatch_completion",    # api/ai.py:419-484  OpenAI 兼容 /chat/completions 出口
}
METERING_EXITS = {
    "_metered_chat",           # api/ai.py:357-416  唯一计量出口
}
# ★ 整条链的**分层不变量**：`被调用者 → 只允许的调用者`
#   直觉上「唯一计量出口」只管最上面那一层，于是**在 ai.py 内部**直接调
#   `_dispatch_completion`（跳过熔断 + 计量）**完全看不见**。
#   把每层的上一层都写出来，这条不变量才闭得上。
INNER_CALLERS = {
    "_chat_completion":   METERING_EXITS,              # 只有计量出口能调
    "_dispatch_completion": {"_chat_completion"},      # 只有上一层能调
    "_anthropic_completion": {"_dispatch_completion"},  # 同上
}
# 可被外部引用的内部函数——引用它们**必须**在同一函数里经计量出口。
BYPASSABLE = {
    "_chat_completion",
    "_dispatch_completion",
    "_anthropic_completion",
}
# AI 协议标记：出现在某函数里即认为该函数「在谈 AI 上游」
AI_MARKERS = (
    "/chat/completions",
    "/v1/messages",
    "anthropic-version",
    "api.anthropic.com",
)
# 计量三件套（AE-3）
QUOTA_GATE = "begin_call"
USAGE_RECORD = "record_usage"
MIN_USAGE_RECORD_SITES = 2      # 成功 + 异常，缺一即漏记


# ══════════════════════════════════════════════════════════════════
# 提取
# ══════════════════════════════════════════════════════════════════

def _callee(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


WRITE_VERBS = {"post", "put", "patch", "delete"}


def _norm_path(p: str) -> str:
    """把路径变量段统一成 `:p`，让两侧口径可比。

    运行时侧的 URL 常写成 f-string（`f"/api/projects/{pid}/assist"`），
    FastAPI 侧的路由模板也是 `{pid}`——但**变量名常常不同**
    （`{project_id}` vs `{id}`），只比原文永远对不上。
    """
    return re.sub(r"\{[^}]+\}", ":p", p)


def _router_prefix(tree: ast.Module) -> str:
    """模块级 `router = APIRouter(prefix=...)` 的 prefix 字面量。

    只认**字面量**；`prefix=build_prefix()` 这类动态值返回空串，
    于是拼出的路径会缺前缀——**该模块的派发点因此拼不全**，
    会被 AE-5 当作「有派发点没有用例」报红（宁可误报也不静默漏判）。
    """
    for n in tree.body:
        if not isinstance(n, ast.Assign) or not isinstance(n.value, ast.Call):
            continue
        fn = n.value.func
        nm = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if nm != "APIRouter":
            continue
        for kw in n.value.keywords:
            if (kw.arg == "prefix" and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)):
                return kw.value.value
    return ""


def collect_dispatch_routes(app: pathlib.Path) -> dict[str, str]:
    """AE-5 静态侧：返回 `{写操作处理器名: 归一化完整路径}`。

    三个刻意的口径选择，每个都对应一类**会造成假绿的写法**：

    1. **AST 调用计数**判定「是否真调计量出口」。首版写的是
       `if "_metered_chat" in body`——**文本搜索**。只要某个处理器的
       docstring 里写一句「本接口经 `_metered_chat` 计量」，
       一个**根本不调**的入口就会被算成派发点。
       这与 AE-3 首版 `body.count("begin_call")` 被 docstring 骗过是同一条。
    2. **完整路径**（`router.prefix` + 装饰器路径）而不是裸装饰器路径。
       本仓 `main.py` 的 15 条 `include_router(...)` **全部不带 `prefix=`**，
       所以模块内 prefix 就是完整前缀。**这是实测结论，不是假设**——
       若将来有人给某个 `include_router` 加了 `prefix=`，这里会拼短，
       表现为「有派发点没有用例」⇒ 报红，而不是静默漏判。
    3. 路径变量段统一成 `:p`（见 `_norm_path`）。
    """
    out: dict[str, str] = {}
    for f in sorted(app.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        prefix = _router_prefix(tree)
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            route = None
            for d in n.decorator_list:
                if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                        and d.func.attr.lower() in WRITE_VERBS
                        and isinstance(d.func.value, ast.Name)
                        and d.func.value.id == "router"):
                    continue
                if (d.args and isinstance(d.args[0], ast.Constant)
                        and isinstance(d.args[0].value, str)):
                    route = _norm_path(prefix + d.args[0].value)
            if route is None:
                continue
            # ★ 真实调用，不是文本提及
            if any(isinstance(c, ast.Call) and _callee(c.func) == "_metered_chat"
                   for c in ast.walk(n)):
                out[n.name] = route
    return out


def _url_expr_to_path(node) -> str | None:
    """把 `{"url": <expr>}` 里的表达式还原成归一化路径。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _norm_path(node.value)
    if isinstance(node, ast.JoinedStr):          # f-string
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                parts.append(":p")
            else:
                return None
        return _norm_path("".join(parts))
    return None


def _setup_url(fn: ast.AST) -> str | None:
    """从 `_scn_*` 的 `return headers, {"url": <expr>, ...}` 里取 url。"""
    found = None
    for n in ast.walk(fn):
        if not isinstance(n, ast.Dict):
            continue
        for k, v in zip(n.keys, n.values):
            if isinstance(k, ast.Constant) and k.value == "url":
                found = _url_expr_to_path(v)
    return found


def collect_runtime_urls(test_file: pathlib.Path) -> dict[str, str] | None:
    """AE-5 运行时侧：返回 `{场景标签: 归一化 url}`。

    **任一环解析不出就整体返回 None**（由判据报红），绝不静默跳过那一条——
    跳过后「6 个场景」会变成「5 个场景」而**看起来只是个更小的正常数字**，
    差集随之消失，判据当场失效。
    """
    if not test_file.is_file():
        return None
    try:
        tree = ast.parse(test_file.read_text(encoding="utf-8"))
    except SyntaxError:
        return None
    scenarios = None
    funcs: dict[str, ast.AST] = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs[n.name] = n
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Name) and t.id == "_SCENARIOS"
                        and isinstance(n.value, (ast.List, ast.Tuple))):
                    scenarios = n.value
    if scenarios is None:
        return None
    out: dict[str, str] = {}
    for el in scenarios.elts:
        label = setup = None
        if isinstance(el, (ast.Tuple, ast.List)) and len(el.elts) == 2:
            if isinstance(el.elts[0], ast.Constant) and isinstance(el.elts[0].value, str):
                label = el.elts[0].value
            if isinstance(el.elts[1], ast.Name):
                setup = el.elts[1].id
        if label is None or setup is None or setup not in funcs:
            return None
        url = _setup_url(funcs[setup])
        if url is None:
            return None
        out[label] = url
    return out or None


def check_coverage(routes: dict[str, str],
                   urls: dict[str, str] | None) -> list[str]:
    """AE-5：运行时用例必须**逐个**覆盖静态派发点（双向差集）。

    ★ 首版只比「条数相等」，于是有两个数字**基数相同就互相抵消**的假绿：
    「新增一条派发点 + 复制一条已有场景」与「删掉一条场景 + 新增一条派发点」
    都是 6 == 6，于是新派发点**静默失覆盖**。
    这与 INC-85「最关键的面若只以一个数字存在，差集永远判不出来」同源：
    **差集两侧必须都是可枚举的集合，而不是两个数。**
    """
    if urls is None:
        return ["AE-5 解析不到 `test_ai_metering_coverage.py` 的 `_SCENARIOS`"
                "或其中任一场景的 `url`（解析失败 = 不可核对，"
                "**不得当作「两侧一致」**）"]
    if not routes:
        return ["AE-5 静态侧扫到 0 个调用计量出口的写操作处理器——"
                "判据无法运行，不得据此得出「运行时已覆盖」"]
    if not urls:
        return ["AE-5 运行时侧扫到 0 个场景——判据无法运行，"
                "不得据此得出「静态派发点已覆盖」"]

    f: list[str] = []
    dup = sorted({p for p in routes.values()
                  if list(routes.values()).count(p) > 1})
    if dup:
        f.append(f"★ AE-5 静态侧有 {len(dup)} 条派发点落在同一路径上 {dup}——"
                 "静态侧统计口径已不可靠，下面的差集不可信")

    by_path = {v: k for k, v in routes.items()}      # 路径 → 处理器名
    url_paths = set(urls.values())                   # ★ urls 的 **key 是标签**，
                                                    #   路径在 **value**；拿 key 去比
                                                    #   会让「每个标签都成孤儿」。
    missing = sorted(p for p in by_path if p not in url_paths)
    orphan = sorted(u for u in url_paths if u not in by_path)
    if missing:
        f.append(f"AE-5 **{len(missing)}/{len(by_path)} 个派发点没有 HTTP 侧用例**"
                 f"：{missing}（对应处理器 {sorted(by_path[p] for p in missing)}）"
                 f" ⇒ 新增 AI 命令时忘了在 `test_ai_metering_coverage.py` 加场景")
    if orphan:
        f.append(f"AE-5 **{len(orphan)}/{len(urls)} 个运行时用例没有对应派发点**"
                 f"：{orphan} ⇒ 用例指向了不存在的入口，"
                 "或该入口已不再经计量出口")
    if not missing and not orphan and len(by_path) != len(urls):
        f.append(f"★ AE-5 两侧条数不等（{len(by_path)} vs {len(urls)}）"
                 "但差集为空——存在重复登记，统计口径需要复核")
    return f


def scan(app: pathlib.Path) -> dict:
    """扫出四类事实。**不读任何文档**——避免自我印证。

    ★ **两遍扫描**：先登记所有函数定义，再处理调用点。
      首版把两者放在同一次 `ast.walk` 里，而 `ast.walk` 是**广度优先**，
      `_dispatch_completion` 的调用（346 行）先于它的定义（419 行）被看到——
      于是「这个被调函数定义在哪个文件」这个判据**时对时错**，
      而它决定 AE-4 收不收这条引用。**顺序依赖的判据 = 不稳定的判据。**
    """
    funcs: dict[str, list[dict]] = {}     # 函数名 → [{file, lineno, ...}]
    egress: list[dict] = []                # AI 出站原语
    calls_to: dict[str, list] = {}         # 函数名 → 调用点
    chat_calls: list[dict] = []            # _chat_completion 的调用点
    bypass_refs: list[dict] = []           # 对可绕过函数的**跨文件**引用
    metering_parts: list[dict] = []        # 计量出口的 AST 调用计数

    trees: list[tuple[str, str, ast.Module]] = []
    for f in sorted(app.rglob("*.py")):
        rel = str(f.relative_to(app))
        src = f.read_text(encoding="utf-8")
        try:
            trees.append((rel, src, ast.parse(src)))
        except SyntaxError:
            continue

    # ── 第一遍：登记函数定义 ────────────────────────────────
    for rel, src, tree in trees:
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.setdefault(n.name, []).append(
                    {"file": rel, "lineno": n.lineno, "end": n.end_lineno,
                     "src": src, "node": n})
                if n.name in METERING_EXITS:
                    # ★ **AST 调用计数，不是文本计数**。
                    #   首版用 `body.count("begin_call")`，而 `_metered_chat` 的
                    #   **docstring 里就写着**「`begin_call` 扣每分钟突发与每日配额」
                    #   ——于是**把配额门禁整行删掉，门禁依然全绿**。
                    #   这是 INC-85「grep 行数 ≠ 表达式计数」的**同一条**，
                    #   而我在写新工具时又犯了一次：
                    #   **记录了某个陷阱 ≠ 免疫该陷阱。**
                    gate = usage = 0
                    for c in ast.walk(n):
                        if not isinstance(c, ast.Call):
                            continue
                        cn = _callee(c.func)
                        if cn == QUOTA_GATE:
                            gate += 1
                        elif cn == USAGE_RECORD:
                            usage += 1
                    metering_parts.append({"name": n.name, "file": rel,
                                           "lineno": n.lineno,
                                           "quota_gate_calls": gate,
                                           "usage_calls": usage})

    def defined_in(name: str, rel: str) -> bool:
        return any(d["file"] == rel for d in funcs.get(name, []))

    # ── 第二遍：处理调用点与出站原语 ────────────────────────
    for rel, src, tree in trees:
        # ★ 宿主函数区间**预建一次**。首版对每个 Call 都 `ast.walk(tree)` 一遍
        #   找宿主函数 —— O(calls × nodes)，而 `api/workflow.py` 那种大文件
        #   直接把 `ai_egress_check --plain` 推到 **43 秒**，
        #   进而把 `design_doc_check`（DC-1 会逐个实跑全部 42 道门禁）
        #   **顶过 300s 超时**。**我加的判据把别人的门禁搞坏了，
        #   而那个门禁的失败看起来像它自己的问题。**
        #   改成「每文件预建区间表 + 线性取最内层」后，复杂度降到 O(calls × funcs)。
        fn_ranges: list[tuple[int, int, str]] = []
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn_ranges.append((n.lineno, n.end_lineno or n.lineno, n.name))
        fn_ranges.sort()

        def host(node):
            best = None
            for lo, hi, nm in fn_ranges:
                if lo <= node.lineno <= hi:
                    if best is None or lo > best[0]:
                        best = (lo, nm)
            return best[1] if best else None

        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            nm = _callee(n.func)
            hname = host(n)
            hline = n.lineno
            for lo, hi, _n2 in fn_ranges:
                if lo <= n.lineno <= hi and lo == n.lineno:
                    hline = lo
                    break

            if nm in BYPASSABLE or nm in METERING_EXITS:
                calls_to.setdefault(nm, []).append(
                    {"file": rel, "lineno": n.lineno, "host": hname,
                     "host_line": hline})
            if nm == "_chat_completion":
                chat_calls.append({"file": rel, "lineno": n.lineno,
                                   "host": hname, "host_line": hline})

            # ── AE-4 素材：**跨文件**引用才算绕过 ──────────────
            # 同文件内的 `_chat_completion → _dispatch_completion` 是正常链路，
            # 由 AE-2 的分层不变量管；把它们也算成「绕过」会对**完全正确的
            # 代码**报红（实测真基线 2 条误报）。
            if (nm in BYPASSABLE and hname is not None
                    and hname not in METERING_EXITS and nm != hname
                    and not defined_in(nm, rel)):
                for lo, hi, fname in fn_ranges:
                    if lo <= n.lineno <= hi and fname == hname:
                        body = "\n".join(src.split("\n")[lo - 1:hi])
                        bypass_refs.append({
                            "name": nm, "file": rel, "lineno": n.lineno,
                            "host": fname, "host_line": lo,
                            # ★ 宿主函数体**在扫描时就带出来**：否则判据里要
                            #   重新定位，而「重新定位」正是最容易写出绕法的地方。
                            "host_body": body,
                        })
                        break

            # ── 出站原语：httpx 调用 ────────────────────────
            uses_httpx = (
                nm.startswith("httpx")
                or (isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "httpx")
            )
            if not uses_httpx:
                continue
            if hname is None:
                body = src
            else:
                for lo, hi, fname in fn_ranges:
                    if lo <= n.lineno <= hi and fname == hname:
                        body = "\n".join(src.split("\n")[lo - 1:hi])
                        break
            if any(mk in body for mk in AI_MARKERS) or hname is None:
                egress.append({"file": rel, "lineno": n.lineno,
                               "host": hname, "host_line": hline})
    # ★★★ `metering_parts` **必须出现在返回值里**。
    #   首版把它加进了局部列表却忘了加进 return，于是 AE-3 整条判据
    #   **从未被求值**——而 `sc.get("metering_parts", [])` 的默认值是空列表，
    #   **AE-3 静默失效、工具照样全绿**。这与「判据接了线但没通电」同族：
    #   **一条不会响的判据，看起来和一条通过了的判据完全一样。**
    return {"funcs": funcs, "egress": egress, "chat_calls": chat_calls,
            "calls_to": calls_to, "bypass_refs": bypass_refs,
            "metering_parts": metering_parts}


# ══════════════════════════════════════════════════════════════════
# 判据
# ══════════════════════════════════════════════════════════════════

def check(sc: dict) -> list[str]:
    f: list[str] = []

    # ── AE-0 防空转 ────────────────────────────────────────────
    if not sc["egress"]:
        return ["AE-0 未扫描到任何 AI 出站原语——判据无法运行，"
                "不得据此得出「出站已收敛」"]
    if not sc["chat_calls"]:
        return ["AE-0 未扫描到任何 `_chat_completion` 调用——判据无法运行，"
                "不得据此得出「计量出口已收敛」"]

    # ── AE-1 出站原语收敛 ─────────────────────────────────────
    bad = [e for e in sc["egress"] if e["host"] not in EGRESS_ALLOWED]
    if bad:
        for e in bad:
            f.append(f"AE-1 AI 出站原语在 `{e['file']}:{e['lineno']}`"
                     f"（函数 `{e['host'] or '（模块层）'}`），不在白名单 {sorted(EGRESS_ALLOWED)} 内"
                     "——**未经计量与配额门禁的上游请求**")
    for name in sorted(EGRESS_ALLOWED):
        if name not in sc["funcs"]:
            f.append(f"AE-1 白名单里的 `{name}` 在代码中**已不存在**"
                     "（白名单与现实漂移 ⇒ 该行是失效的放行口子）")

    # ── AE-2 分层收敛：每一层的调用者只能是上一层 ──────────────
    # 首版 AE-2 只管 `_chat_completion`，于是「在 ai.py 内部直接调
    # `_dispatch_completion` 跳过熔断与计量」是**看不见的**。
    # 这里把整条链的不变量写全（`_dispatch_completion` 与
    # `_anthropic_completion` 是 AI egress 模块**内部**的层级，
    # 只有本模块内的上一层才允许调它们）。
    for callee, allowed_callers in INNER_CALLERS.items():
        for c in sc["calls_to"].get(callee, []):
            if c["host"] in allowed_callers:
                continue
            f.append(f"AE-2 `{c['file']}:{c['lineno']}` 的 `{c['host'] or '（模块层）'}`"
                     f"直接调用了 `{callee}`，而它只允许被 {sorted(allowed_callers)} 调用"
                     " ⇒ 绕过该层的收敛点")
    for callee in INNER_CALLERS:
        if callee not in sc["funcs"]:
            f.append(f"AE-2 收敛链上的 `{callee}` 在代码中**已不存在**（白名单漂移）")
    for name in sorted(METERING_EXITS):
        if name not in sc["funcs"]:
            f.append(f"AE-2 计量出口 `{name}` 在代码中**已不存在**")

    # ── AE-3 记账完整性（**AST 调用计数**，不是文本计数）────────
    # ★ 这里**必须**用 `metering_parts` 里 scan 已算好的 AST 计数。
    #   首版用 `body.count("begin_call")`，而 `_metered_chat` 的 docstring
    #   原文就写着「`begin_call` 扣每分钟突发与每日配额」——
    #   **把配额门禁整行删掉，工具依然全绿**（真实代码注入变异 V3 实测漏网）。
    for d in sc.get("metering_parts", []):
        name, where = d["name"], f"`{d['file']}:{d['lineno']}`"
        if d["quota_gate_calls"] == 0:
            f.append(f"AE-3 计量出口 `{name}`（{where}）**没有调用 {QUOTA_GATE}**"
                     " ⇒ 无配额门禁（**按 AST 调用计数，不按文本**——"
                     "该函数 docstring 里就写着 `begin_call`）")
        if d["usage_calls"] == 0:
            f.append(f"AE-3 计量出口 `{name}`（{where}）**没有调用 {USAGE_RECORD}**"
                     " ⇒ 用量不落库")
        elif d["usage_calls"] < MIN_USAGE_RECORD_SITES:
            f.append(f"AE-3 计量出口 `{name}` 只记了 {d['usage_calls']} 次用量"
                     f"（至少要 {MIN_USAGE_RECORD_SITES} 次：成功 + 上游异常）"
                     "⇒ **异常路径漏记**")

    # ── AE-4 跨模块不得绕过 ───────────────────────────────────
    for r in sc["bypass_refs"]:
        if "_metered_chat" not in r["host_body"]:
            f.append(f"AE-4 `{r['file']}:{r['lineno']}` 的 `{r['host']}`"
                     f"直接引用了 `{r['name']}`，同一函数内**没有** `_metered_chat`"
                     " ⇒ 跨模块绕过计量出口")
    return f


# ══════════════════════════════════════════════════════════════════
# 夹具（INC-85 教训：声明侧与现实侧的旋钮必须分开）
# ══════════════════════════════════════════════════════════════════

GOOD_SRC = '''
import httpx

def _anthropic_completion(cfg, messages, temperature, max_tokens=16):
    with httpx.Client(timeout=5) as c:
        return c.post(cfg["base_url"] + "/v1/messages", json={})

def _dispatch_completion(cfg, messages, temperature, max_tokens=16):
    if cfg.get("anthropic"):
        return _anthropic_completion(cfg, messages, temperature, max_tokens)
    with httpx.Client(timeout=5) as c:
        return c.post(cfg["base_url"] + "/chat/completions", json={})

def _chat_completion(cfg, messages, temperature, max_tokens=16):
    return _dispatch_completion(cfg)

def _metered_chat(store, user, cfg, messages, temperature, *, action, source):
    ai_governance.begin_call(store, user)
    try:
        r = _chat_completion(cfg, messages, temperature)
    except Exception:
        ai_governance.record_usage(store, user, action=action, result="upstream_error")
        raise
    ai_governance.record_usage(store, user, action=action, result="success")
    return r

def cmd_ok(cfg, messages):
    return _metered_chat(cfg, messages, action="a", source="s")
'''
# ★ 夹具**必须定义白名单里的每一个函数**。
#   首版只写了 `_dispatch_completion`，而白名单还有 `_anthropic_completion`
#   ⇒ AE-1 的「白名单条目在代码中不存在」在**干净样本上就报红**，
#   干净夹具因此**永远不可能全绿**——后面每条「应报红」的用例都失去了对照。
#   与「夹具必须覆盖被测对象的全集」同一族。


def _fixture(files: dict[str, str]) -> dict:
    """从「文件名 → 源码」造一份 scan 结果。

    ★ 必须是**多文件**能力：AE-4 判的是**跨文件**绕过，
      单文件夹具在结构上就构造不出跨模块引用——那条判据会「因为测不到」
      而长期保持绿色。与 INC-85 的「两侧旋钮必须分开」同一族：
      **造不出被测状态的夹具，等于没有测那条判据。**
    """
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp(prefix="ae-"))
    for name, src in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    return scan(d)


def self_test() -> int:
    print(f"{CYAN}ai_egress_check 自检{RESET}")
    print("=" * 68)
    cases: list[tuple[str, bool]] = []

    def said(files, *words):
        return any(all(w in m for w in words) for m in check(_fixture(files)))

    one = {"app/ai.py": GOOD_SRC}

    # ── 阴性：合规样本必须零 finding ──
    cases.append(("干净样本 → 零 finding", not check(_fixture(one))))

    # ── AE-0 防空转（**必须先于 AE-1 夹具**，否则下面所有用例都被它遮住）──
    cases.append(("★ AE-0 扫不到出站原语 → 报红而不是「已收敛」",
                  any(m.startswith("AE-0") for m in check(_fixture({"app/x.py": "x = 1\n"})))))
    cases.append(("★ AE-0 扫不到 `_chat_completion` 调用 → 报红",
                  any(m.startswith("AE-0") for m in check(_fixture(
                      {"app/x.py": 'def f():\n    with httpx.Client() as c:\n'
                                   '        return c.post("http://x/chat/completions")\n'})))))

    # ── AE-1 ──
    cases.append(("★ AE-1 新函数自己发 AI 请求 → 报红", said(
        {"app/ai.py": GOOD_SRC + '\n\ndef sneak(cfg):\n'
                              '    with httpx.Client() as c:\n'
                              '        return c.post(cfg["base_url"] + "/chat/completions", json={})\n'},
        "AE-1", "sneak")))
    # ★ 删的是**白名单里**的函数；删 `_chat_completion` 测不到任何东西
    cases.append(("★ AE-1 白名单函数被删 → 报红（失效的放行口子）", said(
        {"app/ai.py": GOOD_SRC.replace("def _anthropic_completion", "def _gone_completion")},
        "AE-1", "已不存在")))

    # ── AE-2 分层不变量 ──
    cases.append(("★ AE-2 绕过计量出口直接调 `_chat_completion` → 报红", said(
        {"app/ai.py": GOOD_SRC + '\n\ndef cheat(cfg, messages):\n'
                              '    return _chat_completion(cfg, messages, 0.5)\n'},
        "AE-2", "cheat")))
    cases.append(("★ AE-2 在本模块内跳过熔断直调 `_dispatch_completion` → 报红", said(
        {"app/ai.py": GOOD_SRC + '\n\ndef skip_breaker(cfg):\n'
                              '    return _dispatch_completion(cfg, [], 0.5)\n'},
        "AE-2", "skip_breaker")))

    # ── AE-3 ──
    cases.append(("★ AE-3 删掉配额门禁 `begin_call` → 报红", said(
        {"app/ai.py": GOOD_SRC.replace("ai_governance.begin_call(store, user)", "pass")},
        "AE-3", "没有调用 begin_call")))
    cases.append(("★ AE-3 删掉全部用量记录 → 报红", said(
        {"app/ai.py": GOOD_SRC.replace("ai_governance.record_usage", "log_it")},
        "AE-3", "没有调用 record_usage")))
    cases.append(("★ AE-3 只在成功路径记账（异常路径漏记）→ 报红", said(
        {"app/ai.py": GOOD_SRC.replace(
            '        ai_governance.record_usage(store, user, action=action, '
            'result="upstream_error")\n', "")},
        "AE-3", "异常路径漏记")))

    # ── AE-4 跨文件 ──
    cases.append(("★ AE-4 跨文件直接引用 `_chat_completion` → 报红", said(
        {"app/ai.py": GOOD_SRC,
         "app/policies.py": "from .ai import _chat_completion\n\n"
                            "def do(cfg, messages):\n"
                            "    return _chat_completion(cfg, messages, 0.5)\n"},
        "AE-4", "do")))
    cases.append(("阴性) 跨文件经 `_metered_chat` 调用 → 不报红", not said(
        {"app/ai.py": GOOD_SRC,
         "app/policies.py": "from .ai import _metered_chat\n\n"
                            "def do(cfg, messages):\n"
                            "    return _metered_chat(cfg, messages, action='a', source='s')\n"},
        "AE-4")))
    # 阴性：同文件内的正常分层链路**不得**被当成绕过（首版真基线 2 条误报）
    cases.append(("阴性) 同文件内 `_chat_completion → _dispatch_completion` 不算绕过",
                  not check(_fixture(one))))

    # ★★ 回归守卫：**docstring 里提到 ≠ 真的调用**。
    #   首版 AE-3 用 `body.count("begin_call")` 文本计数，而 `_metered_chat` 的
    #   docstring 原文就写着「`begin_call` 扣每分钟突发与每日配额」——
    #   于是**把配额门禁整行删掉，工具依然全绿**（真实代码注入变异 V3 实测漏网）。
    #   这条夹具就是那个漏网场景的最小复现。
    cases.append(("★ AE-3 回归：docstring 提到 `begin_call` 但未调用 → 报红", said(
        {"app/ai.py": GOOD_SRC.replace(
            "    ai_governance.begin_call(store, user)\n",
            "") .replace(
            "def _metered_chat(store, user, cfg, messages, temperature, *, action, source):\n",
            'def _metered_chat(store, user, cfg, messages, temperature, *, action, source):\n'
            '    """begin_call 扣每分钟突发与每日配额；record_usage 记用量。"""\n')},
        "AE-3", "没有调用 begin_call")))

    # ── AE-5 静态派发点 ↔ 运行时用例场景 ──────────────────────
    # ★ 两组夹具**必须是两个独立旋钮**：`collect_dispatch_routes(app_dir)`
    #   与 `collect_runtime_urls(test_file)` 各自读自己的目录。若只用一个
    #   共享夹具，「静态新增一条」和「运行时删掉一条」会同时发生，
    #   结构上**造不出真正的差集**（与 INC-85 夹具两侧同旋钮同族）。
    import tempfile as _tf

    def rfix(**handlers) -> dict[str, str]:
        """AE-5 静态侧夹具：`name -> (完整路径, 是否真调计量出口)`。"""
        d = pathlib.Path(_tf.mkdtemp(prefix="ae5r-"))
        for name, (path, call) in handlers.items():
            body = ('    return _metered_chat(store, user, cfg, messages, '
                    '0.5, action="a", source="s")' if call else
                    '    """本接口经 `_metered_chat` 计量。"""\n    return None')
            parent, _, leaf = path.rpartition("/")
            (d / f"m_{name}.py").write_text(
                "from fastapi import APIRouter\n"
                f'router = APIRouter(prefix="{parent}")\n\n'
                f'@router.post("/{leaf}")\n'
                f"async def {name}(store, user, cfg, messages):\n{body}\n",
                encoding="utf-8")
        return collect_dispatch_routes(d)

    def ufix(**scenarios) -> dict[str, str] | None:
        """AE-5 运行时侧夹具：`标签 -> url`。"""
        d = pathlib.Path(_tf.mkdtemp(prefix="ae5u-"))
        rows = "".join(f'    ("{label}", _scn_{i}),\n'
                       for i, label in enumerate(scenarios))
        src = "".join(f'def _scn_{i}():\n'
                      f'    return {{}}, {{"url": "{url}", "json": {{}}}}\n'
                      for i, url in enumerate(scenarios.values()))
        p = d / "m.py"
        p.write_text(src + "_SCENARIOS = [\n" + rows + "]\n", encoding="utf-8")
        return collect_runtime_urls(p)

    def cov(routes, urls):
        return check_coverage(routes, urls)

    # ★ 方向别搞反：`collect_dispatch_routes` 返回的是 `{处理器名: 路径}`。
    #   写反了不会被静默吞掉——下面的 assert 会当场炸（与 INC-85「差集方向
    #   一反就误删合法条目」同源，故此处显式断言方向本身）。
    R_OK = {"a1": "/api/ai/assist", "a2": "/api/specs/parse"}
    U_OK = {"写作辅助": "/api/ai/assist", "规范解析": "/api/specs/parse"}
    assert rfix(a1=("/api/ai/assist", True),
                a2=("/api/specs/parse", True)) == R_OK, \
        f"AE-5 静态侧夹具方向错了：{rfix(a1=('/api/ai/assist', True), a2=('/api/specs/parse', True))}"
    assert ufix(**U_OK) == U_OK, f"AE-5 运行时侧夹具：{ufix(**U_OK)}"

    cases.append(("阴性) AE-5 两侧路径逐条一致 → 不报红",
                  not cov(R_OK, U_OK)))
    cases.append(("★ AE-5 新增派发点无场景 → 报红（新增 AI 命令忘了加用例）",
                  any("没有 HTTP 侧用例" in m and "/api/ai/chat" in m
                      for m in cov({**R_OK, "a3": "/api/ai/chat"}, U_OK))))
    cases.append(("★ AE-5 孤儿场景（指向不存在的入口）→ 报红",
                  any("没有对应派发点" in m for m in cov(
                      R_OK, {**U_OK, "幽灵": "/api/ai/ghost"}))))
    # ★★★ 这条正是首版「只比条数」的**漏网样本**：两个数字基数相同，
    #   一侧多出的派发点被另一侧多出的幽灵场景在计数上互相抵消。
    cases.append(("★★ AE-5 条数相同但集合不同 → **必须**报红（数字比对的假绿）",
                  any("没有 HTTP 侧用例" in m and "/api/ai/chat" in m
                      for m in cov({**R_OK, "a3": "/api/ai/chat"},
                                   {k: v for k, v in U_OK.items()
                                    if k != "规范解析"}))))
    cases.append(("★ AE-5 运行时侧解析失败 → 报红，**不得当作「两侧一致」**",
                  any(m.startswith("AE-5") for m in cov(R_OK, None))))
    cases.append(("★ AE-5 静态侧扫到 0 个派发点 → 报红（防空转）",
                  any(m.startswith("AE-5") for m in cov({}, U_OK))))
    cases.append(("★ AE-5 运行时侧扫到 0 个场景 → 报红（防空转）",
                  any(m.startswith("AE-5") for m in cov(R_OK, {}))))
    # ★ 静态侧「docstring 提到 ≠ 真调」的回归守卫。若静态侧退回文本搜索，
    #   `a_ghost` 会被算成派发点，两侧就「恰好一致」→ 本条**不报红**而失败。
    cases.append(("★ AE-5 回归：docstring 提到 `_metered_chat` 但未真调"
                  " → 不算派发点（AST 计数）",
                  any("没有对应派发点" in m and "/api/ai/ghost" in m
                      for m in cov(rfix(a1=("/api/ai/assist", True),
                                       a_ghost=("/api/ai/ghost", False)),
                                   {**U_OK, "幽灵": "/api/ai/ghost"}))))
    cases.append(("★ AE-5 两条派发点落在同一路径 → 报红（统计口径已不可靠）",
                  any("同一路径" in m for m in cov(
                      {**R_OK, "a3": "/api/ai/assist"},
                      {**U_OK, "重复": "/api/ai/assist"}))))

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
    if not AI_API.is_file():
        sys.stderr.write(f"缺少输入：{AI_API}\n")
        return EXIT_TOOL_BROKEN
    try:
        sc = scan(APP)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"扫描失败：{exc}\n")
        return EXIT_TOOL_BROKEN

    routes = collect_dispatch_routes(APP)
    urls = collect_runtime_urls(METERING_TEST)
    findings = check(sc) + check_coverage(routes, urls)
    print("=" * 72)
    print("AI 出站计量出口守卫（INC-86 残留风险）")
    print("=" * 72)
    print(f"{DIM}reality：backend/app（AST 静态扫描）{RESET}")
    print(f"  AI 出站原语        {len(sc['egress'])} 处 · "
          f"白名单 {sorted(EGRESS_ALLOWED)}")
    print(f"  `_chat_completion` 调用 {len(sc['chat_calls'])} 处 · "
          f"计量出口 {sorted(METERING_EXITS)}")
    print(f"  可绕过函数的外部引用  {len(sc['bypass_refs'])} 处")
    print(f"  静态派发点 / 运行时场景 {len(routes)} / "
          f"{'解析失败' if urls is None else len(urls)}")
    if urls is not None and routes:
        hit = len(set(routes.values()) & set(urls.values()))
        print(f"  {DIM}路径级差集：已对上 {hit} · "
              f"缺用例 {len(set(routes.values()) - set(urls.values()))} · "
              f"孤儿用例 {len(set(urls.values()) - set(routes.values()))}{RESET}")
    print()
    for x in findings:
        print(f"  {RED}✗{RESET} {x}")
    print("=" * 72)
    if findings:
        print(f"{RED}未通过{RESET}（{len(findings)} 项）")
        return EXIT_FINDINGS
    print(f"{GREEN}全部通过{RESET}")
    print(f"{DIM}未验证：运行时是否真计量 / 配额是否真被扣 / 上游账单是否对得上"
          f"（需接口层 UAT 或账单对账）{RESET}")
    return EXIT_OK


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
