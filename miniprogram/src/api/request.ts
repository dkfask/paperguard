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

export function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
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
        if (response.statusCode === 401) {
          session.clear()
        }
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new ApiError(response.statusCode, resolvePayload(response.data), requestId))
          return
        }
        resolve(response.data as T)
      },
      fail: (error) => reject(new ApiError(0, error.errMsg || '网络请求失败')),
    })
  })
}

export interface User {
  id: string
  username: string
  display_name: string
  role: string
  status: string
  must_change_password: boolean
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
  state?: string
  effective_deadline_at?: string
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

export const fetchMe = () => request<User>('/api/auth/me')
export const fetchTasks = () => request<Task[]>('/api/my-tasks')
export const fetchNotifications = () => request<{ items: Array<Record<string, unknown>>; unread: number }>('/api/notifications')
export const markNotificationRead = (id: string) => request<{ ok: boolean }>(`/api/notifications/${id}/read`, { method: 'POST' })
export const changePassword = (currentPassword: string, newPassword: string) => request<{ status: string }>('/api/auth/change-password', {
  method: 'POST',
  data: { current_password: currentPassword, new_password: newPassword },
})
