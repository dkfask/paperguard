import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../src/shared/api/client'
import { authApi } from '../src/features/auth/api'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('typed API client', () => {
  it('serializes object bodies and parses JSON responses', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    const result = await apiClient.request({
      url: '/api/example',
      method: 'POST',
      body: { name: 'PaperGuard' },
    })

    expect(result).toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledWith('/api/example', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ name: 'PaperGuard' }),
    }))
    expect(fetchMock.mock.calls[0][1].headers['content-type'] || fetchMock.mock.calls[0][1].headers['Content-Type']).toBe('application/json')
  })

  it.each([
    [401, { detail: '用户名或密码错误' }, '用户名或密码错误'],
    [503, { detail: { code: 'EMAIL_NOT_CONFIGURED', message: '邮件服务尚未配置' } }, '邮件服务尚未配置'],
  ])('shows only the public authentication message for HTTP %s', async (status, payload, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
      status, headers: { 'x-request-id': 'synthetic-request-id' },
    })))
    await expect(authApi.login({ username: 'fixture', password: 'wrong' })).rejects.toMatchObject({
      message, status, requestId: 'synthetic-request-id',
    })
  })

  it('preserves diagnostic IDs for authenticated business requests', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '没有权限' }), {
      status: 403, headers: { 'x-request-id': 'synthetic-request-id' },
    })))
    await expect(apiClient.request({ url: '/api/projects' })).rejects.toMatchObject({
      message: '没有权限（请求 ID: synthetic-request-id）', requestId: 'synthetic-request-id',
    })
  })

  it('omits the ID when a forced password-change session expires', async () => {
    vi.stubGlobal('location', { pathname: '/login' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '会话已过期' }), {
      status: 401, headers: { 'x-request-id': 'synthetic-request-id' },
    })))
    await expect(authApi.changePassword('OldFixturePassword123', 'NewFixturePassword456')).rejects.toMatchObject({
      message: '请先登录', status: 401, requestId: 'synthetic-request-id',
    })
  })
})
