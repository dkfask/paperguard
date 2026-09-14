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
    /** 「请先修改初始密码」弹窗每次登录流程只提示一次 */
    passwordNoticeShown: false,
    /** 「绑定邮箱设置密码」引导每次登录只提示一次 */
    passwordHintShown: false,
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token && state.user),
  },
  actions: {
    setAuth(token: string, user: User) {
      this.token = token
      this.user = user
      this.bindTicket = ''
      this.passwordNoticeShown = false
      this.passwordHintShown = false
      uni.setStorageSync(TOKEN_KEY, token)
      uni.setStorageSync(USER_KEY, user)
    },
    updateUser(user: User) {
      this.user = user
      uni.setStorageSync(USER_KEY, user)
    },
    updateAuth(token: string, user: User) {
      this.token = token
      this.user = user
      uni.setStorageSync(TOKEN_KEY, token)
      uni.setStorageSync(USER_KEY, user)
    },
    setBindTicket(ticket: string) {
      this.bindTicket = ticket
    },
    markPasswordHintShown() {
      this.passwordHintShown = true
    },
    markPasswordNoticeShown() {
      this.passwordNoticeShown = true
    },
    clear() {
      this.token = ''
      this.user = null
      this.bindTicket = ''
      this.passwordNoticeShown = false
      this.passwordHintShown = false
      uni.removeStorageSync(TOKEN_KEY)
      uni.removeStorageSync(USER_KEY)
    },
  },
})
