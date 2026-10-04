# PR #34：可信上下文与 AI 元数据待复核切片（2026-10-04）

## 身份与边界

- 仓库：`/Users/a1234/Documents/perpetual/paperguard`；原分支 `fix/ui-review-findings-20260929`。
- 来源代码/测试/工具提交：`d87679896285a5b61302a98181f03621adffb38d`；父提交 `abf408702b5aa388cf3e3c6ff663be4d94f7163f`。
- 本文随文档交付提交发布；其 Git 树中的 backend/app、backend/tests、tenancy/tools 与来源提交相同，最终 head/远端一致性由交付终端另验。
- 结论：**局部实现/离线回归通过；全量严格门禁 FAIL，SaaS 放行仍不具备条件**。不是合并、部署、生产迁移、支付或真实 UAT 批准。

## 完成的最小闭环

1. **可信上下文**：member 的 tenant/member/正版本不可缺；platform 不带租户/成员；unresolved 没有任何身份或能力；集合做不可变快照。support 暂全量拒绝，未伪造有效支持授权。SaaS 的异步可信入口按 S0→S4、E1～E7 拒绝，选择头必须匹配当前服务端成员记录。旧业务 user-dict 授权仍独立存在，组织授权和 draft 首位所有者例外未实现。
2. **局部契约**：8 个 SaaS 操作用 bearerAuth、严格输入和类型化信封；3 个写操作有 command，错误携带 request_id 且不回显输入/内部异常。选择头和现有幂等头通过 CORS。该命名空间线协议变化不等于全站 API 包裹；当前仓库未发现前端消费者，不替外部消费者背书。
3. **AI 请求范围**：新 tenant/member 来自真实成员，不用 school_id 冒充；规范化指纹覆盖租户、成员、操作、参数和配置摘要。读取 completed 正文前验证作用域/指纹；跨租户同键冲突拒绝。唯一键仍为用户维度，未宣称完整租户域幂等。
4. **未知结果元数据恢复**：派发异常或供应商成功后记录失败保留 unknown。限定租户的元数据列表/直接 ID 计划不返回正文；普通成员只看自己的成员请求。ai:manage 可原子标记 unknown→reconciliation_required，与追加审计同事务，重放不新增审计；写时校验成员版本、账号与租户状态。审计故障回滚、两线程只写一次、请求中途撤权拒绝均有测试。
5. **真实工具修补**：上下文门禁检查 CI-5 与行为变异；资源扫描识别 Annotated 依赖；AC-03 逐操作检查错误契约，不能让未覆盖操作借用少量局部 ErrorBody 而变绿。规格的被改动语义和代码引用已同步，未改变真实失败项的 gate 声明。

**明确限制**：该恢复计划只针对 AI 请求元数据，不还原租户/任务/文件，不重新派发、不退款、不最终结算。token 仍是字符估算；供应商可信 usage、原子预留结算、最终对账与断电后 dispatching 的自动修复均未完成。

## 本地证据

| 检查 | 结果 | 证据边界 |
|---|---|---|
| `.venv/bin/python -m pytest -q backend/tests` | **900 passed** | 来源提交的后端回归；2 项测试依赖弃用提示 |
| 五个上下文/恢复/账本/授权/SaaS 文件 | **95 passed** | 新增三文件 42 项；其余为原授权与运行时用例 |
| `npm --prefix frontend test` | **84 passed / 20 files** | 单元/组件测试，不是浏览器 UAT |
| `npm --prefix frontend run typecheck` | **通过，exit 0** | 前端类型检查 |
| 注册/错误码/规格引用/规则 ID | **通过** | 127 个公开 Store 方法、222/132 路由/写操作、13 份规格 445 规则；不代表全部业务语义获验收 |
| 默认 `--mode strict --json` | **37/42 PASS，5 GAP，0 BROKEN，exit 1** | 没有改用 ci 模式或错误 brief，也未人为跳过递归检查 |
| 秘密扫描 / 差异检查 | **554 个文件通过 / git diff --check 通过** | 包含本记录与机器摘要，不代表完整安全审计 |
| 输入冻结 | **306 项 SHA-256 无漂移；input_drift=[]** | 源码、测试、工具、设计与登记输入；本文及精简交付 JSON 在运行结束后生成 |

测试 DB/模板目录由 `backend/tests/conftest.py` 指向独立临时目录；新增 fixture 使用 tmp_path。真实本地迁移库仅做 dry-run/只读取证，未制造真实样本或清除隔离行。供应商动作均为隔离替身，未发生真实外部调用。

完整 strict 机器摘要见 [2026-10-04-pr34-context-recovery-gates.json](./2026-10-04-pr34-context-recovery-gates.json)。原始运行日志与冻结文件位于 `/tmp/paperguard-pr34-context/`；早期运行保留为 superseded 取证，不作为本记录的最终报告。新生成的本记录及机器摘要已通过交付秘密扫描和差异检查；最终 Git 推送身份由终端回执确认。

## 保留的五项真实 GAP

| 门禁 | 当前缺口 |
|---|---|
| contract_readiness | 22 条隔离行、templates/requirement_sets 归属未裁决、workflow_events 零样本，四项准入未满足；不得执行 contract/RLS 切换 |
| context_resolution | 125 处角色读取候选仍在；该静态扫描包含成员角色读取，不能等同于 125 个已证实全局越权点；旧业务角色迁移、组织/支持/首位所有者协议仍缺失 |
| api_contract | 5/9 契约缺口；206 操作无安全/匿名声明、215 操作无业务错误、10 目标实体无 tenant_id、9 组织上下文宽泛、129/132 写操作无 command |
| synthetic_data | 角色/支持授权等数据结构缺失，完整 AT 前置数据无法准备；不得用局部 fixture 宣布 45 条 AT 全过 |
| account_path | 教师导入、个人注册、微信建号等三路径缺可确认组织归属；不猜映射、不在认证读取里自动建租户/成员 |

下一阶段先裁决真实归属与隔离清单，按业务对象推进强制 tenant/RLS 与旧角色退出；可信 usage/原子结算/最终对账、真正租户/任务恢复单独开环验证。支付与 C1/C2 放行继续阻断。

## 外部证据

本轮不重试原生 CI 登录、不用 CLI 绕过 CI 诊断。最新远端 CI/独立 review **未读取**；没有执行合并、部署、生产迁移/恢复/支付或真实 UAT。Git 提交/推送不提升为上述任何状态。
