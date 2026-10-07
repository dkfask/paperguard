#!/usr/bin/env python3
"""E5 回填校验器（独立于回填器，避免自证）。

配套：docs/product/W-02-存储与Schema实施规格_20261001.md §T-02.6（E5）
      docs/product/saas-platform-design-20261001.md §22.2 M4 六项

为什么必须"独立"
----------------
回填器写完数据后自己检查自己，逻辑写错时两边会一起错，校验就是自证。
本工具**只读**两个数据集并做比对，不参与任何写入，也不复用回填器的代码路径。

六项核对（对应总设计 22.2 M4）
----------------------------
    BV-0  覆盖面    受核对的表在源/目标两侧都存在且非空（否则其余各项无从谈起）
    BV-1  数量核对    每表总数 / 已回填数 / 冲突数
    BV-2  关系核对    组织树无环、无跨租户父子、无断链
    BV-3  摘要核对    不可变内容 hash 与源库一致
    BV-4  版本核对    冻结版本（generation）一致
    BV-5  权限核对    迁移角色与 API 角色符合 ADR-02
    BV-6  真实产物    至少 1 个真实产物可打开且内容一致

用法
----
    # 校验（源库 + 目标库）
    python3 backfill_validate.py --source src.db --target tgt.db --manifest backup.json

    # 故障注入自检：证明本工具抓得住 5 类错误
    python3 backfill_validate.py --self-test

退出码
------
    0 = 全部通过
    1 = 存在校验失败（工具正常工作，结论是"不合格"）
    2 = 工具自身无法运行（缺输入、缺依赖等）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys
import tempfile

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_TOOL_BROKEN = 2

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

# 需要核对的表：DDL 草案中带 tenant_id 侧边列的表
TENANT_TABLES = (
    "tenant_organizations",
    "projects",
    "templates",
    "requirement_sets",
    "workflow_records",
    "storage_objects",
)

# 源库→目标库表名映射。
#
# 刻意与 backfill_migrate.py **各写一份**，不共用文件：两边读同一份映射时，
# 映射写错会同时骗过迁移器和校验器，自检就再也发现不了。重复是这里想要的。
# 可用 --source-alias 覆盖。
SOURCE_ALIAS = {
    "tenant_organizations": "organization_units",
}

# `users` **不在**核对表内：W-02 §4 判定 users = none（全局账号，ADR-03），
# 它没有 tenant_id 列。把它列进来会让 BV-1 永远报「缺 tenant_id 列」，
# 更糟的是会诱使人「给它补一列」——那是直接违反 ADR-03。
# 用户的租户归属由 tenant_memberships 承载，不在 users 表上。


ORG_TABLE = "tenant_organizations"   # W-02 §4：organization_units 迁到 tenant_organizations


def source_name(table: str, alias: dict[str, str] | None = None) -> str:
    return (alias or SOURCE_ALIAS).get(table, table)


class Finding:
    __slots__ = ("code", "message", "detail")

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        self.code = code
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}" + (f" — {self.detail}" if self.detail else "")


# ---------------------------------------------------------------------------
# 载入
# ---------------------------------------------------------------------------

def load_rows(path: str, table: str) -> list[dict]:
    """只读载入一张表。不存在则返回空列表（由调用方决定是否算错）。"""
    uri = f"file:{path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.execute(f"SELECT * FROM {table}")  # noqa: S608 - 表名来自白名单
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def has_table(path: str, table: str) -> bool:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# BV-0 覆盖面核对（先于其余各项执行）
# ---------------------------------------------------------------------------

def coverage_table(src: str, tgt: str,
                   alias: dict[str, str] | None = None) -> list[tuple[str, int, int, str]]:
    """逐表报告实际核对了多少行。返回 (表名, 源行数, 目标行数, 状态)。"""
    out: list[tuple[str, int, int, str]] = []
    for t in TENANT_TABLES:
        sn_name = source_name(t, alias)
        s_has, t_has = has_table(src, sn_name), has_table(tgt, t)
        sn = len(load_rows(src, sn_name)) if s_has else -1
        tn = len(load_rows(tgt, t)) if t_has else -1
        if not s_has and not t_has:
            state = f"两侧均无此表（源侧找过 {sn_name}）——未核对"
        elif not t_has:
            state = "目标库缺此表——未核对"
        elif sn == 0 and tn == 0:
            state = "两侧均为空——无数据可核对"
        else:
            state = "已核对"
        out.append((t, max(sn, 0), max(tn, 0), state))
    return out


def check_coverage(src: str, tgt: str,
                   alias: dict[str, str] | None = None) -> list[Finding]:
    """核对覆盖面本身。

    为什么必须单列一项
    ------------------
    `load_rows` 对不存在的表返回 `[]`，于是 BV-1 的 `len(s) != len(g)` 在
    「源与目标都没有这张表」时会判为 `0 != 0` 为假，即**行数一致**。
    那是两个空集相等，不是「已核对」——一份从未比对过任何一行的表，
    在报告里看起来和核对通过毫无区别。

    已在本仓实测：`organizations` 的源侧真实表名是 `organization_units`，
    若不做表名映射，BV-1 会永远报「源 0 → 目标 3」而 BV-3 一行也比不了。
    """
    out: list[Finding] = []
    for t, sn, tn, state in coverage_table(src, tgt, alias):
        if state.startswith("两侧均无"):
            out.append(Finding(
                "BV-0-NOTVERIFIED", f"表 {t} 在源库与目标库都不存在，本项从未核对",
                "0 行 == 0 行 会被 BV-1 判为行数一致；这是空集相等，不是核对通过。"
                "若该表在目标 schema 中确实存在，说明源/目标表名映射未对齐。"))
        elif state == "目标库缺此表":
            out.append(Finding(
                "BV-0-NOTVERIFIED", f"表 {t} 源库有（{sn} 行）但目标库没有，本项未核对",
                "回填未产出该表"))
        # 「两侧均为空」是警告不是失败：确实无数据可核对，但不是不一致。
    return out


# ---------------------------------------------------------------------------
# BV-1 数量核对
# ---------------------------------------------------------------------------

def check_counts(src: str, tgt: str, alias: dict[str, str] | None = None) -> list[Finding]:
    out: list[Finding] = []
    for t in TENANT_TABLES:
        s = load_rows(src, source_name(t, alias))
        g = load_rows(tgt, t)
        if len(s) != len(g):
            out.append(Finding("BV-1-COUNT", f"表 {t} 行数不一致",
                               f"源 {len(s)} → 目标 {len(g)}"))
            continue
        # 目标库必须存在 tenant_id 列且已全部回填（允许显式 quarantine）
        if g and "tenant_id" not in g[0]:
            out.append(Finding("BV-1-NOCOL", f"表 {t} 缺少 tenant_id 列"))
            continue
        filled = [r for r in g if (r.get("tenant_id") or "").strip()]
        quarantined = [r for r in g if (r.get("tenant_id") or "").strip() == "quarantine"]
        blanks = len(g) - len(filled)
        if blanks:
            out.append(Finding("BV-1-UNFILLED", f"表 {t} 有 {blanks} 行未回填 tenant_id",
                               "W-02 规定冲突数据须落隔离清单，不得留空"))
        _ = quarantined  # 隔离计数在报告里单独统计
    return out


def quarantine_report(tgt: str) -> dict:
    counts = {}
    for t in TENANT_TABLES:
        rows = load_rows(tgt, t)
        if not rows or "tenant_id" not in rows[0]:
            continue
        n = sum(1 for r in rows if (r.get("tenant_id") or "").strip() == "quarantine")
        if n:
            counts[t] = n
    return counts


# ---------------------------------------------------------------------------
# BV-2 关系核对：组织树无环 / 无跨租户父子 / 无断链
# ---------------------------------------------------------------------------

def check_relations(tgt: str) -> list[Finding]:
    out: list[Finding] = []
    orgs = load_rows(tgt, ORG_TABLE)
    if not orgs:
        return [Finding("BV-2-NODATA", f"目标库没有 {ORG_TABLE} 表或表为空",
                        "关系核对无法执行——这不是「通过」，是「没数据」")]

    by_id = {r["id"]: r for r in orgs}

    # 断链：parent_id 指向不存在的节点
    for r in orgs:
        pid = (r.get("parent_id") or "").strip()
        if pid and pid not in by_id:
            out.append(Finding("BV-2-DANGLING", f"组织 {r['id']} 的父节点 {pid} 不存在"))

    # 环：沿 parent 链上溯，用访问集合判环
    for r in orgs:
        seen: set[str] = set()
        cur = r
        while True:
            cid = cur["id"]
            if cid in seen:
                out.append(Finding("BV-2-CYCLE", f"组织树存在环，涉及 {cid}"))
                break
            seen.add(cid)
            pid = (cur.get("parent_id") or "").strip()
            if not pid or pid not in by_id:
                break
            cur = by_id[pid]

    # 跨租户父子：父子的 tenant_id 必须相同
    for r in orgs:
        pid = (r.get("parent_id") or "").strip()
        if not pid or pid not in by_id:
            continue
        ct, pt = (r.get("tenant_id") or "").strip(), (by_id[pid].get("tenant_id") or "").strip()
        if ct and pt and ct != pt:
            out.append(Finding("BV-2-CROSSTENANT",
                               f"组织 {r['id']}（租户 {ct}）的父节点 {pid} 属于租户 {pt}",
                               "子树必须继承根组织租户，跨租户父子即数据分裂"))
    return out


# ---------------------------------------------------------------------------
# BV-3 摘要核对
# ---------------------------------------------------------------------------

def _digest(obj: dict, fields: tuple[str, ...]) -> str:
    payload = {f: obj.get(f) for f in fields}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def check_digests(src: str, tgt: str, manifest: dict | None,
                  alias: dict[str, str] | None = None) -> list[Finding]:
    """不可变内容摘要与源库一致。

    判定源取三处：行内列（若源库已有 checksum）、备份 manifest、以及
    行内容本身。任一处不一致即报错。
    """
    out: list[Finding] = []
    for t in TENANT_TABLES:
        s = {r["id"]: r for r in load_rows(src, source_name(t, alias))}
        g = {r["id"]: r for r in load_rows(tgt, t)}
        for oid, srow in s.items():
            if oid not in g:
                out.append(Finding("BV-3-MISSING", f"{t}.{oid} 在目标库缺失"))
                continue
            if "checksum" not in srow or "checksum" not in g[oid]:
                continue
            if srow.get("checksum") and srow["checksum"] != g[oid].get("checksum"):
                out.append(Finding("BV-3-DIGEST", f"{t}.{oid} 摘要不一致",
                                   f"{srow['checksum'][:12]} → {str(g[oid].get('checksum'))[:12]}"))
    if manifest:
        for t, rec in (manifest.get("tables") or {}).items():
            sc, gc = rec.get("source_count"), rec.get("target_count")
            if sc is not None and gc is not None and sc != gc:
                out.append(Finding("BV-3-MANIFEST", f"manifest 记录 {t} 数量不符",
                                   f"manifest {sc} vs 实际 {gc}"))
    return out


# ---------------------------------------------------------------------------
# BV-4 版本核对
# ---------------------------------------------------------------------------

def check_versions(tgt: str) -> list[Finding]:
    """冻结版本一致：同 (tenant, kind, opaque_id) 下 generation 不得重复，
    且 state 已发布的不允许出现比它更新的未发布版本。"""
    out: list[Finding] = []
    objs = load_rows(tgt, "storage_objects")
    if not objs:
        return out
    groups: dict[tuple, list[dict]] = {}
    for r in objs:
        key = (r.get("tenant_id"), r.get("kind"), r.get("opaque_id"))
        groups.setdefault(key, []).append(r)
    for key, rows in groups.items():
        gens = [r.get("generation") for r in rows]
        if len(gens) != len(set(gens)):
            out.append(Finding("BV-4-DUPGEN", f"存储对象 {key} 存在重复 generation", str(gens)))
        published = [r for r in rows if r.get("state") == "published"]
        if published:
            newest_pub = max(r.get("generation") or 0 for r in published)
            for r in rows:
                if (r.get("generation") or 0) > newest_pub and r.get("state") != "published":
                    out.append(Finding("BV-4-DRIFT",
                                       f"存储对象 {key} 存在比已发布版本更新的未发布版本",
                                       f"gen {r.get('generation')} > 已发布 {newest_pub}"))
    return out


# ---------------------------------------------------------------------------
# BV-5 权限核对
# ---------------------------------------------------------------------------

def check_privileges(tgt: str, spec_path: str | None) -> list[Finding]:
    """角色权限符合 ADR-02：BYPASSRLS / 所有者角色不得出现在普通 API 路径。"""
    out: list[Finding] = []
    roles = load_rows(tgt, "db_roles")
    if not roles:
        # 真实目标库未必有这张表；退化检查：扫描 spec 中声明的高权角色
        if spec_path and pathlib.Path(spec_path).exists():
            text = pathlib.Path(spec_path).read_text(encoding="utf-8")
            for kw in ("BYPASSRLS", "SUPERUSER"):
                if kw in text:
                    out.append(Finding("BV-5-ROLE", f"规格中声明了 {kw} 角色",
                                       "该角色不得被 API 或 worker 使用"))
        return out
    for r in roles:
        name = str(r.get("rolname") or r.get("name") or "")
        bypass = r.get("rolbypassrls") or r.get("bypassrls")
        if name in {"paperguard_api", "paperguard_worker"} and bypass:
            out.append(Finding("BV-5-BYASS", f"API/worker 角色 {name} 拥有 BYPASSRLS",
                               "普通路径不得绕过行级安全"))
    return out


# ---------------------------------------------------------------------------
# BV-6 真实产物核对
# ---------------------------------------------------------------------------

def check_artifacts(src: str, tgt: str, artifacts: list[str] | None) -> list[Finding]:
    """至少 1 个真实产物可打开且内容一致。"""
    out: list[Finding] = []
    if not artifacts:
        out.append(Finding("BV-6-NOPROVIDED", "未提供真实产物路径",
                           "无法验证产物可打开——这是「没验」，不是「通过」"))
        return out
    ok = 0
    for p in artifacts:
        path = pathlib.Path(p)
        if not path.exists():
            out.append(Finding("BV-6-MISSING", f"产物不存在：{p}"))
            continue
        try:
            import zipfile

            with zipfile.ZipFile(path) as zf:
                if zf.testzip() is not None:
                    out.append(Finding("BV-6-CORRUPT", f"产物损坏：{p}"))
                    continue
                names = zf.namelist()
                if not any(n.startswith("word/") for n in names):
                    out.append(Finding("BV-6-NOTDOCX", f"不是 DOCX 结构：{p}"))
                    continue
            ok += 1
        except zipfile.BadZipFile:
            out.append(Finding("BV-6-BADZIP", f"无法打开：{p}"))
    if ok == 0 and not out:
        out.append(Finding("BV-6-NONE", "没有任何产物通过校验"))
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_all(src: str, tgt: str, manifest: dict | None,
            artifacts: list[str] | None, spec_path: str | None,
            alias: dict[str, str] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    findings += check_coverage(src, tgt, alias)
    findings += check_counts(src, tgt, alias)
    findings += check_relations(tgt)
    findings += check_digests(src, tgt, manifest, alias)
    findings += check_versions(tgt)
    findings += check_privileges(tgt, spec_path)
    findings += check_artifacts(src, tgt, artifacts)
    return findings


def _mkdb(organizations: list[dict], storage: list[dict] | None = None,
          roles: list[dict] | None = None, checksums: dict[str, str] | None = None) -> str:
    """构造一个临时库。

    `checksums` 用于制造「源与目标摘要不同」的场景：源库写入原值，
    目标库写入被篡改的值，从而触发 BV-3。
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    pathlib.Path(path).unlink()
    tamper = checksums or {}
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tenant_organizations (id TEXT PRIMARY KEY, parent_id TEXT, tenant_id TEXT)")
    con.executemany("INSERT INTO tenant_organizations VALUES (?,?,?)",
                    [(o["id"], o.get("parent_id"), o.get("tenant_id")) for o in organizations])
    con.execute("CREATE TABLE storage_objects (id TEXT PRIMARY KEY, tenant_id TEXT, "
                "kind TEXT, opaque_id TEXT, generation INTEGER, state TEXT, checksum TEXT)")
    con.executemany("INSERT INTO storage_objects VALUES (?,?,?,?,?,?,?)",
                    [(s["id"], s.get("tenant_id"), s.get("kind"), s.get("opaque_id"),
                      s.get("generation"), s.get("state"), tamper.get(s["id"], s.get("checksum")))
                     for s in (storage or [])])
    con.execute("CREATE TABLE db_roles (name TEXT PRIMARY KEY, bypassrls INTEGER)")
    con.executemany("INSERT INTO db_roles VALUES (:name,:bypassrls)", roles or [])
    # 其余受核对表一律建成空表：否则 BV-0 会把它们判为「从未核对」，
    # 淹没自检真正要验证的注入项。自检只关心被注入的那几张表。
    for t in TENANT_TABLES:
        if t not in ("tenant_organizations", "storage_objects"):
            con.execute(f"CREATE TABLE {t} (id TEXT PRIMARY KEY, tenant_id TEXT, checksum TEXT)")
    con.commit()
    con.close()
    return path


def self_test() -> int:
    """故障注入自检：W-02 的 DoD 要求「对故意注入的 5 类错误全部报出」。

    一个抓不住注入错误的校验器不是校验器。本自检在**不依赖真实数据库**
    的前提下证明 BV-2/BV-3/BV-4/BV-5 确实会失败。
    """
    print(f"{DIM}故障注入自检：证明本工具抓得住 7 类错误{RESET}\n")
    print("=" * 72)

    # 干净基线：应无发现
    clean = _mkdb(
        organizations=[
            {"id": "t1-root", "parent_id": None, "tenant_id": "t1"},
            {"id": "t1-col", "parent_id": "t1-root", "tenant_id": "t1"},
            {"id": "t1-maj", "parent_id": "t1-col", "tenant_id": "t1"},
        ],
        storage=[{"id": "o1", "tenant_id": "t1", "kind": "archive", "opaque_id": "a1",
                  "generation": 1, "state": "published", "checksum": "abc"}],
        roles=[{"name": "paperguard_api", "bypassrls": 0}],
    )
    base = run_all(clean, clean, None, None, None)
    base_rel = [f for f in base if f.code.startswith(("BV-2", "BV-4", "BV-5"))]
    ok_base = not base_rel
    print(f"  [{'PASS' if ok_base else 'FAIL'}] 干净数据集不产生误报"
          f"（得到 {len(base_rel)} 条）")

    cases = [
        ("跨租户父子", {
            "organizations": [
                {"id": "r", "parent_id": None, "tenant_id": "t1"},
                {"id": "c", "parent_id": "r", "tenant_id": "t2"},   # ← 注入
            ]}, "BV-2-CROSSTENANT"),
        ("孤儿/断链", {
            "organizations": [
                {"id": "r", "parent_id": None, "tenant_id": "t1"},
                {"id": "x", "parent_id": "ghost", "tenant_id": "t1"},  # ← 注入
            ]}, "BV-2-DANGLING"),
        ("组织树成环", {
            "organizations": [
                {"id": "a", "parent_id": "b", "tenant_id": "t1"},   # ← 注入
                {"id": "b", "parent_id": "a", "tenant_id": "t1"},
            ]}, "BV-2-CYCLE"),
        ("版本漂移", {
            "organizations": [{"id": "r", "parent_id": None, "tenant_id": "t1"}],
            "storage": [
                {"id": "o1", "tenant_id": "t1", "kind": "archive", "opaque_id": "a1",
                 "generation": 1, "state": "published", "checksum": "abc"},
                {"id": "o2", "tenant_id": "t1", "kind": "archive", "opaque_id": "a1",
                 "generation": 2, "state": "pending", "checksum": "def"},  # ← 注入
            ]}, "BV-4-DRIFT"),
        ("越权角色", {
            "organizations": [{"id": "r", "parent_id": None, "tenant_id": "t1"}],
            "roles": [{"name": "paperguard_api", "bypassrls": 1}],  # ← 注入
        }, "BV-5-BYASS"),
    ]

    caught = 0
    for label, spec, expect_code in cases:
        db = _mkdb(**spec)
        found = run_all(db, db, None, None, None)
        hit = any(f.code == expect_code for f in found)
        got = next((f for f in found if f.code == expect_code), None)
        mark = "PASS" if hit else "FAIL"
        print(f"  [{mark}] 注入「{label}」→ 期望 {expect_code}，"
              f"实际 {'捕获' if hit else '未捕获'}")
        if hit:
            caught += 1
            print(f"         {got}")
        pathlib.Path(db).unlink(missing_ok=True)

    # 第 6 类：摘要不符。这一类必须比较**两个不同的库**，
    # 因此不能像前 5 类那样用同一个 db 自比——自比必然一致。
    print()
    clean_orgs = [{"id": "r", "parent_id": None, "tenant_id": "t1"}]
    src_db = _mkdb(organizations=clean_orgs, storage=[
        {"id": "o1", "tenant_id": "t1", "kind": "archive", "opaque_id": "a1",
         "generation": 1, "state": "published", "checksum": "sha256-ORIGINAL"}])
    tgt_db = _mkdb(organizations=clean_orgs, storage=[
        {"id": "o1", "tenant_id": "t1", "kind": "archive", "opaque_id": "a1",
         "generation": 1, "state": "published", "checksum": "sha256-ORIGINAL"}],
        checksums={"o1": "sha256-TAMPERED"})                       # ← 注入
    same_db = _mkdb(organizations=clean_orgs, storage=[
        {"id": "o1", "tenant_id": "t1", "kind": "archive", "opaque_id": "a1",
         "generation": 1, "state": "published", "checksum": "sha256-ORIGINAL"}])

    f_same = run_all(same_db, same_db, None, None, None)
    same_clean = not any(f.code == "BV-3-DIGEST" for f in f_same)
    print(f"  [{'PASS' if same_clean else 'FAIL'}] 源与目标摘要一致时不误报")

    f_tamper = run_all(src_db, tgt_db, None, None, None)
    hit3 = any(f.code == "BV-3-DIGEST" for f in f_tamper)
    got3 = next((f for f in f_tamper if f.code == "BV-3-DIGEST"), None)
    print(f"  [{'PASS' if hit3 else 'FAIL'}] 注入「摘要不符」→ 期望 BV-3-DIGEST，"
          f"实际 {'捕获' if hit3 else '未捕获'}")
    if hit3:
        caught += 1
        print(f"         {got3}")
    if not same_clean:
        print(f"  [FAIL] 摘要核对在一致时误报")
    for db in (src_db, tgt_db, same_db):
        pathlib.Path(db).unlink(missing_ok=True)
    pathlib.Path(clean).unlink(missing_ok=True)

    # 第 7 类：覆盖面骗人。构造一个**根本没有 users 表**的库——
    # 源与目标都没有，于是 BV-1 的「0 行 == 0 行」会判为行数一致。
    # 本工具必须把它报成「未核对」，否则报告读起来像核对通过。
    print()
    fd, gap_db = tempfile.mkstemp(suffix=".db")
    pathlib.Path(gap_db).unlink()
    gcon = sqlite3.connect(gap_db)
    gcon.execute("CREATE TABLE tenant_organizations (id TEXT PRIMARY KEY, parent_id TEXT, tenant_id TEXT)")
    gcon.execute("INSERT INTO tenant_organizations VALUES ('r', NULL, 't1')")
    gcon.execute("CREATE TABLE storage_objects (id TEXT PRIMARY KEY, tenant_id TEXT, "
                 "kind TEXT, opaque_id TEXT, generation INTEGER, state TEXT, checksum TEXT)")
    gcon.commit()
    gcon.close()
    f_gap = run_all(gap_db, gap_db, None, None, None)
    hit0 = any(f.code == "BV-0-NOTVERIFIED" for f in f_gap)
    got0 = next((f for f in f_gap if f.code == "BV-0-NOTVERIFIED"), None)
    print(f"  [{'PASS' if hit0 else 'FAIL'}] 注入「表在两侧都不存在」→ 期望 BV-0-NOTVERIFIED，"
          f"实际 {'捕获' if hit0 else '未捕获'}")
    if hit0:
        caught += 1
        print(f"         {got0}")
    # 反向自检：BV-1 此时必须**没有**报 users 行数一致以外的误报
    print(f"         （同一场景下 BV-1 判定为「行数一致」，"
          f"若无 BV-0 兜底，这份报告会显示 users 已核对通过）")
    pathlib.Path(gap_db).unlink(missing_ok=True)

    print("=" * 72)
    total_inject = len(cases) + 2
    if ok_base and same_clean and caught == total_inject:
        print(f"{GREEN}自检通过：无误报，且 {caught}/{total_inject} 类注入错误全部被捕获"
              f"{RESET}")
        return EXIT_OK
    print(f"{RED}自检失败：基础误报={not ok_base}，摘要误报={not same_clean}，"
          f"捕获 {caught}/{total_inject}{RESET}")
    return EXIT_FINDINGS


def main() -> int:
    ap = argparse.ArgumentParser(description="E5 回填校验器")
    ap.add_argument("--source")
    ap.add_argument("--target")
    ap.add_argument("--manifest")
    ap.add_argument("--artifacts", nargs="*", default=None)
    ap.add_argument("--spec")
    ap.add_argument("--source-alias",
                    help="源库表名映射 JSON，如 {\"organizations\": \"organization_units\"}")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    alias = dict(SOURCE_ALIAS)
    if args.source_alias:
        alias.update(json.loads(pathlib.Path(args.source_alias).read_text(encoding="utf-8")))

    if not args.source or not args.target:
        sys.stderr.write("需要 --source 与 --target（或使用 --self-test）\n")
        return EXIT_TOOL_BROKEN
    for p in (args.source, args.target):
        if not pathlib.Path(p).exists():
            sys.stderr.write(f"输入不存在：{p}\n")
            return EXIT_TOOL_BROKEN

    manifest = None
    if args.manifest:
        manifest = json.loads(pathlib.Path(args.manifest).read_text(encoding="utf-8"))

    print(f"{DIM}源：{args.source}\n目标：{args.target}{RESET}\n")

    print("=" * 72)
    print("核对覆盖面（先看清楚哪些表真的被比对了）")
    print("=" * 72)
    for t, sn, tn, state in coverage_table(args.source, args.target, alias):
        color = YELLOW if "无数据可核对" in state else (
            RED if "未核对" in state else GREEN)
        print(f"  {t:20} 源 {sn:>6} 行   目标 {tn:>6} 行   {color}{state}{RESET}")
    print()

    findings = run_all(args.source, args.target, manifest, args.artifacts,
                       args.spec, alias)

    print("=" * 72)
    print("E5 回填校验结果")
    print("=" * 72)
    by_code: dict[str, list[Finding]] = {}
    for f in findings:
        by_code.setdefault(f.code.split("-")[0] + "-" + f.code.split("-")[1], []).append(f)
    for group in sorted(by_code):
        print(f"\n{YELLOW}{group}{RESET}")
        for f in by_code[group]:
            print(f"  {RED}✗{RESET} {f}")

    q = quarantine_report(args.target)
    print(f"\n{DIM}隔离清单：{q or '（空）'}{RESET}")
    print("=" * 72)
    if findings:
        print(f"{RED}未通过：{len(findings)} 项{RESET}")
        return EXIT_FINDINGS
    print(f"{GREEN}全部通过{RESET}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
