import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  fetchOrganizations: vi.fn(),
  getAdminUsers: vi.fn(),
  saveOrganization: vi.fn(),
  deleteOrganization: vi.fn(),
  updateUserOrganizationContext: vi.fn(),
}))

vi.mock('../src/api.js', () => api)
vi.mock('../src/auth.js', () => ({
  auth: { user: { role: 'admin', status: 'active' } },
  isManager: () => true,
}))
vi.mock('../src/i18n', () => ({
  useI18n: () => ({ t: (key) => key }),
}))

import Organizations from '../src/views/Organizations.vue'

describe('Organizations', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.fetchOrganizations.mockResolvedValue([
      { id: 'college-1', name: '计算机学院', type: 'college', parent_id: 'school-1', metadata: {} },
      { id: 'major-1', name: '软件工程', type: 'major', parent_id: 'college-1', metadata: {} },
    ])
    api.getAdminUsers.mockResolvedValue([])
  })

  it('renders a college subtree even when the school root is out of scope', async () => {
    const wrapper = mount(Organizations, {
      global: { stubs: { ConfirmDialog: true } },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('计算机学院')
    expect(wrapper.text()).toContain('软件工程')
    expect(wrapper.text()).not.toContain('organizations.empty')
  })
})
