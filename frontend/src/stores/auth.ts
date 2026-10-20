import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

export interface User {
  id: string
  username: string
  display_name?: string
  email?: string
  email_verified?: boolean
  password_set?: boolean
  onboarding_required?: boolean
  role: string
  status?: string
  teacher_id?: string | null
  must_change_password?: boolean
  organization_context?: Record<string, string>
  requested_role?: string | null
  requested_organization_context?: Record<string, string>
  role_application_status?: string
}

export interface LoginInput {
  username: string
  password: string
}

const TOKEN_KEY = 'pg_token'
const USER_KEY = 'pg_user'
const RECOVERY_PREFIX = 'pg_recovery:'

function getStorage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function readUser(storage: Storage | null): User | null {
  if (!storage) return null
  try {
    return JSON.parse(storage.getItem(USER_KEY) || 'null') as User | null
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', () => {
  const storage = getStorage()
  const token = ref(storage?.getItem(TOKEN_KEY) || '')
  const user = ref<User | null>(readUser(storage))
  const isAuthenticated = computed(() => Boolean(token.value && user.value))

  function setAuth(nextToken: string, nextUser: User) {
    token.value = nextToken
    user.value = nextUser
    storage?.setItem(TOKEN_KEY, nextToken)
    storage?.setItem(USER_KEY, JSON.stringify(nextUser))
  }

  function updateUser(nextUser: Partial<User>) {
    if (!user.value) return
    const updatedUser = { ...user.value, ...nextUser }
    user.value = updatedUser
    storage?.setItem(USER_KEY, JSON.stringify(updatedUser))
  }

  function updateAuth(nextToken: string, nextUser: User) {
    setAuth(nextToken, nextUser)
  }

  function clearAuth() {
    token.value = ''
    user.value = null
    storage?.removeItem(TOKEN_KEY)
    storage?.removeItem(USER_KEY)
    try {
      if (typeof localStorage !== 'undefined') {
        for (let index = localStorage.length - 1; index >= 0; index -= 1) {
          const key = localStorage.key(index)
          if (key?.startsWith(RECOVERY_PREFIX)) localStorage.removeItem(key)
        }
      }
    } catch {
      // Storage cleanup must never prevent logout.
    }
  }

  async function restore() {
    // Authentication is session-backed. The API client remains authoritative
    // and clears this state when the server rejects the token.
    if (!token.value || !user.value) clearAuth()
  }

  function canManage() {
    return Boolean(user.value && ['admin', 'teacher'].includes(user.value.role)
      && (user.value.role === 'admin' || user.value.status === 'active'))
  }

  return { token, user, isAuthenticated, setAuth, updateUser, updateAuth, clearAuth, restore, canManage }
})
