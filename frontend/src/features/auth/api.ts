import { apiClient } from '../../shared/api/client'
import type { LoginInput, User } from '../../stores/auth'

export interface AuthResponse {
  token: string
  user: User
}

export interface FirstLoginCompleteInput {
  email: string
  email_code: string
  new_password: string
}

export interface PasswordResetInput {
  email: string
  email_code: string
  new_password: string
}

export interface OrganizationOption {
  id: string
  name: string
  type: string
  parent_id?: string | null
}

export interface RoleCertificationResponse {
  role: string
  requested_role?: string | null
  requested_organization_context: Record<string, string>
  application_status: string
  organizations: OrganizationOption[]
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
  requestEmailCode: (email: string, purpose = 'register') => apiClient.request<{ ok: boolean; expires_in: number }>({
    url: '/api/auth/email/code',
    method: 'POST',
    authRequired: false,
    body: { email, purpose },
  }),
  resetPassword: (payload: PasswordResetInput) => apiClient.request<{ ok: boolean }>({
    url: '/api/auth/reset-password',
    method: 'POST',
    authRequired: false,
    body: payload,
  }),
  completeFirstLogin: (payload: FirstLoginCompleteInput) => apiClient.request<AuthResponse>({
    url: '/api/auth/first-login/complete',
    method: 'POST',
    body: payload,
  }),
  getRoleCertification: () => apiClient.request<RoleCertificationResponse>({
    url: '/api/auth/role-certification',
    method: 'GET',
  }),
  submitRoleCertification: (role: string, organization_context: Record<string, string> = {}) => apiClient.request<User>({
    url: '/api/auth/role-certification',
    method: 'POST',
    body: { role, organization_context },
  }),
  decideRoleCertification: (userId: string, decision: 'approve' | 'reject') => apiClient.request<User>({
    url: `/api/auth/admin/users/${userId}/role-certification`,
    method: 'POST',
    body: { decision },
  }),
  changePassword: (currentPassword: string, newPassword: string) => apiClient.request<void>({
    url: '/api/auth/change-password',
    method: 'POST',
    includeRequestId: false,
    body: { current_password: currentPassword, new_password: newPassword },
  }),
  logout: () => apiClient.request<void>({ url: '/api/auth/logout', method: 'POST' }),
}
