import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
const respond = vi.hoisted(() => vi.fn())
vi.mock('../src/api.js', () => ({ respondSubmissionComment: respond }))
import SubmissionFeedback from '../src/components/SubmissionFeedback.vue'
const comment = { id: 'comment-1', submission_id: 'submission-old', body: '补充对照结果', status: 'open', responses: [] }
describe('version-bound submission feedback', () => {
  it('preserves a failed reply draft and emits the persisted comment after retry', async () => {
    respond.mockRejectedValueOnce(new Error('暂时不可用')).mockResolvedValue({ ...comment, status: 'addressed', responses: [{ body: '已补充', id: 'response-1' }] })
    const wrapper = mount(SubmissionFeedback, { props: { detail: { comments: [comment] }, canReply: true } })
    await wrapper.get('textarea').setValue('已补充'); await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('已补充'); expect(wrapper.get('[role="alert"]').text()).toBe('暂时不可用')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(respond).toHaveBeenLastCalledWith('comment-1', '已补充')
    expect(wrapper.emitted('replied')[0][0].submission_id).toBe('submission-old'); expect(wrapper.get('textarea').element.value).toBe('')
    wrapper.unmount()
  })
  it('shows feedback read-only for reviewers and hides reply actions on resolved comments', () => {
    const wrapper = mount(SubmissionFeedback, { props: { detail: { review: { decision: 'returned', reason: '补充结论' }, comments: [{ ...comment, status: 'resolved' }] }, canReply: true } })
    expect(wrapper.text()).toContain('补充结论'); expect(wrapper.find('form').exists()).toBe(false)
    wrapper.unmount()
  })
})
