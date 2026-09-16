import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  fetchTemplates: vi.fn(),
  deleteTemplateApi: vi.fn(),
  importDocxTemplate: vi.fn(),
  stripTemplateComments: vi.fn(),
  saveTemplate: vi.fn(),
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
  })

  it('opens the delete confirmation without throwing a page-level error', async () => {
    const wrapper = mount(Templates, {
      global: {
        stubs: {
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
})
