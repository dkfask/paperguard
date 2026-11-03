import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  fetchProject: vi.fn(), fetchProjectCapabilities: vi.fn(), fetchProjectExportJobs: vi.fn(), fetchProjectExportJob: vi.fn(), updateProjectSection: vi.fn(),
  createProjectExportJob: vi.fn(), retryProjectExportJob: vi.fn(), downloadProjectExportJob: vi.fn(),
}))
vi.mock('../src/api.js', async () => {
  const actual = await vi.importActual('../src/api.js')
  return { ...Object.fromEntries(Object.keys(actual).map(key => [key, vi.fn().mockResolvedValue([])])), ...api }
})
vi.mock('../src/auth', () => ({ auth: { user: { id: 'student', role: 'student', status: 'active' } }, isManager: () => false }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'p1' }, query: {} }), useRouter: () => ({ push: vi.fn() }), onBeforeRouteLeave: vi.fn() }))
vi.mock('../src/i18n', () => ({ useI18n: () => ({ lang: { value: 'zh' }, t: key => key }) }))

import Writer from '../src/views/Writer.vue'

let wrapper
beforeEach(() => {
  vi.resetAllMocks()
  localStorage.clear()
  api.fetchProject.mockResolvedValue({ id: 'p1', title: '论文', revision: 3, meta: {},
    sections: [{ key: 'body', type: 'body', title: '正文', paragraphs: [{ id: 'p1', text: '正文内容' }] }],
    template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections: [{ key: 'body', type: 'body', title: '正文' }] } })
  api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true })
  api.fetchProjectExportJobs.mockResolvedValue([])
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.useRealTimers() })

async function openAndExport() {
  wrapper = mount(Writer, { global: { stubs: { BaseModal: true, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
  await flushPromises()
  await wrapper.get('button[aria-label="writer.export"]').trigger('click')
  await flushPromises()
}

describe('Writer export recovery', () => {
  it('saves an edited legacy reference with its inline runs through Writer', async () => {
    const paragraph = { id: 'ref', type: 'paragraph', text: '作者. 学报[J]', inline_runs: [
      { text: '作者. ' }, { text: '学报', italic: true }, { text: '[J]' },
    ] }
    api.fetchProject.mockResolvedValue({ id: 'p1', title: '论文', revision: 3, meta: {},
      sections: [{ key: 'references', type: 'references', title: '参考文献', paragraphs: [paragraph] }],
      template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections: [{ key: 'references', type: 'references', title: '参考文献' }] } })
    api.updateProjectSection.mockImplementation(async (_id, _key, payload) => ({ revision: 4,
      sections: [{ key: 'references', type: 'references', paragraphs: payload.paragraphs }] }))
    wrapper = mount(Writer, { global: { stubs: { BaseModal: true, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
    await flushPromises()
    await wrapper.findAll('.nav-row').find(row => row.text().includes('参考文献')).trigger('click')
    await flushPromises()
    vi.useFakeTimers()
    await wrapper.get('.reference-card textarea').setValue('作者甲. 学报[J]')
    await vi.advanceTimersByTimeAsync(800)
    await flushPromises()
    expect(api.updateProjectSection).toHaveBeenCalledOnce()
    const [projectId, sectionKey, payload, revision] = api.updateProjectSection.mock.calls[0]
    expect([projectId, sectionKey, revision]).toEqual(['p1', 'references', 3])
    expect(payload.paragraphs[0].text).toBe('作者甲. 学报[J]')
    expect(payload.paragraphs[0].inline_runs.map(run => run.text).join('')).toBe(payload.paragraphs[0].text)
    expect(payload.paragraphs[0].inline_runs[1]).toMatchObject({ text: '学报', italic: true })
  })

  it('restores and downloads an existing checked export after loading a read-only project', async () => {
    api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true,
      manuscript_import_enabled: false, template_migration_enabled: false })
    api.fetchProjectExportJobs.mockResolvedValue([{ id: 'saved-task', project_revision: 3,
      status: 'succeeded', filename: '论文.docx', output_check_status: 'pass',
      manual_review: { status: 'not_required' } }])
    wrapper = mount(Writer, { global: { stubs: { BaseModal: true, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
    await flushPromises()
    expect(api.fetchProjectExportJobs).toHaveBeenCalledWith('p1')
    expect(wrapper.find('.writer-import-action').exists()).toBe(false)
    await wrapper.get('[data-test="writer-download-ready"]').trigger('click')
    await flushPromises()
    expect(api.downloadProjectExportJob).toHaveBeenCalledWith('p1', 'saved-task', '论文.docx')
  })

  it('keeps waiting beyond one minute and downloads the completed task', async () => {
    wrapper = mount(Writer, { global: { stubs: { BaseModal: true, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
    await flushPromises()
    vi.useFakeTimers()
    api.createProjectExportJob.mockResolvedValue({ id: 'slow-task', status: 'running', total_timeout_seconds: 900 })
    let checks = 0
    api.fetchProjectExportJob.mockImplementation(async () => {
      checks += 1
      return checks < 32 ? { id: 'slow-task', status: 'running' }
        : { id: 'slow-task', status: 'succeeded', filename: '论文.docx', output_check_status: 'pass' }
    })
    await wrapper.get('button[aria-label="writer.export"]').trigger('click')
    await vi.advanceTimersByTimeAsync(64_000)
    expect(checks).toBe(32)
    expect(api.downloadProjectExportJob).toHaveBeenCalledOnce()
    expect(api.downloadProjectExportJob).toHaveBeenCalledWith('p1', 'slow-task', '论文.docx')
  })

  it('stops waiting when the writer is unmounted', async () => {
    wrapper = mount(Writer, { global: { stubs: { BaseModal: true, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } })
    await flushPromises()
    vi.useFakeTimers()
    api.createProjectExportJob.mockResolvedValue({ id: 'slow-task', status: 'running', total_timeout_seconds: 900 })
    api.fetchProjectExportJob.mockResolvedValue({ id: 'slow-task', status: 'running' })
    await wrapper.get('button[aria-label="writer.export"]').trigger('click')
    await vi.advanceTimersByTimeAsync(2000)
    expect(api.fetchProjectExportJob).toHaveBeenCalledOnce()
    wrapper.unmount()
    wrapper = null
    await vi.advanceTimersByTimeAsync(10_000)
    expect(api.fetchProjectExportJob).toHaveBeenCalledOnce()
    expect(api.downloadProjectExportJob).not.toHaveBeenCalled()
  })

  it.each(['PROJECT_EXPORT_QUEUE_STATE_MISSING', 'PROJECT_EXPORT_QUEUE_STATE_INVALID', 'PROJECT_EXPORT_ATTEMPTS_EXHAUSTED'])(
    'offers a new export instead of retrying unrecoverable task %s', async code => {
      api.createProjectExportJob.mockResolvedValue({ id: 'old-task', status: 'failed', error_code: code })
      await openAndExport()
      expect(wrapper.get('.error-banner').text()).toContain('writer.exportRetryUnavailable')
      expect(wrapper.get('.error-banner').find('button').exists()).toBe(false)
      expect(api.retryProjectExportJob).not.toHaveBeenCalled()
      expect(api.downloadProjectExportJob).not.toHaveBeenCalled()
    },
  )

  it('keeps retry for ordinary failures but stops offering it after a conflicting retry', async () => {
    api.createProjectExportJob.mockResolvedValue({ id: 'old-task', status: 'failed', error_code: 'TEMPORARY_FAILURE' })
    api.retryProjectExportJob.mockRejectedValue(Object.assign(new Error('导出任务队列状态不允许重试'), { status: 409 }))
    await openAndExport()
    await wrapper.get('.error-banner button').trigger('click')
    await flushPromises()
    expect(wrapper.get('.error-banner').text()).toContain('writer.exportRetryUnavailable')
    expect(wrapper.get('.error-banner').find('button').exists()).toBe(false)
    expect(api.retryProjectExportJob).toHaveBeenCalledOnce()
  })

  it('does not retain an old retry action when a new export cannot be created', async () => {
    api.createProjectExportJob.mockResolvedValueOnce({ id: 'old-task', status: 'failed', error_code: 'TEMPORARY_FAILURE' })
      .mockRejectedValueOnce(new Error('内容需要补充'))
    await openAndExport()
    expect(wrapper.get('.error-banner').find('button').exists()).toBe(true)
    await wrapper.get('button[aria-label="writer.export"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.error-banner').text()).toContain('内容需要补充')
    expect(wrapper.get('.error-banner').find('button').exists()).toBe(false)
  })
})
