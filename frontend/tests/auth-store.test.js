import { beforeEach, describe, expect, it } from 'vitest'
import { auth, clearAuth, setAuth } from '../src/auth'

describe('auth compatibility facade', () => {
  beforeEach(() => { clearAuth(); localStorage.clear() })

  it('persists and exposes the authenticated user through the Pinia store', () => {
    const user = { id: 'u1', username: 'student', role: 'student', display_name: '测试用户' }
    setAuth('token-1', user)

    expect(auth.token).toBe('token-1')
    expect(auth.user).toEqual(user)
    expect(sessionStorage.getItem('pg_token')).toBe('token-1')
    expect(JSON.parse(sessionStorage.getItem('pg_user'))).toEqual(user)
  })

  it('clears the session without deleting unsaved drafts for any user', () => {
    localStorage.setItem('pg_recovery:u1:p1', '{"draft":"未保存"}')
    localStorage.setItem('pg_recovery:u2:p2', '{"draft":"另一个用户"}')
    setAuth('token-1', { id: 'u1', username: 'student', role: 'student' })
    clearAuth()

    expect(auth.token).toBe('')
    expect(auth.user).toBeNull()
    expect(sessionStorage.getItem('pg_token')).toBeNull()
    expect(localStorage.getItem('pg_recovery:u1:p1')).toBe('{"draft":"未保存"}')
    expect(localStorage.getItem('pg_recovery:u2:p2')).toBe('{"draft":"另一个用户"}')
  })

  it('keeps recovery drafts when an unauthenticated route restores the session', async () => {
    localStorage.setItem('pg_recovery:u1:p1', '{"draft":"待恢复"}')
    await auth.restore()
    expect(localStorage.getItem('pg_recovery:u1:p1')).toBe('{"draft":"待恢复"}')
  })
})
