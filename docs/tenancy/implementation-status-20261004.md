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

## 授权基础加固（上一阶段）

- 认证读取只解析已有成员；不自动从 `school_id` 创建租户/成员，也不按最近更新时间猜选多租户身份。显式无效租户返回 403。
- `capabilities=[]` 是明确拒绝；租户 `admin` 能力与平台运维能力分开；成员 API 拒绝未知角色与平台能力。新成员默认空能力，管理员需显式授予租户能力。
- 成员 ID 在更新中保持稳定，角色/能力/状态变更原子递增 `auth_version`；重复保存同一授权状态不递增。尚无完整授权缓存或角色多表协议。
- `current_user` 改为异步依赖，数据库解析通过线程池执行，授权上下文在请求异步任务中设置；同步与异步端点回归均可读取同一身份，匿名后续请求不继承它。
- 租户停用和成员撤权阻断租户作用域；租户列表尊重活动账号与显式读取能力。
- BYOK 读取前验证租户/成员；撤权、停用、多成员未选定均不得省略租户信息绕回存量 AI 来源路径。`tenant-legacy-*` 不再享有前缀绕过；仅通过真实租户/活动成员验证后，才按精确匹配保留旧 `school_id` 配置键别名，避免存量用户意外改道平台供应商。认证依赖对 `/api/ai/` 全部配置/测试/调用入口前置拒绝无有效选择的已登记成员身份，防止不经 `_resolve_cfg` 的端点绕过。
- 上一阶段原生 CI 连接要求登录 ChatGPT；用户随后已重新认证，但最新 head 的远端检查状态仍须原生连接确认；不使用 CLI 绕过该诊断连接。

## 可信上下文与 AI 元数据恢复切片（上一阶段，已推送）

来源提交：`d87679896285a5b61302a98181f03621adffb38d`；PR #34；文档交付提交可用 Git 树核对同一来源。

- **上下文**：中性 `unresolved` 不含 actor/role/tenant/member/capability/scope；member 必须具备真实三元身份与正版本；platform 不带租户或成员。集合做不可变快照，旧 worker 的 system 字段只作为日志元数据，不冒充授权身份。CI-3 support 暂全量拒绝，而非信任任意 grant ID。
- **可信入口**：8 个 SaaS 操作使用异步 `trusted_context`；S0→S4、E1～E7 分开拒绝；选择头只定位服务端成员；不自动建成员或猜多租户。成员组织范围暂为空，draft 首位所有者启用路径在该来源提交尚未实现，旧 current_user/user-dict 路由仍独立存在。
- **线协议**：该 SaaS 切片成功响应 `data/meta`，失败响应 `error.code/message/request_id`，三个写操作提供 command；严格 body 模型拒绝伪造身份字段且不回显校验输入；OpenAPI 声明 bearerAuth，CORS 允许选择头。不改变旧前端 API；无消费者的判断来自当前仓库搜索，不替外部集成背书。
- **请求隔离**：新 AI 请求的 tenant/member 只来自已验证成员；旧 school_id 不假充租户。规范化指纹绑定请求参数、来源和配置摘要；重放先核对 tenant/member/fingerprint。唯一键仍是 `(user_id,idempotency_key)`，跨租户同键是冲突拒绝，不是完整租户域幂等。
- **未知结果小闭环**：任意派发异常、供应商成功后本地记录失败进入 unknown；限定租户查询/直接 ID 恢复计划不返回正文；普通成员仅看自己的成员请求，ai:read/manage 可看本租户元数据。ai:manage 的 unknown→reconciliation_required CAS 与审计同事务，重复调用不重复写审计，写时复核版本/账号/租户状态。它**不重发、不退款、不最终结算**，不等于供应商最终结果对账。
- **工具真实修补**：上下文门禁增加 CI-5 与行为变异；资源扫描支持 Annotated 依赖，不能把可信接口错标公开；AC-03 按操作检查错误契约覆盖。保留剩余角色引用与全站契约失败，不改 gate 标记以强行放绿。

## 明确未完成 / 未验证

- 只有服务端选择器，尚未提供完整租户选择 UI/流程、成员状态变更 API 与历史 `school_id` 的授权映射/回填。`ensure_legacy_tenant_membership` 只保留显式兼容辅助用途，认证路径不调用它；不能把辅助方法当作已批准迁移。
- 现有业务表尚未全部完成 `tenant_id` 强制列、数据库 RLS、文件/附件租户隔离和跨租户矩阵；W-02 全量回填与 PostgreSQL 隔离演练仍未完成。
- 当前保留整体 SQLite/data 备份恢复；租户级/任务级恢复、tombstone、撤权重放、密钥同代校验和恢复后对账尚未形成生产可执行闭环。
- AI 请求账本目前提供最小状态记录和幂等重放，尚未完成供应商真实 token usage 统一采集、reservation 账务结算、unknown reconciliation worker、预算/权益/价格账本。
- 支付、订阅、退款、发票、真实扣款和 C1/C2 商业放行均未实现；`ai_usage` 仍是运行治理事实，不是财务账本。
- 未执行生产迁移、部署、真实供应商调用、支付写入或 UAT；CI/本地通过不等于 SaaS 已上线。

## 上一阶段离线验证

来源 `d876798`：后端 **900 passed**；上下文/AI 恢复/账本与既有授权/SaaS 的五个相关测试文件 **95 passed**；前端 **84 passed / 20 files**，typecheck 通过。CI/review 未读取，不替代部署或真实 UAT。最终 strict 与交付证据见发布记录。

## 验证入口

```bash
.venv/bin/pytest -q backend/tests
.venv/bin/pytest -q backend/tests/test_saas_runtime.py backend/tests/test_secret_store.py backend/tests/test_ai_metering_coverage.py
.venv/bin/python docs/tenancy/tools/run_all_gates.py --mode strict --json
```

截至本文生成，验证以本地工作区实际执行结果为准；提交 SHA、CI 和部署身份必须在合并或发布前重新绑定。

## 历史归属准入、显式建户与写作隔离切片（上一阶段本地完成）

来源提交：`1154b943ed2801c82d649e4b365fafb259f85e3e`；前阶段 `d876798` 与文档提交 `a754b818` 已推送，不重复认证修复。新阶段是受限本地实现，不是完整 SaaS 验收。

### A. 只读历史归属预检与拒绝式准入

- `ownership_preflight.py` 不构造 Store、不执行源库 DDL/DML；immutable只读源，非空WAL/journal要求先取得一致离线快照。输出计数/原因/不可逆资源引用，不输出账号、邮件、凭据或正文。
- 显式 `CONFIRMED` 映射与批准引用不可缺；未知归属、组织冲突、身份冲突、未批准旧admin与指导关系规则分别报告。多租户成员属于同一全局账号不是合并/身份冲突依据；无选择器的多租户owner资源仍拒绝。
- 报告绑定源与sidecar、schema版本/摘要、映射、规则/依赖代码、HEAD。复验重算完整报告；改GO字段或digest不构成准入。CLI回填apply先验证再创建目标。
- `GO_REHEARSAL` 只准本地离线演练，`production_authorized:false` 恒定；不是生产迁移/切换授权。
- 本地真实源仍为 **BLOCKED**：资源缺失/冲突30、映射结构1/批准1、账号归属12、旧admin批准2、指导规则批准1；DB及sidecar哈希/mtime/size与阶段前一致。不可与旧回填quarantine22混算。

### B. 全局账号、成员与平台身份分离

- 注册/小程序注册/教师导入模型拒绝客户端租户、成员与能力等额外grant字段；正常全局建号不猜归属，不建成员，不授平台。
- SQLite `0013_saas_approval_pilot`新增平台绑定与建户批准记录。平台授权只由显式离线审批函数建立；login/seed/register/import/HTTP均不调用该函数。仅在临时夹具调用，未批准任何真实账号。
- 平台批准下建draft须负责人、有效学校根、退出联系人、未来试点期限、禁用出站、批准引用和幂等键；精确重放，冲突拒绝，不自动active。
- 成员create-or-replay不是授权upsert；撤权/停用成员不复活，不自动换角色。首位owner限定已批准draft、已验证并初始化账号、期望版本，不隐式升已有成员；owner只获租户能力，不获平台权限。
- 启用入口根据七项服务端事实给出安全checks，但完整准入、组织、模板、权益、配额和AI政策未完成，**仍拒绝启用**。
- 备份/保留策略改用可信平台身份并复查当前批准；保留策略与审计同事务，备份生成后撤权则丢弃文件。旧全局admin/租户admin不能借此获得平台操作。旧审计投影不返回SaaS/平台运维记录。

### C. 一条真实tenant/member写作链

- `/api/saas/v1/projects`真实执行手写空白项目create/list/get/CAS update；tenant/member/owner只来自可信身份，当前账号/成员/租户/能力事务复查，审计失败回滚。
- 列表与直接ID只限自身membership；跨租户/其他成员项目不可见。图片/表格/公式及伪装成paragraph的文件/导入字段同样显式拒绝，当前只支持手写文本；这不等于全站历史静态文件隔离。尚未扩展租户管理员/教师查看他人项目、模板、组织授权或文件访问。
- 旧Store隐藏并拒写此类项目；已登记（含撤权）成员不能回到旧业务入口，shared模式也拒绝未映射旧用户；standalone旧数据兼容路径保留。
- 同租户导出/异步入口明确409，跨租户先404；不创建文件/任务/调用供应商。旧导出service与持久化worker快照也拒绝，绝非假定“之后再隔离”。

### 本轮证据与仍待验证

后端 **987 passed**（其中新预检24、pilot63），前端 **84 passed / 20 files**，typecheck通过；依赖弃用警告2条。源码/测试/工具摘要保存在本地证据目录，提交前后断言一致。严格门禁最终结果见本轮发布记录及JSON，不把ci模式0解释为严格放行。

尚未完成：生产共享DB选型/PG+RLS、全资源/文件/cache/async隔离、完整成员导入邀请与组织授权、owner启用、可信权益/席位/计量/账务、支持与租户恢复。业务裁决见 [待裁决包](conditional-tenancy-decision-packet.md)。未执行合并、部署、生产DDL/DML/恢复、付费供应商或支付调用、真实UAT；原生CI另列状态：本轮原生诊断单次读取仍返回“Sign in to Codex with ChatGPT to connect GitHub”，未继续重试登录、未用CLI绕过。

## 2026-10-05 离线续作：可信usage/mock计量

来源：`6cf37f902b6ff453611c505c17d1a3b929e3cc29`，基线`dfb91fd5c05d93601f0adc2f20c309d17ce86794`。

- 按授权只尝试一次普通非强制push，返回exit128 / Empty reply from server；立即停止网络重试，不更改永久代理/凭据。最新远端分支与PR ref未读回，不宣称推送成功；本轮未在push未确认时读取CI。此前原生CI登录阻塞依然只是此前证据，不猜其现状。
- OpenAI/Anthropic非流式usage保守解析与文本兼容返回，真实transport全部由MockTransport替身；缺失/坏usage为null+unavailable，不把估算/字符改为provider事实，不复制供应商原始payload。
- request scoped `ai_usage_fact`旁表严格INSERT、completed与审计原子提交；核验当前账号/租户/成员版本，保存失败进入unknown，拒绝重发/退款。metadata API展示独立provider token、质量与复核标记，billable恒false。
- 成功无usage仍是completed，但需要计量复核；此标记不是实际复核队列/供应商对账。旧记录保持legacy_chars/估算；新事实属于SQLite本地试点，不是PG/RLS/货币账务。
- 后端1023 passed（新增mock36）；相关6文件96 passed；前端84/20 files及typecheck通过。默认严格门禁/最终本地提交绑定详见 [本轮交付记录](../releases/2026-10-05-pr34-usage-offline.md)。前阶段987用例与38/43是前阶段证据，不冒充本轮strict。

尚未实现：租户域幂等（仍user+key全局唯一，跨tenant同key拒绝）、原子额度reservation/真正账务结算、unknown供应商最终结果核对/校正、真实usage账单对照、流式协议、全资源RLS/文件/任务恢复。保持不合并/不部署/不生产DDL/DML/恢复、不真实支付或付费供应商、不真实UAT。
