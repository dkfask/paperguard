import { afterEach, expect, it, vi } from 'vitest'
import { retryProjectExportJob } from '../src/api.js'

afterEach(() => vi.unstubAllGlobals())

it('preserves the conflict status and request ID when retry is no longer possible', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '导出任务队列状态不允许重试' }), {
    status: 409, headers: { 'Content-Type': 'application/json', 'x-request-id': 'recovery-test' },
  })))
  await expect(retryProjectExportJob('p1', 'old-task')).rejects.toMatchObject({
    status: 409, requestId: 'recovery-test', message: '导出任务队列状态不允许重试（请求 ID: recovery-test）',
  })
})
