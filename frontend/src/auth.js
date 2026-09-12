import { pinia } from './app/pinia'
import { useAuthStore } from './stores/auth'

// Compatibility facade for legacy views. New modules should import useAuthStore.
export const auth = useAuthStore(pinia)

export function setAuth(token, user) {
  auth.setAuth(token, user)
}

export function clearAuth() {
  auth.clearAuth()
}

export function isManager() {
  return auth.canManage()
}
