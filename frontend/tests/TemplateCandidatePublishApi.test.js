import { afterEach, describe, expect, it, vi } from 'vitest'
import { publishPaperTemplateFromCandidate } from '../src/api.js'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('template candidate publication API', () => {
  it('publishes only a confirmed candidate through the parse publish route', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ template: { id: 'paper-template-1' } }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(publishPaperTemplateFromCandidate('candidate-1')).resolves.toEqual({
      template: { id: 'paper-template-1' },
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/templates/parse/publish', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ candidate_id: 'candidate-1' }),
    }))
  })

  it('surfaces server-side candidate publication gates', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '非预览试排未通过' }), {
      status: 409,
      headers: { 'Content-Type': 'application/json' },
    })))

    await expect(publishPaperTemplateFromCandidate('candidate-1')).rejects.toMatchObject({
      message: '非预览试排未通过',
      status: 409,
    })
  })
})
