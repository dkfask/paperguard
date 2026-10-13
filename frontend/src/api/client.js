import { auth, clearAuth } from '../auth'
import { translate } from '../i18n'

const API_BASE = '/api'

/** 带认证头的 fetch; 401 时清除会话并跳转登录页 */
async function apiFetch(url, options = {}, { includeRequestId = true } = {}) {
  const headers = { ...(options.headers || {}) }
  if (auth.token) headers['Authorization'] = 'Bearer ' + auth.token
  const resp = await fetch(url, { ...options, headers })
  if (resp.status === 401) {
    clearAuth()
    if (!location.pathname.startsWith('/login')) {
      const redirect = encodeURIComponent(location.pathname + location.search + location.hash)
      location.href = `/login?redirect=${redirect}`
    }
    const requestId = resp.headers.get('x-request-id')
    const error = new Error(includeRequestId && requestId ? translate('auth.sessionExpired', { requestId }) : translate('auth.sessionRequired'))
    error.status = 401
    error.requestId = requestId || ''
    throw error
  }
  return resp
}

function detailMessage(payload, fallback) {
  const value = payload?.detail ?? payload
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    if (typeof value.message === 'string') return value.message
    if (typeof value.detail === 'string') return value.detail
    try { return JSON.stringify(value) } catch { return fallback }
  }
  return fallback
}

async function responseError(resp, fallback, { includeRequestId = true } = {}) {
  const payload = await resp.json().catch(() => ({}))
  const requestId = resp.headers.get('x-request-id') || ''
  const message = detailMessage(payload, `${fallback} (${resp.status})`)
  const error = new Error(includeRequestId && requestId ? `${message}（请求 ID: ${requestId}）` : message)
  error.status = resp.status
  error.requestId = requestId
  return error
}

export { API_BASE, apiFetch as api, responseError }
