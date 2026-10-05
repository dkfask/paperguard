# PR #34：AI tenant/member 幂等离线切片

日期：2026-10-04
分支：`fix/ui-review-findings-20260929`
范围：W-08 的 SQLite/local mock 切片

## 已完成

- 新增 `0014_ai_tenant_idempotency`，将新 AI 请求的唯一域改为：
  `idempotency_domain + idempotency_scope + action + idempotency_key`。
- `tenant` 域使用 `tenant_id + membership_id`；`platform` 域依赖显式平台绑定；未映射兼容路径使用 `legacy`。
- 旧 `(user_id, key)` 记录保留为 `historical_legacy`，不猜测回填 tenant/member，也不阻塞新的 tenant 域请求。
- `Store.register_ai_request()` 使用 `BEGIN IMMEDIATE` 和部分唯一索引，保证同域同操作同 key 的并发请求只有一个持久请求和一次派发。
- 重放前重新读取账号、租户、成员、能力和 `auth_version`；撤权、停用或版本变化返回 `AUTHORIZATION_STALE`。
- `unknown` / `reconciliation_required` 返回 `AI_RECONCILIATION_REQUIRED`，不自动重发、退款或最终结算。
- 新增 `backend/tests/test_ai_idempotency.py`，覆盖跨租户同 key、载荷冲突、操作隔离、历史记录隔离、授权版本变化、并发和显式平台绑定。

## 本地证据

- 幂等专项：`8 passed`。
- 后端全量：`1031 passed, 2 warnings`。
- 前端：`84 passed / 20 files`，typecheck 通过。
- 严格门禁（初始实跑）：`43 total / 34 passed / 9 gaps / 0 broken`；其中 4 个新增/变化结果经增量归因确认是文档/门禁输入漂移，不是幂等代码回归。文档事实回绑后重新实跑为 `43 total / 38 passed / 5 gaps / 0 broken`，严格退出码仍为 `1`，未把 GAP 改绿。
- `git diff --check`：通过。

## 严格门禁增量归因

历史权威机器报告为 `docs/releases/2026-10-04-pr34-context-recovery-gates.json`：来源 `d87679896285a5b61302a98181f03621adffb38d`，`42 total / 37 passed / 5 gaps / 0 broken`。当前幂等切片初始运行在 `a2b828f772c0eb41fa759bf80545f931cd4426f6` 上为 `43 total / 34 passed / 9 gaps / 0 broken`。

- `ownership_preflight.py::self-test` 是门禁集合新增项，解释总数从 42 增至 43；本项自检通过。
- `spec_citation_check.py::plain`：W-02/W-06/W-07/W-08/W-09/W-10/W-12 的源码行号随既有实现移动而失效；已按当前符号位置回绑，工具恢复通过。分类：`CONTRACT-DRIFT`，不是产品行为回归。
- `schema_disposition_check.py::plain`：当前 SQLite 运行时已有 5 张受限 pilot 表未列入 W-02 §4；已补充显式 `迁移` 判定并与资源登记对齐。分类：`DATA-PRECONDITION` / 设计输入漂移，不是本轮幂等逻辑回归。
- `design_doc_check.py::plain` 与 `::self-test`：收口简报中的规格行数、tenancy 文档行数和自检状态已过期；更新事实标记后恢复 `48/48` 自检与 plain 通过。分类：`TEST-DEFECT`（过期测试输入），不是源码回归。
- 原五项 GAP 未变化：`contract_readiness_check.py::plain`、`context_resolution_check.py::plain`、`api_contract_check.py::plain`、`synthetic_data.py::plain`、`account_path_check.py::plain`。它们继续按设计保持未决，不能作为 SaaS/RLS/账务已完成证据。

增量机器报告：`docs/releases/2026-10-04-pr34-idempotency-gates.json`。初始与修复后命令均为：

```bash
env -u DESIGN_DOC_CHECK_DEPTH .venv/bin/python docs/tenancy/tools/run_all_gates.py --mode strict --json
```

## 未完成与边界

- 本阶段只验证 SQLite 和 mock provider，不代表 PostgreSQL/RLS、真实供应商幂等、真实收费、货币账务、最终 reconciliation worker、生产迁移、部署、合并或 UAT。
- `billable=false` 仍是本地事实标记，不是财务账本。
- 未授权网络重试、force push、生产写入或真实付费供应商调用。
