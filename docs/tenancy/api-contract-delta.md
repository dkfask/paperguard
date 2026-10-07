# API 契约增量：OpenAPI 与前端类型（27.3 文档包第 3 项 · 第二部分）

> 状态：**design_ready**。本文是现状取证 + 目标契约，**不是已完成的实现**。
> 日期：2026-10-01。基线：HEAD `e166f97`，仓库 `.venv` Python 3.12，FastAPI 生成 OpenAPI **3.1.0**。
> 上游：总设计第 18.3 节（通用约定）、第 4.8/4.9 行（不变量）、第 13.1 节；`command-contracts.md`（本文的幂等/事务部分）、`permission-matrix.md`、`resource-registry.yaml`。
> 授权边界：**只读取证**。未修改任何后端或前端代码，未提交。

---

## 2026-10-04 实施增量（局部，不改写历史基线）

来源提交：`d87679896285a5b61302a98181f03621adffb38d`。`/api/saas/v1` 的 8 个操作现在使用 `bearerAuth`、严格输入模型、类型化 `data/meta` 信封、关联 request_id 的 `ErrorBody`；3 个写操作提供 `meta.command`。没有发现前端消费者，旧 `/api` 不做全局包裹；外部集成须按该 SaaS 命名空间的新线协议调整。

请求租户选择用 `X-Expected-Tenant`，它必须匹配服务端当前成员记录；body 不能注入调用者的 tenant_id、membership_id、actor_id；恢复/租户创建命令也不接受 capabilities。成员管理命令的目标 capabilities 是平台显式授予并限制在租户能力白名单内，不是调用者自报权限。租户/member 响应的标识由服务端生成；AI 元数据的 tenant_id 标为只读。CORS 已支持选择头与现有幂等头。

当前 AC 检查仍有 **5/9 缺口**：206 个操作未声明安全/匿名，215 个操作未声明业务错误，10 个目标实体缺 tenant_id，9 个组织上下文仍宽泛，129/132 个写操作无 command。AC-03 新增逐操作覆盖检查及删除一个操作错误声明的变异反例；不能再用 8 个局部错误契约让全站借绿。body 拒绝未知字段只在此切片生效，不代表存量全改。

## 0. 结论摘要

对 FastAPI 应用实际生成的 OpenAPI 做全量解析，得到 **184 条路径 / 215 个操作 / 133 个 schema**。结论是：

> **这份 OpenAPI 目前不能用于客户端生成，也不能作为多租户契约的依据。**
> 不是因为写得不够细，而是因为四项**结构性缺失**——缺失到客户端无法从中推断任何一条接口的认证方式、错误形态或数据归属。

| 编号 | 缺口 | 实测 | 影响 |
|---|---|---|---|
| **G-1** | **零租户身份** | 整个 spec 中 `tenant` / `Tenant` / `TENANT` 出现 **0 次** | 客户端无法表达也无法校验数据归属；总设计 4.8 的租户不变量在契约层没有任何体现 |
| **G-2** | **零安全方案声明** | `components.securitySchemes` 不存在；215 个操作中声明 `security` 的 **0 个** | 客户端无法知道哪些接口要带 token、哪些匿名；鉴权只能靠读源码 |
| **G-3** | **零错误响应声明 + 三形态分裂** | 声明了 `4XX`/`400`/`default` 响应的操作 **0/215**；后端 467 处 `HTTPException` 中结构化 `{code,message}` 仅 **5 处**，裸字符串 **367 处** | 客户端写不出统一错误处理；`detail` 可能是字符串也可能是对象 |
| **G-4** | **无响应信封** | 成功响应直接返回裸对象（121 处）或裸数组（33 处），无统一外层 | 分页、扩展字段、追踪 ID 没有落点 |

另有一项**中等缺口**：

| 编号 | 缺口 | 实测 |
|---|---|---|
| **G-5** | `organization_context` 是无约束 `dict[str, str]` | 出现在 `WritingProject` / `DocumentProject` / `CreateProjectRequest` / `PaperTemplate` / `TemplateDefinition` / `RoleCertificationRequest` / `UserContextRequest` 共 7 个 schema 中，无键名枚举、无必填项、无取值校验 |

---

## 1. 证据边界与方法

| 项 | 状态 | 方法 |
|---|---|---|
| OpenAPI 规模（184/215/133） | **已验证** | `app.openapi()` 实际生成，导出为 JSON 后解析 |
| G-1 零租户身份 | **已验证** | 全文序列化后子串计数：`tenant`/`Tenant`/`TENANT` 各 0 次 |
| G-2 零安全方案 | **已验证** | `components` 顶层键仅 `['schemas']`；无 `securitySchemes`、无顶层 `security` |
| G-3 错误形态 | **已验证（静态）** | 对 `app/api/*.py` 的 467 处 `HTTPException(...)` 做括号配平提取，按是否含 `"code":` 分类 |
| G-3 错误形态 | **未验证（运行时）** | 未发起任何请求；不同端点的实际响应体形态需接口层 UAT 确认 |
| G-5 组织上下文 | **已验证（代码级）** | `CreateProjectRequest.organization_context: dict[str, str] = {}`；`projects.py:241-243` 的写入路径 |
| 各接口的实际鉴权要求 | **已验证（代码级，未进 spec）** | 逐处理器 `Depends(current_user)` / `Depends(require_admin)` 等，见 §2.3 |

### 1.1 方法论说明（含一处自我更正）

第一轮取证时按路径名搜索 `workflow`，得到「0 条 workflow 路径」，并据此差点判定「`workflow_router` 已挂载 52 个路由却未进 spec」这一**严重缺陷**。

**这是错的。** 该 router 的 `prefix` 是 `/api`，路径实际形如 `/api/cohorts`、`/api/submissions`、`/api/stages`、`/api/jobs`、`/api/outbox`，**路径名里本来就不含 "workflow" 三个字母**。spec 中 `tags.workflow` 有 52 个操作，与代码路由数一致，无缺失。

> **教训**：用「按名称搜索」推断「某模块是否缺失」之前，先确认该模块的路径命名是否真的包含那个词。
> 正确做法是比较**数量**（代码路由数 vs spec 操作数）与 **tag**，而不是匹配路径字符串。

---

## 2. 现状取证

### 2.1 规模基线

```text
OpenAPI 版本   3.1.0
info           {"title": "PaperGuard API", "version": "0.1.0"}
路径           184
操作           215
schema         133
components     仅 ['schemas'] —— 无 securitySchemes
```

tag 分布（可作为分包边界的现成依据）：

| tag | 操作数 |
|---|---|
| `workflow` | 52 |
| `policies` | 18 |
| `process-records` | 18 |
| `document-projects` | 16 |
| `organizations` | 6 |
| `teacher` | 4 |
| `specifications` | 4 |
| `document-packages` | 3 |
| `operations` | 3 |
| `audit` | 2 |
| （无 tag） | 89 |

### 2.2 G-1：零租户身份

全 spec 序列化后：

```text
'tenant'   出现 0 次
'Tenant'   出现 0 次
'TENANT'   出现 0 次
```

含组织类字段的 schema 只有 **11 / 133**，且**没有一处是租户标识**：

| schema | 字段 |
|---|---|
| `AuditEventResponse` | `organization_id` |
| `CohortCreate` | `organization_id` |
| `CreateProjectRequest` | `organization_context` |
| `DocumentProject` | `organization_context` |
| `PaperTemplate-Input` / `-Output` | `organization_context` |
| `RoleCertificationRequest` | `organization_context` |
| `TemplateDefinition-Input` / `-Output` | `organization_context` |
| `UserContextRequest` | `organization_context` |
| `WritingProject` | `organization_context` |

**这意味着**：项目、模板、规范这些核心业务实体在契约层**没有任何字段表明它属于哪个租户**。`resource-registry.yaml` 里已登记的租户维度，在 API 契约中完全没有对应物。

### 2.3 G-2：零安全方案声明

```text
components.securitySchemes   不存在
spec.security（全局）        不存在
声明了 security 的操作        0 / 215
```

但代码里鉴权是真实存在的，且分三档：

| 档位 | 代码依赖 | 覆盖 |
|---|---|---|
| 匿名 | 无 `Depends` | `/api/auth/register`、`/api/auth/login`、`/api/auth/mp/*` |
| 已登录 | `Depends(current_user)` | 绝大多数 |
| 管理 | `Depends(require_admin)` / `require_manager` | 少数 |

**后果**：任何 OpenAPI 代码生成器产出的客户端，都会认为这 215 个接口全部匿名可调。

### 2.4 G-3：错误契约三分裂

**声明层**：215 个操作中，声明了 `4XX` / `400` / `default` 响应的有 **0 个**。spec 中能看到的错误响应只有 FastAPI 自动生成的 `HTTPValidationError`（211 处引用），那是 422 参数校验，与业务错误无关。

**实现层**：`app/api/*.py` 中 467 处 `HTTPException(...)` 的 `detail` 形态分布：

| 形态 | 数量 | 占比 | 样例 |
|---|---|---|---|
| 裸字符串 | **367** | 79% | `detail="AI 服务响应超时, 请稍后重试"` |
| 结构化 `{code, message}` | **5** | 1% | `detail={"code": "AI_DAILY_QUOTA_EXCEEDED", "message": "..."}` |
| 其他（变量 / 多行 / 无 detail） | 95 | 20% | 依赖下游异常对象 |

加上 FastAPI 自身的 `HTTPValidationError`，客户端实际要处理**三种互不兼容的响应体**：

```jsonc
// 形态 A：业务结构化（仅 5 处）
{"detail": {"code": "AI_RATE_LIMITED", "message": "操作过于频繁..."}}
// 形态 B：业务裸字符串（367 处）
{"detail": "AI 服务响应超时, 请稍后重试"}
// 形态 C：参数校验（FastAPI 自动）
{"detail": [{"loc": ["body", "title"], "msg": "field required", "type": "value_error.missing"}]}
```

**`{"code","message"}` 已经是本仓库既有的约定**（限流、配额、小程序绑定等新代码都在用），只是**没有被推广**。这意味着推广方向不需要新发明，只需要把既有约定变成强制约定。

### 2.5 G-4：无响应信封

成功响应直接返回裸对象或裸数组：

| 响应 schema 形态 | 出现次数 |
|---|---|
| 裸 `object` | 121 |
| 裸 `array` | 33 |
| 未声明 schema | 21 |
| 具名模型（`RequirementSet` / `WritingProject` / …） | 30 |

没有统一外层，导致以下信息**无处安放**：`request_id`（后端结构化日志已在打）、分页游标、租户标识、配额消耗量、命令状态。

### 2.6 G-5：组织上下文无约束

```jsonc
// CreateProjectRequest.organization_context 的实际 schema
{"additionalProperties": {"type": "string"}, "type": "object", "default": {}}
```

即 `dict[str, str]`——**任意键、任意值、无必填**。写入路径（`projects.py:241-243`）：

```python
organization_context = dict(user.get("organization_context") or {})
if can_manage(user):
    organization_context.update(req.organization_context)
```

**已有的缓解（不可省略）**：客户端覆盖**受 `can_manage(user)` 门禁**，学生与教师无法覆盖；且访问判定 `_project_accessible`（`projects.py:110-135`）在**读取时**按 `_visible_organization_ids(store, user)` 二次校验，`batch_id` 还会回查 cohort 的 `organization_id` 是否可见。

**因此这不是越权漏洞。** 真实缺口是**写入期无校验**：管理者可以写入一个指向不可见组织的 `batch_id`，项目被创建成功，直到访问时才 404/403。错误被推迟到了读路径，且错误信息会泄漏「该 ID 存在」。

---

## 3. 目标契约

### 3.1 核心决策：租户身份是**只读响应字段**

> **`tenant_id` 永远不出现在任何请求体中。**

这是本文最重要的一条设计决策，直接决定多租户底座的成败。

| 候选方案 | 后果 | 判定 |
|---|---|---|
| 客户端在请求体传 `tenant_id` | 客户端可任意指定归属 = 越权写入。必须再加一层「校验这个用户能否写入该租户」，但**攻击面仍在客户端** | **否决** |
| 客户端传 `X-Tenant-Id` 头，服务端校验 | 比请求体好一点（不污染业务模型），但仍是「客户端声明意图」，且**同一用户切租户**会带来跨租户缓存、审计、限流键的复杂度 | **降级为可选**（仅用于平台运营的显式跨租户操作） |
| 服务端从认证主体派生，只在响应回显 | 归属不可伪造；客户端仍能知道自己拿到了什么，可以做 UI 分区和错误自检 | **采纳** |

落地形态：

1. **响应**：所有租户级实体的 schema 增加 `tenant_id: string`，标记为只读。
2. **请求**：不新增任何租户字段。若某接口需要「在哪个租户上下文操作」，由**服务端**从 `current_user` 的成员关系解析。
3. **跨租户操作**（平台运营、代客户操作）另设独立路径并强制 `require_platform_admin`，不复用普通接口。

### 3.2 安全方案

在 `components.securitySchemes` 声明 `bearerAuth`（HTTP Bearer），并按 §2.3 的三档给每个操作打标：

```yaml
components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
```

标注规则（可机器校验，见 §6）：

| 情形 | 标注 |
|---|---|
| 处理器有 `Depends(current_user)` 或更强 | `security: [{bearerAuth: []}]` |
| 处理器无鉴权依赖 | 不标 `security`，且**必须**在 `tags` 或 `x-anonymous: true` 中显式声明 |
| 混用（同一 tag 内有匿名与登录） | 拆 tag 或逐操作标注，不允许留空 |

**判定规则**：`/api/auth/login`、`/api/auth/register`、`/api/auth/mp/*`、`/api/check*` 保持匿名；其余全部需要 `bearerAuth`。

### 3.3 统一错误信封

**目标：所有 4xx/5xx 都返回同一形态。**

```jsonc
{
  "error": {
    "code": "AI_RATE_LIMITED",          // 机器可判定，点分大写，稳定契约
    "message": "操作过于频繁，请稍后再试",  // 人类可读，可本地化
    "request_id": "req-6d71d85f749c4103", // 必带，对齐结构化日志
    "details": []                        // 可选，字段级错误
  }
}
```

**推广策略：复用既有约定，不新造。** 现有 5 处 `{code, message}` 就是目标形态的子集；367 处裸字符串是待迁移存量。迁移按「**新代码强制 + 存量按 tag 批量**」推进，不要求一次性改完（见 §7）。

`code` 的取值域必须登记在册（`docs/tenancy/error-codes.md`，本文不展开），规则：

- 命名：`<域>_<原因>`，全大写下划线，如 `AI_RATE_LIMITED`、`TENANT_CONTEXT_MISSING`
- 稳定性：一旦发布即不可改语义；废弃用 `code` 保留 + `message` 变更的方式过渡
- 禁止：把内部异常信息、SQL、路径直接放进 `message`

### 3.4 命令信封

与 `command-contracts.md` §6 的目标契约模板对齐，落到 API 层即三个可选请求头 + 一个可选响应字段：

**请求头**：

| 头 | 适用 | 语义 |
|---|---|---|
| `Idempotency-Key` | 有副作用的命令 | 同一 key 重放返回首次结果；key 相同但请求体不同 → `409 IDEMPOTENCY_KEY_REUSED` |
| `If-Match: <revision>` | 有并发冲突的命令 | 携带 `base_revision` 语义；不匹配 → `409 REVISION_CONFLICT` |
| `X-Expected-Tenant` | 平台运营专用 | **仅** `require_platform_admin` 路径接受；普通路径出现该头 → `400 TENANT_OVERRIDE_FORBIDDEN` |

**响应**：写操作统一返回

```jsonc
{
  "data": { /* 原业务对象 */ },
  "meta": {
    "request_id": "req-...",
    "command": {
      "action": "project.create",
      "status": "succeeded",        // accepted | running | succeeded | failed | unknown
      "idempotent_replay": false
    }
  }
}
```

`status: unknown` 是为总设计第 5 项「AI 未知结果 runbook」预留的：上游已受理但结果不可判定时，**不要伪造失败**，返回 `unknown` 并给出可查询的命令 ID。

### 3.5 组织上下文收口

把 `dict[str, str]` 换成具名 schema：

```jsonc
"OrganizationContext": {
  "type": "object",
  "properties": {
    "school_id":  {"type": "string"},
    "college_id": {"type": "string"},
    "major_id":   {"type": "string"},
    "batch_id":   {"type": "string"}
  },
  "additionalProperties": false,   // 关键：拒绝未知键
  "description": "组织上下文。写入时受服务端门禁；读取时为服务端派生值。"
}
```

三项配套要求：

1. **`additionalProperties: false`**——未知键直接 422，而不是静默存下来。
2. **写入期校验**——`college_id` 必须挂在 `school_id` 之下、`major_id` 必须挂在 `college_id` 之下、`batch_id` 必须属于可见组织。校验在**写入时**做，错误在**写路径**返回。
3. **与 `resource-registry.yaml` 对齐**——键名与登记表中的组织节点类型一一对应，不允许出现登记表外的键。

---

## 4. 目标 OpenAPI 增量

完整可校验的增量定义见 `openapi-target-delta.yaml`。它以**叠加补丁**形式描述，不重写整份 spec，因此可以与真实生成的 spec 做机器比对（见 §6）。

补丁包含四组变更：

| 组 | 变更 | 期望影响面 |
|---|---|---|
| `securitySchemes` | 新增 `bearerAuth` | 1 个新 components 段 |
| `security` | 为 215 个操作逐个标注 | 约 205 个操作新增 `security` |
| `schemas` | 新增 `Error`、`ErrorBody`、`CommandMeta`、`OrganizationContext`；为 9 个实体 schema 增加 `tenant_id` | 4 个新 schema + 9 个字段 |
| `responses` | 新增 `BadRequest`/`Unauthorized`/`Forbidden`/`NotFound`/`Conflict`/`RateLimited` 六个复用响应 | 215 个操作各挂 2～6 个 |

---

## 5. 前端类型

目标类型定义见 `frontend/src/shared/api/tenant-types.ts`，与 §3 的目标契约一一对应。

设计要点：

1. **`TenantScoped` 基类**：所有租户级实体 `extends TenantScoped`，编译期强制携带 `tenant_id`。
2. **`ApiErrorBody` 与后端错误信封同构**——`ApiError` 类扩展它，`request_id` 必带。
3. **`ApiEnvelope<T>`** 泛型信封，配合 `client.ts` 的解包。
4. **不手写业务实体**——已有的 `shared/api/types.ts`（25 行，只有 3 个 summary 接口）保持原样，避免与后端模型双份维护。本文件只放**跨切面**类型（租户、错误、信封、命令状态）。

> **刻意不做**：不把 133 个后端 schema 全部翻译成 TypeScript。
> 理由：那会立刻产生第二份真相，并在后端改字段时静默过期。
> 正确做法是后端产出 OpenAPI，前端由 `openapi-typescript` 生成，本文只固定**跨切面**部分。

---

## 6. 校验器

`tools/api_contract_check.py`，复用本目录既有校验器风格（退出码 0/1）。

它做两件事：

1. **从真实应用生成 OpenAPI**（临时数据库，不碰 `data/paperguard.db`），把当前 spec 存为基线。
2. **按 §3 的目标逐项检查**，输出缺口清单。

| 检查 | 判据 |
|---|---|
| AC-01 | `components.securitySchemes.bearerAuth` 存在 |
| AC-02 | 每个操作都声明了 `security` 或显式 `x-anonymous: true` |
| AC-03 | 所有 `4xx`/`5xx` 响应引用统一 `Error` schema |
| AC-04 | 所有 `4xx`/`5xx` 响应带 `request_id` |
| AC-05 | 每个租户级实体 schema 含 `tenant_id` 且为 `readOnly` |
| AC-06 | **没有任何请求体 schema 含 `tenant_id`**（§3.1 的核心不变量） |
| AC-07 | `organization_context` 全部为具名 `OrganizationContext` 引用，无内联 `dict[str,str]` |
| AC-08 | `Idempotency-Key` 头在声明了幂等的操作上存在 |
| AC-09 | 写操作响应含 `meta.command.status` |

> **AC-06 是最重要的一条**：它把「租户身份不可由客户端指定」从文档约定变成**可执行的回归门禁**。
> 一旦有人往请求体里加 `tenant_id`，校验器立刻失败。

---

## 7. 迁移顺序

不要求一次性改完 215 个操作。按「**风险 × 成本**」排序：

| 阶段 | 内容 | 判据 |
|---|---|---|
| **P0** | 声明 `securitySchemes` + AC-01/AC-06 落地 | 契约不再声称全部接口匿名；租户字段仍为 0 |
| **P1** | 新增 4 个跨切面 schema + 6 个复用错误响应 | 存量接口不动，新接口一律按新约定 |
| **P2** | 按 tag 批量迁移错误形态，**从 `operations`/`audit`（5 个操作）起步** | 风险最低，且能立刻验证前端统一错误处理可行 |
| **P3** | 迁移 `teacher`(4) → `organizations`(6) → `document-projects`(16) | 组织相关，租户维度受益最直接 |
| **P4** | `policies`(18) → `process-records`(18) | |
| **P5** | `workflow`(52) | 最大的一块，单独排期 |
| **P6** | 补齐 9 个实体的 `tenant_id` + 前端接入 `openapi-typescript` | 需要 §3.1 的服务端派生逻辑先落地 |

**P2 的验收方式**：前端接入统一 `ApiError` 后，`workflow` tag 下 52 个操作的错误提示**不再需要逐个适配**。

---

## 8. 交给工作包的动作

| 编号 | 动作 | 归属 | 阻塞 |
|---|---|---|---|
| AC-FIX-1 | 声明 `bearerAuth` 并逐操作标注 `security` | W-03 T-03.1 | 无 |
| AC-FIX-2 | 新增 `Error`/`ErrorBody`/`CommandMeta`/`OrganizationContext` 四个 schema | W-03 T-03.1 | AC-FIX-1 |
| AC-FIX-3 | 实现统一异常处理器，把 5 处结构化约定推广 | W-03 T-03.2 | AC-FIX-2 |
| AC-FIX-4 | `organization_context` 收口为具名 schema + 写入期校验 | W-03 T-03.2 | 无（可与 AC-FIX-2 并行） |
| AC-FIX-5 | 9 个实体 schema 增加只读 `tenant_id` | W-02 T-02.4 | §3.1 的服务端派生逻辑 |
| AC-FIX-6 | 前端接入 `openapi-typescript` + 统一 `ApiError` | W-03 T-03.3 | AC-FIX-1/2 |
| AC-FIX-7 | 登记 `error-codes.md` 取值域 | W-03 T-03.2 | AC-FIX-2 |

---

## 9. 明确不做

- **不把 133 个 schema 全量翻译成 TypeScript**——会产生第二份真相并静默过期（§5）
- **不在请求体中增加任何租户字段**——即使「只加一个可选字段」也不行（§3.1 / AC-06）
- **不要求一次性迁移 367 处裸字符串错误**——按 §7 分阶段，每阶段独立可验收
- **不修改任何后端或前端代码**——本文只做取证与设计
- **不把「spec 里没有」表述为「系统没有鉴权」**——鉴权在代码里真实存在（§2.3），缺的是**声明**
- **不把 G-1～G-5 表述为安全漏洞**——已逐项核实实际缓解措施（§2.6），它们是**契约债**，不是运行时越权

---

## 附：可复现命令

```bash
cd backend
../.venv/bin/python -c "
import json, os, tempfile
os.environ.setdefault('PAPERGUARD_ADMIN_PASSWORD', 'x')
os.environ['PAPERGUARD_DB_PATH'] = os.path.join(tempfile.mkdtemp(), 'o.db')
from app.main import app
s = app.openapi()
raw = json.dumps(s, ensure_ascii=False)
print('paths/ops/schemas:', len(s['paths']),
      sum(len([k for k in v if k in ('get','post','put','patch','delete')]) for v in s['paths'].values()),
      len(s['components']['schemas']))
print('tenant 出现次数:', raw.count('tenant') + raw.count('Tenant') + raw.count('TENANT'))
print('securitySchemes:', s['components'].get('securitySchemes', '不存在'))
"
```
