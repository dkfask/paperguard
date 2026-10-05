# 错误码字典

> 状态：**as_is_inventory + target_registry**。本文登记**代码中真实存在**的错误码，
> 并给出目标契约要求但**尚未实现**的码位。**不是**一份"应该有"的理想清单。
> 日期：2026-10-04。运行时基线：`4bcedb7` 加本轮授权加固；其余历史统计保留原口径，非全量重审。
> 配套：`api-contract-delta.md` §3.3（统一错误信封）、`at-traceability-matrix.yaml`（AT-29 依赖本文）。

---

## 0. 结论摘要

代码中**并存两套互不相同的错误码机制**，且**没有任何统一登记表**：

> ⚠️ **计数口径**：`backend/app/api/` 顶层与 `backend/app/` 全量（含 services/domain）的
> 分布差别很大。下表**全量口径为 `backend/app/`**，并在括号中给出 `api/` 顶层的值。
> 混用两个口径会得出互相矛盾的结论（本文件撰写时确实发生过，见 §7）。

| 体系 | 唯一码数 | 抛出点数（`app/` 全量） | 出现位置 | 响应形态 |
|---|---|---|---|---|
| **A. HTTP 异常码** | **52** | 85（2026-10-04 全量重新扫描） | `HTTPException(detail={"code","message"})` | 4xx/5xx 响应体 |
| **B. 异步任务码** | **9** | — | 任务/归档/渲染记录的 `error_code` 列 | **200 成功响应体**内 |
| **C. 无码裸字符串** | — | **404**（`api/` 顶层 367） | `HTTPException(detail="...")` | 4xx/5xx 响应体，只有中文文案 |
| （其他形式） | — | 103（`api/` 顶层 95） | 变量 / 多行 / 无 detail | — |

**三个结构性问题：**

1. **B 体系不在错误信封里。** 异步任务的失败码出现在 `GET` 成功的 200 响应中，
   客户端必须**在两个不同位置、用两种不同形态**处理错误。这是 `api-contract-delta.md` G-3
   没有覆盖到的第四种形态。
2. **C 体系占绝对多数。** 404 处裸字符串，客户端无法程序化判断，只能匹配中文文案——
   而中文文案会随 i18n 变化，**改文案就改行为**。
3. **无登记表。** 本文件是第一次把 40 个码集中登记。此前没有任何一处能回答
   "一共有哪些错误码""某个码的前端该显示什么"。

---

## 1. 命名规范

### 1.1 格式

```text
<域>_<原因>          全大写，下划线分隔
```

| 域 | 含义 | 已有的域 |
|---|---|---|
| `AUTH_` | 认证与凭据 | （当前用 `INVALID_CREDENTIALS`，**未加域前缀**） |
| `EMAIL_` | 邮件验证 | ✅ |
| `MP_` | 微信小程序 | ✅ |
| `ONBOARDING_` | 首次引导 | ✅ |
| `AI_` | AI 调用与治理 | ✅ |
| `TENANT_` | 租户（目标） | 待建 |
| `JOB_` | 异步任务（目标） | 待建 |

### 1.2 稳定性

| 规则 | 说明 |
|---|---|
| **一旦发布即不可改语义** | 前端可能已按 `code` 分支 |
| 废弃用**保留码 + 换文案** | 不删除、不改写，让旧客户端继续工作 |
| **不得**把内部异常、SQL、路径放进 `message` | AT-29 要求 |
| 一个码对应**一种**可恢复动作 | 若客户端对策不同，就是两个码 |

> **反例警示**：`EMAIL_CODE_INVALID` 与 `EMAIL_CODE_MISMATCH` 现在都是 400，
> 但前者是"验证码不存在/格式错"，后者是"与本次发送的不一致"——
> 客户端对策不同（前者提示重发，后者提示重输）。若合并成一个码，
> 客户端只能二选一，对其中一个场景就是错的。

---

## 2. 体系 A：HTTP 异常码（52 个，已实现）

全部来自 `HTTPException(detail={"code", "message"})`。

### 2.1 认证与会话

| Code | HTTP | 位置 | 触发 | 客户端对策 |
|---|---|---|---|---|
| `INVALID_CREDENTIALS` | 401 | `auth.py:422` | 用户名或密码错 | 提示重输，**不区分哪一项错** |
| `INVALID_PASSWORD` | 400 | `auth.py:544` | 改密时新密码不合规 | 提示策略要求 |
| `FIRST_LOGIN_STUDENT_REQUIRED` | 403 | `auth.py:194` | 学生首次登录须先改密 | 跳转到改密页 |
| `ONBOARDING_REQUIRED` | 403 | `auth.py:630` | 须完成引导 | 跳转引导流程 |
| `ONBOARDING_NOT_REQUIRED` | 409 | `auth.py:198` | 已完成却重复调用 | 幂等语义，可直接返回成功 |
| `ACCOUNT_UNAVAILABLE` | 403 | `auth.py:406` | 账号停用/未激活 | 联系管理员 |

### 2.2 邮件验证

| Code | HTTP | 位置 | 触发 | 客户端对策 |
|---|---|---|---|---|
| `EMAIL_INVALID` | 422 | `auth.py:178` | 邮箱格式错 | 输入框标红 |
| `EMAIL_TAKEN` | 409 | `auth.py:180` | 邮箱已被占用 | 提示换邮箱 |
| `EMAIL_ALREADY_BOUND` | 409 | `auth.py:500` | 已绑定过 | 提示先解绑 |
| `EMAIL_CODE_REQUIRED` | 422 | `auth.py:126` | 未提供验证码 | 弹出输入 |
| `EMAIL_CODE_INVALID` | 400 | `auth.py:279` | 验证码格式/不存在 | 重新发送 |
| `EMAIL_CODE_MISMATCH` | 400 | `auth.py:286` | 与本次发送不符 | 重新输入 |
| `EMAIL_CODE_EXPIRED` | 400 | `auth.py:281` | 超时 | 重新发送 |
| `EMAIL_CODE_COOLDOWN` | 429 | `auth.py:259` | 发送过于频繁 | 倒计时等待 |
| `EMAIL_CODE_LOCKED` | 429 | `auth.py:283` | 错误次数过多被锁 | 提示锁定时长 |
| `EMAIL_PURPOSE_INVALID` | 422 | `auth.py:251` | 用途参数非法 | 程序缺陷 |
| `EMAIL_SEND_FAILED` | 502 | `auth.py:269` | 发送失败 | 可重试 |
| `EMAIL_NOT_CONFIGURED` | 503 | `auth.py:122` | 服务端未配 SMTP | **运维问题**，不重试 |

### 2.3 微信小程序

| Code | HTTP | 位置 | 触发 | 客户端对策 |
|---|---|---|---|---|
| `MP_CODE_REQUIRED` | 422 | `api/auth.py:171` | 缺微信 code | 重新 wx.login |
| `MP_LOGIN_FAILED` | 401 | `auth.py:381` | 换取 session 失败 | 重新登录 |
| `MP_NOT_CONFIGURED` | 503 | `auth.py:317` | 服务端未配小程序 | **运维问题** |
| `MP_PROVIDER_UNAVAILABLE` | 502 | `auth.py:376` | 微信侧不可达 | 可重试 |
| `MP_SESSION_REQUIRED` | 403 | `auth.py:494` | 无小程序会话 | 重新登录 |
| `MP_BIND_TICKET_REQUIRED` | 422 | `api/auth.py:180` | 缺绑定票据 | 重新发起绑定 |
| `MP_BIND_TICKET_INVALID` | 400 | `auth.py:416` | 票据无效/过期 | 重新发起绑定 |
| `MP_BINDING_CONFLICT` | 409 | `auth.py:441` | 绑定冲突 | 提示当前绑定状态 |
| `MP_TEMPLATE_REQUIRED` | 422 | `api/auth.py:268` | 缺模板 ID | 程序缺陷 |

### 2.4 AI 治理

| Code | HTTP | 位置 | 触发 | 客户端对策 |
|---|---|---|---|---|
| `AI_RATE_LIMITED` | 429 | `ai_governance.py:88` | 超出每分钟突发 | 倒计时后重试 |
| `AI_DAILY_QUOTA_EXCEEDED` | 429 | `ai_governance.py:99` | 超出日配额 | **当日不可重试**，提示明日 |
| `AI_RESERVATION_INVALID` | 409 | `ai_governance.py:136` | 持久化 quota 预留不存在或已失效 | **不可重发**，读取请求状态并人工处理 |
| `AI_UPSTREAM_CIRCUIT_OPEN` | 503 | `api/ai.py:345` | 熔断打开 | 稍后重试，勿立即重试 |
| `CONTENT_SECURITY_UNAVAILABLE` | 503 | `wechat_content_security.py:74` | 内容安全服务不可用 | **不自动重试** |

### 2.5 体系 A 的一致性问题

| 问题 | 详情 |
|---|---|
| **域前缀不全** | `INVALID_CREDENTIALS`、`INVALID_PASSWORD`、`ACCOUNT_UNAVAILABLE`、`FIRST_LOGIN_STUDENT_REQUIRED` 缺 `AUTH_` 前缀 |
| **状态码不统一** | `MP_PROVIDER_UNAVAILABLE` 用 502，`MP_NOT_CONFIGURED` 用 503——两者都是"服务端依赖不可用"，但客户端对策不同（前者重试、后者不重试），**这个区分是对的**，应写进规范而非改掉 |
| **未覆盖域** | 租户、权限、额度、任务、并发冲突**全部无码** |

---

### 2.7 运行时基础新增码（2026-10-04）

> 这些出口只证明基础路径已实现，不证明 W-03 完整上下文契约或 W-08 对账已完成。

| Code | HTTP | 位置 | 实际触发与客户端边界 |
|---|---|---|---|
| `AUTHORIZATION_STALE` | 403 | `backend/app/api/ai.py:_metered_chat` | 重放前发现账号、租户、成员、能力或 auth_version 已变化；不得返回旧结果 |
| `AI_SECRET_UNAVAILABLE` | 503 | `backend/app/api/ai.py:77` | 凭据缺失主密钥或认证解密失败；联系管理员迁移，不回退其他供应商 |
| `CAPABILITY_REQUIRED` | 403 | `backend/app/api/saas.py:29` | 活动成员能力不足或无有效成员身份；不重试越权操作 |
| `IDEMPOTENCY_KEY_REUSED` | 409 | `backend/app/api/ai.py:_metered_chat` | 同一幂等域、操作和 key 提交了不同载荷；使用新 key 或核对原请求；跨 tenant/member 域不会互相复用 |
| `PLATFORM_OPERATION_ADMIN_REQUIRED` | 403 | `backend/app/api/operations.py:63` | 平台级运维要求活动全局管理员，租户角色不授予该权限 |
| `REQUEST_IN_PROGRESS` | 409 | `backend/app/api/ai.py:_metered_chat` | 原请求已登记但尚未形成可重放的 completed 结果；并发重复不得再次派发 |
| `TENANT_CONTEXT_INVALID` | 403 | `backend/app/api/ai.py:123` | 租户未知、停用、成员无效或未选定；禁止 BYOK 和平台配置回退 |
| `TENANT_CONTEXT_MISSING` | 400 | `backend/app/services/tenant_access.py:87` | 租户作用域参数为空；需补齐经服务端验证的租户身份 |
| `TENANT_NOT_FOUND` | 404 | `backend/app/api/saas.py:102` | 租户不存在或不可为其新增成员 |
| `TENANT_SCOPE_FORBIDDEN` | 403 | `backend/app/api/saas.py:39` | 非活动成员或租户停用；不得跨租户访问 |
| `TENANT_SLUG_TAKEN` | 409 | `backend/app/api/saas.py:83` | 租户标识已占用；选择其他标识 |
| `USER_NOT_FOUND` | 404 | `backend/app/services/saas_identity.py:101` | 待添加成员的全局用户不存在 |


### 2.8 可信 SaaS 与 AI 待复核新增码（2026-10-04）

| Code | HTTP | 位置 | 实际触发与客户端边界 |
|---|---|---|---|
| `AI_RECONCILIATION_REQUIRED` | 409 | `backend/app/api/ai.py:460` | 原幂等请求结果未知或已待复核；禁止重发/退款，走人工复核 |
| `AI_RECONCILIATION_STATE_INVALID` | 409 | `backend/app/services/ai_recovery.py:47` | 仅 unknown 可进入待复核；禁止改写 completed/failed_final |
| `AI_REQUEST_NOT_FOUND` | 404 | `backend/app/services/ai_recovery.py:30` | 限定租户/成员范围内不可见；不透露跨租户存在性 |
| `AI_RESULT_UNKNOWN` | 502 | `backend/app/api/ai.py:519` | 派发异常或结果记录失败；不能据此断言供应商未执行 |
| `AUTH_ACCOUNT_BLOCKED` | 403 | `backend/app/auth.py:739` | 账号停用、需改密或未完成引导；不能继续共享 SaaS 操作 |
| `AUTH_SESSION_INVALID` | 401 | `backend/app/services/tenant_access.py:122` | 缺少或失效的会话；重新登录 |
| `TENANT_HEADER_NOT_HONORED` | 403 | `backend/app/services/tenant_access.py:155` | 租户选择器无对应成员记录；不可用头建立授权 |
| `TENANT_MEMBERSHIP_INACTIVE` | 403 | `backend/app/services/tenant_access.py:152` | 成员已撤销/停用；同会话必须拒绝 |
| `TENANT_MEMBERSHIP_REQUIRED` | 403 | `backend/app/services/tenant_access.py:146` | 没有有效或唯一选定的成员租户；不得猜选 |
| `TENANT_NOT_ACTIVE` | 403 | `backend/app/services/tenant_access.py:159` | 租户停用/缺失/未启用；首位所有者例外仍无持久化契约 |

SaaS 局部路由工厂还输出 `API_INPUT_INVALID`（422）、`API_INTERNAL_ERROR`（500）与 `API_REQUEST_REJECTED`（保留原 HTTP 状态）。这些来自类型化错误工厂，不是 `HTTPException` 常量出口，因此不计入当前 EC HTTP 异常码计数；响应不回显请求输入或内部异常。验证见 `test_ai_recovery_contract.py`。

### 2.9 归属/建号/项目试点（实际服务端出口）

| Code | HTTP | 位置 | 触发与边界 |
|---|---|---|---|
| `AUTHORIZATION_STALE` | 403 | `backend/app/services/saas_identity.py:58` | 平台批准/成员/账号/租户或能力已撤销、停用或版本变化；必须重新解析授权 |
| `IDEMPOTENCY_CONFLICT` | 409 | `backend/app/services/saas_identity.py:78` | 同一建户幂等键的执行者或规范化请求指纹不同，拒绝覆盖既有客户 |
| `IDEMPOTENCY_KEY_REQUIRED` | 422 | `backend/app/services/saas_identity.py:72` | 建户命令缺少有效幂等键，拒绝创建draft |
| `LEGACY_TENANT_PATH_DISABLED` | 403 | `backend/app/auth.py:654` | 已登记成员或shared模式访问尚未隔离的旧业务命名空间，拒绝回退 |
| `MEMBERSHIP_ACCOUNT_INELIGIBLE` | 409 | `backend/app/services/saas_identity.py:102` | 目标全局账号停用或未完成账号设置，不创建成员 |
| `MEMBERSHIP_ALREADY_EXISTS` | 409 | `backend/app/services/saas_identity.py:107` | 已有活动成员的角色/能力与创建命令不同，不能用重复建号变更授权 |
| `MEMBERSHIP_REACTIVATION_FORBIDDEN` | 409 | `backend/app/services/saas_identity.py:106` | 已有成员被撤权/停用，create-or-replay不得复活 |
| `OWNER_IDENTITY_UNVERIFIED` | 409 | `backend/app/services/saas_identity.py:123` | 首位owner须为已验证并完成初始化的活动全局账号 |
| `PROJECT_ASYNC_ISOLATION_PENDING` | 409 | `backend/app/services/tenant_projects.py:47` | 文件/导出/异步/图片/导入块尚未隔离；手写试点拒绝未实现动作 |
| `PROJECT_SCOPE_NOT_FOUND` | 404 | `backend/app/services/tenant_projects.py:22` | 当前tenant/member/owner作用域下项目不可见，跨租户不透露存在性 |
| `PROJECT_VERSION_CONFLICT` | 409 | `backend/app/services/tenant_projects.py:64` | 写作项目revision不匹配，CAS更新拒绝 |
| `TENANT_ACTIVATION_BLOCKED` | 409 | `backend/app/services/saas_identity.py:144` | 客户状态或七项服务端readiness事实不满足，仍禁止启用 |
| `TENANT_APPROVAL_INVALID` | 409 | `backend/app/services/saas_identity.py:83` | 建户负责人/有效学校根/退出联系人/期限等服务端核对不通过 |
| `TENANT_OWNER_SETUP_FORBIDDEN` | 409 | `backend/app/services/saas_identity.py:121` | 非已批准draft、已设owner或已有成员要求隐式提升，拒绝设置 |
| `TENANT_VERSION_CONFLICT` | 409 | `backend/app/services/saas_identity.py:129` | 首位owner/启用命令的租户期望版本不匹配，拒绝陈旧写入 |

## 3. 体系 B：异步任务码（9 个，已实现）

这些码**不在错误响应里**，而是任务/导出/渲染记录上的 `error_code` 字段，
通过 `GET` 任务详情的 **200 响应**返回。

| Code | 载体 | 触发 | 客户端对策 |
|---|---|---|---|
| `ARCHIVE_GENERATION_FAILED` | `archives.error_code` | 归档生成失败 | 可人工重试 |
| `PROJECT_EXPORT_FAILED` | 导出记录 `error_code` | 项目导出失败 | 可人工重试 |
| `EXPORT_RECORD_NOT_FOUND` | `services/export_jobs.py:140,143` | 导出记录不存在 | 刷新列表 |
| `EXPORT_RECORD_CONFLICT` | `services/export_jobs.py:153` | 导出记录状态冲突 | 刷新后重试 |
| `DOCUMENT_RENDER_FAILED` | 渲染记录 `error_code` | 文档渲染失败 | 可人工重试 |
| `DOCUMENT_RENDER_CANCELLED` | `api/document_projects.py:502` | 渲染被取消 | **不是失败**，展示为已取消 |
| `DOCUMENT_RENDER_JOB_NOT_FOUND` | `services/document_render_jobs.py:227` | 渲染任务不存在 | 刷新列表 |
| `DOCUMENT_RENDER_JOB_CONFLICT` | `services/document_render_jobs.py:239` | 渲染任务状态冲突 | 刷新后重试 |
| `CHECK_FAILED` | 检查报告 `error_code` | 规范检查未完成 | 提示重跑检查 |

> `DOCUMENT_RENDER_CANCELLED` 由前端消费：`Writer.vue:1872` 读 `current.error_code`，
> 缺省为 `PROJECT_EXPORT_FAILED`。这是**唯一一个前端已经在用的错误码**，
> 也证明 B 体系对用户是有价值的——问题只在于它没有进入统一信封。

### 3.1 B 体系已有的良好实践

值得保留并推广到体系 A：

| 实践 | 说明 |
|---|---|
| **`_NOT_FOUND` 与 `_CONFLICT` 分开** | 404 类与 409 类客户端对策不同，不合并 |
| **`_CANCELLED` 与 `_FAILED` 分开** | 取消不是失败，混在一起会让用户以为操作出错 |
| **码在记录上而非响应上** | 可反复查询同一任务的失败原因，不必每次重新触发 |

第三条尤其重要：**错误被持久化了**。用户刷新页面仍能看到失败原因。
体系 A 的错误则是一次性的——刷新即丢失，这正是需要错误信封 + `request_id` 的原因。

### 3.1 为什么 B 体系必须被单独处理

```jsonc
// 体系 A：错误在 4xx/5xx 里
HTTP 429
{"detail": {"code": "AI_RATE_LIMITED", "message": "..."}}

// 体系 B：错误在 200 里
HTTP 200
{"id": "exp-123", "status": "failed", "error_code": "PROJECT_EXPORT_FAILED", ...}
```

客户端若只对 `4xx` 走错误分支，**体系 B 的失败会被当成成功处理**。
必须额外检查 `status == 'failed'` 并读 `error_code`。

**目标设计**：`api-contract-delta.md` §3.4 的 `ApiEnvelope.meta.command.status` 已经预留了
`failed` 状态，体系 B 应迁移为 `meta.command.status = 'failed'` + `meta.command.code`，
这样 A 和 B 归一。

---

## 4. 体系 C：无码裸字符串（`app/` 全量 404 处，`api/` 顶层 367 处）

`HTTPException(detail="中文文案")`。客户端只能显示文案，**无法程序化分支**。

### 4.1 抽样（按域）

| 域 | 样例 | 应归入 |
|---|---|---|
| AI 上游 | `"AI 服务响应超时, 请稍后重试"` | `AI_UPSTREAM_TIMEOUT` |
| AI 上游 | `"无法连接 AI 服务, 请检查 Base URL"` | `AI_UPSTREAM_UNREACHABLE` |
| AI 上游 | `"AI 服务返回了无法解析的内容"` | `AI_UPSTREAM_UNPARSEABLE` |
| 资源 | `"模板不存在"` | `TEMPLATE_NOT_FOUND` |
| 资源 | `"项目不存在"` | `PROJECT_NOT_FOUND` |
| 权限 | `"未授权"` 等 | `AUTH_FORBIDDEN` |

### 4.2 风险

| 风险 | 说明 |
|---|---|
| **文案即行为** | 改 i18n 文案就改了程序行为，客户端无法感知 |
| **无法做前端差异化** | 所有错误只能弹同一个 toast |
| **无法统计** | 想知道"多少请求因超时失败"只能扫日志文本 |
| **可能泄漏内部信息** | `_parse_error_detail(resp)` 把**上游返回的原始内容**直接放进 `detail`——若上游错误体含内部信息，会原样透出（AT-29 风险） |

---

## 5. 目标码位（尚未实现，仅占位）

以下码位是 `api-contract-delta.md` 目标契约要求、但代码中**尚不存在**的。
**列在此处是为了占位和防止将来重复发明，不是"已实现"。**

### 5.1 租户与权限

| Code | HTTP | 用途 |
|---|---|---|
| `TENANT_OVERRIDE_FORBIDDEN` | 400 | 普通路径携带 `X-Expected-Tenant` |
| `TENANT_SUSPENDED_SECURITY` | 403 | 安全封禁（**不可用导出绕过**） |
| `TENANT_RESTRICTED` | 403 | 商业限制（**保留合法读取与退出**） |
| `AUTH_FORBIDDEN` | 403 | 通用无权限（替代裸字符串） |

### 5.1.1 上下文解析失败出口（来自 W-03 §2.3，2026-10-01 补）

`W-03-租户上下文与授权实施规格_20261001.md` §2.2 的状态机定义五个成功步骤（S0～S4）
与**七个失败出口**。这些出口已在 §2.8 登记为局部共享 SaaS 实现；不再重复列入占位表。支持授权、成员组织授权与 draft 首位所有者启用仍未完成。


> **E3 与 E4 必须分开，不能合并成一个 `AUTH_FORBIDDEN`。**
> 两者 HTTP 都是 403，但 E3 是「你不在这里」（授权问题），
> E4 是「你曾在这里但已被停用」（执行问题）。合并后运营无法区分
> 「权限没配」与「执行被禁」，而这两者的处置动作完全不同。

> **E5 与 `TENANT_OVERRIDE_FORBIDDEN` 也不是同一件事。**
> 后者是「普通路径**携带**了 `X-Expected-Tenant`」（400，请求本身不合法）；
> E5 是「平台路径**携带了但无法兑现**」（403，身份够但目标不可达）。
> 两者请求形态相似、拒绝原因不同，合并会让排障时无法区分
> 「越权尝试」与「合法平台请求的目标租户不存在」。

> **与 §5.1 的 `TENANT_CONTEXT_MISSING` / `TENANT_CONTEXT_INVALID` 的分工**：
> 那两个是 422（请求体/参数层面的问题，可由客户端修正后重试）；
> 本表六个是 401/403（**身份或授权**层面的问题，重试无意义）。
> 不得用 422 表达 403 语义——那会让客户端误以为改参数就能通过。

### 5.2 幂等与并发

| Code | HTTP | 用途 |
|---|---|---|
| `REVISION_CONFLICT` | 409 | `If-Match` 与当前 revision 不符 |
| `QUOTA_EXHAUSTED` | 429 | 三维额度耗尽（替代 `AI_DAILY_QUOTA_EXCEEDED` 的通用形态） |

### 5.3 AI 计量（来自 `ai-metering-runbook.md` §5.3）

| Code | HTTP | 用途 |
|---|---|---|
| `AI_UPSTREAM_UNKNOWN` | 504 | **未知结果**——请求已发出但无法判定是否计费。**客户端不得自动重试** |
| `AI_UPSTREAM_TIMEOUT` | 504 | 超时（可重试，与 unknown 区分） |
| `AI_UPSTREAM_UNREACHABLE` | 502 | 连接失败 |
| `AI_UPSTREAM_UNPARSEABLE` | 502 | 响应无法解析 |

> **`AI_UPSTREAM_UNKNOWN` 与 `AI_UPSTREAM_TIMEOUT` 必须分开。**
> 两者都是 504，但前者上游可能已计费，**自动重试会重复计费**；
> 后者可以安全重试。合并成一个码就是让客户端在计费风险上二选一。

### 5.4 任务

| Code | HTTP | 用途 |
|---|---|---|
| `JOB_NOT_FOUND` | 404 | 任务不存在 |
| `JOB_CANCELLED` | 409 | 任务已取消，与"失败"区分 |
| `JOB_NOT_RETRYABLE` | 409 | 终态失败，人工重试无意义 |

### 5.5 租户 AI 政策与凭据（来自 W-07，2026-10-01 补）

`W-07-租户AI政策BYOK密钥与出站实施规格_20261001.md` §1/§2/§3 的拒绝出口。
**以下四个码当前均未实现**，与 §5.1～§5.3 同为占位。

| Code | HTTP | 出口 | 触发条件 | 客户端对策 |
|---|---|---|---|---|
| `AI_POLICY_DENIED` | 403 | TP-2 | **租户政策禁止外发**——先于任何配置来源判定 | **不得重试**；联系管理员 |
| `AI_NOT_CONFIGURED` | 403 | TP-4 | 租户**未配置**任何可用 AI 来源，且未获准平台回退 | **不得重试**；引导租户配置 |
| `AI_CREDENTIAL_UNAVAILABLE` | 503 | SK-5 / SK-10 | 凭据缺主密钥、密钥错误或密文不可解密 | **不得自动重试**；密钥须人工修复 |
| `AI_OUTBOUND_BLOCKED` | 400 | OB-1～OB-6 | 出站守卫判定私网/环回/元数据端点/非法协议或端口 | 修正 Base URL |

> **`AI_POLICY_DENIED` 与 `AI_NOT_CONFIGURED` 必须分开。**
> 两者 HTTP 都是 403，但前者是「**政策不让**」（改配置也没用，需管理员改政策），
> 后者是「**还没配**」（配了就能用）。合并后用户会反复重试并以为系统坏了。

> **`AI_OUTBOUND_BLOCKED` 是对现状的补登记。**
> 当前 `outbound_guard.py:46-48` 抛的是**裸 `HTTPException(400, detail=<str>)`——无 `code` 字段**，
> 客户端无法机器判定「这是被出站策略拦下」还是「Base URL 格式非法」。
> AT-17 要求「出站拒绝**可核对**」，无码响应满足不了。

> **与 §5.3 的分工**：`AI_UPSTREAM_*` 全部是**已经发出之后**的上游结果；
> 本表前三个是**发出之前**的拒绝。**「没发出去」与「发出去了但结果未知」
> 是两件事**，客户端对策完全不同（前者可安全重试，后者不可）——
> 这正是 W-08 `rejected_pre_dispatch` 与 `unknown` 必须分开的对外体现。

---

## 5.6 登记但**未被任何规格认领**的占位码（2026-10-01 补，INC-64）

`error_codes_check` 的 EC-06 只核对「占位与已实现**重名**」，
**它不能证明某个占位码真的被某份规格使用**。
实测：本节 §5.1～§5.5 原登记的 **26 个**占位码中，两个 `TENANT_CONTEXT_*` 与 `IDEMPOTENCY_KEY_REUSED` 已移至 §2.7，六个 W-03 出口码已移至 §2.8；剩余 **17 个**占位码里，**11 个**在 13 份规格与总设计里
**一次都没出现**：

| # | 码位 | 来源节 |
|---|---|---|
| 1 | `AI_OUTBOUND_BLOCKED` | §5.5 |
| 2 | `AI_UPSTREAM_TIMEOUT` | §5.3 |
| 3 | `AI_UPSTREAM_UNKNOWN` | §5.3 |
| 4 | `AI_UPSTREAM_UNPARSEABLE` | §5.3 |
| 5 | `AI_UPSTREAM_UNREACHABLE` | §5.3 |
| 6 | `AUTH_FORBIDDEN` | §5.1 |
| 7 | `JOB_CANCELLED` | §5.4 |
| 8 | `JOB_NOT_FOUND` | §5.4 |
| 9 | `JOB_NOT_RETRYABLE` | §5.4 |
| 10 | `QUOTA_EXHAUSTED` | §5.4 |
| 11 | `REVISION_CONFLICT` | §5.1 |

**必须说清这三件事，否则本表会给出虚假的安全感**：

1. **它们不是「已设计」**。占位登记只表示「将来要实现时不要重复发明」，
   **不表示任何规格已经承诺用它**。W-08 的八态状态机与未知结果分类
   （RQ-1～RQ-14）**全程没有点名任何错误码**——这是刻意的，
   因为错误码归属要在 W-08 实施时才定。
2. **EC-06 通过 ≠ 这些码有主人**。这是 INC-18 的同一族问题的**后半段**：
   INC-18 发现「W-03 引入的错误码是幽灵码」，处置是**补登记**；
   但**登记本身不解决问题**——不指派主人，它们仍然是幽灵码位，只是从
   「没登记」变成了「登记了但没人用」。
3. **历史设计与当前实现需要区分**。两个 `TENANT_CONTEXT_*` 已在 2026-10-04 的最小运行时实现并移至 §2.7；下面仅描述 W-03 目标出口的命名差异。W-03 的七个失败出口
   （E1～E7）用的是 `AUTH_SESSION_INVALID` / `AUTH_ACCOUNT_BLOCKED` /
   `TENANT_MEMBERSHIP_REQUIRED` / `TENANT_MEMBERSHIP_INACTIVE` /
   `TENANT_HEADER_NOT_HONORED` / `TENANT_NOT_ACTIVE`（E6/E7 共用），
   **`TENANT_CONTEXT_INVALID` 与 `TENANT_CONTEXT_MISSING` 不在其中**。
   保留登记是因为它们表达了「上下文解析失败」这一类需求，
   当前已有最小出口，但**并不表示 W-03 七个目标出口全部实现**。

> **处置原则**：不为消除「未认领」而给规格硬塞错误码——
> 那样只会让码位看起来有主人，而实际实现时未必用得上。
> **显式列出未认领清单，比让它们混在已登记区里更诚实。**

## 6. 维护规则

### 6.1 新增错误码的检查清单

```text
□ 符合 <域>_<原因> 命名
□ 已在本文件登记（含 HTTP 状态、位置、客户端对策）
□ 前端有对应处理分支（不是通用 fallback）
□ message 不含内部异常 / SQL / 路径 / 上游原始响应
□ 与相邻码的客户端对策确实不同（否则应合并）
□ 有测试覆盖该码的触发路径
```

### 6.2 门禁化（AT-T-7，建议）

新增 `docs/tenancy/tools/error_codes_check.py`：

| 检查 | 判据 |
|---|---|
| EC-01 | 代码中所有 `"code": "XXX"` 都已登记在本文件 |
| EC-02 | 登记表中的码在代码中确实存在（防"登记了但没实现"） |
| EC-03 | 无码裸字符串数量**不高于基线**（367），防止新增 |
| EC-04 | `message` 中不含 SQL 关键字 / 路径模式 / 堆栈 |
| EC-05 | 体系 B 的码同时登记在命令信封规范中 |

> **EC-03 的设计要点**：不要求一次降到 0（不现实），只要求**不再增长**。
> 存量 367 处按 `api-contract-delta.md` §7 的 P2～P5 分阶段清理。

---

## 7. 证据边界

| 项 | 状态 | 方法 |
|---|---|---|
| 体系 A 的 31 个码 + 状态码 + 位置 | **已验证** | 对 `HTTPException(...)` 做括号配平提取，全仓扫描 |
| 体系 B 的 9 个码 + 载体 | **已验证** | 搜 `error_code` 字段写入点 |
| 体系 C 的计数（404 / 367） | **已验证（静态）** | 同上，区分是否含 `"code":`；**两个口径分别为 `backend/app/` 全量与 `backend/app/api/` 顶层**，不可混用 |
| 各码的**运行时实际表现** | **未验证** | 未发起任何请求；部分码的分支可能不可达 |
| `_parse_error_detail` 是否真的泄漏上游内容 | **未验证** | 需构造上游返回错误体的场景实测（AT-29） |

---

### 7.1 撰写过程中的一次计数错误（保留记录）

本文初稿写的是「体系 A 31 个 / 体系 C 367 处 / 其他 95 处 / 合计 467」，
并在后续章节复用了这三个数字。复算时发现：

- **31** 是**唯一码数**（去重后），不是抛出点数；
- **367 / 95 / 467** 是 `backend/app/api/` **顶层目录**的统计；
- `backend/app/` **全量**（含 `services/`、`domain/`）实际是 **47 / 404 / 103 / 554**。

两个口径混用，且「唯一码数」与「抛出点数」混列，导致初稿的百分比也全部失真。
修正后已在 §0 明确标注口径。

> **教训**：统计类断言必须同时写明 **①统计范围** 与 **②是否去重**。
> 两者任一缺失，下一个读数字的人就会拿它做错误的比较。
> 这与本项目此前的教训一致（`quota-and-billing-review.md` 中「25 张表」也是全量口径）。

---

## 8. 明确不做

- **不修改任何错误码的现有语义**（会破坏已发布客户端）
- **不把 367 处裸字符串一次性改完**（不现实，且会引入回归风险）
- **不把目标码位写成"已实现"**（§5 明确标注占位）
- **不设计具体文案**（文案属前端 i18n，本文只给对策）
- **不声称已统计各错误码的生产发生率**（无遥测，见 `ai-metering-runbook.md` M-4）
