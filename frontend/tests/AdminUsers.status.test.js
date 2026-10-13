import { afterEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
const api = vi.hoisted(() => ({ getAdminUsers: vi.fn(), updateAdminUserRoleStatus: vi.fn() }))
vi.mock('../src/api.js', () => api)
vi.mock('../src/auth.js', () => ({ auth: { user: { role: 'admin' } } }))
vi.mock('../src/features/auth/api', () => ({ authApi: { decideRoleCertification: vi.fn() } }))
import AdminUsers from '../src/views/AdminUsers.vue'
let wrapper
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
describe('user certification status', () => {
  it('refreshes the open account and removes certification actions after success', async () => {
    const teacher = { id: 'teacher-1', username: 'teacher-test', display_name: '测试教师', role: 'teacher', status: 'pending' }
    api.getAdminUsers.mockResolvedValueOnce([teacher]).mockResolvedValue([{ ...teacher, status: 'active' }])
    api.updateAdminUserRoleStatus.mockResolvedValue({ ...teacher, status: 'active' })
    wrapper = mount(AdminUsers, { attachTo: document.body }); await flushPromises()
    await wrapper.findAll('button').find(el => el.text() === '查看详情').trigger('click'); await flushPromises()
    const approve = new DOMWrapper([...document.querySelectorAll('[role="dialog"] button')].find(el => el.textContent === '通过认证'))
    await approve.trigger('click'); await flushPromises()
    expect(document.querySelector('[role="dialog"]').textContent).toContain('已认证')
    expect([...document.querySelectorAll('[role="dialog"] button')].some(el => el.textContent === '通过认证')).toBe(false)
  })
})
