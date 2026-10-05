# PR #34：W-08/C1 前置非付费 quota 原子预留切片（2026-10-05）

分支：`fix/ui-review-findings-20260929`。本地离线实现与测试记录；不代表合并、部署、生产迁移、真实收费或供应商 UAT。

## 实现

- 请求登记、tenant/member/platform/legacy 幂等域和非货币 quota 双窗口预留统一进入 `Store.register_ai_request_with_reservation()` 的 `BEGIN IMMEDIATE` 事务。
- `ai_quota_reservation` 复用 `workflow_records`，保存 `request_id`、可信作用域、窗口、quota 限制/来源/配置键、`unit=request`、`quantity=1`、状态版本与 `billable=false`。
- quota 拒绝记录为 `failed_final/released` 的前置事实；重复幂等请求不再次扣 quota；同 key 不同 payload 不额外占用。
- 派发和完成状态更新与 reservation sidecar 对齐；只有证明未派发才可按 reservation ID 释放一次。unknown 保持冻结，不自动释放、退款或重发；usage fact 保存失败时 request 进入 unknown。
- 审计写入失败会回滚 request、reservation 和两窗口 quota counter。

## 验证边界

- 专项测试：`backend/tests/test_ai_quota_reservations.py`：**6 passed**。
- 相关 AI 回归：**52 passed**；后端全量：**1037 passed, 2 warnings**；前端：**84 passed / 20 files**；typecheck 通过；secret scan：**568 files passed**；`git diff --check` 与 whitespace guard 通过。strict gate 单独记录，不能互相替代。
- strict gate 继续保留原五项真实 GAP；不把 `billable=false`、本地测试或 mock provider 当作收费/账务/生产证据。

## 未放行

未执行网络重试、force push、合并、部署、生产 DDL/DML/恢复、真实支付、真实付费供应商调用或严格 UAT。货币账务、真实租户权益配置、PG/RLS、供应商最终结果 reconciliation 和故障注入仍待后续阶段。
