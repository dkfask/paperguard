import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  fetchTemplates: vi.fn(),
  deleteTemplateApi: vi.fn(),
  importDocxTemplate: vi.fn(),
  stripTemplateComments: vi.fn(),
  saveTemplate: vi.fn(),
  assessTemplateAdmission: vi.fn(),
  fetchTemplate: vi.fn(),
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

describe('Templates', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.fetchTemplates.mockResolvedValue([template])
    api.deleteTemplateApi.mockResolvedValue(undefined)
    api.assessTemplateAdmission.mockImplementation(async value => ({
      status: 'office_review_required', assessment_hash: 'template-assessment', definition_sha256: 'definition',
      source_docx_sha256: '', blockers: [], auto_verifiable_constraints: [], manual_checks: [],
      can_claim_compliant_delivery: false,
      template_id: value.id, version: value.version,
    }))
  })

  it('opens the delete confirmation without throwing a page-level error', async () => {
    const wrapper = mount(Templates, {
      global: {
        stubs: {
          teleport: true,
          AsyncState: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    await expect(wrapper.get('button.icon-action.danger').trigger('click')).resolves.toBeUndefined()
    expect(wrapper.text()).toContain('templates.deleteTitle:自定义模板')
  })

  it('deletes the selected template and refreshes the available list', async () => {
    const wrapper = mount(Templates, {
      global: {
        stubs: {
          teleport: true,
          AsyncState: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    await wrapper.get('button.icon-action.danger').trigger('click')
    await wrapper.get('.confirm-dialog button.danger').trigger('click')
    await flushPromises()

    expect(api.deleteTemplateApi).toHaveBeenCalledWith('custom-template')
    expect(api.fetchTemplates).toHaveBeenCalledTimes(2)
  })

  async function importPreview() {
    const result = {
      filename: 'abstract-keywords.docx', style_names: ['Heading 1', 'Abstract', 'Key Words'],
      styles: Object.fromEntries(['heading3', 'english_abstract', 'keywords', 'english_keywords', 'heading1', 'heading2'].map(key => [key, { font_en: 'Times New Roman', size_pt: 12 }])),
      sections: [{ key: 'english_abstract', type: 'english_abstract', title: 'Abstract', children: [] }, { key: 'ch1', type: 'heading1', title: '第1章 绪论', children: [] }],
    }
    api.importDocxTemplate.mockResolvedValue(result)
    const wrapper = mount(Templates, { global: { stubs: { teleport: true, AsyncState: true, RouterLink: { template: '<a><slot /></a>' } } } })
    await flushPromises()
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['fixture'], result.filename)] })
    await input.trigger('change'); await flushPromises()
    return { wrapper, result }
  }

  it('uses translated names for English abstract and both keyword styles', async () => {
    const { wrapper } = await importPreview()
    const names = wrapper.findAll('.style-preview-item summary').map(el => el.text())
    expect(names).toContain('section.englishAbstract')
    expect(names).toContain('section.keywords')
    expect(names).toContain('section.englishKeywords')
    expect(names).not.toContain('english_abstract')
    expect(names).not.toContain('keywords')
    expect(names).not.toContain('english_keywords')
    wrapper.unmount()
  })

  it('separates heading levels from other styles without changing the saved definitions', async () => {
    const { wrapper, result } = await importPreview()
    const groups = wrapper.findAll('.style-preview-group')
    expect(groups).toHaveLength(2)
    expect(groups[0].findAll('summary').map(el => el.text())).toEqual(['section.heading1', 'section.heading2', 'section.heading3'])
    expect(groups[1].findAll('summary').map(el => el.text())).toEqual(['section.englishAbstract', 'section.keywords', 'section.englishKeywords'])
    api.fetchTemplate.mockResolvedValue({ sections: [], meta_fields: [] })
    api.saveTemplate.mockResolvedValue({ id: 'imported' })
    await wrapper.get('.import-actions button.primary').trigger('click')
    await flushPromises()
    const saved = api.saveTemplate.mock.calls[0][0]
    expect(saved.styles).toEqual(result.styles)
    expect(saved.sections).toEqual([{ ...result.sections[0], required: true }, result.sections[1]])
    expect(router.push).toHaveBeenCalledWith('/templates/imported/edit')
    wrapper.unmount()
  })

  it('keeps an unsupported imported template as a draft for correction without making it usable', async () => {
    const { wrapper } = await importPreview()
    const blocker = { code: 'cover_table', path: 'cover_blocks[0]', message: '封面表格尚未映射' }
    api.assessTemplateAdmission.mockResolvedValue({
      status: 'blocked', assessment_hash: 'blocked-assessment', blockers: [blocker],
      auto_verifiable_constraints: [], manual_checks: [], can_claim_compliant_delivery: false,
    })
    api.saveTemplate.mockResolvedValue({ id: 'imported' })
    api.fetchTemplate.mockResolvedValue({ sections: [], meta_fields: [] })
    await wrapper.get('.import-actions button.primary').trigger('click')
    await flushPromises()
    expect(api.assessTemplateAdmission).not.toHaveBeenCalled()
    expect(api.saveTemplate.mock.calls[0][0].id).toBe('')
    expect(api.saveTemplate).toHaveBeenCalledWith(expect.objectContaining({ sections: expect.any(Array), source_docx: expect.any(String) }), true)
    expect(router.push).toHaveBeenCalledWith('/templates/imported/edit')
    wrapper.unmount()
  })
})
