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
- 严格门禁：`43 total / 34 passed / 9 gaps / 0 broken`；GAP 保持真实未决项，未修改门禁逻辑。
- `git diff --check`：通过。

## 未完成与边界

- 本阶段只验证 SQLite 和 mock provider，不代表 PostgreSQL/RLS、真实供应商幂等、真实收费、货币账务、最终 reconciliation worker、生产迁移、部署、合并或 UAT。
- `billable=false` 仍是本地事实标记，不是财务账本。
- 未授权网络重试、force push、生产写入或真实付费供应商调用。
