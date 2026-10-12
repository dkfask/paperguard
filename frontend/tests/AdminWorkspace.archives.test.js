import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
const api = vi.hoisted(() => ({ fetchArchives: vi.fn(), fetchCohorts: vi.fn(), fetchWorkflowNotifications: vi.fn(), fetchWorkflowReport: vi.fn(), markWorkflowNotificationRead: vi.fn(), fetchSubmissions: vi.fn(), fetchMyTasks: vi.fn() }))
vi.mock('../src/api.js', () => api)
vi.mock('../src/auth.js', () => ({ auth: { user: { role: 'admin' } } }))
vi.mock('vue-router', () => ({ RouterLink: { template: '<a><slot /></a>' } }))
import AdminWorkspace from '../src/views/AdminWorkspace.vue'
let wrapper
afterEach(() => wrapper?.unmount())
describe('named administrator archive jobs', () => {
  it('resolves the accessible submission and task without showing storage IDs', async () => {
    api.fetchCohorts.mockResolvedValue([]); api.fetchWorkflowNotifications.mockResolvedValue({ items: [], unread: 0 })
    api.fetchArchives.mockResolvedValue([{ id: 'archive-1', submission_id: 'submission-internal-1', status: 'succeeded' }])
    api.fetchSubmissions.mockResolvedValue([{ id: 'submission-internal-1', participant_id: 'participant-1', submission_number: 'SUB-0001' }])
    api.fetchMyTasks.mockResolvedValue([{ id: 'participant-1', project_title: '测试论文', student_name: '测试学生', stage_name: '终稿' }])
    wrapper = mount(AdminWorkspace, { global: { stubs: { ScreenDeck: { template: '<div><slot /></div>' } } } }); await flushPromises()
    const row = wrapper.get('.workspace-archive-row')
    expect(row.text()).toContain('测试论文'); expect(row.text()).toContain('测试学生'); expect(row.text()).toContain('终稿'); expect(row.text()).toContain('SUB-0001')
    expect(row.text()).not.toContain('submission-internal-1')
  })
})
