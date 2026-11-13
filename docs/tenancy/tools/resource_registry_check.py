#!/usr/bin/env python3
"""资源登记表 ↔ 真实代码 一致性校验器（INC-85）。

配套：docs/tenancy/resource-registry.yaml

为什么需要它
------------
`resource-registry.yaml` 是**租户隔离改造的唯一入口清单**。它自己的
`meta.access_surface_note` 写着：「访问面是 **Store 的公开方法**，不是表名。
按表名 grep 在 10 张表上返回 0 命中却是真实热路径。**登记表与后续隔离改造
一律以方法为入口。**」

也就是说：**这个文件里的方法清单决定了哪些代码会被隔离改造审视到。**
一个没被登记的方法，在改造时不会有人看它——而这不会有任何告警。

INC-85 的实测后果
-----------------
本工具建立之前，`access_surfaces[store-methods]` 这条写着
「Store 的 111 个公开方法 = 真实数据访问面」`count: 111`，
**却一个方法名都没有列**。于是：

1. 「访问面全集」只是文件里的一个**数字**，不是一份**枚举**。
2. 「某个新方法没被登记」这种缺口**无法被判定**——
   差集的两侧没有一方是枚举出来的，差的只能是 `111` 和另一个猜出来的数。
3. 真正的全集是**三处并集**（25 张表的 `store_methods` 108 个 ∪
   `delegated-methods` 13 个，重叠 10 ⇒ 111），这个并集**只存在于读者心里**。
4. 更糟的是两个集合的**口径本就不同**（一处的问句是「访问哪张表」，
   另一处的问句是「方法体内有没有表名字符串」），文件里却没写明，
   读者按 `108 + 13 = 121 ≠ 111` 核对只会得出「这个文件写错了」。

它检查什么
----------
RR-1  **访问面必须被枚举**，且 reality 的公开方法全部已登记
      （`store-methods.methods` 不得缺失/为空；`reality - 已登记并集` 必须为空；
      `reality - store-methods.methods` 也必须为空——否则 `count` 又变回谎言）
RR-2  **可复算数字**由现实重算比对：`store_public_methods` / `store_total_methods` /
      `store_construction_points` / `depends_get_store` / `tables_registered` /
      `business_tables` / 各 `access_surface` 的 `count` / `count_total` / `count_by_phase`
RR-3  **幽灵方法**：登记了但 Store 上不存在
RR-4  **口径差异必须显式声明**：`overlap_with_delegated` 存在且等于实算交集
      （不声明的话，读者只能靠加法，而加法必然得出「文件写错了」）
RR-5  **路由与守卫口径**（INC-86）：`route_inventory.total` / `by_guard` /
      `public_routes` / `command_contracts.write_operations` 按现实重算；
      `method_note` 与 `public_routes_note` **必须存在**（方法说明不完整时，
      任何人复算都可能得到另一个数而不知情）
RR-6  **证据指针必须可解析**（INC-86）：`file:line` 指向的文件存在、行号在范围内；
      对 `{符号, loc}` 这种**无歧义两键配对**，额外要求**该行落在被点名符号的函数体内**
      ——`ai.py:674` 是**合法文件里的合法行号**，只查前两项**两项都通过**

两个必须写明的防空转
--------------------
`0 个公开方法`（RR-0）与 `0 条路由`（RR-5）都必须报红。
**「0 == 全部覆盖」是空洞通过**：那不是通过，是解析器坏了。

三个「本门禁查不到」的边界（如实登记，不假装覆盖）
---------------------------------------------------
1. **A/B/C 分类**（有幂等 / 有事务 / 都没有）**不做校验**——它需要在处理器体 ∪
   一层 service 的范围里判定事务与幂等，属语义分析。`command_contracts.classification`
   的 1 / 38 / 90 因此**只被人读、不被机器核对**。
2. **多值与带尾注的指针**（`a.py:21,159,163,173`、`a.py:70-82 启动函数…`）
   **不做符号校验**——它们的「第一个数字」与符号没有对应关系，
   校验它们在实测中产出 **50 条纯误报**。**会误报的检查器等于没有检查器。**
3. **散落在散文里的指针不做任何校验**（只检查结构化字段）。

关于退出码
----------
0 = 通过；1 = 存在问题（差距已记录）；2 = 工具自身不可运行。
"""

from __future__ import annotations

import ast
import pathlib
import sys

try:  # 纯数据一致性工具，宁可明确报「工具不可运行」，也不猜 YAML
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
APP = REPO_ROOT / "backend/app"
REGISTRY = REPO_ROOT / "docs/tenancy/resource-registry.yaml"

EXIT_OK, EXIT_FINDINGS, EXIT_TOOL_BROKEN = 0, 1, 2
GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")


# ══════════════════════════════════════════════════════════════════
# 一、从现实里量
# ══════════════════════════════════════════════════════════════════

def measure(app: pathlib.Path) -> dict:
    """从真实代码量出登记表声称的那些数字。**不读登记表**，避免自我印证。"""
    db = app / "db.py"
    src = db.read_text(encoding="utf-8")
    cls = next((n for n in ast.parse(src).body
                if isinstance(n, ast.ClassDef) and n.name == "Store"), None)
    if cls is None:
        raise RuntimeError(f"{db} 里找不到 class Store")
    funcs = [n for n in cls.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    allm = {n.name for n in funcs}
    public = {n.name for n in funcs if not n.name.startswith("_")}

    def _callee(node) -> str:
        return (node.id if isinstance(node, ast.Name)
                else node.attr if isinstance(node, ast.Attribute) else "")

    ctor = dep = 0
    for f in sorted(app.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            # 口径 2：Store(...) 调用**表达式**数，排除以字符串为实参的调用
            if _callee(n.func) == "Store" and not any(
                    isinstance(a, ast.Constant) and isinstance(a.value, str)
                    for a in n.args):
                ctor += 1
            # 口径 1：Depends(get_store) 调用数。docstring 里的同名散文**不算**
            if _callee(n.func) == "Depends":
                for a in list(n.args) + [k.value for k in n.keywords]:
                    if _callee(a) == "get_store":
                        dep += 1

    return {
        "public": public,
        "all": allm,
        "store_public_methods": len(public),
        "store_total_methods": len(allm),
        "store_construction_points": ctor,
        "depends_get_store": dep,
    }


# ══════════════════════════════════════════════════════════════════
# 二、登记表侧
# ══════════════════════════════════════════════════════════════════

def surfaces(reg: dict) -> dict:
    return {a.get("id"): a for a in (reg.get("access_surfaces") or [])}


def table_methods(reg: dict) -> set[str]:
    out: set[str] = set()
    for t in reg.get("tables") or []:
        out |= set(t.get("store_methods") or [])
    return out


def surface_methods(reg: dict) -> set[str]:
    out: set[str] = set()
    for a in reg.get("access_surfaces") or []:
        out |= set(a.get("methods") or [])
    return out


# ══════════════════════════════════════════════════════════════════
# 三、判据
# ══════════════════════════════════════════════════════════════════

def check(reg: dict, meas: dict) -> list[str]:
    """返回 findings 列表（空 = 通过）。

    `meas` 与 `reg` 都由调用方注入，**自检因此可以用夹具而不是改真实文件**。
    """
    f: list[str] = []
    surf = surfaces(reg)
    tab = table_methods(reg)
    sm = surface_methods(reg)
    registered = tab | sm
    public = meas["public"]

    # ── 空洞通过防护 ────────────────────────────────────────────
    # 「reality 解析出 0 个方法」时，下面每条判据都会以「全部覆盖」通过。
    # 那不是通过，是解析器坏了。
    if not public:
        return ["RR-0 Store 公开方法解析出 0 个——判据无法运行，"
                "不得据此得出「访问面已全部登记」"]

    # ── RR-1 访问面必须被枚举，且 reality 全部已登记 ─────────────
    ms = surf.get("store-methods") or {}
    enumerated = ms.get("methods")
    if not enumerated:
        f.append("RR-1 `access_surfaces[store-methods]` 没有列出任何方法名"
                 "（只有 count 的话，「访问面全集」就只是一个数字，"
                 "『新方法漏登记』这种缺口永远无法判定）")
    else:
        gap = sorted(public - set(enumerated))
        if gap:
            f.append(f"RR-1 `store-methods.methods` 声称是访问面全集，"
                     f"但漏了 reality 里的 {len(gap)} 个公开方法：{gap}")
        ghost_enum = sorted(set(enumerated) - meas["all"])
        if ghost_enum:
            f.append(f"RR-1 `store-methods.methods` 列了 Store 上不存在的方法："
                     f"{ghost_enum}")

    missing = sorted(public - registered)
    if missing:
        f.append(f"RR-1 reality 里 {len(missing)} 个公开方法未被登记表任何位置登记"
                 f"（逐表 store_methods ∪ access_surfaces.methods）：{missing}")

    # ── RR-2 可复算数字 ───────────────────────────────────────
    meta = reg.get("meta") or {}
    recalc = {
        "store_public_methods": meas["store_public_methods"],
        "store_total_methods": meas["store_total_methods"],
        "store_construction_points": meas["store_construction_points"],
        "depends_get_store": meas["depends_get_store"],
        "tables_registered": len(reg.get("tables") or []),
    }
    for k, real in recalc.items():
        if k not in meta:
            f.append(f"RR-2 meta 缺少 `{k}`（不可复算的数字等于没有约束）")
        elif meta[k] != real:
            f.append(f"RR-2 `{k}` 声明 {meta[k]}，reality 实算 {real}")

    excl = meta.get("business_tables_excludes")
    if excl is None:
        f.append("RR-2 meta 缺少 `business_tables_excludes`：「哪张表不是业务表」"
                 "是人工判断，不列出排除清单就不可复算"
                 "（原文件只写 business_tables: 24，没写怎么排除的）")
    else:
        unknown = sorted(set(excl) - {t.get("resource") for t in (reg.get("tables") or [])})
        if unknown:
            f.append(f"RR-2 `business_tables_excludes` 含未登记的表：{unknown}")
        real_biz = len(reg.get("tables") or []) - len(
            [x for x in excl if x in {t.get("resource") for t in (reg.get("tables") or [])}])
        if meta.get("business_tables") != real_biz:
            f.append(f"RR-2 `business_tables` 声明 {meta.get('business_tables')}，"
                     f"按排除清单实算 {real_biz}")

    sc = surf.get("store-ctor") or {}
    locs = sc.get("locations_all_17") or sc.get("locations") or []
    if sc:
        if sc.get("count_total") != len(locs):
            f.append(f"RR-2 `store-ctor.count_total` 声明 {sc.get('count_total')}，"
                     f"实列 {len(locs)} 处构造点")
        phase = {}
        for it in locs:
            phase[it.get("phase")] = phase.get(it.get("phase"), 0) + 1
        if sc.get("count_by_phase") != phase:
            f.append(f"RR-2 `store-ctor.count_by_phase` 声明 {sc.get('count_by_phase')}，"
                     f"实算 {phase}")
    dm = surf.get("delegated-methods") or {}
    if dm and dm.get("count") != len(dm.get("methods") or []):
        f.append(f"RR-2 `delegated-methods.count` 声明 {dm.get('count')}，"
                 f"实列 {len(dm.get('methods') or [])} 个")
    if ms and enumerated and ms.get("count") != len(enumerated):
        f.append(f"RR-2 `store-methods.count` 声明 {ms.get('count')}，"
                 f"实列 {len(enumerated)} 个")

    # ── RR-3 幽灵方法 ─────────────────────────────────────────
    ghost = sorted(registered - meas["all"])
    if ghost:
        f.append(f"RR-3 登记表登记了 {len(ghost)} 个 Store 上不存在的方法：{ghost}")

    # ── RR-4 口径差异必须显式声明 ──────────────────────────────
    ov = ms.get("overlap_with_delegated")
    real_ov = len(tab & set(dm.get("methods") or []))
    if ov is None:
        f.append("RR-4 `store-methods` 缺少 `overlap_with_delegated` 声明："
                 "逐表 store_methods 与 delegated-methods 是**两个不同口径**的集合"
                 "（「访问哪张表」vs「方法体内有无表名字符串」），实测重叠 "
                 f"{real_ov} 个。不声明的话读者只能相加，"
                 f"而 {len(tab)} + {len(dm.get('methods') or [])} = "
                 f"{len(tab) + len(dm.get('methods') or [])} ≠ "
                 f"{len(public)}，必然得出「文件写错了」")
    elif ov != real_ov:
        f.append(f"RR-4 `overlap_with_delegated` 声明 {ov}，实算交集 {real_ov}")

    return f


# ══════════════════════════════════════════════════════════════════
# 三之二、路由与证据指针（INC-86）
# ══════════════════════════════════════════════════════════════════

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
ROLE_GUARDS = ("current_user", "require_manager", "require_admin",
               "require_template_manager", "trusted_context")


def measure_routes(app: pathlib.Path) -> dict:
    """数路由与守卫。**必须同时遍历 `FunctionDef` 与 `AsyncFunctionDef`。**

    INC-86 的起点就是这里：`ast.AsyncFunctionDef` **不是** `ast.FunctionDef`
    的子类（是兄弟类），只判 `isinstance(n, ast.FunctionDef)` 会漏掉全部
    `async def` 处理器——实测写操作 129 → 116（少 13 条 POST），而 116
    **看起来完全像一个正常的小数字**，于是「少 13 条」被当成了新事实。

    `Depends()` 有**三种**出现位置，只扫参数默认值会看不见后两种：
      ① 参数默认值 · ② 路由级 `dependencies=[...]` · ③ router 级 `APIRouter(dependencies=[...])`
    登记表 `by_guard` 的 25 / 4 拆分**依赖 ② ③**（feature flag），所以必须一起测。
    """
    # router 级 dependencies：模块里 `APIRouter(dependencies=[...])` 里点名的依赖
    router_level: dict[str, set[str]] = {}
    for f in sorted(app.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "APIRouter"):
                continue
            for kw in n.keywords:
                if kw.arg != "dependencies":
                    continue
                for x in ast.walk(kw.value):
                    nm = (x.id if isinstance(x, ast.Name)
                          else getattr(x, "attr", "") if isinstance(x, ast.Attribute) else "")
                    if nm:
                        router_level.setdefault(str(f), set()).add(nm)

    def dep_names(node) -> set[str]:
        out = set()
        for x in ast.walk(node):
            nm = (x.id if isinstance(x, ast.Name)
                  else getattr(x, "attr", "") if isinstance(x, ast.Attribute) else "")
            if nm:
                out.add(nm)
        return out

    rows: list[dict] = []
    for f in sorted(app.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decs = [d for d in n.decorator_list
                    if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and d.func.attr.lower() in HTTP_METHODS
                    and isinstance(d.func.value, ast.Name)
                    and d.func.value.id == "router"]
            if not decs:
                continue
            # ★ `defaults` 对应**末尾** len(defaults) 个参数；从头配对会整体错位
            args = list(n.args.args) + list(n.args.kwonlyargs)
            defs = list(n.args.defaults) + [d for d in n.args.kw_defaults if d]
            pairs = dict(zip(args[-len(defs):], defs)) if defs else {}
            sig_guards = set()
            for d in pairs.values():
                if (isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                        and d.func.id == "Depends"):
                    sig_guards |= dep_names(d)
            # ④ Annotated[RequestContext, Depends(trusted_context)] is DI,
            # not a default neutral context. Do not label these guarded routes public.
            for argument in list(n.args.posonlyargs) + args:
                if argument.annotation is None:
                    continue
                for dependency in ast.walk(argument.annotation):
                    if isinstance(dependency, ast.Call) and isinstance(dependency.func, ast.Name) and dependency.func.id in {"Depends", "Security"}:
                        sig_guards |= dep_names(dependency)
            # ② 路由级 dependencies（写在装饰器的 keywords 里）
            route_deps: set[str] = set()
            for d in decs:
                for kw in d.keywords:
                    if kw.arg == "dependencies":
                        route_deps |= dep_names(kw.value)
            all_deps = sig_guards | route_deps | router_level.get(str(f), set())
            rows.append({
                "method": decs[0].func.attr.upper(),
                "path": (decs[0].args[0].value
                         if decs[0].args and isinstance(decs[0].args[0], ast.Constant)
                         else "?"),
                "fn": n.name, "guards": sig_guards, "all_deps": all_deps,
            })
    by_guard = {g: sum(1 for r in rows if g in r["guards"]) for g in ROLE_GUARDS}
    by_guard["none_public"] = sum(1 for r in rows if not (r["guards"] & set(ROLE_GUARDS)))
    # 登记表那个**派生分类**的口径：带 require_manager **且** 带 feature flag。
    # ★ `require_manager` 与派生量**同名不同口径**：登记表里的 `require_manager`
    #   是「不带 flag」的那部分，不是签名里全部 `require_manager`。**同名不同口径是本轮反复出现的坑**，
    #   所以两个量都显式给出，不让任何一个含糊。
    mgr_total = by_guard["require_manager"]
    by_guard["require_manager_with_flag"] = sum(
        1 for r in rows if "require_manager" in r["guards"]
        and "require_general_document_platform" in r["all_deps"])
    by_guard["require_manager_plain"] = (
        mgr_total - by_guard["require_manager_with_flag"])
    return {
        "routes": rows,
        "total": len(rows),
        "writes": sum(1 for r in rows if r["method"].lower() in
                      ("post", "put", "patch", "delete")),
        "by_guard": by_guard,
        # ★ `public_routes` 是**人工策展清单**，不是可推导谓词
        #   （登记表口径 = 12 条匿名 + 3 条小程序自助；任何「无某类守卫」的谓词
        #     都会算出 160 条）。因此只核对**列出的路径在现实中存在**。
        "paths": {r["path"] for r in rows},
    }


def check_routes(reg: dict, rt: dict) -> list[str]:
    """RR-5：路由与守卫口径。**口径不同的两个数字必须各自写明，否则不可核对。**"""
    f: list[str] = []
    if not rt["total"]:
        return ["RR-5 路由解析出 0 条——判据无法运行，不得据此得出「清单完整」"]

    ri = ((reg.get("authorization_matrix") or {}).get("route_inventory") or {})
    if not ri:
        return ["RR-5 登记表缺 `authorization_matrix.route_inventory`"]

    if ri.get("total") != rt["total"]:
        f.append(f"RR-5 `route_inventory.total` 声明 {ri.get('total')}，"
                 f"实算 {rt['total']}")
    if not ri.get("method_note"):
        f.append("RR-5 `route_inventory` 缺 `method_note`：**方法说明不完整时，"
                 "任何人复算都可能得到另一个数而不知情**（INC-86 实测两种显而易见的"
                 "实现分别得到 116 与 20，正确值是 214）")
    if not ri.get("public_routes_note"):
        f.append("RR-5 `route_inventory` 缺 `public_routes_note`：`public_routes` 与 "
                 "`by_guard.none_public` 是**两个不同口径**，不写明会让读者得出「文件写错了」")

    # 派生量 → 真实键名。**不给它编一个可推导的谓词**：任何「无某类守卫」的写法
    # 都会算出 160 条（INC-86 首版就是这么误报的）。
    # ★ `require_manager` 在登记表里是「**不带** flag」的口径，与全量口径**同名不同义**。
    DERIVED = {
        "require_manager": "require_manager_plain",
        "require_general_document_platform_plus_manager": "require_manager_with_flag",
    }
    bg = ri.get("by_guard") or {}
    for k, v in bg.items():
        key = DERIVED.get(k, k)
        if key not in rt["by_guard"]:
            f.append(f"RR-5 `by_guard.{k}` 不是本门禁可实算的口径"
                     "（若它是派生量，必须在 method_note 里写明推导方式）")
        elif rt["by_guard"][key] != v:
            f.append(f"RR-5 `by_guard.{k}` 声明 {v}，实算 {rt['by_guard'][key]}"
                     + (f"（派生量，登记表口径 = {k}）" if k in DERIVED else ""))

    # ── 划分不变量 ────────────────────────────────────────────
    if rt["by_guard"].get("trusted_context", 0) and "trusted_context" not in bg:
        f.append("RR-5 trusted_context 守卫未登记；不得视为公开路由")

    # `by_guard` 六项看着像把 total 划分成互斥的六份，所以「加起来等于 total」
    # 是它**唯一的自洽证据**。INC-86 的原值 25+4 也是 29，**不变量照样成立**，
    # 错的是那 5 条的归属——**不变量抓不到归属错误，只抓得到算术不平**。
    # 因此这里只把它当**算术兜底**，归属正确性靠上面那两条逐项比对。
    if bg and all(k in bg for k in ("require_manager",
                                    "require_general_document_platform_plus_manager",
                                    "none_public")):
        s = sum(bg.values())
        if s != ri.get("total"):
            f.append(f"RR-5 `by_guard` 六项之和 {s} ≠ `route_inventory.total` "
                     f"{ri.get('total')}（划分不变量破了）")
        if (bg["require_manager"] + bg["require_general_document_platform_plus_manager"]
                != rt["by_guard"].get("require_manager")):
            f.append("RR-5 `by_guard` 里 require_manager 的两个分类之和 ≠ 实算全量"
                     "（分类没有覆盖全部 require_manager 路由）")

    # `public_routes` 是**人工策展清单**——只核对「列出的路径在现实中存在」，
    # **不核对它等于某个可推导集合**（它不是）。
    ghost_paths = sorted(set(ri.get("public_routes") or []) - rt["paths"])
    if ghost_paths:
        f.append(f"RR-5 `public_routes` 列了 {len(ghost_paths)} 条现实中不存在的路径："
                 f"{ghost_paths}")

    cc = reg.get("command_contracts") or {}
    if cc.get("write_operations") != rt["writes"]:
        f.append(f"RR-5 `command_contracts.write_operations` 声明 "
                 f"{cc.get('write_operations')}，实算 {rt['writes']}")
    return f


LOC_RE = None


def check_evidence_pointers(reg: dict, app: pathlib.Path,
                            root: pathlib.Path) -> list[str]:
    """RR-6：证据指针 `file:line` 必须可解析。

    INC-86：`critical_gap.begin_call_site` 写 `api/ai.py:674`，而该行是
    **另一个函数的一个参数行**；`command-contracts.md` 还据此贴了一段
    **源码里根本不存在的代码**。**数字对了、指向错了、结论反了**，
    而没有任何机器核对过这些指针。

    边界（必须写明，否则会变成一台误报机器）：
      · 只检查**登记为当前证据**的指针；显式标注 `*_ORIGINAL_INVALID` /
        `*_SUPERSEDED` / `*_ORIGINAL` 的一律跳过（它们是历史记录，本就该失效）；
      · `file:line` 只验证「文件存在 + 行号在范围内」；
      · 只有当指针与一个**具名符号**同处一个字典项时，才额外验证
        「该行落在那个符号的函数体内」——这才是能抓住 `ai.py:674` 的那一条。
    """
    import re as _re
    global LOC_RE
    if LOC_RE is None:
        # ★ **不带 `$` 锚**（INC-86 变异 M16 实测）：带锚时任何带尾注的指针
        #   （例：`"backend/app/api/ai.py:388  （在 _metered_chat 内）"`）
        #   **整条被静默跳过**——而登记表里恰恰有几条这么写的。
        #   「只检查格式完全规整的指针」= 一台在大半真实输入上不响的检查器。
        LOC_RE = _re.compile(r"^(backend/[\w./-]+\.py):(\d+)(?:-(\d+))?")
    f: list[str] = []
    cache: dict[str, int] = {}

    def nlines(rel: str) -> int | None:
        """返回行数；文件不存在返回 None。"""
        if rel in cache:
            return cache[rel]
        p = root / rel
        cache[rel] = (len(p.read_text(encoding="utf-8").splitlines())
                      if p.is_file() else None)
        return cache[rel]

    def host_of(rel: str, line: int) -> str | None:
        """该行所在的最内层函数/类名。"""
        p = root / rel
        if not p.is_file():
            return None
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            return None
        best = None
        for n in ast.walk(tree):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if n.lineno <= line <= (n.end_lineno or n.lineno):
                if best is None or n.lineno > best[0]:
                    best = (n.lineno, n.name)
        return best[1] if best else None

    def _is_historical(key: str) -> bool:
        return bool(_re.search(r"_ORIGINAL_INVALID$|_SUPERSEDED$|_ORIGINAL$", key))

    # ★ 单一 `path:line` 形态。多值（`a.py:21,159`）与带尾注的
    #   （`a.py:70-82 启动函数…`）**不做符号校验**——它们的「第一个数字」
    #   与符号没有对应关系，校验它们只会产出纯误报。
    STRICT = _re.compile(r"^(backend/[\w./-]+\.py):(\d+)$")

    def symbol_check(d: dict, at: str, skip: bool) -> None:
        """符号包含判据（INC-86 的核心形态）。

        `{fn: _metered_chat, loc: "…/ai.py:388"}` 而 388 **不在**
        `_metered_chat` 的函数体内——文件存在、行号在范围内，**两项都通过**。
        只有「该行落在被点名符号内」才抓得住。

        ★★ **只对无歧义的两键配对生效**，这是被 50 条误报逼出来的：
          必须**恰好**是 `{符号键, 指针键}` 两项、符号键取值形如标识符、
          指针键取值形如**单一** `path:line`。
          首版把字典里**任何**短标识符串都当成「被点名符号」，于是
          `{resource: users, kind: table, location: …}` 里的
          `table` / `auth_secret` / `registered` 全成了符号，
          再拿它们去要求指针落进对应函数——**一条正确的文件报出 50 条红**。
          **会误报的检查器等于没有检查器**：宁可少判，不可乱判。
          散落在散文与多值字段里的指针**不做符号校验**（边界如实登记）。
        """
        if skip or not isinstance(d, dict) or len(d) != 2:
            return
        sym = [x.strip() for x in d.values()
               if isinstance(x, str)
               and _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", x.strip())]
        ptr = [x.strip() for x in d.values() if isinstance(x, str) and STRICT.match(x.strip())]
        if len(sym) != 1 or len(ptr) != 1:
            return
        m = STRICT.match(ptr[0])
        rel, ln = m.group(1), int(m.group(2))
        total = nlines(rel)
        if total is None or not (1 <= ln <= total):
            return                      # 越界 / 缺文件由下面那条报，不重复
        host = host_of(rel, ln)
        if host != sym[0]:
            f.append(f"RR-6 证据指针 `{ptr[0]}` 落在 `{host or '（模块层）'}`，"
                     f"而登记的符号是 `{sym[0]}`：{at}")

    def walk(node, path="", skip=False):
        if isinstance(node, dict):
            for k, v in node.items():
                # ★ 跳过粒度是**这一项自己的键**，不是父字典——
                #   按父字典判定会让「同级的合法指针」被一起放过（静默少查 = 假绿）。
                child_skip = skip or (isinstance(k, str) and _is_historical(k))
                at = f"{path}.{k}" if path else str(k)
                walk(v, at, child_skip)
                # ★ 必须在**遍历每一项时**对 `v` 判定；写在循环之后会拿到
                #   循环变量的**泄漏值**（最后一项），检查的根本不是子字典。
                symbol_check(v, at, child_skip)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]", skip)
                symbol_check(v, f"{path}[{i}]", skip)
        elif isinstance(node, str) and not skip:
            m = LOC_RE.match(node.strip())
            if m:
                rel, ln = m.group(1), int(m.group(2))
                total = nlines(rel)
                if total is None:
                    f.append(f"RR-6 证据指针 `{node}` 的文件不存在：{path}")
                elif not (1 <= ln <= total):
                    f.append(f"RR-6 证据指针 `{node}` 行号越界"
                             f"（该文件共 {total} 行）：{path}")

    walk(reg)
    return f


# ══════════════════════════════════════════════════════════════════
# 四、夹具
# ══════════════════════════════════════════════════════════════════

def _fixture(*, enumerate_surface=True, drop_from_tables=(),
             meta_depends=None, reality_depends=3,
             ghost_in_tables=(), overlap_decl=1,
             add_excludes=True) -> tuple[dict, dict]:
    """造一份「本应全绿」的登记表 + 对应 reality。

    **登记表侧与 reality 侧的旋钮必须分开**——首版把 `depends_get_store`
    和幽灵方法在两侧一起改，于是「声明 ≠ 实算」这个状态**根本构造不出来**，
    RR-2 / RR-3 这两条最核心的判据**从未被证明会响**。
    这与 INC-81「改共用夹具生成器后要看它影响了几条既有夹具」同源：
    改 A 坏了 B，而 B 的失败看起来像 B 自己有问题。

    夹具**自己声明规模而不复述 reality 的数字**（INC-83 的教训：
    夹具 docstring 写「13 张」而代码遍历 `MATRIX`，扩容后夹具静默变红，
    看起来像判据坏了）。
    """
    allm = {"alpha", "beta", "gamma", "delta"}
    reg = {
        "meta": {
            "store_public_methods": len(allm),
            "store_total_methods": len(allm),
            "store_construction_points": reality_depends,
            "depends_get_store": reality_depends if meta_depends is None
                                 else meta_depends,
            "tables_registered": 2,
            "business_tables": 1,
        },
        "tables": [
            {"resource": "t1", "store_methods": ["alpha", "beta"]},
            {"resource": "t2", "store_methods": ["gamma", *drop_from_tables]},
        ],
        "access_surfaces": [
            {"id": "store-ctor",
             "locations_all_17": [{"loc": "a.py:1", "phase": "request"},
                                  {"loc": "b.py:1", "phase": "worker"}],
             "count_total": 2, "count_by_phase": {"request": 1, "worker": 1}},
            {"id": "store-methods",
             "methods": (["alpha", "beta", "gamma", "delta"] if enumerate_surface
                         else None),
             "count": 4, "overlap_with_delegated": overlap_decl},
            {"id": "delegated-methods",
             "methods": ["beta", "delta"], "count": 2},
        ],
    }
    if not enumerate_surface:
        reg["access_surfaces"][1].pop("methods")
    if ghost_in_tables:
        # 只登记，不进 reality —— 这才是「幽灵方法」这个状态
        reg["tables"][0]["store_methods"].append(ghost_in_tables[0])
    if add_excludes:
        reg["meta"]["business_tables_excludes"] = ["t2"]
    meas = {
        "public": set(allm),
        "all": set(allm),
        "store_public_methods": len(allm),
        "store_total_methods": len(allm),
        "store_construction_points": reality_depends,
        "depends_get_store": reality_depends,
    }
    return reg, meas


def _selftest_routes() -> list[tuple[str, bool]]:
    """RR-5 / RR-6 的自检。

    **夹具里必须真的有一个 `async def` 路由**——INC-86 的起点就是
    `ast.AsyncFunctionDef` 不是 `ast.FunctionDef` 的子类。
    一份全 `def` 的夹具**证明不了本判据不是 async-blind 的**：
    那种夹具在两种实现下都会绿，等于没测。
    """
    import tempfile
    cases: list[tuple[str, bool]] = []
    root = pathlib.Path(tempfile.mkdtemp(prefix="rr-routes-"))
    (root / "backend/app").mkdir(parents=True)
    (root / "backend/app/demo.py").write_text('''
import asyncio
from fastapi import APIRouter, Depends
router = APIRouter()

async def require_manager(): ...

@router.get("/a")
def read_a(store=Depends(get_store)):
    return 1

@router.post("/b")
async def write_b(manager=Depends(require_manager)):
    return 2

@router.post("/c")
def write_c():
    return 3
''', encoding="utf-8")

    rt = measure_routes(root / "backend/app")
    good = {
        "authorization_matrix": {"route_inventory": {
            "total": 3,
            "method_note": "含 async 的 AST 遍历",
            "public_routes_note": "无角色守卫",
            "by_guard": {"current_user": 0, "require_manager": 1,
                         "require_admin": 0, "require_template_manager": 0,
                         "require_general_document_platform_plus_manager": 0,
                         "none_public": 2},
            # total=3, 六项之和=0+1+0+0+0+2=3 ✓ 划分不变量成立
            "public_routes": ["/a", "/c"],
        }},
        "command_contracts": {"write_operations": 2},
    }
    cases.append(("★ RR-5 干净路由夹具 → 零 finding（含 1 条 `async def`）",
                   not check_routes(good, rt)))
    cases.append(("★ RR-5 夹具确实含 async 路由（写操作应为 2 而非 1）",
                   rt["writes"] == 2 and rt["total"] == 3))

    def r5(reg):
        return any(x.startswith("RR-5") for x in check_routes(reg, rt))

    bad = {**good, "command_contracts": {"write_operations": 1}}
    cases.append(("★ RR-5 `write_operations` 少算 async 那条 → 报红", r5(bad)))

    bad = {**good, "authorization_matrix": {"route_inventory": {
        **good["authorization_matrix"]["route_inventory"], "total": 2}}}
    cases.append(("★ RR-5 `route_inventory.total` 少 1 → 报红", r5(bad)))

    bad = {**good, "authorization_matrix": {"route_inventory": {
        **{k: v for k, v in good["authorization_matrix"]["route_inventory"].items()
           if k != "method_note"}}}}
    cases.append(("★ RR-5 缺 `method_note`（方法说明不完整）→ 报红", r5(bad)))

    bad = {**good, "authorization_matrix": {"route_inventory": {
        **{k: v for k, v in good["authorization_matrix"]["route_inventory"].items()
           if k != "public_routes_note"}}}}
    cases.append(("★ RR-5 缺 `public_routes_note`（两个口径不分）→ 报红", r5(bad)))

    annotation_fixture = root / "annotated_app"
    annotation_fixture.mkdir()
    (annotation_fixture / "demo.py").write_text("""
from typing import Annotated
from fastapi import APIRouter, Depends
router = APIRouter()
@router.get("/trusted")
def trusted(ctx: Annotated[RequestContext, Depends(trusted_context)]): ...
""")
    annotated = measure_routes(annotation_fixture)
    cases.append(("★ RR-5 Annotated trusted_context 不漏算为公开",
                  annotated["by_guard"]["trusted_context"] == 1 and annotated["by_guard"]["none_public"] == 0))

    # ── RR-6 证据指针 ──
    ev = {"command_contracts": {"evidence": {
        "site": "backend/app/demo.py:8",                 # async def write_b 体内
        "stale": "backend/app/demo.py:9999",             # 越界
        "gone": "backend/app/api/nope.py:12",            # 文件不存在
    }}}
    msgs = check_evidence_pointers(ev, root / "backend/app", root)
    cases.append(("★ RR-6 行号越界的指针 → 报红",
                  any("越界" in m for m in msgs)))
    cases.append(("★ RR-6 文件不存在的指针 → 报红",
                  any("文件不存在" in m for m in msgs)))
    cases.append(("阴性) RR-6 合法指针不报红", len(msgs) == 2))

    ev2 = {"g": {"legacy_ORIGINAL_INVALID": {"site": "backend/app/demo.py:9999"}}}
    cases.append(("★ RR-6 标注 `*_ORIGINAL_INVALID` 的历史指针 → **跳过**",
                  not check_evidence_pointers(ev2, root / "backend/app", root)))

    # ── RR-6 符号包含（INC-86 的原始形态）────────────────────
    # ★ 行号**由夹具自己算**，不写死——写死就会随夹具文本改动而失效，
    #   而且失败时看起来像判据坏了（INC-83 同一族）。
    demo = (root / "backend/app/demo.py")
    _t = ast.parse(demo.read_text(encoding="utf-8"))
    lines = {}
    for _n in ast.walk(_t):
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lines[_n.name] = (_n.lineno, _n.end_lineno)
    b_start = lines["write_b"][0]
    a_start = lines["read_a"][0]
    good_ev = {"e": {"begin_call_site": {
        "fn": "write_b", "loc": f"backend/app/demo.py:{b_start}"}}}
    cases.append((f"★ RR-6 `{{fn,loc}}` 指向的符号正确（行 {b_start}）→ 不报红",
                  not check_evidence_pointers(good_ev, root / "backend/app", root)))
    bad_ev = {"e": {"begin_call_site": {
        "fn": "write_b", "loc": f"backend/app/demo.py:{a_start}"}}}
    cases.append((f"★ RR-6 `{{fn,loc}}` 行号落在**另一个函数**内（行 {a_start}）→ 报红"
                  "（INC-86 原始缺陷：合法文件、合法行、错符号）",
                  any("而登记的符号是" in m
                      for m in check_evidence_pointers(bad_ev, root / "backend/app", root))))
    # 阴性：字典里的枚举值**不得**被当成「被点名符号」
    #   （首版把 `kind: table` 之类也算进 named，于是正确文件报出 50 条红）
    enum_ev = {"t": [{"resource": "x", "kind": "table", "sensitivity": "auth_secret",
                      "location": f"backend/app/demo.py:{a_start}",
                      "registry_status": "registered"}]}
    cases.append(("★ 阴性：多键字典里的枚举值不被误当成符号（防 50 条误报回归）",
                  not check_evidence_pointers(enum_ev, root / "backend/app", root)))
    # 阴性：多值 / 带尾注的指针不做符号校验（超出严格形态）
    multi_ev = {"e": [{"fn": "write_b", "loc": f"backend/app/demo.py:{a_start},{b_start}"},
                      {"fn": "write_b", "loc": f"backend/app/demo.py:{a_start} 启动函数"}]}
    cases.append(("阴性：多值 / 带尾注指针不做符号校验",
                  not check_evidence_pointers(multi_ev, root / "backend/app", root)))
    return cases


def self_test() -> int:
    print(f"{CYAN}resource_registry_check 自检{RESET}")
    print("=" * 68)
    cases: list[tuple[str, bool]] = []

    def said(reg, meas, code, *words) -> bool:
        """判据是否**因为预期的那句话**而报红。

        只按 code 前缀判定是装饰性的：一条判据可以因为另一个原因报红，
        夹具照样显示绿——**夹具红的原因必须是你想测的那个原因**。
        """
        for x in check(reg, meas):
            if x.startswith(code) and all(w in x for w in words):
                return True
        return False

    # ── 阴性：干净样本必须全绿（**先证明这套夹具真的能全绿**）──
    reg, meas = _fixture()
    cases.append(("干净样本 → 零 finding", not check(reg, meas)))

    # ── RR-1：只记数字不记枚举（INC-85 的原始形态）──
    reg, meas = _fixture(enumerate_surface=False)
    cases.append(("★ RR-1 `store-methods` 只有 count 没有方法名 → 报红",
                   said(reg, meas, "RR-1", "没有列出任何方法名")))

    # ── RR-1：枚举在，但漏了 reality 里的方法 ──
    reg, meas = _fixture()
    reg["access_surfaces"][1]["methods"] = ["alpha", "beta", "gamma"]
    cases.append(("★ RR-1 枚举漏 1 个 reality 方法 → 报红",
                   said(reg, meas, "RR-1", "漏了 reality")))

    # ── RR-1：某方法任何位置都没登记（**要断言它是因为「未登记」而红，
    #          不是因为「枚举不全」而红**——两者是不同缺陷）──
    reg, meas = _fixture()
    reg["access_surfaces"][1]["methods"] = ["alpha", "beta", "gamma"]
    reg["access_surfaces"][2]["methods"] = ["beta"]
    reg["tables"][0]["store_methods"] = ["alpha", "beta"]
    reg["tables"][1]["store_methods"] = ["gamma"]
    cases.append(("★ RR-1 `delta` 三处都没登记 → 因「未登记」报红",
                   said(reg, meas, "RR-1", "未被登记表任何位置登记")))

    # ── RR-1 边界：枚举不完整 ≠ 空洞通过 ──
    reg, meas = _fixture(enumerate_surface=False)
    meas["public"] = set()
    cases.append(("★ RR-0 reality 解析为 0 个方法 → 报红而不是「全部覆盖」",
                   said(reg, meas, "RR-0", "解析出 0 个")))

    # ── RR-2：depends_get_store 错了 1（INC-85 的真实缺陷形态）──
    reg, meas = _fixture(meta_depends=4, reality_depends=3)
    cases.append(("★ RR-2 `depends_get_store` 声明 4 实为 3 → 报红",
                   said(reg, meas, "RR-2", "`depends_get_store`")))

    # ── RR-2：business_tables 没有排除清单 → 不可复算 ──
    reg, meas = _fixture(add_excludes=False)
    cases.append(("★ RR-2 缺 `business_tables_excludes` → 报红",
                   said(reg, meas, "RR-2", "business_tables_excludes")))

    # ── RR-2：count_by_phase 与实列不符 ──
    reg, meas = _fixture()
    reg["access_surfaces"][0]["count_by_phase"] = {"request": 2, "worker": 0}
    cases.append(("★ RR-2 `count_by_phase` 与实列不符 → 报红",
                   said(reg, meas, "RR-2", "count_by_phase")))

    # ── RR-3：登记了 Store 上没有的方法 ──
    reg, meas = _fixture(ghost_in_tables=("phantom",))
    cases.append(("★ RR-3 幽灵方法 `phantom` → 报红",
                   said(reg, meas, "RR-3", "phantom")))

    # ── RR-4：缺 overlap 声明 / 声明值错 ──
    reg, meas = _fixture(overlap_decl=0)
    cases.append(("★ RR-4 `overlap_with_delegated` 声明 0 实为 1 → 报红",
                   said(reg, meas, "RR-4", "overlap_with_delegated")))
    reg, meas = _fixture()
    reg["access_surfaces"][1].pop("overlap_with_delegated")
    cases.append(("★ RR-4 缺 `overlap_with_delegated` 声明 → 报红",
                   said(reg, meas, "RR-4", "两个不同口径")))

    # ── 口径回归：两个集合**允许重叠**，不得把 delegated 当成逐表的差集 ──
    #   `beta`/`delta` 同时出现在两边是**合法的**（实测重叠 10 个）。
    #   这条阴性用例守护 INC-85 自己的修法：若有人把 `overlap_with_delegated`
    #   理解成「必须为 0」，会把登记表改成一份**漏掉 10 个方法**的清单。
    reg, meas = _fixture(overlap_decl=1)
    reg["access_surfaces"][2]["methods"] = ["beta", "delta"]
    cases.append(("口径回归：delegated 与逐表重叠 1 个 → 不得报红",
                   not said(reg, meas, "RR-1")))

    # ── RR-5 / RR-6 专项（INC-86）──────────────────────────────
    cases.extend(_selftest_routes())

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
    if yaml is None:
        sys.stderr.write("缺少 pyyaml，resource_registry_check 无法运行\n")
        return EXIT_TOOL_BROKEN
    if not REGISTRY.is_file() or not (APP / "db.py").is_file():
        sys.stderr.write(f"缺少输入：{REGISTRY} 或 {APP / 'db.py'}\n")
        return EXIT_TOOL_BROKEN
    try:
        reg = yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {}
        meas = measure(APP)
        rt = measure_routes(APP)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"解析失败：{exc}\n")
        return EXIT_TOOL_BROKEN

    findings = check(reg, meas) + check_routes(reg, rt) \
        + check_evidence_pointers(reg, APP, REPO_ROOT)

    print("=" * 72)
    print("资源登记表 ↔ 真实代码 一致性校验（INC-85）")
    print("=" * 72)
    print(f"{DIM}登记表：{REGISTRY.relative_to(REPO_ROOT)}{RESET}")
    print(f"{DIM}reality：backend/app/db.py（class Store）{RESET}\n")
    surf = surfaces(reg)
    ms = surf.get("store-methods") or {}
    dm = surf.get("delegated-methods") or {}
    enum = ms.get("methods") or []
    reg_all = table_methods(reg) | surface_methods(reg)
    print(f"  reality 公开方法      {meas['store_public_methods']} 个")
    print(f"  store-methods 枚举    {len(enum)} 个（声明 {ms.get('count')}）")
    print(f"  逐表 store_methods    {len(table_methods(reg))} 个")
    print(f"  delegated-methods     {len(dm.get('methods') or [])} 个（声明 {dm.get('count')}）")
    print(f"  已登记并集            {len(reg_all)} 个")
    print(f"  未登记差集            {len(meas['public'] - reg_all)} 个")
    print(f"  路由总数 / 写操作     {rt['total']} / {rt['writes']}")
    print(f"  by_guard 实算         " + " · ".join(
        f"{k}={v}" for k, v in sorted(rt["by_guard"].items())))
    print()
    for x in findings:
        print(f"  {RED}✗{RESET} {x}")
    print("=" * 72)
    if findings:
        print(f"{RED}未通过{RESET}（{len(findings)} 项）")
        return EXIT_FINDINGS
    print(f"{GREEN}全部通过{RESET}")
    return EXIT_OK


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
