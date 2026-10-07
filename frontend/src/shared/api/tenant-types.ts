/**
 * 跨切面 API 类型（27.3 文档包第 3 项）
 *
 * 配套文档：docs/tenancy/api-contract-delta.md §3 / §5
 * 契约补丁：docs/tenancy/openapi-target-delta.yaml
 * 校验器：docs/tenancy/tools/api_contract_check.py
 *
 * ## 为什么不包含全部 133 个业务 schema
 *
 * 手写业务实体会立刻产生**第二份真相**，并在后端改字段时静默过期——
 * 类型不会报错，页面会在运行时才炸。正确做法是后端产出 OpenAPI、
 * 前端由 `openapi-typescript` 生成（迁移 P6 阶段接入）。
 *
 * 本文件只固定**跨切面**部分：租户身份、错误信封、命令状态、组织上下文。
 * 这些是手写成本低、跨全站复用、且最容易在重构中被遗漏的部分。
 *
 * ## 核心不变量
 *
 * `tenant_id` 是**只读**的。客户端不得在任何请求体中指定租户——
 * 归属由服务端从认证主体派生。AC-06 会把这条变成可执行的回归门禁。
 */

// ---------------------------------------------------------------------------
// 租户
// ---------------------------------------------------------------------------

/** 所有租户级实体的公共字段。 */
export interface TenantScoped {
  /** 租户标识。只读——服务端派生，客户端不得指定。 */
  readonly tenant_id: string
}

/**
 * 组织上下文。
 *
 * 相比后端现状的 `dict<string, string>`，这里用具名键把「任意键」收紧为
 * 「四个已登记的组织节点类型」，与 resource-registry.yaml 一一对应。
 *
 * 注意：写入仍受服务端门禁（仅管理者可覆盖），且读取时组织上下文是
 * 服务端派生值而非客户端断言。
 */
export interface OrganizationContext {
  school_id?: string
  college_id?: string
  major_id?: string
  batch_id?: string
}

/** 键名白名单。用于表单校验，避免提交登记表外的键。 */
export const ORGANIZATION_CONTEXT_KEYS = [
  'school_id',
  'college_id',
  'major_id',
  'batch_id',
] as const

export type OrganizationContextKey = (typeof ORGANIZATION_CONTEXT_KEYS)[number]

// ---------------------------------------------------------------------------
// 错误信封
// ---------------------------------------------------------------------------

/**
 * 统一错误体。
 *
 * 现状是三种不兼容形态并存：结构化 {code,message} 仅 5 处、
 * 裸字符串 367 处、FastAPI 原生校验数组 211 处。本类型是目标态。
 */
export interface ApiErrorBody {
  error: ApiErrorDetail
}

export interface ApiErrorDetail {
  /**
   * 机器可判定的稳定错误码，`<域>_<原因>` 全大写下划线。
   * 一旦发布即不可改语义；废弃时保留 code、变更 message。
   */
  code: ApiErrorCode
  /** 人类可读文案，可被前端本地化覆盖。 */
  message: string
  /** 必带。对齐后端结构化日志，供排障关联。 */
  request_id: string
  /** 可选，字段级错误明细。 */
  details?: ApiErrorFieldDetail[]
}

export interface ApiErrorFieldDetail {
  field?: string
  message?: string
  code?: string
}

/**
 * 错误码取值域。
 *
 * 这里只登记**已存在于后端**的码。新增码必须同步登记到
 * docs/tenancy/error-codes.md，否则本联合类型的 `never` 分支会失败。
 */
export type ApiErrorCode =
  // AI 治理
  | 'AI_RATE_LIMITED'
  | 'AI_DAILY_QUOTA_EXCEEDED'
  | 'AI_UPSTREAM_CIRCUIT_OPEN'
  // 小程序
  | 'MP_CODE_REQUIRED'
  | 'MP_BIND_TICKET_REQUIRED'
  | 'MP_TEMPLATE_REQUIRED'
  // 目标契约预留
  | 'IDEMPOTENCY_KEY_REUSED'
  | 'REVISION_CONFLICT'
  | 'TENANT_OVERRIDE_FORBIDDEN'
  | 'TENANT_CONTEXT_MISSING'
  | 'TENANT_CONTEXT_INVALID'

// ---------------------------------------------------------------------------
// 命令状态
// ---------------------------------------------------------------------------

/**
 * `unknown` 是刻意存在的：上游已受理但结果不可判定时，
 * **不要伪造失败**。返回 unknown 并给出可查的 command_id。
 * 对应总设计第 5 项「AI 未知结果 runbook」。
 */
export type CommandStatus = 'accepted' | 'running' | 'succeeded' | 'failed' | 'unknown'

export interface CommandMeta {
  /** 点分命名，如 `project.create`。 */
  action: string
  status: CommandStatus
  /** true 表示本次返回首次结果的缓存，未产生新副作用。 */
  idempotent_replay?: boolean
  /** status 为 unknown 时的排障入口。 */
  command_id?: string
  /** 资源当前版本号，供下一次 If-Match 使用。 */
  revision?: number
}

export interface QuotaUsage {
  burst_used?: number
  burst_limit?: number
  daily_used?: number
  daily_limit?: number
}

export interface ApiMeta {
  request_id: string
  command?: CommandMeta
  quota?: QuotaUsage
}

/** 写操作的统一成功响应。 */
export interface ApiEnvelope<T> {
  data: T
  meta: ApiMeta
}

// ---------------------------------------------------------------------------
// 客户端契约
// ---------------------------------------------------------------------------

/**
 * 客户端调用约定。与后端 x-command-headers 对应。
 *
 * 这些是**约定**而非运行时校验——真正的强制在后端。
 * 前端照此传参，后端按 AC-08/AC-09 校验。
 */
export interface CommandRequestOptions {
  /**
   * 幂等键。同一 key 重放返回首次结果，不产生新副作用。
   * key 相同但请求体不同 → 409 IDEMPOTENCY_KEY_REUSED。
   *
   * 生成要求：调用方作用域内唯一。建议 `${action}-${crypto.randomUUID()}`。
   */
  idempotencyKey?: string
  /**
   * 资源 revision（与既有 `base_revision` 语义一致）。
   * 不匹配 → 409 REVISION_CONFLICT。
   */
  ifMatch?: string | number
}

/**
 * 声明为**永不**由客户端设置。
 *
 * 它的存在是为了让「误用」变成编译错误而不是安全缺陷：
 * 任何试图把 tenant_id 放进请求体的代码都会在此处失败。
 */
export type ForbiddenClientField = {
  tenant_id?: never
  organization_id?: never
}
