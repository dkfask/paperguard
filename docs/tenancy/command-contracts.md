# 命令契约：状态、幂等与事务（27.3 文档包第 3 项 · 第一部分）

> 状态：**design_ready**。**本文记录的是现状取证 + 目标契约模板，不是已完成的实现。**
> 日期：2026-10-01。基线：HEAD `e166f97`。
> 上游：总设计第 18.3 节（通用约定）、第 4.8/4.9 行（不变量）、第 13.1 节；`docs/tenancy/permission-matrix.md`。
> 授权边界：只读静态分析。**未修改任何代码、未发起任何请求、未产生任何上游调用。**

## 2026-10-04 当前切片

来源 `d87679896285a5b61302a98181f03621adffb38d`：AST 路由 222、写操作 132。新增 AI 请求 unknown 待复核命令用状态 CAS + 同事务追加审计，重复成功为无副作用重放，非供应商派发/最终结算命令。SaaS 三个写操作已带 `meta.command`；其余全站 command 契约债未偿还。下文 2026-10-01 的数字保留为历史取证，不作为当前计数；当前全量以资源登记表和工具实跑为准。

## 2026-10-04 W-08 当前实现注记

AI 派发出口已经不再使用全局 `(user_id, idempotency_key)` 作为新请求的唯一域。SQLite `0014_ai_tenant_idempotency` 使用 `(idempotency_domain, idempotency_scope, action, idempotency_key)` 部分唯一索引，并由 `register_ai_request()` 在 `BEGIN IMMEDIATE` 中完成登记。历史行保持 `historical_legacy`，不猜测租户；本注记只覆盖 AI mock 派发切片，不改变下文对全站写操作债务的历史盘点，也不表示真实收费或最终对账已完成。

## 0. 结论摘要

全量 **129 条写操作**（POST/PUT/PATCH/DELETE）的契约现状：

| 类别 | 数量 | 占比 |
|---|---|---|
| A 有幂等 | **1** | 1% |
| B 有事务、无幂等 | 38 | 29% |
| C **既无事务也无幂等** | **90** | **70%** |

**最重要的单一发现（2026-10-02 更正，原结论已作废）**：`ai_governance.begin_call()`（突发限流 + 日配额门禁）全仓**只有 1 个调用点**——`api/ai.py:389`，位于 **`_metered_chat`**（`ai.py:358-417`）内部。

> **原结论（已作废）**：「6 条调用外部 AI 的写操作中只有 `ai_assist` 一条计量，其余 5 条全部不计量、不限流」，并据此判定违反总设计 13.1 节。
> **该结论是错的，且错因值得记住**：它**用一个内部函数的调用点数量，去推断有多少条业务路径经过它**。
> `begin_call` 位于**收敛点内部**——6 条命令全部经 `_metered_chat` 派发，因此它的调用点**本来就该是 1 个**。
> **调用点为 1 是修复后的正确状态，不是缺陷指标。**

**现实**：6 条外部 AI 写操作**全部**经 `_metered_chat`，该函数同时执行「出站校验 → `begin_call` 配额门禁 → `record_usage` 用量记录」。`_metered_chat` 的 docstring 明确记载这是为修本缺口而建的（「**修复前**……5 条路径完全不计量」）。

**残留的真实风险**（与原结论方向不同，且**未验证**）：`_metered_chat` 是单点——它一旦被绕过即全线失守；且**实际消耗量与调用次数未验证**（需接口层 UAT 或上游账单对账）。

这直接违反总设计第 13.1 节第 553 行的明令：

> 配置测试也会产生真实上游消耗，应经过相同出站限制、计量和预算；**不得作为绕过额度的隐藏入口**。

## 1. 证据边界与方法

| 项 | 状态 | 方法 |
|---|---|---|
| 写操作清单（129 条） | **已验证** | AST 解析 `@router.*` 装饰器。**⚠ 该方法必须同时遍历 `ast.FunctionDef` 与 `ast.AsyncFunctionDef`**——后者**不是**前者的子类（是兄弟类），只判 `isinstance(n, ast.FunctionDef)` 会漏掉全部 `async def` 处理器，实测 **116 条（少 13 条 POST）**。详见 `resource-registry.yaml` 的 `route_inventory.method_note` |
| 事务/幂等判定 | **已验证（handler + 一层 service）** | 见下方方法论说明 |
| `begin_call` 调用点全集 | **已验证** | 全仓 grep |
| 直连 AI 的模块全集 | **已验证** | 全仓 grep `chat/completions`、`/v1/messages` |
| **运行时实际配额绕过次数** | **未验证** | 未发起任何请求；需接口层 UAT |
| 熔断实际行为 | **已验证（代码级）** | `api/ai.py:337-341` |

### 1.1 方法论说明（重要）

第一轮扫描只看 `api/*.py` 的处理器函数体，得出「`create_render_job`、`create_project_export_job` 无事务」的结论。**这是错的**——两者都委托给 service，而 `services/document_render_jobs.py:99 create_document_render_job()` 与 `services/export_jobs.py:47 create_export_job()` **内部使用事务**。

因此最终统计的口径是：**处理器函数体 ∪ 其直接调用的 service 函数体**。仍属保守下界（未做全调用图递归），已在 §6 标注。

> 附带发现：`Store._connect()` 的上下文管理器不关闭连接（见 `data-root-fragmentation.md` F-09），AST 判定的「有事务」只代表**进入了事务上下文**，不代表连接被正确归还。

## 2. 幂等：唯一实现

`POST /api/submissions`（`api/workflow.py:1485 confirm_submission`）是**全仓唯一**实现幂等的命令：

| 要素 | 实现 |
|---|---|
| 幂等键 | `SubmissionConfirm.idempotency_key`，`Field(min_length=8, max_length=160)`（`models/workflow.py:88`） |
| 存储 | `workflow_idempotency` 表，`PK(user_id, action, idem_key)`（`db.py:321-329`） |
| 内容指纹 | `request_hash` 列（`db.py:325`）→ 满足总设计 4.8 行「相同键不同内容必须冲突」 |
| 事务 | `db.py:1410 workflow_transaction()` + `db.py:1507 save_workflow_idempotency_conn()` |

**但幂等范围不满足目标模型**：现 PK 是 `(user_id, action, idem_key)`，总设计 9.4 节要求**租户+成员+操作+键**。同一账号在两租户（AT-01）会互相顶掉幂等记录。已在登记表 `workflow_idempotency` 条目登记。

## 3. 事务：38 条有事务

统一入口 `db.py:1410 workflow_transaction()`，用于「业务状态 + 任务 + outbox 同事务提交」（总设计 19.1 行）。`_conn(...)` / `*_in_transaction(...)` 变体供 service 层复用。

**未验证**：事务内是否真的包含了全部应原子提交的写入。逐条核对属 W-03/W-09 范围。

## 4. AI 计量出口：`begin_call` 唯一调用点是**收敛的设计目标**（2026-10-02 全节重写）

> **本节此前整节基于一个错误结论**：「`begin_call` 只有 1 个调用点 ⇒ 6 条 AI 命令里 5 条不计量」。
> 该推理把**内部函数的调用点数量**当成了**业务路径覆盖数**。实测 6 条命令**全部**经 `_metered_chat`，
> 因此 `begin_call` 的调用点**恰好应为 1**。原表、原结论与原「落地顺序第 1 步」一并作废。

### 4.1 已验证事实

```
$ grep -rn "begin_call" --include=*.py backend/app | grep -v "def begin_call"
backend/app/api/ai.py:389:    ai_governance.begin_call(store, user)      # ← 生产代码唯一调用点
```

该行位于 **`_metered_chat`**（`api/ai.py:358-417`）内，**不在 `ai_assist` 内**。
`_metered_chat` 的 docstring 自述其存在理由：

> 真实上游调用的**唯一计量出口**。任何会发往供应商的调用都必须经过这里。
> 修复前配额与用量记账内联在 `ai_assist` 一处，而全仓共有 7 个 `_chat_completion` 调用点
> （两个配置测试、assist、规范导入含重试、需求辅助、规范解析含重试）——其中 5 条路径**完全不计量**
> ……违反总设计 13.1 节「配置测试也会产生真实上游消耗……不得作为绕过额度的隐藏入口」。

**`ai_governance.begin_call`**（`services/ai_governance.py:77-100`）实现两级门禁：
① `consume_ai_quota(user_id, minute_period, ...)`（`ai_governance.py:87`）——每分钟突发限流；
② `consume_ai_quota(user_id, day_period, ...)`（`ai_governance.py:96`）——日配额，失败时把突发计数还回。

### 4.2 六条外部 AI 写操作的计量现状（2026-10-02 逐条 AST 实测重算）

| 命令 | 端点 | 计量路径 | 配额门禁 | 用量记录 | 幂等 | 事务 |
|---|---|---|---|---|---|---|
| `ai_assist` | `POST /api/ai/assist`（`ai.py:737`） | `_metered_chat` | ✓ | ✓ | ✗ | ✗ |
| `test_ai_config` | `POST /api/ai/config/test`（`ai.py:608`） | `_metered_chat` | ✓ | ✓ | ✗ | ✗ |
| `test_admin_ai_config` | `POST /api/ai/admin/config/test`（`ai.py:711`） | `_metered_chat` | ✓ | ✓ | ✗ | ✗ |
| `import_policy_with_ai` | `POST /api/policies/import`（`policies.py:209`） | `ai_api._metered_chat`（`policies.py:230,241`） | ✓ | ✓ | ✗ | ✗ |
| `assist_requirement_check` | `POST /api/projects/{id}/requirements/{item_id}/assist`（`projects.py:1345`） | `_metered_chat`（`projects.py:1375`） | ✓ | ✓ | ✗ | ✗ |
| `parse_spec_candidate` | `POST /api/specs/parse`（`specs.py:64`） | `ai_api._metered_chat`（`specs.py:85`） | ✓ | ✓ | ✗ | ✗ |

> **合计：6 条外部 AI 命令全部经 `_metered_chat`，配额门禁与用量记录均已覆盖。**
> 原表所列 5 处 `✗` 全部作废。

`policy_import` 与 `spec_engine` 不直接发请求——它们**接收注入的 `ai_completion` 可调用对象**（`services/spec_engine.py:312,332-341`），实际发起调用的是 `api/ai.py` 的 `_chat_completion`。**全仓只有 `api/ai.py` 触达 `chat/completions` 与 `/v1/messages`**，这一点原文正确、保留。

### 4.3 修复后仍然成立的两项风险（**均未验证**）

1. **单点风险**：`_metered_chat` 是唯一出口，**一旦被绕过即全线失守**。
   代码里**没有任何机制**阻止新增 `_chat_completion(...)` 直调——`CR-4`-类静态门禁当前**不存在**。
   **这是本节现在的头号待办**，与原「5 条不计量」是不同的问题。
2. **实际消耗未对账**：`record_usage` 是否与上游账单一致，**未验证**（需接口层 UAT 或上游账单对账）。

### 4.4 出站侧的既有缓解（仍然有效，保留）

1. **出站守卫**：`assert_safe_outbound_url(cfg["base_url"], ...)`（`api/ai.py:388`，`_metered_chat` 内、**先于** `begin_call`），注释明写「测试连通性会真的发请求，出口必须完整校验」
2. **熔断**：`_chat_completion` 内部 `ai_breaker.allow(key)`（`api/ai.py:337-340`），覆盖**全部**上游调用路径

**熔断不是限流**——它只在连续失败后打开（`AI_UPSTREAM_CIRCUIT_OPEN`），对成功调用速率无约束。

### 4.5 原「后果」一节作废（2026-10-02）

原文列举的后果全部建立在「这 5 条不计量、不留 `ai_usage` 记录」之上，而**该前提已被推翻**（§4.2）。
特别地，`test_admin_ai_config` 走 `action`/`source` 参数进入 `record_usage`，其消耗**是留痕的**。

**仍然成立的那一半**：叠加 GAP-P-04（`ai_usage_summary` 无对外端点、`ai_usage` 无使用聚合界面），
**当前无法通过产品界面回答「谁花了多少」**——`db.py` 建表注释把这作为动机，但**读得到 ≠ 看得见**。
这是**可观测性缺口**，不是计量缺口。

## 5. 其余 90 条 C 类命令

90 条既无事务也无幂等的写操作中，**大部分本身是天然幂等或低风险**（`mark_notification_read`、`logout`、`set_retention` 属 PUT/语义幂等）。不应把 90 条一概视为缺陷。

**需要逐条定级的子集**：

| 风险类型 | 代表命令 | 定级依据 |
|---|---|---|
| 创建外部可见效果 | `create_project`、`create_policy`、`create_organization`、`create_template` | 重复提交产生重复实体 |
| 批量写多对象 | `batch_upgrade_policy_projects`（`policies.py:650`）、`bulk_import_students`（`teacher.py:153`）、`import_members`（`workflow.py:796`） | 重复执行产生倍增效果；**总设计 6.3 行要求批量变更「任何越界在第一笔写入前整体拒绝」** |
| 文件/产物写入 | `upload_image`、`import_docx_template`、`upload_policy_document`、`restore_local_snapshot` | 重复执行产生孤儿产物（总设计 19.2 行） |
| 状态机推进 | `decide_requirement_waiver`、`decide_requirement_dispute`、`submit_policy_review`、`publish_policy` | 状态机跃迁需前置状态校验 + 并发保护 |
| 外发通知 | `request_email_code`（`auth.py:156`） | 重复触发邮件 |

> **本表只列类型与代表，不替每条命令下结论。** 逐条定级属 W-03（授权）与 W-08（计量）联合范围，需结合各命令的状态机定义。

## 6. 目标契约模板（总设计 18.3 行）

总设计第 833 行要求：**每个写接口声明 capability、状态前提、事务边界、幂等范围、版本字段和审计动作。**

```yaml
command: ai.assist
method: POST
path: /api/tenants/{tenant_id}/ai/assist
capability: content.write
state_precondition: [membership.status == active, entitlement.ai.enabled]
transaction_boundary: >
  预留 + 业务状态 + outbox 同事务；外部调用不在事务内（总设计 4.9 行）
idempotency:
  required: true
  header: Idempotency-Key
  scope: [tenant_id, membership_id, action, key]
  conflict: 409            # 相同键不同内容必须冲突（总设计 4.8 行）
  on_unknown: AI_RESULT_UNKNOWN + poll_request   # 禁止自动重发（总设计 4.7 行）
version_field: request.resource_version          # 同版本竞争返回 409
audit_action: ai.request_dispatched
rate_limit: {burst_per_minute: N, daily_quota: M} # 必须经 ai_governance.begin_call
metering: {record_usage: true, credential_ref_frozen: true}
```

**落地顺序建议**（不改变现有行为的前提下）：
1. ~~给 6 条 AI 命令补 `begin_call` + `record_usage`~~ —— **2026-10-02 作废：已全部经 `_metered_chat`，无缺口可补**。改为：给 `_chat_completion` 直调加**静态门禁**（禁止绕过唯一计量出口），见 §4.3 第 1 项
2. 再给批量类命令补 `Idempotency-Key`（防倍增）
3. 最后给状态机推进类补 `version` 字段与 409

## 7. 与验收用例的对应

| AT | 场景 | 当前 | 阻塞 |
|---|---|---|---|
| AT-20 | AI 重复/并发同幂等键、同键不同内容 | **不能** | 5/6 AI 命令无幂等；`workflow_idempotency` 范围过窄 |
| AT-21 | 熔断/配额/出站拒绝，风控尝试与消耗分开记录 | **不能** | **5 条** AI 命令无配额门禁 |
| AT-22 | HTTP 超时、收到响应后落库失败 | **不能** | 无 `unknown` 状态机；无预留记录 |
| AT-24 | 用量聚合、明细可核对 | **不能** | GAP-P-04：无聚合端点；4 条 AI 命令不写 `ai_usage` |
| AT-26 | 同任务/事件重复消费 | 部分 | outbox 有 `dedupe_key`，但无幂等契约声明 |

## 8. 交给工作包的动作

| 编号 | 动作 | 归属 | 优先级 |
|---|---|---|---|
| CC-1 | 为 `test_ai_config`、`test_admin_ai_config`、`import_policy_with_ai`、`assist_requirement_check`、`parse_spec_candidate` 补 `begin_call` + `record_usage` | W-08 | **高** |
| CC-2 | 冻结 `credential_ref/version` + `policy_version`（总设计 13.1 行第 548 行） | W-08 | 高 |
| CC-3 | 引入 `Idempotency-Key` 头，作用域为租户+成员+操作+键 | W-08 / W-03 | 高 |
| CC-4 | `workflow_idempotency` PK 扩展为 `(tenant_id, membership_id, action, idem_key)` | W-02 T-02.5 | 中 |
| CC-5 | 为 129 条写操作逐条产出契约 YAML（§6 模板），登记 capability/状态/事务/幂等/版本/审计 | W-03 + W-08 | 中 |
| CC-6 | 批量类命令（`policies.py:650`、`teacher.py:153`、`workflow.py:796`）补整体拒绝语义 | W-03 | 中 |
| CC-7 | 状态机推进类命令补 `version` 与 409 | W-03 | 中 |

## 9. 明确不做

- 不修改任何端点、守卫或计量逻辑（本文件只做取证与设计）
- 不发起任何请求验证配额绕过（需授权，且应作为 CC-1 的验证步骤）
- 不把「代码路径无配额门禁」表述为「已发生费用损失」——实际消耗**未验证**
- 不为 90 条 C 类命令逐条编造契约——只列类型与代表，定级属 W-03/W-08
- 不声称已交付 27.3 第 3 项全部内容（OpenAPI 契约与前端类型**尚未产出**）
