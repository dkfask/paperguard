import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  parseDocumentTemplate: vi.fn(),
  confirmDocumentTemplateCandidate: vi.fn(),
  parseDocumentSpec: vi.fn(),
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
    api.parseDocumentTemplate.mockResolvedValue(templateCandidate)
    api.confirmDocumentTemplateCandidate.mockResolvedValue({ ...templateCandidate, status: 'confirmed' })
    api.parseDocumentSpec.mockResolvedValue(specCandidate)
    api.confirmDocumentSpecCandidate.mockResolvedValue({ ...specCandidate, status: 'confirmed' })
    api.publishDocumentPackage.mockResolvedValue({
      package: { id: 'package:template-1@1:spec-1@1' },
      template: { name: '实验报告模板' },
      spec_set: { name: '实验报告规范' },
    })
  })

  it('requires both confirmed candidates before publishing a package', async () => {
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
})
