import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
const api = vi.hoisted(() => ({
  fetchProject: vi.fn(), fetchProjectExportJobs: vi.fn(), fetchProjectCapabilities: vi.fn(),
  downloadProjectExportReviewCandidate: vi.fn(), confirmProjectExportManualReview: vi.fn(),
  downloadProjectExportJob: vi.fn(), downloadProjectExportPdf: vi.fn(), updateProjectSectionsStructure: vi.fn(), updateProjectSection: vi.fn(), createProjectExportJob: vi.fn(),
}))
const role = vi.hoisted(() => ({ manager: true }))
vi.mock('../src/api.js', async () => {
  const actual = await vi.importActual('../src/api.js')
  return { ...Object.fromEntries(Object.keys(actual).map(key => [key, vi.fn().mockResolvedValue([])])), ...api }
})
vi.mock('../src/auth', () => ({ auth: { user: { id: 'teacher', role: 'teacher', status: 'active' } }, isManager: () => role.manager }))
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'p1' }, query: {} }), useRouter: () => ({ push: vi.fn() }), onBeforeRouteLeave: vi.fn() }))
vi.mock('../src/i18n', () => ({ useI18n: () => ({ lang: { value: 'zh' }, t: (key, params) => params?.status ? `${key} ${params.status}` : key }) }))
import Writer from '../src/views/Writer.vue'
const review = { status: 'required', output_sha256: 'sha-current', checklist: [{ id: 'image-font', description: '图中标注符号文字字号不大于图题的字号' }] }
function open() { return mount(Writer, { global: { stubs: { BaseModal: true, RouterLink: true, BlockEditor: true, WritingSpecificationPreview: true } } }) }
beforeEach(() => {
  vi.clearAllMocks()
  role.manager = true
  localStorage.clear()
  api.fetchProject.mockResolvedValue({ id: 'p1', title: '验收论文', revision: 3, sections: [{ key: 'body', type: 'body', title: '正文', paragraphs: [{ id: 'p1', text: '正文内容' }] }], meta: {}, template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections: [{ key: 'body', type: 'body', title: '正文' }] } })
  api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true, template_migration_enabled: true })
  api.fetchProjectExportJobs.mockResolvedValue([
    { id: 'old', project_revision: 2, status: 'succeeded', manual_review: { ...review, checklist: [{ id: 'old', description: '旧文件' }] } },
    { id: 'current', project_revision: 3, status: 'succeeded', filename: 'paper.docx', output_check_status: 'pass', output_check_count: 10, manual_review: review },
  ])
})
afterEach(() => { vi.useRealTimers() })
describe('Writer pending automatic output checks', () => {
  it('keeps a staff draft editable while closed delivery and migration controls stay hidden', async () => {
    api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: false, template_migration_enabled: false })
    const wrapper = open(); await flushPromises()
    expect(wrapper.find('.writer-export-action').exists()).toBe(false)
    expect(wrapper.find('.writer-preview-action').exists()).toBe(false)
    expect(wrapper.find('button[title="writer.switchTemplateTitle"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('writer.personalDeliveryUnavailable')
    expect(api.fetchProjectExportJobs).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="writer-manual-review"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('shows actionable missing fields without offering to bypass the export gate', async () => {
    api.createProjectExportJob.mockRejectedValue({ missing: ['封面「论文题目」未填写'] })
    const wrapper = open(); await flushPromises()
    await wrapper.get('button[aria-label="writer.export"]').trigger('click'); await flushPromises()
    expect(wrapper.get('.missing-panel').text()).toContain('封面「论文题目」未填写')
    expect(wrapper.get('.missing-panel').text()).not.toContain('writer.exportAnyway')
    expect(wrapper.get('.missing-panel').findAll('button')).toHaveLength(1)
    wrapper.unmount()
  })
  it('shows the current pending check without asking the teacher to confirm it', async () => {
    const wrapper = open(); await flushPromises()
    const panel = wrapper.get('[data-test="writer-manual-review"]')
    expect(wrapper.text()).toContain('writer.automaticCheckPendingTitle')
    expect(wrapper.text()).toContain('writer.exportCheckUnverifiable')
    expect(wrapper.text()).not.toContain('writer.exportGenerated')
    expect(panel.text()).toContain('writer.automaticCheckPendingDetail')
    expect(panel.text()).toContain('writer.automaticCheckPendingRules')
    expect(panel.text()).toContain('图中标注符号文字字号不大于图题的字号')
    expect(panel.text()).not.toContain('旧文件')
    expect(panel.findAll('input')).toHaveLength(0)
    expect(panel.findAll('button').map(button => button.text())).toEqual(['writer.viewPendingChecks'])
    const guide = panel.find('[data-test="figure-typography-guide"]')
    expect(guide.exists()).toBe(true)
    expect(guide.attributes('data-guide-variant')).toBe('blocked')
    expect(api.downloadProjectExportReviewCandidate).not.toHaveBeenCalled()
    expect(api.confirmProjectExportManualReview).not.toHaveBeenCalled()
    expect(api.downloadProjectExportJob).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('omits the figure typography guide when export blocking is unrelated to image fonts', async () => {
    api.fetchProjectExportJobs.mockResolvedValue([
      {
        id: 'current',
        project_revision: 3,
        status: 'succeeded',
        filename: 'paper.docx',
        output_check_status: 'pass',
        output_check_count: 10,
        manual_review: {
          status: 'required',
          output_sha256: 'sha-current',
          checklist: [{ id: 'office-review:1', description: '在目标版本 Word/WPS 打开成品，核对分页、目录。' }],
        },
      },
    ])
    const wrapper = open(); await flushPromises()
    const panel = wrapper.get('[data-test="writer-manual-review"]')
    expect(panel.text()).toContain('核对分页、目录')
    expect(panel.find('[data-test="figure-typography-guide"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('does not offer download based on a legacy manual confirmation', async () => {
    api.fetchProjectExportJobs.mockResolvedValue([
      { id: 'current', project_revision: 3, status: 'succeeded', filename: 'current.docx', output_check_status: 'pass', manual_review: { status: 'confirmed' } },
    ])
    const wrapper = open(); await flushPromises()
    const panel = wrapper.get('[data-test="writer-manual-review"]')
    expect(panel.findAll('input, button')).toHaveLength(0)
    expect(wrapper.find('[data-test="writer-download-ready"]').exists()).toBe(false)
    expect(api.confirmProjectExportManualReview).not.toHaveBeenCalled()
    expect(api.downloadProjectExportJob).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('restores a current automatically checked file for student download after refresh', async () => {
    role.manager = false
    api.fetchProjectExportJobs.mockResolvedValue([
      { id: 'old', project_revision: 2, status: 'succeeded', manual_review: { status: 'confirmed' } },
      { id: 'current', project_revision: 3, status: 'succeeded', filename: 'current.docx', output_check_status: 'pass', manual_review: { status: 'not_required' } },
    ])
    const wrapper = open(); await flushPromises()
    expect(wrapper.find('[data-test="writer-manual-review"]').exists()).toBe(false)
    await wrapper.get('[data-test="writer-download-ready"]').trigger('click'); await flushPromises()
    expect(api.downloadProjectExportJob).toHaveBeenCalledWith('p1', 'current', 'current.docx')
    wrapper.unmount()
  })

  it('offers optional PDF independently from the checked Word download', async () => {
    role.manager = false
    api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true, optional_pdf_enabled: true })
    api.fetchProjectExportJobs.mockResolvedValue([
      { id: 'current', project_revision: 3, status: 'succeeded', filename: 'current.docx', output_check_status: 'pass', manual_review: { status: 'not_required' } },
    ])
    const wrapper = open(); await flushPromises()
    await wrapper.get('[data-test="writer-download-pdf"]').trigger('click'); await flushPromises()
    expect(api.downloadProjectExportPdf).toHaveBeenCalledWith('p1', 'current', 'current.pdf')
    expect(api.downloadProjectExportJob).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="writer-download-ready"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps the Word download available when optional PDF generation fails', async () => {
    api.fetchProjectCapabilities.mockResolvedValue({ formal_delivery_enabled: true, optional_pdf_enabled: true })
    api.fetchProjectExportJobs.mockResolvedValue([
      { id: 'current', project_revision: 3, status: 'succeeded', filename: 'current.docx', output_check_status: 'pass', manual_review: { status: 'not_required' } },
    ])
    api.downloadProjectExportPdf.mockRejectedValueOnce(new Error('PDF 暂不可用'))
    const wrapper = open(); await flushPromises()
    await wrapper.get('[data-test="writer-download-pdf"]').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('PDF 暂不可用')
    await wrapper.get('[data-test="writer-download-ready"]').trigger('click'); await flushPromises()
    expect(api.downloadProjectExportJob).toHaveBeenCalledWith('p1', 'current', 'current.docx')
    wrapper.unmount()
  })

  it('does not offer student confirmation or restore stale files', async () => {
    role.manager = false
    const wrapper = open(); await flushPromises()
    expect(wrapper.get('[data-test="writer-manual-review"]').findAll('button').map(button => button.text())).toEqual(['writer.viewPendingChecks'])
    expect(wrapper.find('[data-test="writer-download-ready"]').exists()).toBe(false)
    wrapper.unmount()
    api.fetchProjectExportJobs.mockResolvedValue([{ id: 'old', project_revision: 2, status: 'succeeded', manual_review: { status: 'confirmed' } }])
    const refreshed = open(); await flushPromises()
    expect(refreshed.find('[data-test="writer-manual-review"]').exists()).toBe(false)
    expect(refreshed.find('[data-test="writer-download-ready"]').exists()).toBe(false)
    refreshed.unmount()
  })

  it('clears the pending status when the user edits the manuscript', async () => {
    const wrapper = open(); await flushPromises()
    expect(wrapper.find('[data-test="writer-manual-review"]').exists()).toBe(true)
    await wrapper.findAll('.nav-row').find(row => row.text().includes('正文')).trigger('click')
    await wrapper.get('textarea').setValue('继续编辑的正文')
    expect(api.downloadProjectExportJob).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="writer-manual-review"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="writer-download-ready"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('Writer reference save queue', () => {
  it('keeps new edits and assigned numbers in queued saves and local recovery', async () => {
    vi.useFakeTimers()
    const section = { key: 'references', type: 'references', title: '参考文献', required: true, paragraphs: [
      { id: 'ref-a', type: 'paragraph', text: '', reference: { kind: 'journal', authors: '作者甲', title: '题名甲' } },
    ] }
    api.fetchProject.mockResolvedValue({ id: 'p1', revision: 3, sections: [section], meta: {}, template_snapshot: { id: 't1', styles: {}, sections: [section], meta_fields: [] } })
    api.fetchProjectExportJobs.mockResolvedValue([])
    let first, second
    api.updateProjectSection.mockImplementationOnce(() => new Promise(resolve => { first = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { second = resolve }))
    const wrapper = open(); await flushPromises()
    await wrapper.findAll('.nav-row').find(row => row.text().includes('参考文献')).trigger('click')
    await wrapper.get('.reference-grid input').setValue('第一轮作者')
    await vi.advanceTimersByTimeAsync(801); await flushPromises()
    expect(api.updateProjectSection).toHaveBeenCalledTimes(1)
    await wrapper.get('.reference-grid input').setValue('保存期间继续编辑')
    await vi.advanceTimersByTimeAsync(801); await flushPromises()
    first({ revision: 4, sections: [{ ...section, paragraphs: [{ ...section.paragraphs[0], reference_number: 6 }] }] })
    await flushPromises()
    expect(api.updateProjectSection).toHaveBeenCalledTimes(2)
    const [, key, payload, revision] = api.updateProjectSection.mock.calls[1]
    expect(key).toBe('references')
    expect(revision).toBe(4)
    expect(payload.paragraphs[0]).toMatchObject({ reference_number: 6, reference: { authors: '保存期间继续编辑' } })
    const draft = JSON.parse(localStorage.getItem('pg_recovery:teacher:p1'))
    expect(draft.base_revision).toBe(4)
    expect(draft.snapshot.sections[0].paragraphs[0]).toMatchObject({ reference_number: 6, reference: { authors: '保存期间继续编辑' } })
    second({ revision: 5, sections: [{ ...section, paragraphs: [{ ...section.paragraphs[0], reference_number: 6 }] }] })
    await flushPromises()
    expect(wrapper.get('.reference-grid input').element.value).toBe('保存期间继续编辑')
    expect(wrapper.get('.reference-card strong').text()).toBe('[6]')
    expect(localStorage.getItem('pg_recovery:teacher:p1')).toBeNull()
    wrapper.unmount()
  })
})

describe('Writer heading level changes', () => {
  function projectWithHeading() {
    const sections = [
      { key: 'chapter-3', type: 'heading1', title: '第 3 章 系统分析', required: true, paragraphs: [{ id: 'chapter-text', type: 'paragraph', text: '章内正文' }] },
      { key: 'section-3-1', type: 'heading2', title: '3.1 系统功能分析', required: false, paragraphs: [{ id: 'body-1', type: 'paragraph', text: '这段正文必须保留' }] },
    ]
    return {
      id: 'p1', title: '验收论文', revision: 3, sections, meta: {},
      template_snapshot: { id: 't1', name: '模板', styles: {}, meta_fields: [], sections },
    }
  }

  it.each(['heading1', 'heading3'])('changes a heading2 to %s without losing its title or body', async (nextType) => {
    const source = projectWithHeading()
    api.fetchProject.mockResolvedValue(source)
    api.updateProjectSectionsStructure.mockImplementation(async (_id, sections, revision) => ({
      ...source,
      revision: revision + 1,
      sections: sections.map((item) => ({
        ...item,
        paragraphs: source.sections.find((section) => section.key === item.key)?.paragraphs || [],
      })),
    }))

    const wrapper = open()
    await flushPromises()
    await wrapper.findAll('.nav-row').find((row) => row.text().includes('3.1 系统功能分析')).trigger('click')
    await flushPromises()
    const selector = wrapper.get('[data-test="writer-heading-level"]')
    expect(selector.element.value).toBe('heading2')
    await selector.setValue(nextType)
    await flushPromises()

    expect(api.updateProjectSectionsStructure).toHaveBeenCalledOnce()
    const [, submitted, revision] = api.updateProjectSectionsStructure.mock.calls[0]
    expect(revision).toBe(3)
    expect(submitted.find((section) => section.key === 'section-3-1')).toMatchObject({
      key: 'section-3-1', type: nextType, title: '3.1 系统功能分析', required: false,
    })
    expect(submitted).toHaveLength(2)
    expect(wrapper.get('[data-test="writer-heading-level"]').element.value).toBe(nextType)
    expect(wrapper.find('textarea').element.value).toContain('这段正文必须保留')
    wrapper.unmount()
  })

  it('keeps the current heading level visible when saving the change fails', async () => {
    const source = projectWithHeading()
    api.fetchProject.mockResolvedValue(source)
    api.updateProjectSectionsStructure.mockRejectedValue(new Error('保存失败'))

    const wrapper = open()
    await flushPromises()
    await wrapper.findAll('.nav-row').find((row) => row.text().includes('3.1 系统功能分析')).trigger('click')
    await flushPromises()
    const selector = wrapper.get('[data-test="writer-heading-level"]')
    await selector.setValue('heading3')
    await flushPromises()

    expect(wrapper.get('[data-test="writer-heading-level"]').element.value).toBe('heading2')
    expect(wrapper.text()).toContain('保存失败')
    wrapper.unmount()
  })
})

describe('Writer heading hierarchy guidance', () => {
  it('shows affected descendants and invalid headings after saving a parent level change', async () => {
    const sections = [
      { key: 'chapter', type: 'heading1', title: '第一章', required: true, paragraphs: [{ id: 'p1', type: 'paragraph', text: '正文' }] },
      { key: 'section', type: 'heading2', title: '1.1 父标题', required: false, paragraphs: [{ id: 'p2', type: 'paragraph', text: '原正文' }] },
      { key: 'child-a', type: 'heading3', title: '1.1.1 子标题甲', required: false, paragraphs: [{ id: 'p3', type: 'paragraph', text: '甲正文' }] },
      { key: 'child-b', type: 'heading3', title: '1.1.2 子标题乙', required: false, paragraphs: [{ id: 'p4', type: 'paragraph', text: '乙正文' }] },
    ]
    const source = { id: 'p1', title: '论文', revision: 3, sections, meta: {}, template_snapshot: { id: 't1', styles: {}, meta_fields: [], sections } }
    api.fetchProject.mockResolvedValue(source)
    api.updateProjectSectionsStructure.mockImplementation(async (_id, values, revision) => ({
      ...source, revision: revision + 1,
      sections: values.map(item => ({ ...item, paragraphs: source.sections.find(section => section.key === item.key)?.paragraphs || [] })),
    }))
    const wrapper = open(); await flushPromises()
    await wrapper.findAll('.nav-row').find(row => row.text().includes('父标题')).trigger('click')
    await wrapper.get('[data-test="writer-heading-level"]').setValue('heading1'); await flushPromises()
    expect(wrapper.get('[data-test="writer-heading-affected"]').text()).toContain('1.1.1 子标题甲')
    expect(wrapper.get('[data-test="writer-heading-affected"]').text()).toContain('1.1.2 子标题乙')
    expect(wrapper.get('[data-test="writer-heading-issues"]').text()).toContain('1.1.1 子标题甲')
    expect(wrapper.get('[data-test="writer-heading-issues"]').text()).toContain('1.1.2 子标题乙')
    expect(wrapper.find('textarea').element.value).toContain('原正文')
    await wrapper.get('[data-test="writer-heading-issues"] button').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-test="writer-heading-level"]').element.value).toBe('heading3')
    wrapper.unmount()
  })
})
