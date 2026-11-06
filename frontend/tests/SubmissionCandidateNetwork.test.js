import { afterEach, describe, expect, it, vi } from 'vitest'
import { confirmSubmission, createSubmissionCandidate } from '../src/api.js'
import { currentLang } from '../src/i18n.js'

afterEach(() => {
  currentLang.value = 'zh'
  vi.unstubAllGlobals()
})

describe('submission precheck response errors', () => {
  it('explains a network failure without claiming a precheck result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(createSubmissionCandidate({ participant_id: 'p1' })).rejects.toMatchObject({
      message: '未能取得预检结果，请检查网络连接或稍后重试。',
    })
  })

  it('localizes a network failure in English', async () => {
    currentLang.value = 'en'
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(createSubmissionCandidate({ participant_id: 'p1' })).rejects.toMatchObject({
      message: 'The precheck result could not be obtained. Check your connection or try again later.',
    })
  })

  it('preserves the backend business error and request ID', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '当前阶段尚未开放提交' }), {
      status: 409,
      headers: { 'Content-Type': 'application/json', 'x-request-id': 'req-precheck-test' },
    })))

    await expect(createSubmissionCandidate({ participant_id: 'p1' })).rejects.toMatchObject({
      status: 409,
      requestId: 'req-precheck-test',
      message: '当前阶段尚未开放提交（请求 ID: req-precheck-test）',
    })
  })

  it('preserves a cancelled request', async () => {
    const cancelled = new DOMException('Cancelled', 'AbortError')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(cancelled))

    await expect(createSubmissionCandidate({ participant_id: 'p1' })).rejects.toBe(cancelled)
  })
})


describe('formal submission response errors', () => {
  it('explains that the result is unknown and the same request can be retried', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(confirmSubmission({ candidate_id: 'c1', idempotency_key: 'test-key' })).rejects.toMatchObject({
      message: '未能取得提交结果，请重新点击确认正式提交，系统会使用同一请求重试。',
    })
  })

  it('gives the same guidance when the successful response body is interrupted', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockRejectedValue(new TypeError('network error while reading the body')),
    }))

    await expect(confirmSubmission({ candidate_id: 'c1', idempotency_key: 'test-key' })).rejects.toMatchObject({
      message: '未能取得提交结果，请重新点击确认正式提交，系统会使用同一请求重试。',
    })
  })

  it('also treats an unreadable successful response as an unknown result', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{', { status: 200 })))

    await expect(confirmSubmission({ candidate_id: 'c1', idempotency_key: 'test-key' })).rejects.toMatchObject({
      message: '未能取得提交结果，请重新点击确认正式提交，系统会使用同一请求重试。',
    })
  })

  it('preserves the formal submission business error and request ID', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '预检结果已过期' }), {
      status: 409,
      headers: { 'Content-Type': 'application/json', 'x-request-id': 'req-submit-test' },
    })))

    await expect(confirmSubmission({ candidate_id: 'c1', idempotency_key: 'test-key' })).rejects.toMatchObject({
      status: 409,
      requestId: 'req-submit-test',
      message: '预检结果已过期（请求 ID: req-submit-test）',
    })
  })

  it('localizes the formal submission network guidance in English', async () => {
    currentLang.value = 'en'
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(confirmSubmission({ candidate_id: 'c1', idempotency_key: 'test-key' })).rejects.toMatchObject({
      message: 'The submission result could not be obtained. Click Confirm submission again to retry the same request.',
    })
  })
})
