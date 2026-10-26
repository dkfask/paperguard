import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  getDocumentProjectEditor: vi.fn(),
  fetchDocumentRenderJobs: vi.fn(),
  createDocumentRenderJob: vi.fn(),
  fetchDocumentRenderJob: vi.fn(),
  downloadDocumentRenderArtifact: vi.fn(),
  confirmDocumentRenderReview: vi.fn(),
  renderDocumentProject: vi.fn(),
  updateDocumentProject: vi.fn(),
  uploadDocumentProjectImage: vi.fn(),
  validateDocumentProject: vi.fn(),
}))
const route = vi.hoisted(() => ({ params: { id: 'project-1' } }))

vi.mock('../src/api.js', () => api)
vi.mock('../src/auth.js', () => ({ isManager: () => true }))
vi.mock('../src/i18n', () => ({
  useI18n: () => ({
  t: (key, params = {}) => key === 'documentProjects.saved' ? `saved ${params.revision}` : key === 'documentProjects.blockKind.paragraph' ? '段落' : key === 'writer.exportFormatStatus' ? `${key} ${params.status} ${params.count}` : key,
  }),
}))
vi.mock('vue-router', () => ({ useRoute: () => route }))

import DocumentProjectEditor from '../src/views/DocumentProjectEditor.vue'

describe('DocumentProjectEditor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getDocumentProjectEditor.mockResolvedValue({
      project: {
        id: 'project-1', title: '我的报告', revision: 2,
        template_version_id: 'template@1', spec_version_id: 'spec@1',
        renderer_version: 'generic-docx-v1', rule_engine_version: 'generic-check-v1',
        field_values: { summary: '', category: '', approved: false, count: 0 }, sections: { intro: [{ id: 'block-1', kind: 'paragraph', text: '正文', data: {}, order: 0 }] },
      },
      package: { document_type_id: 'lab-report' },
      template: { name: '实验报告模板' },
      spec_version: { rules: [] },
      editor_schema: {
        fields: [
          { key: 'summary', label: '摘要', type: 'textarea', required: true, constraints: {} },
          { key: 'category', label: '类别', type: 'select', required: false, constraints: { options: ['A', 'B'] } },
          { key: 'count', label: '数量', type: 'number', required: false, constraints: { min: 0, max: 10 } },
          { key: 'approved', label: '已确认', type: 'checkbox', required: false, constraints: {} },
        ],
        sections: [{ key: 'intro', title: '引言', kind: 'content', content_schema: {} }],
      },
    })
    api.renderDocumentProject.mockResolvedValue(undefined)
    api.fetchDocumentRenderJobs.mockResolvedValue([])
  })

  it('exports the current generic document project', async () => {
    const wrapper = mount(DocumentProjectEditor, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    await wrapper.find('.export-action').trigger('click')
    await flushPromises()

    expect(api.renderDocumentProject).toHaveBeenCalledWith('project-1')
  })

  it('keeps review candidates separate until download and every item is confirmed', async () => {
    const review = { status: 'required', output_sha256: 'sha-a', checklist: [{ id: 'page', description: '核对分页' }, { id: 'toc', description: '核对目录' }] }
    api.renderDocumentProject.mockRejectedValue(Object.assign(new Error('需要核查'), { manualReview: review }))
    api.createDocumentRenderJob.mockResolvedValue({ id: 'render-1', status: 'succeeded', manual_review: review })
    api.downloadDocumentRenderArtifact.mockResolvedValue({ outputSha256: 'sha-a' })
    api.confirmDocumentRenderReview.mockResolvedValue({ manual_review: { status: 'confirmed' }, artifact: { output_check_count: 12 } })
    const wrapper = mount(DocumentProjectEditor, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    await wrapper.get('.export-action').trigger('click')
    await flushPromises()
    expect(api.downloadDocumentRenderArtifact).not.toHaveBeenCalled()
    expect(wrapper.get('[data-test="document-review-confirm"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-test="document-review-download"]').trigger('click')
    await flushPromises()
    const checks = wrapper.findAll('[data-test="document-manual-review"] input')
    await checks[0].setValue(true)
    expect(wrapper.get('[data-test="document-review-confirm"]').attributes('disabled')).toBeDefined()
    await checks[1].setValue(true)
    await wrapper.get('[data-test="document-review-confirm"]').trigger('click')
    await flushPromises()
    expect(api.confirmDocumentRenderReview).toHaveBeenCalledWith('project-1', 'render-1', { output_sha256: 'sha-a', confirmed_check_ids: ['page', 'toc'] })
    expect(api.downloadDocumentRenderArtifact).toHaveBeenLastCalledWith('project-1', 'render-1')
    expect(wrapper.find('[data-test="document-manual-review"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('refuses a changed review candidate and clears review when content changes', async () => {
    const review = { status: 'required', output_sha256: 'sha-a', checklist: [{ id: 'page', description: '核对分页' }] }
    api.renderDocumentProject.mockRejectedValue(Object.assign(new Error('需要核查'), { manualReview: review }))
    api.createDocumentRenderJob.mockResolvedValue({ id: 'render-1', status: 'succeeded', manual_review: review })
    api.downloadDocumentRenderArtifact.mockResolvedValue({ outputSha256: 'sha-other' })
    const wrapper = mount(DocumentProjectEditor, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    await wrapper.get('.export-action').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="document-review-download"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('writer.manualReviewCandidateChanged')
    expect(wrapper.get('[data-test="document-review-confirm"]').attributes('disabled')).toBeDefined()
    expect(api.confirmDocumentRenderReview).not.toHaveBeenCalled()
    await wrapper.get('[data-test="document-field-summary"]').setValue('修改内容')
    expect(wrapper.find('[data-test="document-manual-review"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('restores the matching pending review after reopening without downloading it', async () => {
    api.fetchDocumentRenderJobs.mockResolvedValue([
      { id: 'old', project_revision: 1, status: 'succeeded', manual_review: { status: 'required', checklist: [{ id: 'old', description: 'old review' }] } },
      { id: 'current', project_revision: 2, status: 'succeeded', manual_review: { status: 'required', output_sha256: 'sha-current', checklist: [{ id: 'current', description: 'current review' }] } },
    ])
    const wrapper = mount(DocumentProjectEditor, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    expect(wrapper.get('[data-test="document-manual-review"]').text()).toContain('current review')
    expect(wrapper.get('[data-test="document-manual-review"]').text()).not.toContain('old review')
    expect(api.downloadDocumentRenderArtifact).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows generated, format-check and visual-review states separately', async () => {
    api.renderDocumentProject.mockResolvedValue({ status: 'unverifiable', count: 5 })
    const wrapper = mount(DocumentProjectEditor, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    await wrapper.find('.export-action').trigger('click')
    await flushPromises()

    const status = wrapper.get('.export-check-status')
    expect(status.text()).toContain('writer.exportGenerated')
    expect(status.text()).toContain('writer.exportCheckUnverifiable')
    expect(status.text()).toContain('writer.exportVisualPending')
    expect(status.text()).toContain('5')
    await wrapper.get('[data-test="document-field-summary"]').setValue('修改后的摘要')
    expect(wrapper.find('.export-check-status').exists()).toBe(false)
    wrapper.unmount()
  })

  it('saves pending edits before exporting and keeps newer edits if a save is in flight', async () => {
    const fixture = await api.getDocumentProjectEditor()
    let finishSave
    api.updateDocumentProject.mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve }))
    api.updateDocumentProject.mockImplementation(async (_, value) => ({ ...fixture.project, ...value, revision: value.base_revision + 1 }))
    api.validateDocumentProject.mockResolvedValue({ findings: [], not_checked: [] })
    api.renderDocumentProject.mockResolvedValue({ status: 'pass', count: 22 })
    const wrapper = mount(DocumentProjectEditor, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    const field = wrapper.get('[data-test="document-field-summary"]')
    await field.setValue('第一版摘要')
    await wrapper.find('.export-action').trigger('click')
    expect(api.renderDocumentProject).not.toHaveBeenCalled()
    expect(api.updateDocumentProject).toHaveBeenCalledWith('project-1', expect.objectContaining({field_values: expect.objectContaining({summary: '第一版摘要'})}))
    await field.setValue('第二版摘要')
    finishSave({ ...fixture.project, field_values: { ...fixture.project.field_values, summary: '第一版摘要' }, revision: 3 })
    await flushPromises()
    expect(api.updateDocumentProject).toHaveBeenLastCalledWith('project-1', expect.objectContaining({base_revision: 3, field_values: expect.objectContaining({summary: '第二版摘要'})}))
    expect(field.element.value).toBe('第二版摘要')
    expect(api.renderDocumentProject).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('does not export an old server revision when saving edits fails', async () => {
    api.updateDocumentProject.mockRejectedValue(new Error('保存失败'))
    const wrapper = mount(DocumentProjectEditor, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    await wrapper.get('[data-test="document-field-summary"]').setValue('尚未保存的摘要')
    await wrapper.find('.export-action').trigger('click')
    await flushPromises()
    expect(api.renderDocumentProject).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('保存失败')
    wrapper.unmount()
  })

  it('does not attach a completed export result to content edited during export', async () => {
    let finishExport
    api.renderDocumentProject.mockImplementationOnce(() => new Promise(resolve => { finishExport = resolve }))
    const wrapper = mount(DocumentProjectEditor, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    await wrapper.find('.export-action').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="document-field-summary"]').setValue('导出期间新输入')
    finishExport({ status: 'pass', count: 22 })
    await flushPromises()
    expect(wrapper.find('.export-check-status').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders schema field types and structured content block controls', async () => {
    const wrapper = mount(DocumentProjectEditor, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect(wrapper.get('[data-test="document-field-summary"]').element.tagName).toBe('TEXTAREA')
    expect(wrapper.get('[data-test="document-field-category"]').element.tagName).toBe('SELECT')
    expect(wrapper.get('[data-test="document-field-category"]').findAll('option')).toHaveLength(3)
    expect(wrapper.get('[data-test="document-field-count"]').attributes('type')).toBe('number')
    expect(wrapper.get('[data-test="document-field-approved"]').attributes('type')).toBe('checkbox')

    await wrapper.get('[data-test="block-kind-block-1"]').setValue('table')
    expect(wrapper.get('[data-test="document-table-editor"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="documentProjects.tableCell 1-1"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('localizes schema defaults while preserving custom template names', async () => {
    const fixture = await api.getDocumentProjectEditor()
    fixture.editor_schema.fields[0].label = 'Summary'
    fixture.editor_schema.fields[1].label = 'Category'
    fixture.editor_schema.sections[0] = { ...fixture.editor_schema.sections[0], key: 'body', title: 'Body' }
    fixture.project.sections.body = fixture.project.sections.intro
    delete fixture.project.sections.intro
    api.getDocumentProjectEditor.mockResolvedValue(fixture)
    const wrapper = mount(DocumentProjectEditor, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    expect(wrapper.text()).toContain('documentProjects.defaultField.summary')
    expect(wrapper.text()).toContain('documentProjects.defaultField.category')
    expect(wrapper.text()).toContain('documentProjects.defaultBody')
    expect(wrapper.text()).toContain('段落')
    expect(wrapper.text()).toContain('数量')
    expect(wrapper.get('.editor-subtitle').text()).toContain('实验报告模板')
    expect(wrapper.get('.editor-subtitle').text()).not.toContain('template@1')
    expect(wrapper.get('.editor-subtitle').text()).not.toContain('lab-report')
    wrapper.unmount()
  })
})
