import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  parseDocumentTemplate: vi.fn(),
  getDocumentTemplateCandidates: vi.fn(),
  confirmDocumentTemplateCandidate: vi.fn(),
  parseDocumentSpec: vi.fn(),
  getDocumentSpecCandidates: vi.fn(),
  confirmDocumentSpecCandidate: vi.fn(),
  publishDocumentPackage: vi.fn(),
}))

vi.mock('../src/api.js', () => api)
const identity = vi.hoisted(() => ({ user: { role: 'admin' }, canManage: vi.fn(() => true) }))
vi.mock('../src/auth.js', () => ({
  auth: identity,
}))
vi.mock('../src/i18n', () => ({
  useI18n: () => ({ t: (key) => key }),
}))

import DocumentPackagePublisher from '../src/views/DocumentPackagePublisher.vue'

const templateCandidate = {
  candidate_id: 'template-candidate-1',
  template_id: 'template-1',
  name: '实验报告模板',
  version: '1',
  status: 'needs_confirmation',
  issues: [],
  definition: {
    sections: [{ key: 'intro', title: '引言', children: [] }],
    fields: [{ key: 'author', label: '作者', required: true }],
    styles: { body: {} },
  },
  source_asset: { id: 'asset-1', storage_key: 'asset-1', scan_status: 'validated' },
}

const specCandidate = {
  candidate_id: 'spec-candidate-1',
  spec_set_id: 'spec-1',
  name: '实验报告规范',
  version: '1',
  status: 'needs_confirmation',
  issues: [],
  extraction_mode: 'deterministic',
  source_characters: 120,
  rules: [{ id: 'body-min', kind: 'auto', checker: 'text.word_count', description: '正文至少 100 字' }],
}

const mounted = []
function mountPublisher() {
  const wrapper = mount(DocumentPackagePublisher, {
    global: { stubs: { teleport: true, RouterLink: { template: '<a><slot /></a>' } } },
  })
  mounted.push(wrapper)
  return wrapper
}
afterEach(() => { mounted.splice(0).forEach(wrapper => wrapper.unmount()) })

function chooseFile(wrapper, selector, file) {
  const input = wrapper.get(selector).element
  Object.defineProperty(input, 'files', { configurable: true, value: [file] })
  return wrapper.get(selector).trigger('change')
}

describe('DocumentPackagePublisher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    identity.user = { role: 'admin' }
    api.getDocumentTemplateCandidates.mockResolvedValue([])
    api.getDocumentSpecCandidates.mockResolvedValue([])
    api.parseDocumentTemplate.mockImplementation(async () => JSON.parse(JSON.stringify(templateCandidate)))
    api.confirmDocumentTemplateCandidate.mockImplementation(async candidate => ({ ...candidate, status: 'confirmed' }))
    api.parseDocumentSpec.mockImplementation(async () => JSON.parse(JSON.stringify(specCandidate)))
    api.confirmDocumentSpecCandidate.mockImplementation(async candidate => ({ ...candidate, status: 'confirmed' }))
    api.publishDocumentPackage.mockResolvedValue({
      package: { id: 'package:template-1@1:spec-1@1' },
      template: { name: '实验报告模板' },
      spec_set: { name: '实验报告规范' },
    })
  })

  it('saves a teacher template for personal writing using named document types', async () => {
    identity.user = { role: 'teacher' }
    const wrapper = mountPublisher()
    expect(wrapper.get('h1').text()).toBe('personalTemplates.title')
    expect(wrapper.text()).not.toContain('documentPackagePublisher.publishTitle')
    expect(wrapper.text()).toContain('personalTemplates.specOptional')
    await chooseFile(wrapper, '[data-test="template-file"]', new File(['docx'], 'application.docx'))
    await wrapper.get('[data-test="template-parse"]').trigger('click'); await flushPromises()
    await wrapper.get('[data-test="template-confirm"]').trigger('click'); await flushPromises()
    await wrapper.get('[aria-label="personalTemplates.documentType"]').setValue('grant-application')
    await wrapper.get('[data-test="publish"]').trigger('click'); await flushPromises()
    expect(api.publishDocumentPackage).toHaveBeenCalledWith(expect.objectContaining({ usage: 'personal', document_type_id: 'grant-application' }))
    expect(wrapper.text()).toContain('personalTemplates.published')
  })

  it('allows legacy and modern Word files in both upload steps', () => {
    const wrapper = mountPublisher()

    expect(wrapper.get('[data-test="template-file"]').attributes('accept')).toContain('.doc')
    expect(wrapper.get('[data-test="template-file"]').attributes('accept')).toContain('.docx')
    expect(wrapper.get('[data-test="spec-file"]').attributes('accept')).toContain('.doc')
    expect(wrapper.get('[data-test="spec-file"]').attributes('accept')).toContain('.docx')
  })

  it('publishes with an optional separate standard when one is provided', async () => {
    const wrapper = mountPublisher()
    const templateFile = new File(['docx'], 'report.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    const specFile = new File(['rules'], 'rules.md', { type: 'text/markdown' })

    expect(wrapper.get('[data-test="publish"]').attributes('disabled')).toBeDefined()
    await chooseFile(wrapper, '[data-test="template-file"]', templateFile)
    await wrapper.get('[data-test="template-parse"]').trigger('click')
    await flushPromises()
    expect(api.parseDocumentTemplate).toHaveBeenCalledWith(templateFile)
    await wrapper.get('[data-test="template-confirm"]').trigger('click')
    await flushPromises()

    await chooseFile(wrapper, '[data-test="spec-file"]', specFile)
    await wrapper.get('[data-test="spec-parse"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="spec-confirm"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="publish"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-test="publish"]').trigger('click')
    await flushPromises()

    expect(api.publishDocumentPackage).toHaveBeenCalledWith(expect.objectContaining({
      document_type_id: 'generic',
      template_candidate: expect.objectContaining({ status: 'confirmed' }),
      spec_candidate: expect.objectContaining({ status: 'confirmed' }),
    }))
    expect(wrapper.text()).toContain('documentPackagePublisher.published')
  })

  it('allows a confirmed form template to publish without a separate standard', async () => {
    const wrapper = mountPublisher()
    const templateFile = new File(['docx'], 'application-form.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })

    await chooseFile(wrapper, '[data-test="template-file"]', templateFile)
    await wrapper.get('[data-test="template-parse"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="template-confirm"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="spec-enabled"]').element.checked).toBe(false)
    expect(wrapper.get('[data-test="publish"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-test="publish"]').trigger('click')
    await flushPromises()

    expect(api.publishDocumentPackage).toHaveBeenCalledWith({
      document_type_id: 'generic',
      template_candidate: expect.objectContaining({ status: 'confirmed' }),
    })
  })

  it('reviews editable fields and rule params before confirmation', async () => {
    const wrapper = mountPublisher()
    const templateFile = new File(['docx'], 'report.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    const specFile = new File(['rules'], 'rules.md', { type: 'text/markdown' })

    await chooseFile(wrapper, '[data-test="template-file"]', templateFile)
    await wrapper.get('[data-test="template-parse"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="template-candidate-name"]').setValue('修订后的模板')
    await wrapper.get('[data-test="template-field-label"]').setValue('报告作者')
    await wrapper.get('[data-test="template-section-title"]').setValue('背景与目标')
    await wrapper.get('[role="dialog"] .base-modal-close').trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    await wrapper.get('[data-test="template-review"]').trigger('click')
    expect(wrapper.get('[data-test="template-candidate-name"]').element.value).toBe('修订后的模板')
    await wrapper.get('[data-test="template-confirm"]').trigger('click')
    await flushPromises()

    expect(api.confirmDocumentTemplateCandidate).toHaveBeenCalledWith(expect.objectContaining({
      name: '修订后的模板',
      definition: expect.objectContaining({
        fields: [expect.objectContaining({ label: '报告作者' })],
        sections: [expect.objectContaining({ title: '背景与目标' })],
      }),
    }))

    await chooseFile(wrapper, '[data-test="spec-file"]', specFile)
    await wrapper.get('[data-test="spec-parse"]').trigger('click')
    await flushPromises()
    const ruleParams = wrapper.get('[aria-label="documentPackagePublisher.ruleParams"]')
    await ruleParams.setValue('{"min": 120}')
    await wrapper.get('[data-test="spec-confirm"]').trigger('click')
    await flushPromises()

    expect(api.confirmDocumentSpecCandidate).toHaveBeenCalledWith(expect.objectContaining({
      rules: [expect.objectContaining({ params: { min: 120 } })],
    }))
  })

  it('restores saved candidates after the page loads', async () => {
    api.getDocumentTemplateCandidates.mockResolvedValue([{ ...templateCandidate, name: '已保存模板' }])
    api.getDocumentSpecCandidates.mockResolvedValue([{ ...specCandidate, name: '已保存规范' }])
    const wrapper = mountPublisher()
    await flushPromises()

    expect(wrapper.get('[data-test="template-saved"]').exists()).toBe(true)
    await wrapper.get('[data-test="template-saved"]').setValue('template-candidate-1')
    expect(wrapper.text()).toContain('已保存模板')
    expect(wrapper.get('[data-test="spec-saved"]').exists()).toBe(true)
  })

  it('keeps parsed structure and permissions intact through the simplified preview', async () => {
    const definition = {
      ...templateCandidate.definition,
      mode: 'hybrid', export_strategy: 'source_template',
      sections: [{
        key: 'ch1', title: '第一章 包含较长名称的研究背景与技术方法', kind: 'heading1', order: 3,
        style_ref: 'body', required: true, repeatable: true, allow_user_add: false, allow_user_delete: true,
        children: [{ key: 'ch1_1', title: '国内外研究进展', kind: 'heading2', order: 0,
          children: [{ key: 'ch1_1_1', title: '关键技术比较', kind: 'heading3', children: [] }] }],
      }],
    }
    api.parseDocumentTemplate.mockResolvedValue({ ...templateCandidate, definition })
    const wrapper = mountPublisher()
    await chooseFile(wrapper, '[data-test="template-file"]', new File(['docx'], 'outline.docx'))
    await wrapper.get('[data-test="template-parse"]').trigger('click')
    await flushPromises()

    const row = wrapper.get('[data-test="template-section-ch1"]')
    expect(row.get('textarea').element.value).toBe(definition.sections[0].title)
    expect(wrapper.findAll('.section-level').map(item => item.text())).toEqual([
      'section.heading1', 'section.heading2', 'section.heading3',
    ])
    expect(row.find('select, input').exists()).toBe(false)
    expect(row.text()).not.toContain('ch1')
    await wrapper.get('[data-test="template-confirm"]').trigger('click')
    await flushPromises()
    expect(api.confirmDocumentTemplateCandidate).toHaveBeenCalledWith(expect.objectContaining({ definition }))
  })
})
