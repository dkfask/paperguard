#!/usr/bin/env python3
"""建号路径契约门禁：每个创建账号的入口是否写入 organization_context

为什么要有这道门禁
------------------
PaperGuard 现在有五条建号路径（`store.save_user()` 的非测试调用点），
其中**只有一条**强制 `school_id`，而另外两条是面向真实用户的产品功能
（教师名册导入、微信注册）。结果是系统会**持续产出**无组织归属的 active 学生
——在 ADR-02 的共享库 + RLS 迁移后，这批账号会静默变成「看不到任何数据」。

一次性调查抓不住这类缺口：只要有人再加一个 `save_user()`，同样的问题就会复发。
所以把「每条建号路径都必须显式处理 organization_context」变成机器可验证的契约。

本工具能判定什么、不能判定什么（必须说清）
------------------------------------
    能：静态判定**每个 save_user 调用点**在写入前是否设置了
        `organization_context`（或所属字典字面量里是否含该键）。
    不能：判定 `school_id` 是否**被强制**。「设了 organization_context 但没校验
        school_id 存在」属于需要人读代码的语义问题，本工具会显式标注
        `context-unverified` 而不是替它背书。

退出码
------
    0 = 全部建号路径都显式处理了 organization_context
    1 = 存在不处理的路径（门禁报红）
    2 = 工具不可运行（找不到 backend/app 目录等）
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
from typing import NamedTuple

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

SAVE_FUNCS = {"save_user", "create_user", "update_user"}


class Site(NamedTuple):
    path: str
    line: int
    func: str
    expr: str          # 传给 save_user 的表达式（摘要）
    role: str | None   # 该处显式设置的 role
    status: str | None
    sets_ctx: bool     # 写入前是否设置了 organization_context
    ctx_value: str     # 设置方式摘要
    is_create: bool    # 是否为「建号」而非「更新已有账号」


def _looks_like_create(keys: set[str]) -> bool:
    """区分建号与更新。

    首版把 12 处 save_user 全部当建号路径，误报 5 条（登录写 token、解绑、
    改密码等更新也走 save_user）。一个会误报的检查器等于没有检查器。
    判据：写入的字段里带 `created_at` 才是建号——更新的调用不会碰它。
    """
    return "created_at" in keys


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _dict_keys(node: ast.AST) -> set[str] | None:
    if isinstance(node, ast.Dict):
        out = set()
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.add(k.value)
        return out
    return None


class FuncScan(ast.NodeVisitor):
    """在单个函数体内追踪：save_user 的实参、以及此前对同一名字的字段赋值。"""

    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.sites: list[Site] = []
        self._fields: dict[str, dict[str, str]] = {}   # 变量名 → {字段: 摘要}
        self._dicts: dict[str, tuple[set[str], str]] = {}  # 变量名 → (键集合, 源码)
        self._func = "<module>"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        prev_func, prev_fields, prev_dicts = self._func, self._fields, self._dicts
        self._func, self._fields, self._dicts = node.name, {}, {}
        self.generic_visit(node)
        self._func, self._fields, self._dicts = prev_func, prev_fields, prev_dicts

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815

    # --- 字段赋值：user["organization_context"] = ... ---
    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for tgt in node.targets:
            if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                key = _const_str(tgt.slice)
                if key:
                    self._fields.setdefault(tgt.value.id, {})[key] = _src(node.value)
            # 整体赋值：user = {"role": "student", "organization_context": {...}}
            # 必须记下来——save_user(user) 传的是变量名，不是字典字面量。
            elif isinstance(tgt, ast.Name) and isinstance(node.value, ast.Dict):
                keys = _dict_keys(node.value) or set()
                self._dicts[tgt.id] = (keys, _src(node.value))
        self.generic_visit(node)

    # --- save_user(...) 调用点 ---
    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if _call_name(node.func) in SAVE_FUNCS and node.args:
            arg = node.args[0]
            keys = _dict_keys(arg)
            if keys is not None:
                has_ctx = "organization_context" in keys
                ctx = "dict 字面量含 organization_context" if has_ctx else ""
                role = _const_str_by_key(arg, "role")
                status = _const_str_by_key(arg, "status")
                is_create = _looks_like_create(keys)
            else:
                name = arg.id if isinstance(arg, ast.Name) else None
                fields = dict(self._fields.get(name, {})) if name else {}
                dkeys, dsrc = self._dicts.get(name, (set(), "")) if name else (set(), "")
                if "organization_context" in dkeys and "organization_context" not in fields:
                    fields["organization_context"] = dsrc
                has_ctx = "organization_context" in fields
                ctx = fields.get("organization_context", "")
                role = fields.get("role") or _str_in_src(dsrc, "role")
                status = fields.get("status") or _str_in_src(dsrc, "status")
                is_create = _looks_like_create(set(fields) | dkeys)
            self.sites.append(Site(
                self.rel, node.lineno, _call_name(node.func),
                _src(arg)[:60], role, status, has_ctx, ctx, is_create))
        self.generic_visit(node)


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _const_str_by_key(dict_node: ast.AST, key: str) -> str | None:
    if not isinstance(dict_node, ast.Dict):
        return None
    for k, v in zip(dict_node.keys, dict_node.values):
        if _const_str(k) == key:
            return _const_str(v)
    return None


def _str_in_src(src: str, key: str) -> str | None:
    """从 `ast.unparse` 出来的字典源码里取 `key: "value"` 的字面量值。

    注意 `ast.unparse` 输出的是**单引号**（`'role': 'student'`），
    只按双引号匹配会静默取不到值——那会让 role 变成「未设」，
    进而把按角色分类的判定悄悄降级。
    """
    if not src:
        return None
    for marker in (f'"{key}":', f"'{key}':"):
        i = src.find(marker)
        if i < 0:
            continue
        rest = src[i + len(marker):].lstrip()
        if rest[:1] in ('"', "'"):
            q = rest[0]
            j = rest.find(q, 1)
            if j > 0:
                return rest[1:j]
    return None


def _src(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:                                    # pragma: no cover
        return "<unparse>"


def scan(app_dir: pathlib.Path) -> list[Site]:
    sites: list[Site] = []
    for py in sorted(app_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:
            sys.stderr.write(f"解析失败 {py}: {e}\n")
            continue
        rel = str(py.relative_to(app_dir.parent.parent))
        scanner = FuncScan(rel)
        scanner.visit(tree)
        sites.extend(scanner.sites)
    return sites


# 已知且已裁决为「不适用」的路径：内置管理员与系统夹具，不产生学生租户归属问题
ALLOWLIST = {
    "seed_admin": "内置管理员种子账号（auth.py:76）",
}


def classify(site: Site) -> tuple[str, str]:
    """返回 (等级, 说明)。等级：ok / risk / not-applicable / unverified。"""
    fn = site.func
    if fn == "update_user" and not site.sets_ctx:
        return "ok", "更新路径不新建账号，无上下文不算缺口"
    if site.role in {"admin", "college_admin"}:
        return "not-applicable", f"角色 {site.role}，租户归属由 Q-09/Q-11 另行裁决"
    if not site.sets_ctx:
        return "risk", "该路径不写 organization_context，会产出无组织归属的账号"
    if "or {}" in site.ctx_value:
        return "unverified", "设置了上下文但可能取空（`or {}`），未验证 school_id 存在"
    return "ok", "显式设置 organization_context"


def report(sites: list[Site], strict: bool) -> int:
    saves = [s for s in sites if s.func in {"save_user", "create_user"}]
    create = [s for s in saves if s.is_create]
    update = [s for s in sites if s.func == "update_user"] + [s for s in saves if not s.is_create]
    print("=" * 72)
    print("建号路径契约核对")
    print("=" * 72)
    print(f"{DIM}扫描到写入调用 {len(sites)} 处：建号 {len(create)} 处、"
          f"更新 {len(update)} 处（更新不新建账号，不计入契约）{RESET}\n")

    risky: list[tuple[Site, str]] = []
    unver: list[tuple[Site, str]] = []
    for s in create:
        level, why = classify(s)
        mark = {"ok": f"{GREEN}✓{RESET}", "not-applicable": f"{DIM}—{RESET}",
                "unverified": f"{YELLOW}?{RESET}", "risk": f"{RED}✗{RESET}"}[level]
        print(f"  {mark} {s.path}:{s.line}  {s.func}()")
        ctx = f"  ctx={s.ctx_value[:52]}" if s.sets_ctx else ""
        print(f"      {DIM}role={s.role or '未设'} status={s.status or '未设'}{ctx}{RESET}")
        print(f"      {why}")
        if level == "risk":
            risky.append((s, why))
        elif level == "unverified":
            unver.append((s, why))

    print()
    print("─" * 72)
    print(f"  建号路径            {len(create)}  {DIM}（仅统计带 created_at 的调用）{RESET}")
    print(f"  不写上下文（风险）  {len(risky)}")
    print(f"  写了但可能取空      {len(unver)}")
    print("=" * 72)

    if unver:
        print(f"{YELLOW}以下路径设置了上下文但未验证 school_id 存在——"
              f"本工具不做语义判定，需人工核对：{RESET}")
        for s, why in unver:
            print(f"  ! {s.path}:{s.line} {why}")

    if risky:
        print(f"{RED}以下建号路径不写 organization_context：{RESET}")
        for s, why in risky:
            print(f"  ✗ {s.path}:{s.line}（{s.func}, role={s.role}）{why}")
        print()
        print("  在 ADR-02 迁移后，这类账号会因为没有 tenant_id 而被 RLS 过滤，")
        print("  表现为「看不到任何数据」——静默失效，比报错更难发现。")
        print("  处置见设计文档 INC-09；本工具不替业务决定归属策略。")
        return EXIT_FINDINGS
    print(f"{GREEN}全部建号路径都显式处理了 organization_context{RESET}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 自检：在合成源码上证明本工具抓得住「新长出来的无守卫路径」
# ---------------------------------------------------------------------------

def self_test() -> int:
    print(f"{DIM}建号路径门禁自检：证明它抓得住后来新增的无守卫路径{RESET}\n")
    print("=" * 72)
    # 先清空：自检会往这个目录里追加变异文件，不清理会让上一轮的残留
    # 被本轮一起扫进来——测试自身的状态污染会伪装成「工具判错」。
    tmp = pathlib.Path("/tmp/account_path_selftest")
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp)
    app = tmp / "backend" / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "__init__.py").write_text("", encoding="utf-8")

    guarded = '''
from app.db import store

def create_via_self_service(payload):
    if "school_id" not in payload:
        raise ValueError("必须选择学校")
    user = {"id": "u1", "role": payload["role"]}
    user["organization_context"] = {"school_id": payload["school_id"]}
    store.save_user(user)
'''
    unguarded = '''
from app.db import store

def create_via_import(rows):
    for r in rows:
        store.save_user({
            "id": r["id"], "role": "student", "status": "active",
            "username": r["username"],
        })
'''
    downgraded = '''
from app.db import store

def register_with_email(email):
    user = {"id": "u2", "role": "user", "status": "active",
            "organization_context": {}}
    store.save_user(user)
'''
    takes_ctx = '''
from app.db import store

def approve(user, requested):
    user["organization_context"] = user.get("requested_organization_context") or {}
    store.save_user(user)
'''
    for name, src in (("guarded.py", guarded), ("imports.py", unguarded),
                      ("email_reg.py", downgraded), ("approve.py", takes_ctx)):
        (app / name).write_text(src, encoding="utf-8")

    sites = scan(app)
    by_file = {s.path.split("/")[-1]: s for s in sites}
    results: list[tuple[str, bool]] = []

    results.append(("识别出 4 个建号调用点", len(sites) == 4))
    results.append(("自助路径（有守卫）判为 ok",
                    "guarded.py" in by_file and classify(by_file["guarded.py"])[0] == "ok"))
    results.append(("教师导入路径（无守卫）判为 risk",
                    "imports.py" in by_file and classify(by_file["imports.py"])[0] == "risk"))
    results.append(("无守卫路径确实没检测到上下文写入",
                    "imports.py" in by_file and not by_file["imports.py"].sets_ctx))
    results.append(("`or {}` 取空被判为 unverified 而非 ok",
                    "approve.py" in by_file and classify(by_file["approve.py"])[0] == "unverified"))
    results.append(("dict 字面量含上下文判为 ok",
                    "email_reg.py" in by_file and classify(by_file["email_reg.py"])[0] == "ok"))

    # 变异：往已判 ok 的文件里追加一条**真正的建号**路径（必须带 created_at，
    # 否则它会被正确地判为「更新」而漏报——那样这个变异就失去意义了）
    (app / "imports.py").write_text(unguarded + '''
def create_via_wechat(openid):
    store.save_user({"id": openid, "role": "student", "status": "active",
                     "created_at": "now"})
''', encoding="utf-8")
    sites2 = scan(app)
    rc = report(sites2, strict=True)
    results.append(("新增无守卫路径后门禁转红", rc == EXIT_FINDINGS))
    # 建号 vs 更新：更新调用不带 created_at，必须被排除在契约之外
    upd = tmp / "backend" / "app" / "update.py"
    upd.write_text(
        "from app.db import store\n"
        "def touch_session(user, token):\n"
        "    store.save_user(user)\n"          # 更新：没有 created_at
        "def make_user(uid):\n"
        "    store.save_user({\"id\": uid, \"role\": \"student\", \"created_at\": \"now\"})\n",
        encoding="utf-8")
    s3 = scan(app)
    upd_sites = [x for x in s3 if x.path.endswith("update.py")]
    creates = [x.line for x in upd_sites if x.is_create]
    results.append(("无 created_at 的调用判为更新（不算建号）",
                    len(upd_sites) == 2 and creates == [5]))
    # 单引号 dict 的 role 提取
    results.append(("ast.unparse 单引号下 role 仍能提取",
                    any(x.is_create and x.role == "student" for x in s3)))

    print()
    passed = sum(1 for _, ok in results if ok)
    for label, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print("=" * 72)
    if passed == len(results):
        print(f"{GREEN}自检通过：{passed}/{len(results)}，"
              f"且能抓住后来新增的无守卫建号路径{RESET}")
        return EXIT_OK
    print(f"{RED}自检失败：{passed}/{len(results)}{RESET}")
    return EXIT_FINDINGS


def main() -> int:
    ap = argparse.ArgumentParser(description="建号路径 organization_context 契约门禁")
    ap.add_argument("--app-dir", help="backend/app 目录（默认按仓库布局推断）")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.app_dir:
        app_dir = pathlib.Path(args.app_dir)
    else:
        here = pathlib.Path(__file__).resolve()
        app_dir = here.parent.parent.parent.parent / "backend" / "app"
    if not app_dir.is_dir():
        sys.stderr.write(f"找不到 backend/app 目录：{app_dir}\n"
                         "请用 --app-dir 指定。\n")
        return EXIT_TOOL_BROKEN

    print(f"{DIM}扫描目录：{app_dir}{RESET}")
    return report(scan(app_dir), strict=True)


if __name__ == "__main__":
    raise SystemExit(main())
