#!/usr/bin/env python3
"""Q-01 组织根 → 租户映射表：候选生成器 + 人工确认结果的校验器

为什么需要这个工具
------------------
`account-migration-inventory.md` §3.1 前置条件第 1 条是
「Q-01（组织树根 → 租户映射）已由人工确认并产出映射表」，
而 `backfill_migrate.py` 把这份映射表列为**唯一必填输入**——
没有它就没有「不猜测」的地基，回填只能全量隔离。

人工凭空写这份表有风险：漏一个根、同一学校拆成两个租户、把学院当租户根，
都会在回填后变成**静默的数据分裂**。本工具把「凭空写」变成
「机器推导候选 → 人工逐条确认 → 机器校验确认结果」。

两种模式
--------
    --emit-candidates    扫描组织树，输出候选映射 + 结构体检报告
    --check <map.json>   校验人工确认后的映射表是否自洽

设计上的克制
------------
1. **只提 `type == 'school'` 的根节点**。学院/专业/课程/批次不是租户边界。
2. **不发明租户 ID 的语义**。可以给出确定性的占位 ID（`tenant-cand-<org_id>`），
   但报告里必须标为「待分配」，不能让人误以为这是最终值。
3. **不替业务决定「一个学校是否该拆成多个租户」**。只报告每棵子树的
   规模（组织数、可归属用户数），拆分与否是商业决定。
4. **用户缺 `organization_context` 是数据问题，不是映射问题**。
   报告会把它单列——否则会给人「Q-01 裁决完就万事大吉」的错误印象。

退出码
------
    0 = 通过 / 已产出
    1 = 发现问题（结构缺陷或映射不自洽）
    2 = 工具不可运行（缺输入、缺依赖）
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
import sqlite3
import sys
import tempfile

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, CYAN, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[36m", "\033[0m")

# 只有这两种 type 可能成为租户边界
TENANT_ROOT_TYPES = ("school",)


def _json(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def read_orgs(src: str) -> dict[str, dict]:
    """组织树：把 data JSON 拆成真实列（源库是 blob，不拆则父子关系无从核对）。"""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        try:
            cur = con.execute("SELECT * FROM organization_units")
        except sqlite3.OperationalError:
            return {}          # 表不存在交由调用方给出明确错误，而不是抛栈
        cols = [d[0] for d in cur.description]
        out: dict[str, dict] = {}
        for raw in cur.fetchall():
            d = dict(zip(cols, raw))
            body = _json(d.get("data"))
            out[d["id"]] = {
                "id": d["id"],
                "name": body.get("name"),
                "type": (body.get("type") or "").strip() or None,
                "parent_id": (body.get("parent_id") or "").strip() or None,
                "code": body.get("code"),
            }
        return out
    finally:
        con.close()


def read_users(src: str) -> list[dict]:
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        try:
            cur = con.execute("SELECT id, role, organization_context FROM users")
        except sqlite3.OperationalError:
            return []
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 结构体检
# ---------------------------------------------------------------------------

def walk_to_root(oid: str, orgs: dict[str, dict]) -> tuple[str | None, str]:
    """上溯到根。返回 (根 id | None, 状态)。"""
    seen: set[str] = set()
    cur = oid
    while True:
        if cur in seen:
            return None, f"自 {oid} 上溯在 {cur} 成环"
        seen.add(cur)
        node = orgs.get(cur)
        if node is None:
            return None, f"自 {oid} 上溯到不存在的组织 {cur}"
        if not node["parent_id"]:
            return cur, "ok"
        cur = node["parent_id"]


def subtree_of(root: str, orgs: dict[str, dict]) -> list[str]:
    out: list[str] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        for oid, node in orgs.items():
            if node["parent_id"] == cur and oid not in out:
                out.append(oid)
                stack.append(oid)
    return out


def audit(orgs: dict[str, dict], users: list[dict]) -> dict:
    roots: list[str] = []
    non_school_roots: list[dict] = []
    broken: list[dict] = []
    roots_of: dict[str, str] = {}

    for oid in orgs:
        root, status = walk_to_root(oid, orgs)
        if root is None:
            broken.append({"org": oid, "reason": status})
            continue
        roots_of[oid] = root

    for r in sorted(set(roots_of.values())):
        roots.append(r)
        node = orgs[r]
        if node["type"] not in TENANT_ROOT_TYPES:
            non_school_roots.append({
                "org": r, "type": node["type"], "name": node["name"],
                "note": f"根节点 type={node['type']!r} 不在 {TENANT_ROOT_TYPES}，"
                        "不能作为租户边界——请确认这棵树是不是缺了学校层",
            })

    # 用户归属：区分「映射问题」与「数据问题」
    users_no_school = [u["id"] for u in users
                       if not (_json(u["organization_context"]).get("school_id") or "").strip()]
    users_under = {r: [] for r in roots}
    for u in users:
        sid = (_json(u["organization_context"]).get("school_id") or "").strip()
        if sid and sid in roots_of and roots_of[sid] in users_under:
            users_under[roots_of[sid]].append(u["id"])

    return {
        "roots": roots,
        "non_school_roots": non_school_roots,
        "broken": broken,
        "roots_of": roots_of,
        "subtree": {r: subtree_of(r, orgs) for r in roots},
        "users_without_school_id": users_no_school,
        "users_under_root": users_under,
    }


# ---------------------------------------------------------------------------
# 模式一：产出候选
# ---------------------------------------------------------------------------

def emit(src: str, out_path: str | None, apply_style: str) -> int:
    orgs = read_orgs(src)
    users = read_users(src)
    a = audit(orgs, users)

    if not orgs:
        sys.stderr.write("源库没有组织数据。\n")
        return EXIT_TOOL_BROKEN

    print(f"{DIM}源：{src}{RESET}\n")
    print("=" * 72)
    print("Q-01 租户映射候选（机器推导，人工逐条确认）")
    print("=" * 72)
    print(f"  组织总数            {len(orgs)}")
    print(f"  根节点数            {len(a['roots'])}")
    print(f"  可作租户边界的根    {len(a['roots']) - len(a['non_school_roots'])}"
          f"（仅 type ∈ {TENANT_ROOT_TYPES}）")

    if a["broken"]:
        print(f"\n{RED}结构缺陷：以下组织无法上溯到根{RESET}")
        for b in a["broken"]:
            print(f"  ✗ {b['org']}：{b['reason']}")
    if a["non_school_roots"]:
        print(f"\n{YELLOW}非学校根节点：不能作为租户边界{RESET}")
        for r in a["non_school_roots"]:
            print(f"  ! {r['org']}（{r['name']}, type={r['type']}）：{r['note']}")

    candidates: dict[str, str] = {}
    print(f"\n{YELLOW}候选映射{RESET}")
    for r in a["roots"]:
        node = orgs[r]
        subs = a["subtree"][r]
        n_users = len(a["users_under_root"][r])
        if node["type"] not in TENANT_ROOT_TYPES:
            print(f"  ✗ {r}（{node['name']}）type={node['type']} —— 拒绝提为候选")
            continue
        tid = f"tenant-cand-{r}"
        candidates[r] = tid
        print(f"  ✓ 根 {r}（{node['name']}，code={node['code']}）")
        print(f"      下辖组织 {len(subs)} 个，"
              f"有 school_id 且归属此根的用户 {n_users} 个")
        print(f"      候选租户 ID：{tid}  {DIM}（占位值，最终 ID 待分配）{RESET}")
        if n_users == 0:
            print(f"      {DIM}注意：该根下当前没有任何用户能靠 school_id 归属{RESET}")

    print(f"\n{CYAN}解锁能力量化（这是最容易被误读的部分）{RESET}")
    total = len(users)
    mappable = sum(len(v) for v in a["users_under_root"].values())
    gap = len(a["users_without_school_id"])
    print(f"  用户总数                              {total}")
    print(f"  Q-01 裁决后可归属的用户                {mappable}")
    print(f"  仍会隔离：缺 organization_context.school_id  {gap}")
    print(f"  {'—' * 60}")
    print(f"  → Q-01 裁决**只能**解锁 {mappable}/{total} 个用户；")
    print(f"    剩下 {gap} 个是**数据缺口**，不是映射问题，需要回填 school_id 或")
    print(f"    业务裁决归属方式，与本工具无关。")
    if a["broken"] or a["non_school_roots"]:
        print(f"\n{RED}存在结构缺陷，候选表仅供参考，不得直接采用。{RESET}")
        return EXIT_FINDINGS

    if out_path:
        payload = {
            "_status": "PENDING_HUMAN_CONFIRMATION",
            "_note": "候选由工具推导，未经人工确认；租户 ID 为占位值。"
                     "采用前请逐条核对根节点是否等于业务上的租户边界。",
            "tenant_map": candidates,
        }
        pathlib.Path(out_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n{GREEN}候选已写入 {out_path}（状态：待人工确认）{RESET}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 模式二：校验人工确认后的映射表
# ---------------------------------------------------------------------------

def check(src: str, map_path: str, quiet: bool = False) -> int:
    orgs = read_orgs(src)
    users = read_users(src)
    a = audit(orgs, users)

    try:
        raw = json.loads(pathlib.Path(map_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        sys.stderr.write(f"映射表读不出来：{e}\n")
        return EXIT_TOOL_BROKEN
    tmap = raw.get("tenant_map", raw) if isinstance(raw, dict) else {}
    if not isinstance(tmap, dict) or not tmap:
        sys.stderr.write("映射表为空或格式不对（应为 {根组织id: tenant_id}）。\n")
        return EXIT_TOOL_BROKEN

    problems: list[str] = []
    if not quiet:
        print("=" * 72)
        print("Q-01 映射表自洽性校验")
        print("=" * 72)

    roots = set(a["roots"])

    # 1) 映射到不存在的组织
    for org in tmap:
        if org not in orgs:
            problems.append(f"映射表含源库不存在的组织：{org}")

    # 2) 映射到非根节点 = 拆散一棵学校树
    for org in tmap:
        if org in orgs and orgs[org]["parent_id"]:
            problems.append(
                f"{org} 不是根节点（父级 {orgs[org]['parent_id']}），"
                "把它当租户边界会拆散一棵树")

    # 3) 根节点的 type 不对
    for org in tmap:
        if org in orgs and orgs[org]["type"] not in TENANT_ROOT_TYPES:
            problems.append(f"{org} 的 type={orgs[org]['type']!r} 不是 school，"
                            "不应作为租户边界")

    # 4) 租户 ID 重复 = 两个根挤进同一租户 = 数据分裂
    seen_t: dict[str, str] = {}
    for org, tid in tmap.items():
        if tid in seen_t:
            problems.append(f"租户 {tid} 同时映射了 {seen_t[tid]} 与 {org}，"
                            "两个根挤进同一租户会造成数据分裂")
        else:
            seen_t[tid] = org

    # 5) 租户 ID 复用给不同根（已在 4 覆盖），以及根漏映射
    missing = roots - set(tmap)
    if missing:
        problems.append(f"以下根节点未出现在映射表中，会导致其整棵子树无法归属："
                        f"{', '.join(sorted(missing))}")

    # 6) 占位 ID 未替换
    placeholders = [o for o in tmap if tmap[o].startswith("tenant-cand-")]
    if placeholders:
        problems.append(f"仍有 {len(placeholders)} 个租户使用占位 ID"
                        f"（tenant-cand-*），正式映射表不应保留")

    # 7) 状态字段
    if isinstance(raw, dict) and raw.get("_status") == "PENDING_HUMAN_CONFIRMATION":
        problems.append("映射表仍标记为「待人工确认」，不得用于迁移")

    # 覆盖度量化
    mappable = sum(len(v) for v in a["users_under_root"].values())
    ok_users = 0
    for u in users:
        sid = (_json(u["organization_context"]).get("school_id") or "").strip()
        if sid and sid in orgs:
            root, st = walk_to_root(sid, orgs)
            if root and root in tmap:
                ok_users += 1

    print(f"  映射条目            {len(tmap)}")
    print(f"  源库根节点          {len(roots)}")
    print(f"  可归属用户          {ok_users}/{len(users)}")
    print(f"  缺 school_id 隔离   {len(a['users_without_school_id'])}"
          f"  {DIM}（数据缺口，与映射表无关）{RESET}")
    print()

    if problems:
        for p in problems:
            print(f"  {RED}✗{RESET} {p}")
        if not quiet:
            print("=" * 72)
            print(f"{RED}映射表未通过：{len(problems)} 项问题{RESET}")
        return EXIT_FINDINGS
    if not quiet:
        print(f"  {GREEN}✓ 映射表自洽{RESET}")
        print("=" * 72)
        print(f"{GREEN}通过{RESET}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def _mksrc(path: str, orgs: list[tuple[str, str, str]],
           users: list[tuple[str, str]]) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE organization_units (id TEXT PRIMARY KEY, data TEXT, updated_at TEXT);
        CREATE TABLE users (id TEXT PRIMARY KEY, role TEXT, organization_context TEXT);
    """)
    for oid, ty, pid in orgs:
        con.execute("INSERT INTO organization_units VALUES (?,?,?)",
                    (oid, json.dumps({"id": oid, "name": oid, "type": ty,
                                      "parent_id": pid}), ""))
    for uid, role, ctx in users:
        con.execute("INSERT INTO users VALUES (?,?,?)", (uid, role, ctx))
    con.commit()
    con.close()


def self_test() -> int:
    print(f"{DIM}Q-01 工具自检：正向 + 变异{RESET}\n")
    print("=" * 72)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="q01_selftest_"))
    passed = 0
    total = 0

    def _quiet(fn, *a, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **kw)

    def t(label: str, cond: bool) -> None:
        nonlocal passed, total
        total += 1
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if cond:
            passed += 1

    # 正例：两所学校，其中一棵树下有用户
    good = str(tmp / "good.db")
    _mksrc(good,
           [("s1", "school", ""), ("c1", "college", "s1"),
            ("s2", "school", ""), ("c2", "college", "s2")],
           [("u1", "student", json.dumps({"school_id": "s1"})),
            ("u2", "teacher", json.dumps({"school_id": "s2"}))])
    a = audit(read_orgs(good), read_users(good))
    t("识别出 2 个学校根", set(a["roots"]) == {"s1", "s2"})
    t("每个根各 1 个用户", all(len(v) == 1 for v in a["users_under_root"].values()))

    m = str(tmp / "m.json")
    pathlib.Path(m).write_text(json.dumps(
        {"_status": "CONFIRMED", "tenant_map": {"s1": "t-1", "s2": "t-2"}}),
        encoding="utf-8")
    t("人工确认后的正确映射表通过校验", _quiet(check, good, m, quiet=True) == EXIT_OK)

    # 变异 1：非根节点当租户边界
    bad1 = str(tmp / "b1.json")
    pathlib.Path(bad1).write_text(json.dumps(
        {"_status": "CONFIRMED", "tenant_map": {"s1": "t-1", "s2": "t-2",
                                                "c1": "t-3"}}), encoding="utf-8")
    t("变异「学院当租户根」被拒", _quiet(check, good, bad1, quiet=True) == EXIT_FINDINGS)

    # 变异 2：两个根挤同一租户
    bad2 = str(tmp / "b2.json")
    pathlib.Path(bad2).write_text(json.dumps(
        {"_status": "CONFIRMED", "tenant_map": {"s1": "t-1", "s2": "t-1"}}),
        encoding="utf-8")
    t("变异「两个根挤同一租户」被拒", _quiet(check, good, bad2, quiet=True) == EXIT_FINDINGS)

    # 变异 3：漏映射一个根
    bad3 = str(tmp / "b3.json")
    pathlib.Path(bad3).write_text(json.dumps(
        {"_status": "CONFIRMED", "tenant_map": {"s1": "t-1"}}), encoding="utf-8")
    t("变异「漏映射一个根」被拒", _quiet(check, good, bad3, quiet=True) == EXIT_FINDINGS)

    # 变异 4：未替换占位 ID
    bad4 = str(tmp / "b4.json")
    pathlib.Path(bad4).write_text(json.dumps(
        {"_status": "CONFIRMED",
         "tenant_map": {"s1": "tenant-cand-s1", "s2": "t-2"}}), encoding="utf-8")
    t("变异「残留占位 ID」被拒", _quiet(check, good, bad4, quiet=True) == EXIT_FINDINGS)

    # 变异 5：仍标记为待确认
    bad5 = str(tmp / "b5.json")
    pathlib.Path(bad5).write_text(json.dumps(
        {"_status": "PENDING_HUMAN_CONFIRMATION",
         "tenant_map": {"s1": "t-1", "s2": "t-2"}}), encoding="utf-8")
    t("变异「未人工确认」被拒", _quiet(check, good, bad5, quiet=True) == EXIT_FINDINGS)

    # 结构缺陷：根节点 type 不是 school
    weird = str(tmp / "weird.db")
    _mksrc(weird, [("x1", "college", "")], [("u1", "student",
           json.dumps({"school_id": "x1"}))])
    t("非 school 根被识别为不可用边界",
      len(audit(read_orgs(weird), read_users(weird))["non_school_roots"]) == 1)

    # 结构缺陷：成环
    cyc = str(tmp / "cyc.db")
    _mksrc(cyc, [("a", "college", "b"), ("b", "college", "a")], [])
    t("组织树成环被报为结构缺陷",
      len(audit(read_orgs(cyc), read_users(cyc))["broken"]) > 0)

    # 空表
    empty = str(tmp / "empty.db")
    sqlite3.connect(empty).close()
    t("无组织数据时拒绝产出", emit(empty, None, "school") == EXIT_TOOL_BROKEN)

    print("=" * 72)
    if passed == total:
        print(f"{GREEN}自检通过：{passed}/{total}{RESET}")
        return EXIT_OK
    print(f"{RED}自检失败：{passed}/{total}{RESET}")
    return EXIT_FINDINGS


def main() -> int:
    ap = argparse.ArgumentParser(description="Q-01 租户映射候选生成与校验")
    ap.add_argument("--source", help="源库 SQLite 路径（只读）")
    ap.add_argument("--emit-candidates", action="store_true", help="产出候选映射")
    ap.add_argument("--out", help="候选映射输出路径")
    ap.add_argument("--check", help="校验人工确认后的映射表")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.source or not pathlib.Path(args.source).exists():
        sys.stderr.write("需要 --source 且文件存在。\n")
        return EXIT_TOOL_BROKEN
    if args.check:
        return check(args.source, args.check)
    if args.emit_candidates:
        return emit(args.source, args.out, "school")
    ap.print_help()
    return EXIT_TOOL_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
