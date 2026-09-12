import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  getDocumentProjectEditor: vi.fn(),
  renderDocumentProject: vi.fn(),
  updateDocumentProject: vi.fn(),
  uploadDocumentProjectImage: vi.fn(),
  validateDocumentProject: vi.fn(),
}))
const route = vi.hoisted(() => ({ params: { id: 'project-1' } }))

vi.mock('../src/api.js', () => api)
vi.mock('../src/i18n', () => ({
  useI18n: () => ({
    t: (key, params = {}) => key === 'documentProjects.saved' ? `saved ${params.revision}` : key,
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
})
