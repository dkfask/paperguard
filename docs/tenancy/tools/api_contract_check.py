#!/usr/bin/env python3
"""AC 契约校验器：把 api-contract-delta.md 的目标契约变成可执行门禁。

配套文档：docs/tenancy/api-contract-delta.md §6
契约补丁：docs/tenancy/openapi-target-delta.yaml

设计要点：

1. **只读**。它在临时目录里生成一份独立的 OpenAPI 快照，不碰
   `data/paperguard.db`，不启动服务，不发起任何业务请求。
2. **退出码即结论**。0 = 全部达标；1 = 存在缺口。缺口逐条打印，
   便于直接粘进工作台记录。
3. **不修改任何文件**。它是门禁，不是迁移工具。

用法：
    cd backend && ../.venv/bin/python ../docs/tenancy/tools/api_contract_check.py
    # 或从仓库根目录：
    python3 docs/tenancy/tools/api_contract_check.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"

# 匿名接口白名单：这些处理器没有鉴权依赖，按约定不声明 security。
# 判据是**代码里的 Depends**，不是路径名猜测。
ANONYMOUS_PATHS = {
    "/api/version",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/mp/login",
    "/api/auth/mp/register",
    "/api/auth/mp/bind",
    "/api/auth/mp/bind-email",
    "/api/auth/mp/unbind",
    "/api/check",
    "/api/check-template",
}

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

# 租户级实体：需要只读 tenant_id（对应 resource-registry.yaml）
TENANT_SCOPED_SCHEMAS = (
    "WritingProject",
    "DocumentProject",
    "PaperTemplate-Input",
    "PaperTemplate-Output",
    "TemplateDefinition-Input",
    "TemplateDefinition-Output",
    "RequirementSet",
    "SpecParseCandidate",
    "TemplateParseCandidate",
    "OrganizationUnit",
)

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def load_spec() -> dict:
    """生成当前 OpenAPI 快照（只读，用临时数据库）。"""
    tmp = tempfile.mkdtemp(prefix="paperguard-ac-check-")
    os.environ.setdefault("PAPERGUARD_ADMIN_PASSWORD", "AcCheckOnlyNotReal")
    os.environ["PAPERGUARD_DB_PATH"] = str(Path(tmp) / "ac_check.db")
    sys.path.insert(0, str(BACKEND))
    from app.main import app  # noqa: E402

    return app.openapi()


def iter_ops(spec: dict):
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method in HTTP_METHODS:
                yield path, method, op


def resolve(spec: dict, schema: dict) -> dict:
    """解开 $ref（仅一层，够用）。"""
    ref = schema.get("$ref")
    if not ref:
        return schema
    name = ref.rsplit("/", 1)[-1]
    return spec.get("components", {}).get("schemas", {}).get(name, {})


def collect_request_schema_names(spec: dict, op: dict) -> set[str]:
    """取出该操作请求体引用到的所有 schema 名（含嵌套一层）。"""
    names: set[str] = set()
    body = op.get("requestBody", {}).get("content", {})
    for media in body.values():
        node = media.get("schema", {})
        for _ in range(3):  # 展开最多 3 层，够抓 inline dict
            ref = node.get("$ref")
            if not ref:
                break
            name = ref.rsplit("/", 1)[-1]
            names.add(name)
            node = spec.get("components", {}).get("schemas", {}).get(name, {})
            props = node.get("properties", {})
            if not props:
                break
            # 无 $ref 的内联对象：检查它自己是否直接含 tenant_id
            for pname, pschema in props.items():
                if pname == "tenant_id":
                    names.add("__INLINE_TENANT_ID__")
    return names


def has_inline_tenant_id(spec: dict, op: dict) -> bool:
    """请求体是否**内联**声明了 tenant_id（即便外层有 $ref）。"""
    body = op.get("requestBody", {}).get("content", {})
    for media in body.values():
        node = media.get("schema", {})
        for _ in range(4):
            if "tenant_id" in (node.get("properties") or {}):
                return True
            ref = node.get("$ref")
            if not ref:
                break
            node = resolve(spec, node)
            if not node:
                break
    return False


def build_compliant_spec() -> dict:
    """构造一份**满足全部 AC** 的最小 spec，用于阳性对照。

    为什么必须有它：一个只会报红的检查器没有价值——它既可能因为契约
    真的没达标而红，也可能因为检查逻辑写错而红。必须先证明「达标时
    它会绿」，这个门禁才是可信的。
    """
    def err_resp(desc: str) -> dict:
        return {
            "description": desc,
            "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorBody"}}},
        }

    schemas: dict = {
        "ErrorBody": {
            "type": "object",
            "properties": {"error": {"$ref": "#/components/schemas/Error"}},
        },
        "Error": {
            "type": "object",
            "required": ["code", "message", "request_id"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "request_id": {"type": "string"},
            },
        },
        "CommandMeta": {
            "type": "object",
            "properties": {"action": {"type": "string"},
                           "status": {"type": "string"}},
        },
        "ApiMeta": {
            "type": "object",
            "properties": {"request_id": {"type": "string"},
                           "command": {"$ref": "#/components/schemas/CommandMeta"}},
        },
        "ApiEnvelope": {
            "type": "object",
            "properties": {"data": {"type": "object"},
                           "meta": {"$ref": "#/components/schemas/ApiMeta"}},
        },
        "OrganizationContext": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "school_id": {"type": "string"},
                "college_id": {"type": "string"},
                "major_id": {"type": "string"},
                "batch_id": {"type": "string"},
            },
        },
    }
    for name in TENANT_SCOPED_SCHEMAS:
        schemas[name] = {
            "type": "object",
            "properties": {
                "tenant_id": {"type": "string", "readOnly": True},
                "organization_context": {
                    "$ref": "#/components/schemas/OrganizationContext"},
            },
        }
    # 请求体：只能引用不含 tenant_id 的入参模型
    schemas["CreateProjectRequest"] = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "organization_context": {
                "$ref": "#/components/schemas/OrganizationContext"},
        },
    }

    # 注意：必须以状态码为键。写成裸的 {"description","content"} 会被
    # 直接摊进 responses 里，成为一个名叫 "description" 的「状态码」，
    # 于是 2xx 分支永远匹配不到 —— 合成 spec 自己先坏掉。
    write_response = {
        "200": {
            "description": "ok",
            "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/ApiEnvelope"}}},
        }
    }
    error_responses = {
        "400": err_resp("bad request"),
        "401": err_resp("unauthorized"),
        "403": err_resp("forbidden"),
        "404": err_resp("not found"),
        "409": err_resp("conflict"),
        "429": err_resp("rate limited"),
    }

    paths: dict = {}
    for p in sorted(ANONYMOUS_PATHS):
        paths[p] = {"get": {
            "responses": {**write_response, **error_responses},
        }}
    paths["/api/projects"] = {
        "get": {"security": [{"bearerAuth": []}],
                "responses": {**write_response, **error_responses}},
        "post": {
            "security": [{"bearerAuth": []}],
            "parameters": [{"name": "Idempotency-Key", "in": "header",
                            "schema": {"type": "string"}}],
            "requestBody": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/CreateProjectRequest"}}}},
            "responses": {**write_response, **error_responses},
        },
    }

    return {
        "openapi": "3.1.0",
        "paths": paths,
        "components": {
            "schemas": schemas,
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"},
            },
        },
    }


def collect_reachable_props(spec: dict, schema: dict, _depth: int = 0) -> set[str]:
    """递归展开 $ref，返回该 schema 可达的全部属性名。

    为什么必须递归：`ErrorBody` 本身只有 `error` 一个属性，`request_id`
    在 `Error` 里；`ApiEnvelope` 只有 `data`/`meta`，`command` 在
    `ApiMeta` 里。只做一层 `json.dumps` 会把**已达标**的契约判成不达标——
    这是写这类检查器最常见的假红。
    """
    if _depth > 6 or not isinstance(schema, dict):
        return set()
    props = set((schema.get("properties") or {}).keys())
    ref = schema.get("$ref")
    if ref:
        name = ref.rsplit("/", 1)[-1]
        target = spec.get("components", {}).get("schemas", {}).get(name)
        if target:
            props |= collect_reachable_props(spec, target, _depth + 1)
    for value in (schema.get("properties") or {}).values():
        props |= collect_reachable_props(spec, value, _depth + 1)
    for extra in ("items", "not"):
        if isinstance(schema.get(extra), dict):
            props |= collect_reachable_props(spec, schema[extra], _depth + 1)
    return props


def error_contract_status(spec: dict):
    """All operations need an error declaration; a tiny slice cannot green AC-03."""
    missing, bad, good = [], [], []
    for path, method, op in iter_ops(spec):
        declared = False
        for code, resp in op.get("responses", {}).items():
            if not code.startswith(("4", "5")) or code == "422":
                continue  # preserve FastAPI legacy validation exception policy
            declared = True
            schema = resp.get("content", {}).get("application/json", {}).get("schema", {})
            (good if "ErrorBody" in __import__("json").dumps(schema) else bad).append(f"{method} {path} {code}")
        if not declared:
            missing.append(f"{method} {path}")
    return missing, bad, good


def main() -> int:
    if not BACKEND.exists():
        print(f"{RED}找不到 backend 目录：{BACKEND}{RESET}")
        return 1

    self_test = "--self-test" in sys.argv
    if self_test:
        print(f"{DIM}阳性对照：用构造的合规 spec 验证检查逻辑本身{RESET}\n")
        spec = build_compliant_spec()
    else:
        spec = load_spec()
    paths = spec.get("paths", {})
    schemas = spec.get("components", {}).get("schemas", {})
    raw = __import__("json").dumps(spec, ensure_ascii=False)

    ops = list(iter_ops(spec))
    print(f"{DIM}基线：{len(paths)} 路径 / {len(ops)} 操作 / {len(schemas)} schema{RESET}\n")

    results: list[tuple[str, bool, str]] = []

    # ---- AC-01 安全方案存在 ----
    schemes = spec.get("components", {}).get("securitySchemes", {})
    ok01 = "bearerAuth" in schemes
    results.append(("AC-01", ok01,
                    "components.securitySchemes.bearerAuth 已声明"
                    if ok01 else "缺 components.securitySchemes.bearerAuth"))

    # ---- AC-02 每个操作都声明 security 或显式匿名 ----
    missing_sec = []
    for path, _m, op in ops:
        has_sec = bool(op.get("security"))
        anon = op.get("x-anonymous") is True or path in ANONYMOUS_PATHS
        if not (has_sec or anon):
            missing_sec.append(path)
    results.append(("AC-02", not missing_sec,
                    "全部操作均已声明 security 或显式匿名"
                    if not missing_sec
                    else f"{len(missing_sec)} 个操作两者皆无，例：{missing_sec[:3]}"))

    # ---- AC-03/AC-04 错误响应统一 ----
    #
    # 注意一个容易写出的假绿：如果 spec 里**一个 4xx/5xx 都没声明**，
    # 下面的循环不会执行任何检查，然后「全部通过」——而这恰恰是最严重的
    # 现状（215 个操作里 0 个声明错误响应）。
    # 因此 AC-03 必须先断言「错误响应确实存在」，再断言「形态统一」。
    missing_errors, bad_err, ok_err = error_contract_status(spec)
    ok03 = bool(ops) and not missing_errors and not bad_err
    results.append(("AC-03", ok03,
                    f"全部操作错误契约覆盖，{len(ok_err)} 个错误响应引用 ErrorBody"
                    if ok03 else f"{len(missing_errors)} 个操作未声明业务错误；{len(bad_err)} 个错误响应未引用 ErrorBody"))
    if self_test:
        from copy import deepcopy
        mutation = deepcopy(spec)
        first_path = next(iter(mutation["paths"]))
        first_method = next(iter(mutation["paths"][first_path]))
        responses = mutation["paths"][first_path][first_method]["responses"]
        for code in list(responses):
            if code.startswith(("4", "5")):
                del responses[code]
        if not error_contract_status(mutation)[0]:
            print("AC-03 mutation failed: uncovered operation was accepted")
            return 2
        print("AC-03 mutation passed: one uncovered operation cannot borrow other operations' errors")

    # ---- AC-04 错误响应带 request_id（递归展开 ErrorBody → Error） ----
    errbody_props = collect_reachable_props(spec, {"$ref": "#/components/schemas/ErrorBody"})
    results.append(("AC-04", bool(ok_err) and "request_id" in errbody_props,
                    "ErrorBody 可达属性含必带 request_id"
                    if ok_err and "request_id" in errbody_props
                    else f"ErrorBody 尚未定义或其可达属性缺 request_id（当前：{sorted(errbody_props) or '无'}）"))

    # ---- AC-05 实体含只读 tenant_id ----
    missing_ts = [
        n for n in TENANT_SCOPED_SCHEMAS
        if "tenant_id" not in (schemas.get(n, {}).get("properties") or {})
    ]
    not_ro = [
        n for n in TENANT_SCOPED_SCHEMAS
        if "tenant_id" in (schemas.get(n, {}).get("properties") or {})
        and schemas[n]["properties"]["tenant_id"].get("readOnly") is not True
    ]
    ok05 = not missing_ts and not not_ro
    detail05 = []
    if missing_ts:
        detail05.append(f"{len(missing_ts)} 个实体无 tenant_id：{missing_ts[:3]}")
    if not_ro:
        detail05.append(f"{len(not_ro)} 个未标 readOnly：{not_ro[:3]}")
    results.append(("AC-05", ok05,
                    "全部租户级实体含 readOnly tenant_id" if ok05 else "；".join(detail05)))

    # ---- AC-06 请求体不得含 tenant_id（最重要） ----
    offenders = []
    for path, _m, op in ops:
        names = collect_request_schema_names(spec, op)
        if "__INLINE_TENANT_ID__" in names or has_inline_tenant_id(spec, op):
            offenders.append(path)
    results.append(("AC-06", not offenders,
                    "没有任何请求体声明 tenant_id"
                    if not offenders
                    else f"{len(offenders)} 个请求体声明了 tenant_id，例：{offenders[:3]}"))

    # ---- AC-07 organization_context 收口 ----
    loose_ctx = []
    for name, s in schemas.items():
        ctx = (s.get("properties") or {}).get("organization_context")
        if not ctx:
            continue
        resolved = resolve(spec, ctx) if "$ref" in ctx else ctx
        if resolved.get("type") == "object" and resolved.get("additionalProperties") is not False:
            loose_ctx.append(name)
    results.append(("AC-07", not loose_ctx,
                    "organization_context 均为具名且禁止未知键"
                    if not loose_ctx
                    else f"{len(loose_ctx)} 个仍是内联 dict[str,str]：{loose_ctx}"))

    # ---- AC-08 幂等头 ----
    idem_ops = [p for p, _m, op in ops
                if any(h.get("name") == "Idempotency-Key"
                       for h in op.get("parameters", []))]
    results.append(("AC-08", bool(idem_ops),
                    f"{len(idem_ops)} 个操作声明了 Idempotency-Key"
                    if idem_ops else "没有任何操作声明 Idempotency-Key 头"))

    # ---- AC-09 写操作含命令状态（递归展开响应 schema） ----
    writes = [(p, op) for p, m, op in ops if m in ("post", "put", "patch", "delete")]
    no_cmd = []
    for p, op in writes:
        found = False
        for code, resp in op.get("responses", {}).items():
            if not code.startswith("2"):
                continue
            sch = resp.get("content", {}).get("application/json", {}).get("schema", {})
            if "command" in collect_reachable_props(spec, sch):
                found = True
                break
        if not found:
            no_cmd.append(p)
    results.append(("AC-09", not no_cmd,
                    "全部写操作响应可达 meta.command.status"
                    if not no_cmd
                    else f"{len(no_cmd)}/{len(writes)} 个写操作响应无 command 元信息"))

    # ---- 输出 ----
    print("=" * 72)
    print("AC 契约验收")
    print("=" * 72)
    for code, ok, msg in results:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{mark}] {code}  {msg}")

    failed = [r for r in results if not r[1]]
    print("=" * 72)
    if self_test:
        if failed:
            print(f"{RED}阳性对照失败：检查逻辑有误{RESET}")
            for code, _ok, msg in failed:
                print(f"  {code}: {msg}")
            print(f"{DIM}一个只会报红的检查器没有价值——请先修检查逻辑。{RESET}")
            return 1
        print(f"{GREEN}阳性对照通过：{len(results)}/{len(results)} 项达标，"
              f"检查逻辑可在达标态变绿{RESET}")
        return 0
    if failed:
        print(f"{RED}{len(failed)}/{len(results)} 项未达标{RESET}")
        print(f"{DIM}这与 api-contract-delta.md §0 的现状取证一致——"
              f"契约债尚未偿还，本门禁用于跟踪偿还进度。{RESET}")
        return 1
    print(f"{GREEN}全部 {len(results)} 项达标{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
