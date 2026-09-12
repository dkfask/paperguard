import { describe, it, expect } from 'vitest'

describe('API Client', () => {
  it('should be importable', async () => {
    const { api } = await import('../src/api/client.js')
    expect(api).toBeDefined()
    expect(typeof api).toBe('function')
  })
})

describe('Auth API', () => {
  it('should export logoutApi', async () => {
    const { logoutApi } = await import('../src/api/auth.js')
    expect(logoutApi).toBeDefined()
    expect(typeof logoutApi).toBe('function')
  })
})

describe('Projects API', () => {
  it('should export project functions', async () => {
    const api = await import('../src/api/projects.js')
    expect(api.fetchProjects).toBeDefined()
    expect(api.createProject).toBeDefined()
    expect(api.updateProjectSection).toBeDefined()
  })
})
