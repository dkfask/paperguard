import { reactive } from 'vue'

const TOKEN_KEY = 'pg_token'
const USER_KEY = 'pg_user'

function loadUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY)) || null } catch { return null }
}

export const auth = reactive({
  token: localStorage.getItem(TOKEN_KEY) || '',
  user: loadUser(),
})

export function setAuth(token, user) {
  auth.token = token
  auth.user = user
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearAuth() {
  auth.token = ''
  auth.user = null
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function isManager() {
  return !!auth.user && ['admin', 'teacher'].includes(auth.user.role)
}
