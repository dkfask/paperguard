import { useSessionStore } from '../stores/session'
import { describeApiError } from '../utils/error-messages'

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const REQUEST_TIMEOUT = 15_000

export interface ApiErrorPayload {
  code?: string
  message?: string
  submission_id?: string
}

export class ApiError extends Error {
  status: number
  code: string
  requestId: string
  submissionId: string

  constructor(status: number, payload: ApiErrorPayload | string, requestId = '') {
    const detail = typeof payload === 'string' ? payload : payload.message || payload.code || '请求失败'
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.code = typeof payload === 'string' ? '' : payload.code || ''
    this.requestId = requestId
    this.submissionId = typeof payload === 'string' ? '' : payload.submission_id || ''
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

export function describeError(error: unknown): string {
  return describeApiError(error)
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
  project_title?: string
  project_id?: string
  state?: string
  is_late?: boolean
  effective_deadline_at?: string
  revision_deadline_at?: string
  initial_deadline_at?: string
  acknowledged_snapshot_hash?: string
  student_id?: string
  student_name?: string
  current_submission?: { id: string; number: string; state: string; submitted_at?: string; review_cycle?: number } | null
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
export const fetchParticipant = (participantId: string) =>
  request<{ participant: Task; stage?: Record<string, unknown>; cohort?: Record<string, unknown>; config?: Record<string, unknown> }>(
    `/api/stage-participants/${participantId}`,
  )
export const fetchNotifications = () => request<{ items: Array<Record<string, unknown>>; unread: number }>('/api/notifications')
export const markNotificationRead = (id: string) => request<{ ok: boolean }>(`/api/notifications/${id}/read`, { method: 'POST' })
export const acknowledgeTask = (participantId: string, snapshotHash = '') =>
  request<Task>(`/api/stage-participants/${participantId}/acknowledge`, {
    method: 'POST',
    data: snapshotHash ? { snapshot_hash: snapshotHash } : {},
  })
export const changePassword = (currentPassword: string, newPassword: string) => request<{ status: string }>('/api/auth/change-password', {
  method: 'POST',
  data: { current_password: currentPassword, new_password: newPassword },
})

/** 上报订阅消息授权结果: 接受 +1 次推送机会, 拒绝清零(一次性订阅)。 */
export const reportSubscribe = (templateId: string, accepted: boolean) => request<{ ok: boolean; remaining: number }>('/api/auth/mp/subscribe', {
  method: 'POST',
  data: { template_id: templateId, accepted },
})
