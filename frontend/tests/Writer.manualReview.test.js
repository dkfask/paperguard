import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
const api = vi.hoisted(() => ({
  fetchProject: vi.fn(), fetchProjectExportJobs: vi.fn(),
  downloadProjectExportReviewCandidate: vi.fn(), confirmProjectExportManualReview: vi.fn(),
  downloadProjectExportJob: vi.fn(),
  updateProjectSectionsStructure: vi.fn(),
}))
vi.mock('../src/api.js', async () => {
  const actual = await vi.importActual('../src/api.js')
  return { ...Object.fromEntries(Object.keys(actual).map(key => [key, vi.fn().mockResolvedValue([])])), ...api }
})
vi.mock('../src/auth', () => ({ auth: { user: { id: 'teacher', role: 'teacher', status: 'active' } }, isManager: () => true }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'p1' }, query: {} }), useRouter: () => ({ push: vi.fn() }), onBeforeRouteLeave: vi.fn() }))
vi.mock('../src/i18n', () => ({ useI18n: () => ({ lang: { value: 'zh' }, t: key => key }) }))
import Writer from '../src/views/Writer.vue'
const review = { status: 'required', output_sha256: 'sha-current', checklist: [{ id: 'page', description: '核对最终分页' }] }
function open() { return mount(Writer, { global: { stubs: { BaseModal: true, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } }) }
beforeEach(() => {
  vi.clearAllMocks()
  api.fetchProject.mockResolvedValue({ id: 'p1', title: '验收论文', revision: 3, sections: [{ key: 'body', type: 'body', title: '正文', paragraphs: [{ id: 'p1', text: '正文内容' }] }], meta: {}, template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections: [{ key: 'body', type: 'body', title: '正文' }] } })
  api.fetchProjectExportJobs.mockResolvedValue([
    { id: 'old', project_revision: 2, is_current_for_project: false, status: 'succeeded', manual_review: { ...review, checklist: [{ id: 'old', description: '旧文件' }] } },
    { id: 'current', project_revision: 3, is_current_for_project: true, status: 'succeeded', filename: 'paper.docx', output_check_status: 'pass', output_check_count: 10, manual_review: review },
  ])
})
describe('Writer pending output review', () => {
  it('restores only the current revision and requires candidate download before confirmation', async () => {
    api.downloadProjectExportReviewCandidate.mockResolvedValue({ outputSha256: 'sha-current' })
    api.confirmProjectExportManualReview.mockResolvedValue({ manual_review: { status: 'confirmed' }, output_check_status: 'pass', output_check_count: 10 })
    const wrapper = open(); await flushPromises()
    const panel = wrapper.get('[data-test="writer-manual-review"]')
    expect(wrapper.text()).toContain('writer.exportCandidateGenerated')
    expect(wrapper.text()).not.toContain('writer.exportGenerated')
    expect(panel.text()).toContain('核对最终分页')
    expect(panel.text()).not.toContain('旧文件')
    expect(api.downloadProjectExportJob).not.toHaveBeenCalled()
    expect(panel.get('input').attributes('disabled')).toBeDefined()
    await panel.findAll('button')[0].trigger('click'); await flushPromises()
    await panel.get('input').setValue(true)
    await panel.findAll('button')[1].trigger('click'); await flushPromises()
    expect(api.confirmProjectExportManualReview).toHaveBeenCalledWith('p1', 'current', { output_sha256: 'sha-current', confirmed_check_ids: ['page'] })
    expect(api.downloadProjectExportJob).toHaveBeenCalledWith('p1', 'current', 'paper.docx')
    wrapper.unmount()
  })
  it('keeps confirmation unavailable if downloaded bytes have another digest', async () => {
    api.downloadProjectExportReviewCandidate.mockResolvedValue({ outputSha256: 'wrong' })
    const wrapper = open(); await flushPromises()
    const panel = wrapper.get('[data-test="writer-manual-review"]')
    await panel.findAll('button')[0].trigger('click'); await flushPromises()
    expect(panel.get('input').attributes('disabled')).toBeDefined()
    expect(api.confirmProjectExportManualReview).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('restores and downloads the current confirmed export after reopening Writer', async () => {
    api.fetchProjectExportJobs.mockResolvedValue([
      { id: 'approved', is_current_for_project: true, status: 'succeeded', filename: 'approved.docx', manual_review: { ...review, status: 'confirmed' }, output_check_status: 'pass', output_check_count: 12 },
    ])
    const wrapper = open(); await flushPromises()
    const download = wrapper.get('[data-test="writer-confirmed-export"] button')
    await download.trigger('click'); await flushPromises()
    expect(api.downloadProjectExportJob).toHaveBeenCalledWith('p1', 'approved', 'approved.docx')
    wrapper.unmount()
  })
  it.each(['required', 'confirmed'])('clears a %s export review after a section move', async (reviewStatus) => {
    const sections = [
      { key: 'body', type: 'body', title: '正文', required: false, paragraphs: [] },
      { key: 'appendix', type: 'body', title: '附录', required: false, paragraphs: [] },
    ]
    const project = {
      id: 'p1', title: '验收论文', revision: 3, sections, meta: {},
      template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections },
    }
    api.fetchProject.mockResolvedValue(project)
    api.fetchProjectExportJobs.mockResolvedValue([
      { id: 'stale-after-move', project_revision: 3, is_current_for_project: true, status: 'succeeded', filename: 'paper.docx', manual_review: { ...review, status: reviewStatus } },
    ])
    api.updateProjectSectionsStructure.mockImplementation(async (_projectId, structure, baseRevision) => ({
      ...project,
      revision: baseRevision + 1,
      sections: structure.map((section) => ({ ...section, paragraphs: [] })),
    }))
    const wrapper = open(); await flushPromises()
    expect(wrapper.get(reviewStatus === 'required' ? '[data-test="writer-manual-review"]' : '[data-test="writer-confirmed-export"]')).toBeTruthy()
    await wrapper.get('[aria-label="writer.moveDown「正文」"]').trigger('click'); await flushPromises()
    expect(api.updateProjectSectionsStructure).toHaveBeenCalledOnce()
    expect(wrapper.find('[data-test="writer-manual-review"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="writer-confirmed-export"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('does not restore a pending review for a stale template snapshot at the same project revision', async () => {
    api.fetchProjectExportJobs.mockResolvedValue([
      { id: 'stale-template', project_revision: 3, is_current_for_project: false, status: 'succeeded', filename: 'paper.docx', manual_review: review },
    ])
    const wrapper = open(); await flushPromises()
    expect(wrapper.find('[data-test="writer-manual-review"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="writer-confirmed-export"]').exists()).toBe(false)
    expect(api.downloadProjectExportReviewCandidate).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
