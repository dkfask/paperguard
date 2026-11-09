import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  fetchTemplate: vi.fn(), fetchPolicies: vi.fn(), saveTemplate: vi.fn(), assessTemplateAdmission: vi.fn(),
}))
const router = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('../src/api', () => api)
vi.mock('vue-router', () => ({ useRoute: () => ({ params: { id: 'published' } }), useRouter: () => router }))
vi.mock('../src/i18n', () => ({ useI18n: () => ({ t: key => key, formatDate: () => '' }) }))

import TemplateEditor from '../src/views/TemplateEditor.vue'

describe('published candidate template', () => {
  it('shows a read-only view and an entry to import a new candidate', async () => {
    api.fetchTemplate.mockResolvedValue({
      id: 'published', name: '正式模板', description: '模板说明', version: '1',
      admission_provenance: { source: 'document_template_candidate' },
      sections: [{ key: 'chapter', title: '第一章', children: [] }],
      styles: { body: { font_zh: '宋体', font_en: 'Times New Roman', size_pt: 12 } }, meta_fields: [],
    })
    api.fetchPolicies.mockResolvedValue([])
    const wrapper = mount(TemplateEditor, { global: { mocks: { $router: router } } })
    await flushPromises()
    const readonly = wrapper.get('[data-test="immutable-template-view"]')
    expect(readonly.text()).toContain('正式模板')
    expect(readonly.text()).toContain('第一章')
    expect(readonly.text()).toContain('宋体')
    expect(wrapper.find('.template-form-deck').exists()).toBe(false)
    expect(wrapper.find('.resource-hero-actions .primary').exists()).toBe(false)
    expect(api.assessTemplateAdmission).not.toHaveBeenCalled()
    await readonly.get('button').trigger('click')
    expect(router.push).toHaveBeenCalledWith('/templates')
    expect(api.saveTemplate).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
