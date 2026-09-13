import { defineStore } from 'pinia'
import type { User } from '../api/request'

const TOKEN_KEY = 'pg_mp_token'
const USER_KEY = 'pg_mp_user'

function readUser(): User | null {
  try {
    return uni.getStorageSync(USER_KEY) || null
  } catch {
    return null
  }
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    token: String(uni.getStorageSync(TOKEN_KEY) || ''),
    user: readUser() as User | null,
    bindTicket: '',
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token && state.user),
  },
  actions: {
    setAuth(token: string, user: User) {
      this.token = token
      this.user = user
      this.bindTicket = ''
      uni.setStorageSync(TOKEN_KEY, token)
      uni.setStorageSync(USER_KEY, user)
    },
    setBindTicket(ticket: string) {
      this.bindTicket = ticket
    },
    clear() {
      this.token = ''
      this.user = null
      this.bindTicket = ''
      uni.removeStorageSync(TOKEN_KEY)
      uni.removeStorageSync(USER_KEY)
    },
  },
})
