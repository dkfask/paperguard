import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Operations from '../src/views/Operations.vue'
import { fetchHealth, fetchRetentionPolicy, fetchWorkflowJobs, updateRetentionPolicy } from '../src/api'

vi.mock('../src/auth', () => ({ auth: { user: { role: 'admin', display_name: '测试管理员' } } }))
vi.mock('../src/api', () => ({ fetchHealth: vi.fn(), fetchWorkflowJobs: vi.fn(), fetchRetentionPolicy: vi.fn(), downloadOperationsBackup: vi.fn(), updateRetentionPolicy: vi.fn() }))

beforeEach(() => {
  vi.resetAllMocks()
  fetchHealth.mockResolvedValue({ status: 'ok' })
  fetchWorkflowJobs.mockResolvedValue([])
  fetchRetentionPolicy.mockResolvedValue({ configured: false, policy: null })
})
const render = () => mount(Operations, { global: { stubs: { RouterLink: true } } })

describe('operations states preserve server authorization', () => {
  it('shows denied policy scope while keeping successful probes and disabling writes', async () => {
    fetchRetentionPolicy.mockRejectedValue(Object.assign(new Error('请选择一个有效成员租户（请求 ID: req-test）'), { status: 403 }))
    const wrapper = render()
    await flushPromises()
    expect(wrapper.get('.async-state.denied').text()).toContain('req-test')
    expect(wrapper.get('.workspace-stats').text()).toContain('不可用')
    expect(wrapper.get('.operations-status-list').text()).toContain('正常')
    expect(wrapper.find('.operations-retention-form').exists()).toBe(false)
    const backup = wrapper.findAll('button').find(button => button.text().includes('生成备份'))
    expect(backup.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('keeps a policy load failure visible and can retry into the authorized form', async () => {
    fetchRetentionPolicy.mockRejectedValueOnce(Object.assign(new Error('策略服务不可用'), { status: 500 }))
    const wrapper = render()
    await flushPromises()
    expect(wrapper.get('.async-state.error').text()).toContain('策略服务不可用')
    await wrapper.get('.async-state.error button').trigger('click')
    await flushPromises()
    expect(fetchRetentionPolicy).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.operations-retention-form').exists()).toBe(true)
    wrapper.unmount()
  })

  it('shows a failed probe as a failure instead of showing normal empty metrics', async () => {
    fetchHealth.mockRejectedValue(new Error('健康探针读取失败'))
    const wrapper = render()
    await flushPromises()
    expect(wrapper.get('.async-state.error').text()).toContain('健康探针读取失败')
    expect(wrapper.find('.workspace-stats').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the policy draft and form visible when saving fails, then retries that draft', async () => {
    updateRetentionPolicy.mockRejectedValueOnce(new Error('保存失败，请重试'))
    updateRetentionPolicy.mockResolvedValueOnce({ configured: true, policy: { draft_days: 42 } })
    const wrapper = render()
    await flushPromises()
    await wrapper.get('.operations-retention-form input').setValue('42')
    await wrapper.get('.operations-retention-form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('.error-banner').text()).toContain('保存失败')
    expect(wrapper.get('.operations-retention-form input').element.value).toBe('42')
    expect(wrapper.find('.ok-banner').exists()).toBe(false)
    await wrapper.get('.operations-retention-form').trigger('submit')
    await flushPromises()
    expect(updateRetentionPolicy).toHaveBeenLastCalledWith(expect.objectContaining({ draft_days: 42 }))
    expect(wrapper.get('.ok-banner').text()).toContain('已保存')
    wrapper.unmount()
  })
})
