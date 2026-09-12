import { beforeEach, describe, expect, it } from 'vitest'
import { auth, clearAuth, setAuth } from '../src/auth'

describe('auth compatibility facade', () => {
  beforeEach(() => clearAuth())

  it('persists and exposes the authenticated user through the Pinia store', () => {
    const user = { id: 'u1', username: 'student', role: 'student', display_name: '测试用户' }
    setAuth('token-1', user)

    expect(auth.token).toBe('token-1')
    expect(auth.user).toEqual(user)
    expect(sessionStorage.getItem('pg_token')).toBe('token-1')
    expect(JSON.parse(sessionStorage.getItem('pg_user'))).toEqual(user)
  })

  it('clears both session state and local recovery entries on logout', () => {
    localStorage.setItem('pg_recovery:test', '{}')
    setAuth('token-1', { id: 'u1', username: 'student', role: 'student' })
    clearAuth()

    expect(auth.token).toBe('')
    expect(auth.user).toBeNull()
    expect(sessionStorage.getItem('pg_token')).toBeNull()
    expect(localStorage.getItem('pg_recovery:test')).toBeNull()
  })
})
