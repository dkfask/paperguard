import { API_BASE, api, responseError } from './client'

export async function logoutApi() {
  const resp = await api(`${API_BASE}/auth/logout`, { method: 'POST' })
  if (!resp.ok) throw await responseError(resp, '退出登录失败')
}

export async function changePassword(currentPassword, newPassword) {
  const resp = await api(`${API_BASE}/auth/change-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
  if (!resp.ok) throw await responseError(resp, '修改密码失败')
  return resp.json()
}
