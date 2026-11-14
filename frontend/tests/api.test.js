import { afterEach, describe, it, expect, vi } from 'vitest'

afterEach(() => vi.unstubAllGlobals())

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

  it.each([
    ['updateProjectSection', ['p1', 'chapter', {}, 1]],
    ['updateProjectMeta', ['p1', {}, 1]],
    ['restoreProjectSnapshot', ['p1', {}, 1]],
  ])('preserves the revision-conflict code in %s', async (method, args) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 409,
      json: async () => ({ detail: { code: 'project_revision_conflict', message: '版本已变化', revision: 2 } }),
    }))
    const api = await import('../src/api.js')
    await expect(api[method](...args)).rejects.toMatchObject({
      status: 409, code: 'project_revision_conflict', revision: 2,
    })
  })
})
