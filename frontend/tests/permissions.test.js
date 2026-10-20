import { describe, expect, it } from 'vitest'
import { homePath } from '../src/shared/auth/permissions'

describe('homePath', () => {
  it('sends the open management roles and teachers to the role workspace', () => {
    expect(homePath({ role: 'admin' })).toBe('/workspace')
    expect(homePath({ role: 'teacher' })).toBe('/workspace')
    expect(homePath({ role: 'college_admin' })).toBe('/login')
  })

  it('keeps students on the writing home', () => {
    expect(homePath({ role: 'student' })).toBe('/')
    expect(homePath(null)).toBe('/')
  })
})
