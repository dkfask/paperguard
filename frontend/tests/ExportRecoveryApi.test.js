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

it('retains specific missing facts and unsupported checks for homepage exports', async () => {
  const { exportProject } = await import('../src/api.js')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: {
    message: '无法交付 DOCX', missing: ['参考文献 [2] 缺少作者'], manual_checks: ['封面规则尚未自动核验'],
  } }), { status: 409, headers: { 'Content-Type': 'application/json' } })))
  await expect(exportProject('p1')).rejects.toMatchObject({
    message: '无法交付 DOCX', missing: ['参考文献 [2] 缺少作者'], manualChecks: ['封面规则尚未自动核验'],
  })
})
