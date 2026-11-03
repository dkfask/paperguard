import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'

const projectApi = vi.hoisted(() => ({ export: vi.fn() }))
vi.mock('../src/features/projects/api', () => ({ projectsApi: projectApi }))
vi.mock('../src/features/workspace/queries', () => ({
  useWorkspaceOverview: () => ({
    data: ref({ projects: [{ id: 'p1', title: '测试论文' }], templates: [], packs: [], deletedProjects: [] }),
    error: ref(null), isPending: ref(false), isFetching: ref(false), refetch: vi.fn(),
  }),
}))
vi.mock('vue-router', () => ({ useRoute: () => ({ query: {} }), useRouter: () => ({ push: vi.fn() }) }))
vi.mock('../src/i18n', () => ({ useI18n: () => ({ t: key => key, formatDate: value => value }) }))

import Home from '../src/views/Home.vue'

let wrapper
beforeEach(() => { vi.resetAllMocks() })
afterEach(() => { wrapper?.unmount() })

function mountHome() {
  return mount(Home, {
    global: { stubs: {
      RouterLink: { template: '<a><slot /></a>' },
      ScreenCollection: { props: ['items'], template: '<div><slot :items="items" /></div>' },
      BaseModal: true, ConfirmDialog: true,
    } },
  })
}

describe('homepage DOCX export errors', () => {
  it('shows the specific missing facts and unresolved automatic checks', async () => {
    const error = new Error('无法交付 DOCX')
    error.missing = ['参考文献 [2] 缺少作者']
    error.manualChecks = ['封面规则尚未自动核验']
    projectApi.export.mockRejectedValue(error)
    wrapper = mountHome()
    await wrapper.get('button[aria-label="exportDocx"]').trigger('click')
    await flushPromises()
    const alert = wrapper.get('[role="alert"]').text()
    expect(alert).toContain('无法交付 DOCX')
    expect(alert).toContain('参考文献 [2] 缺少作者')
    expect(alert).toContain('封面规则尚未自动核验')
  })

  it('clears a previous export error before a successful retry', async () => {
    projectApi.export.mockRejectedValueOnce(new Error('首次失败')).mockResolvedValueOnce({ status: 'pass', count: 1 })
    wrapper = mountHome()
    await wrapper.get('button[aria-label="exportDocx"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('首次失败')
    await wrapper.get('button[aria-label="exportDocx"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('首次失败')
    expect(wrapper.find('.export-check-status').exists()).toBe(true)
  })
})
