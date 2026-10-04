# SaaS 运行时基础实施状态（2026-10-04）

> 绑定：PR #34，分支 `fix/ui-review-findings-20260929`。本文是实现状态与未决项，不是上线批准。

## 已落地（本提交范围）

- **W-01/W-03 最小运行时边界**：新增 `tenants`、`tenant_memberships` 存储，登录请求从服务端成员关系解析 `tenant_id`、`membership_id`、`capabilities`、`auth_version`，提供只读/受保护的 `/api/saas/v1/context`、租户列表和成员管理接口。
- **平台运维授权**：`/api/operations/retention` 与 `/api/operations/backup` 仅平台管理员可执行；保留策略变更和备份生成写入审计。
- **AI 配置边界**：显式未知租户不再借用平台 AI 配置；系统配置仍支持存量 `school_id` 兼容映射，管理员接口只返回所请求授权范围的配置投影。
- **密钥保护**：新写入使用 AES-GCM AEAD；缺少 `PAPERGUARD_SECRET_KEY`、明文存量凭据或认证标签失败时 fail-closed，不回退其他供应商。依赖和测试契约已同步。
- **AI 请求账本最小闭环**：新增请求、幂等键、配额 reservation、dispatching、completed、failed_final、unknown 状态，记录输入/输出 token 估算值；相同幂等键只重放已完成结果，unknown 不自动退款或重试。
- **熔断隔离**：熔断键包含 provider/base URL/model 与 API Key 的不可逆摘要，BYOK 凭据不会共享同一熔断状态。
- **审计追加写**：统一审计表改为严格 `INSERT`；重复事件 ID 失败，不允许覆盖既有事件。重复导出尝试改用尝试序号生成唯一审计事件。

## 明确未完成 / 未验证

- 现有业务表尚未全部完成 `tenant_id` 强制列、数据库 RLS、文件/附件租户隔离和跨租户矩阵；W-02 全量回填与 PostgreSQL 隔离演练仍未完成。
- 当前保留整体 SQLite/data 备份恢复；租户级/任务级恢复、tombstone、撤权重放、密钥同代校验和恢复后对账尚未形成生产可执行闭环。
- AI 请求账本目前提供最小状态记录和幂等重放，尚未完成供应商真实 token usage 统一采集、reservation 账务结算、unknown reconciliation worker、预算/权益/价格账本。
- 支付、订阅、退款、发票、真实扣款和 C1/C2 商业放行均未实现；`ai_usage` 仍是运行治理事实，不是财务账本。
- 未执行生产迁移、部署、真实供应商调用、支付写入或 UAT；CI/本地通过不等于 SaaS 已上线。

## 验证入口

```bash
.venv/bin/pytest -q backend/tests
.venv/bin/pytest -q backend/tests/test_saas_runtime.py backend/tests/test_secret_store.py backend/tests/test_ai_metering_coverage.py
python3 docs/tenancy/tools/run_all_gates.py --help
```

截至本文生成，验证以本地工作区实际执行结果为准；提交 SHA、CI 和部署身份必须在合并或发布前重新绑定。
