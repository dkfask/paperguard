import { apiClient } from '../../shared/api/client'
import type { LoginInput, User } from '../../stores/auth'

export interface AuthResponse {
  token: string
  user: User
}

export const authApi = {
  login: (credentials: LoginInput) => apiClient.request<AuthResponse>({
    url: '/api/auth/login',
    method: 'POST',
    authRequired: false,
    body: credentials,
  }),
  register: (payload: LoginInput & { display_name?: string; role?: string; email?: string; email_code?: string }) => apiClient.request<User>({
    url: '/api/auth/register',
    method: 'POST',
    authRequired: false,
    body: payload,
  }),
  requestEmailCode: (email: string) => apiClient.request<{ ok: boolean; expires_in: number }>({
    url: '/api/auth/email/code',
    method: 'POST',
    authRequired: false,
    body: { email, purpose: 'register' },
  }),
  changePassword: (currentPassword: string, newPassword: string) => apiClient.request<void>({
    url: '/api/auth/change-password',
    method: 'POST',
    body: { current_password: currentPassword, new_password: newPassword },
  }),
  logout: () => apiClient.request<void>({ url: '/api/auth/logout', method: 'POST' }),
}
