import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  fetchTemplates: vi.fn(),
  deleteTemplateApi: vi.fn(),
  parseDocumentTemplate: vi.fn(),
  confirmDocumentTemplateCandidate: vi.fn(),
  publishPaperTemplateFromCandidate: vi.fn(),
  stripTemplateComments: vi.fn(),
}))
const router = vi.hoisted(() => ({ push: vi.fn() }))

vi.mock('../src/api.js', () => api)
vi.mock('../src/auth.js', () => ({
  auth: { user: { role: 'admin' } },
}))
vi.mock('../src/i18n', () => ({
  useI18n: () => ({
    lang: { value: 'zh' },
    t: (key, params = {}) => params.name ? `${key}:${params.name}` : key,
    formatDate: () => '2026/09/16',
  }),
}))
vi.mock('vue-router', () => ({ useRouter: () => router }))

import Templates from '../src/views/Templates.vue'

const template = {
  id: 'custom-template',
  name: '自定义模板',
  description: '用于删除回归测试',
  updated_at: '2026-09-16T00:00:00Z',
  section_count: 3,
}

const profileConfirmationStub = {
  props: ['template', 'candidate', 'dirty'],
  emits: ['published'],
  template: '<button type="button" data-test="profile-publish" :disabled="dirty" @click="$emit(\'published\', { candidate: candidate.prepared_candidate || candidate })">publish profile</button>',
}

function mountTemplates() {
  return mount(Templates, {
    global: {
      stubs: {
        teleport: true,
        AsyncState: true,
        RouterLink: { template: '<a><slot /></a>' },
        WritingProfileConfirmation: profileConfirmationStub,
      },
    },
  })
}

function templateCandidate(overrides = {}) {
  return {
    candidate_id: 'candidate-1',
    template_id: 'paper-template-1',
    filename: 'abstract-keywords.docx',
    version: '1',
    status: 'needs_confirmation',
    definition: {
      source_docx: 'template-source.docx',
      sections: [{ key: 'ch1', title: '第1章 绪论', kind: 'heading1', children: [] }],
      fields: [],
      styles: Object.fromEntries(['heading3', 'english_abstract', 'keywords', 'english_keywords', 'heading1', 'heading2'].map(key => [key, { font_en: 'Times New Roman', size_pt: 12 }])),
      extensions: { writing_specification: null, writing_specification_markdown: '' },
    },
    admission_assessment: {
      status: 'office_review_required',
      assessment_hash: 'candidate-assessment',
      auto_verifiable_constraints: [],
      blockers: [],
      manual_checks: [],
    },
    issues: [],
    ...overrides,
  }
}

describe('Templates', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.fetchTemplates.mockResolvedValue([template])
    api.deleteTemplateApi.mockResolvedValue(undefined)
    api.parseDocumentTemplate.mockResolvedValue(templateCandidate())
    api.confirmDocumentTemplateCandidate.mockImplementation(async candidate => ({ ...candidate, status: 'confirmed' }))
    api.publishPaperTemplateFromCandidate.mockResolvedValue({ template: { id: 'published-template' } })
  })

  it('opens the delete confirmation without throwing a page-level error', async () => {
    const wrapper = mountTemplates()
    await flushPromises()

    await expect(wrapper.get('button.icon-action.danger').trigger('click')).resolves.toBeUndefined()
    expect(wrapper.text()).toContain('templates.deleteTitle:自定义模板')
  })

  it('deletes the selected template and refreshes the available list', async () => {
    const wrapper = mountTemplates()
    await flushPromises()

    await wrapper.get('button.icon-action.danger').trigger('click')
    await wrapper.get('.confirm-dialog button.danger').trigger('click')
    await flushPromises()

    expect(api.deleteTemplateApi).toHaveBeenCalledWith('custom-template')
    expect(api.fetchTemplates).toHaveBeenCalledTimes(2)
  })

  async function importPreview(candidate = templateCandidate()) {
    api.parseDocumentTemplate.mockResolvedValue(candidate)
    const wrapper = mountTemplates()
    await flushPromises()
    const input = wrapper.get('input[type="file"]')
    const file = new File(['fixture'], candidate.filename)
    Object.defineProperty(input.element, 'files', { value: [file] })
    await input.trigger('change')
    await flushPromises()
    return { wrapper, candidate, file }
  }

  it('parses an uploaded Word file as a candidate and previews its recognized styles', async () => {
    const { wrapper, candidate, file } = await importPreview()
    const names = wrapper.findAll('.style-preview-item summary').map(el => el.text())

    expect(api.parseDocumentTemplate).toHaveBeenCalledWith(file)
    expect(names).toContain('section.englishAbstract')
    expect(names).toContain('section.keywords')
    expect(names).toContain('section.englishKeywords')
    expect(names).not.toContain('english_abstract')
    expect(wrapper.get('[data-test="import-template-admission"]').text()).toContain('templateAdmission.officeReview')
    expect(wrapper.get('[data-test="profile-publish"]').exists()).toBe(true)
    expect(wrapper.text()).toContain(candidate.filename)
    wrapper.unmount()
  })

  it('confirms the reviewed candidate and publishes it before opening the editor', async () => {
    const { wrapper, candidate } = await importPreview()
    await wrapper.get('[data-test="profile-publish"]').trigger('click')
    await flushPromises()

    expect(api.confirmDocumentTemplateCandidate).toHaveBeenCalledWith(expect.objectContaining({
      candidate_id: candidate.candidate_id,
      template_id: candidate.template_id,
    }))
    expect(api.publishPaperTemplateFromCandidate).toHaveBeenCalledWith(candidate.candidate_id)
    expect(api.confirmDocumentTemplateCandidate.mock.invocationCallOrder[0]).toBeLessThan(api.publishPaperTemplateFromCandidate.mock.invocationCallOrder[0])
    expect(api.fetchTemplates).toHaveBeenCalledTimes(2)
    expect(router.push).toHaveBeenCalledWith('/templates/published-template/edit')
    wrapper.unmount()
  })

  it('allows diagnosis of an initially blocked candidate but does not confirm or publish it', async () => {
    const blocked = templateCandidate({
      admission_assessment: {
        status: 'blocked', assessment_hash: 'blocked-assessment', auto_verifiable_constraints: [],
        blockers: [{ code: 'unknown_structure', path: 'definition.anchors', message: '结构尚未支持' }],
        manual_checks: [],
      },
    })
    const { wrapper } = await importPreview(blocked)

    expect(wrapper.get('[data-test="import-template-admission"]').text()).toContain('结构尚未支持')
    expect(wrapper.get('[data-test="candidate-admission-diagnostic"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="profile-publish"]').exists()).toBe(true)
    await wrapper.get('[data-test="profile-publish"]').trigger('click')
    await flushPromises()
    expect(api.confirmDocumentTemplateCandidate).not.toHaveBeenCalled()
    expect(api.publishPaperTemplateFromCandidate).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows blockers returned by the formal trial and stops before candidate confirmation', async () => {
    const prepared = templateCandidate({
      admission_assessment: {
        status: 'blocked', assessment_hash: 'trial-assessment', auto_verifiable_constraints: [],
        blockers: [{ code: 'source_layout', path: 'definition.sections', message: '试排后仍有版式阻断' }],
        manual_checks: [],
      },
    })
    const initial = templateCandidate({ prepared_candidate: prepared })
    const { wrapper } = await importPreview(initial)

    await wrapper.get('[data-test="profile-publish"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="import-template-admission"]').text()).toContain('试排后仍有版式阻断')
    expect(api.confirmDocumentTemplateCandidate).not.toHaveBeenCalled()
    expect(api.publishPaperTemplateFromCandidate).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps the candidate open and does not navigate when confirmation is rejected', async () => {
    const { wrapper } = await importPreview()
    api.confirmDocumentTemplateCandidate.mockRejectedValueOnce(new Error('候选确认被拒绝'))

    await wrapper.get('[data-test="profile-publish"]').trigger('click')
    await flushPromises()

    expect(api.publishPaperTemplateFromCandidate).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('候选确认被拒绝')
    wrapper.unmount()
  })

  it('keeps the candidate open and does not navigate when publication fails', async () => {
    const { wrapper } = await importPreview()
    api.publishPaperTemplateFromCandidate.mockRejectedValueOnce(new Error('模板发布门禁未通过'))

    await wrapper.get('[data-test="profile-publish"]').trigger('click')
    await flushPromises()

    expect(api.confirmDocumentTemplateCandidate).toHaveBeenCalledTimes(1)
    expect(router.push).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('模板发布门禁未通过')
    wrapper.unmount()
  })
})
