import { beforeEach, describe, expect, it, vi } from 'vitest'
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
vi.mock('../src/auth.js', () => ({
  auth: { canManage: vi.fn(() => true) },
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

function chooseFile(wrapper, selector, file) {
  const input = wrapper.get(selector).element
  Object.defineProperty(input, 'files', { configurable: true, value: [file] })
  return wrapper.get(selector).trigger('change')
}

describe('DocumentPackagePublisher', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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

  it('allows legacy and modern Word files in both upload steps', () => {
    const wrapper = mount(DocumentPackagePublisher, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })

    expect(wrapper.get('[data-test="template-file"]').attributes('accept')).toContain('.doc')
    expect(wrapper.get('[data-test="template-file"]').attributes('accept')).toContain('.docx')
    expect(wrapper.get('[data-test="spec-file"]').attributes('accept')).toContain('.doc')
    expect(wrapper.get('[data-test="spec-file"]').attributes('accept')).toContain('.docx')
  })

  it('publishes with an optional separate standard when one is provided', async () => {
    const wrapper = mount(DocumentPackagePublisher, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
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
    const wrapper = mount(DocumentPackagePublisher, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
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
    const wrapper = mount(DocumentPackagePublisher, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    const templateFile = new File(['docx'], 'report.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
    const specFile = new File(['rules'], 'rules.md', { type: 'text/markdown' })

    await chooseFile(wrapper, '[data-test="template-file"]', templateFile)
    await wrapper.get('[data-test="template-parse"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="template-candidate-name"]').setValue('修订后的模板')
    await wrapper.get('[data-test="template-field-label"]').setValue('报告作者')
    await wrapper.get('[data-test="template-section-title"]').setValue('背景与目标')
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
    const wrapper = mount(DocumentPackagePublisher, {
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await flushPromises()

    expect(wrapper.get('[data-test="template-saved"]').exists()).toBe(true)
    await wrapper.get('[data-test="template-saved"]').setValue('template-candidate-1')
    expect(wrapper.text()).toContain('已保存模板')
    expect(wrapper.get('[data-test="spec-saved"]').exists()).toBe(true)
  })
})
