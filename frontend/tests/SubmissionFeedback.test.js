import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../src/api', () => ({ respondSubmissionComment: vi.fn() }))
vi.mock('vue-router', () => ({ RouterLink: { name: 'RouterLink', props: ['to'], template: '<a><slot /></a>' } }))
import SubmissionFeedback from '../src/components/SubmissionFeedback.vue'
import { respondSubmissionComment } from '../src/api'

describe('student feedback locations', () => {
  it('links a uniquely anchored comment to the current project and submission', () => {
    const detail = { participant: { project_id: 'project-1' }, submission: { id: 'submission-1' },
      version: { content_snapshot: { sections: [{ key: 's1', title: '研究方法', paragraphs: [{ id: 'p1' }] }] } },
      comments: [{ id: 'c1', block_id: 'p1', body: '补充数据', status: 'open' }] }
    const wrapper = mount(SubmissionFeedback, { props: { detail, canReply: true } })
    const link = wrapper.getComponent({ name: 'RouterLink' })
    expect(link.props('to')).toEqual({ path: '/write/project-1', query: { review_block: 'p1', review_submission: 'submission-1', review_comment: 'c1' } })
    expect(wrapper.text()).toContain('研究方法')
  })

  it('does not offer a location for an absent or ambiguous snapshot block', () => {
    const detail = { participant: { project_id: 'project-1' }, submission: { id: 's1' },
      version: { content_snapshot: { sections: [{ key: 's1', paragraphs: [{ id: 'p1' }, { id: 'p1' }] }] } },
      comments: [{ id: 'c1', block_id: 'p1', body: '补充数据', status: 'open' }] }
    const wrapper = mount(SubmissionFeedback, { props: { detail, canReply: true } })
    expect(wrapper.findComponent({ name: 'RouterLink' }).exists()).toBe(false)
  })

  it('shows the original quoted text together with the teacher feedback', () => {
    const detail = { participant: { project_id: 'project-1' }, submission: { id: 'submission-1' },
      version: { content_snapshot: { sections: [{ key: 's1', title: '研究方法', paragraphs: [{ id: 'p1' }] }] } },
      comments: [{ id: 'c1', block_id: 'p1', body: '请核对结果', quoted_text: '结果😀', status: 'open' }] }
    const wrapper = mount(SubmissionFeedback, { props: { detail, canReply: true } })
    expect(wrapper.get('blockquote').text()).toBe('结果😀')
    expect(wrapper.get('.feedback-comment').text()).toContain('请核对结果')
  })

  it('shows earlier unresolved feedback and replies to its original submission', async () => {
    const prior = { id: 'old-comment', submission_id: 'old-submission', block_id: 'old-block',
      body: '补充数据来源', status: 'open', severity: 'blocking', source_submission_number: 'SUB-OLD',
      source_section_title: '研究方法', source_text: '原稿内容', responses: [] }
    const detail = { participant: { project_id: 'project-1' }, submission: { id: 'current-submission' },
      version: { content_snapshot: { sections: [] } }, comments: [], prior_unresolved_comments: [prior] }
    respondSubmissionComment.mockResolvedValue({ ...prior, status: 'addressed' })
    const wrapper = mount(SubmissionFeedback, { props: { detail, canReply: true } })
    const feedback = wrapper.get('[data-test="student-prior-feedback"]')
    expect(feedback.text()).toContain('SUB-OLD')
    expect(feedback.text()).toContain('原稿内容')
    expect(feedback.getComponent({ name: 'RouterLink' }).props('to')).toEqual({
      path: '/write/project-1', query: { review_block: 'old-block', review_submission: 'old-submission', review_comment: 'old-comment' },
    })
    await feedback.get('textarea').setValue('已补充来源')
    await feedback.get('form').trigger('submit')
    await flushPromises()
    expect(respondSubmissionComment).toHaveBeenCalledWith('old-comment', '已补充来源')
    expect(wrapper.emitted('replied')[0][0].status).toBe('addressed')
  })
})
