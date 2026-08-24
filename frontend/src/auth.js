import { reactive } from 'vue'

const TOKEN_KEY = 'pg_token'
const USER_KEY = 'pg_user'

const storage = window.sessionStorage

function loadUser() {
  try { return JSON.parse(storage.getItem(USER_KEY)) || null } catch { return null }
}

export const auth = reactive({
  token: storage.getItem(TOKEN_KEY) || '',
  user: loadUser(),
})

export function setAuth(token, user) {
  auth.token = token
  auth.user = user
  storage.setItem(TOKEN_KEY, token)
  storage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearAuth() {
  auth.token = ''
  auth.user = null
  storage.removeItem(TOKEN_KEY)
  storage.removeItem(USER_KEY)
}

export function isManager() {
  return !!auth.user && ['admin', 'teacher'].includes(auth.user.role)
}
