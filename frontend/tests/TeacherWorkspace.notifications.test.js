import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  fetchMyTasks: vi.fn(),
  fetchProcessRecordInbox: vi.fn(),
  fetchWorkflowNotifications: vi.fn(),
  markWorkflowNotificationRead: vi.fn(),
}))
const router = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('../src/api.js', () => api)
vi.mock('../src/auth.js', () => ({ auth: { user: { role: 'teacher', status: 'active' } } }))
vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRouter: () => router,
}))
import TeacherWorkspace from '../src/views/TeacherWorkspace.vue'

let wrapper
beforeEach(() => {
  vi.clearAllMocks()
  api.fetchMyTasks.mockResolvedValue([])
  api.fetchProcessRecordInbox.mockResolvedValue([])
  api.markWorkflowNotificationRead.mockResolvedValue({})
})
afterEach(() => wrapper?.unmount())

describe('teacher submission notifications', () => {
  it('opens the archive list from an archive notification', async () => {
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [{
      id: 'event-archive', resource_type: 'archive', resource_id: 'archive-1',
      data: { title: '终稿归档暂未完成' },
    }], unread: 1 })
    wrapper = mount(TeacherWorkspace)
    await flushPromises()
    await wrapper.get('.notification-row').trigger('click')
    await flushPromises()
    expect(router.push).toHaveBeenCalledWith({ path: '/workflow', query: { panel: 'archives' } })
  })

  it.each([null, '2026-10-30T00:00:00Z'])('opens the linked submission whether read_at is %s', async readAt => {
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [{
      id: 'event-1', resource_type: 'submission', resource_id: 'submission-1', read_at: readAt,
      data: { title: '有新的论文提交待审核' },
    }], unread: readAt ? 0 : 1 })
    wrapper = mount(TeacherWorkspace)
    await flushPromises()
    await wrapper.get('.notification-row').trigger('click')
    await flushPromises()
    expect(api.markWorkflowNotificationRead).toHaveBeenCalledTimes(readAt ? 0 : 1)
    expect(router.push).toHaveBeenCalledWith({ path: '/workflow', query: { panel: 'review', submission: 'submission-1' } })
  })

  it('opens the submission even when marking the notification as read fails', async () => {
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [{
      id: 'event-1', resource_type: 'submission', resource_id: 'submission-1',
      data: { title: '有新的论文提交待审核' },
    }], unread: 1 })
    api.markWorkflowNotificationRead.mockRejectedValueOnce(new Error('消息状态保存失败'))
    wrapper = mount(TeacherWorkspace)
    await flushPromises()
    await wrapper.get('.notification-row').trigger('click')
    await flushPromises()
    expect(router.push).toHaveBeenCalledWith({ path: '/workflow', query: { panel: 'review', submission: 'submission-1' } })
    expect(wrapper.text()).toContain('消息状态保存失败')
  })

  it('opens the submission without waiting for a stalled read request', async () => {
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [{
      id: 'event-1', resource_type: 'submission', resource_id: 'submission-1',
      data: { title: '有新的论文提交待审核' },
    }], unread: 1 })
    api.markWorkflowNotificationRead.mockImplementationOnce(() => new Promise(() => {}))
    wrapper = mount(TeacherWorkspace)
    await flushPromises()
    await wrapper.get('.notification-row').trigger('click')
    await flushPromises()
    expect(router.push).toHaveBeenCalledWith({ path: '/workflow', query: { panel: 'review', submission: 'submission-1' } })
  })
})
