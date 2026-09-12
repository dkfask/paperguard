import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../src/shared/api/client'

afterEach(() => vi.restoreAllMocks())

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
})
