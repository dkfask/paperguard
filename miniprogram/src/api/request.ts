import { useSessionStore } from '../stores/session'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const REQUEST_TIMEOUT = 15_000

export interface ApiErrorPayload {
  code?: string
  message?: string
}

export class ApiError extends Error {
  status: number
  code: string
  requestId: string

  constructor(status: number, payload: ApiErrorPayload | string, requestId = '') {
    const detail = typeof payload === 'string' ? payload : payload.message || payload.code || '请求失败'
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.code = typeof payload === 'string' ? '' : payload.code || ''
    this.requestId = requestId
  }
}

interface RequestOptions {
  method?: UniApp.RequestOptions['method']
  data?: Record<string, unknown>
  authRequired?: boolean
  headers?: Record<string, string>
  timeout?: number
  /** 内部标记: 401 静默重登后只重试一次 */
  __retried?: boolean
}

function resolvePayload(value: unknown): ApiErrorPayload | string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const detail = (value as { detail?: unknown }).detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail === 'object') return detail as ApiErrorPayload
    return value as ApiErrorPayload
  }
  return '请求失败'
}

// 后端 detail.code → 用户语言(设计方案 §7.3); 未命中时回退到通用状态码解释。
const ERROR_MESSAGES: Record<string, string> = {
  MP_NOT_CONFIGURED: '小程序登录尚未配置，请联系管理员',
  MP_LOGIN_FAILED: '微信登录凭证无效，请重试',
  MP_CODE_REQUIRED: '微信登录凭证缺失，请重试',
  MP_BIND_TICKET_REQUIRED: '绑定会话已过期，请重新获取微信凭证',
  ACCOUNT_UNAVAILABLE: '账号当前不可使用，请联系管理员',
  CHECK_PENDING: '格式检查仍在进行，请稍后重试',
  CHECK_FAILED: '格式检查失败，请稍后重试或联系管理员',
  CHECK_STALE: '正文已在电脑端更新，请重新预检',
  BLOCKING_FINDINGS: '存在未关闭的阻断项，暂不能提交',
  DEADLINE_PASSED: '已超过有效截止时间，如需补交请联系教师或管理员',
  SUBMISSION_ALREADY_PENDING: '已存在待审核的提交，请勿重复提交',
  REVISION_CONFLICT: '内容已在他端修改，请在电脑端处理冲突',
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code && ERROR_MESSAGES[error.code]) return ERROR_MESSAGES[error.code]
    if (error.status === 401) return '登录状态已过期，请重新登录'
    if (error.status === 403) return '没有执行该操作的权限'
    if (error.status === 404) return '资源不存在或无权访问'
    if (error.status === 409) return '状态已变化，请刷新后重试'
    if (error.status === 429 || error.status === 503) return '服务繁忙，请稍后重试'
    return error.message || '请求失败，请稍后重试'
  }
  return error instanceof Error ? error.message : '请求失败，请稍后重试'
}

function wechatLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    uni.login({
      provider: 'weixin',
      success: (result) => resolve(result.code),
      fail: (error) => reject(new ApiError(0, (error as { errMsg?: string })?.errMsg || '微信登录不可用')),
    })
  })
}

// 401 后的静默重登(设计方案 §4.2): 并发请求共享同一次重登, 拿到 bind_ticket 则引导去绑定页。
let refreshingSession: Promise<{ bound: boolean }> | null = null

export async function ensureMpSession(): Promise<{ bound: boolean }> {
  const session = useSessionStore()
  const code = await wechatLoginCode()
  const result = await mpLogin(code)
  if (result.bound && result.token && result.user) {
    session.setAuth(result.token, result.user)
    return { bound: true }
  }
  session.setBindTicket(result.bind_ticket || '')
  session.clear()
  return { bound: false }
}

function redirectToLogin() {
  uni.reLaunch({ url: '/pages/login/index' })
}

function rawRequest<T>(path: string, options: RequestOptions): Promise<T> {
  const session = useSessionStore()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers || {}),
  }
  if (options.authRequired !== false && session.token) {
    headers.Authorization = `Bearer ${session.token}`
  }

  return new Promise((resolve, reject) => {
    uni.request({
      url: `${API_BASE_URL}${path}`,
      method: options.method || 'GET',
      data: options.data,
      header: headers,
      timeout: options.timeout || REQUEST_TIMEOUT,
      success: (response) => {
        const requestId = String(response.header?.['X-Request-ID'] || response.header?.['x-request-id'] || '')
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new ApiError(response.statusCode, resolvePayload(response.data), requestId))
          return
        }
        resolve(response.data as T)
      },
      fail: (error) => reject(new ApiError(0, (error as { errMsg?: string })?.errMsg || '网络请求失败')),
    })
  })
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const session = useSessionStore()
  try {
    return await rawRequest<T>(path, options)
  } catch (error) {
    const isAuthed = options.authRequired !== false
    const unauthorized = error instanceof ApiError && error.status === 401
    if (!isAuthed || !unauthorized || options.__retried) {
      if (unauthorized && isAuthed) {
        // 重登也失败: 清会话回绑定页, 不留死循环
        session.clear()
        redirectToLogin()
      }
      throw error
    }
    if (!refreshingSession) {
      refreshingSession = ensureMpSession().finally(() => {
        refreshingSession = null
      })
    }
    const refreshed = await refreshingSession
    if (!refreshed.bound) {
      redirectToLogin()
      throw new ApiError(401, { code: 'MP_SESSION_EXPIRED', message: '登录已过期，请重新绑定校内账号' })
    }
    return rawRequest<T>(path, { ...options, __retried: true })
  }
}

export interface User {
  id: string
  username: string
  display_name: string
  role: string
  status: string
  must_change_password: boolean
  /** 掩码邮箱; 空串表示未绑定 */
  email?: string
  email_verified?: boolean
  /** 微信注册账号在绑定邮箱前为 false */
  password_set?: boolean
  onboarding_required?: boolean
}

export interface AuthResponse {
  bound: boolean
  token?: string
  user?: User
  bind_ticket?: string
  expires_at?: string
}

export interface Task {
  id: string
  title?: string
  stage_name?: string
  cohort_name?: string
  state?: string
  is_late?: boolean
  effective_deadline_at?: string
  revision_deadline_at?: string
  current_submission?: { id: string; number: string; state: string } | null
  [key: string]: unknown
}

export const mpLogin = (code: string) => request<AuthResponse>('/api/auth/mp/login', {
  method: 'POST',
  authRequired: false,
  data: { code },
})

export const mpBind = (bindTicket: string, username: string, password: string) => request<AuthResponse>('/api/auth/mp/bind', {
  method: 'POST',
  authRequired: false,
  data: { bind_ticket: bindTicket, username, password },
})

export const mpUnbind = (password: string) => request<{ status: string }>('/api/auth/mp/unbind', {
  method: 'POST',
  data: { password },
})

export interface RegisterWithWechatResponse extends AuthResponse {
  needs_email_setup?: boolean
}

/** 微信一键注册: 无需预先拥有校内账号; 返回后引导绑定邮箱设置桌面端密码。 */
export const mpRegister = (bindTicket: string) => request<RegisterWithWechatResponse>('/api/auth/mp/register', {
  method: 'POST',
  authRequired: false,
  data: { bind_ticket: bindTicket },
})

/** 绑定邮箱并设置桌面端密码。 */
export const bindMpEmail = (email: string, emailCode: string, password: string) => request<{ user: User; token?: string }>('/api/auth/mp/bind-email', {
  method: 'POST',
  data: { email, email_code: emailCode, password },
})

export const completeFirstLogin = (email: string, emailCode: string, password: string) => request<{ user: User; token: string }>('/api/auth/first-login/complete', {
  method: 'POST',
  data: { email, email_code: emailCode, new_password: password },
})

export const sendEmailCode = (email: string, purpose = 'register') => request<{ ok: boolean; expires_in: number }>('/api/auth/email/code', {
  method: 'POST',
  authRequired: false,
  data: { email, purpose },
})

export const fetchMe = () => request<User>('/api/auth/me')
export const fetchTasks = () => request<Task[]>('/api/my-tasks')
export const fetchNotifications = () => request<{ items: Array<Record<string, unknown>>; unread: number }>('/api/notifications')
export const markNotificationRead = (id: string) => request<{ ok: boolean }>(`/api/notifications/${id}/read`, { method: 'POST' })
export const changePassword = (currentPassword: string, newPassword: string) => request<{ status: string }>('/api/auth/change-password', {
  method: 'POST',
  data: { current_password: currentPassword, new_password: newPassword },
})

/** 上报订阅消息授权结果: 接受 +1 次推送机会, 拒绝清零(一次性订阅)。 */
export const reportSubscribe = (templateId: string, accepted: boolean) => request<{ ok: boolean; remaining: number }>('/api/auth/mp/subscribe', {
  method: 'POST',
  data: { template_id: templateId, accepted },
})
