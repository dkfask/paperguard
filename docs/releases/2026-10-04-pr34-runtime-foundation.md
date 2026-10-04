# PR #34 运行时基础修复记录（2026-10-04）

- 目标：把 PR #34 从“只报告 SaaS gaps”推进到可验证的最小运行时闭环；不合并、不部署、不执行生产写入。
- 当前分支：`fix/ui-review-findings-20260929`。
- 变更身份：实现提交 `baa7479`（以 `git rev-parse HEAD` 的完整 SHA 为准）；CI 检查和部署身份仍需在合并或发布前重新绑定。本文件不把本地绿灯当作上线证据。
- 实现摘要：W-01/W-03 租户成员运行时、`/api/saas/v1` 最小边界、平台运维收权与审计、AI 配置未知租户 fail-closed、AES-GCM 凭据保护、请求/幂等/reservation/unknown 最小账本、凭据隔离熔断、审计 append-only。
- 未完成：全资源 `tenant_id`/RLS 与迁移回填、租户/任务级恢复和 reconciliation、真实 provider token usage/权益账务、支付及生产 UAT。

## 验证

- `.venv/bin/pytest -q backend/tests`：本地后端回归，结果以最终提交重新执行为准。
- `.venv/bin/pytest -q backend/tests/test_saas_runtime.py backend/tests/test_secret_store.py backend/tests/test_ai_metering_coverage.py`：SaaS/密钥/计量聚焦回归。
- `git diff --check`：提交前执行。
- `DESIGN_DOC_CHECK_DEPTH=2 python3 docs/tenancy/tools/run_all_gates.py --mode report --json`：设计门禁；当前结果仍受既有设计门禁文档漂移/工具契约影响；本次实际结果为退出 2（见终端输出），不得将门禁未通过解释为 SaaS 实现完成。
