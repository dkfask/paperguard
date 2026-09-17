import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { notificationTarget } from '../src/utils/student-flow.ts'
import { parseSubscribeTemplateIds, reportsFromSubscribeResult } from '../src/utils/subscribe.ts'
import { describeApiError } from '../src/utils/error-messages.ts'

describe('MP3 subscribe templates', () => {
  it('parses configured template ids and ignores blanks', () => {
    assert.deepEqual(parseSubscribeTemplateIds(' tmpl-a ,tmpl-b, ,tmpl-c '), ['tmpl-a', 'tmpl-b', 'tmpl-c'])
    assert.deepEqual(parseSubscribeTemplateIds(''), [])
    assert.deepEqual(parseSubscribeTemplateIds(undefined), [])
  })

  it('reports accept versus reject per template', () => {
    const reports = reportsFromSubscribeResult(
      ['tmpl-review', 'tmpl-deadline'],
      { 'tmpl-review': 'accept', 'tmpl-deadline': 'reject' },
    )
    assert.deepEqual(reports, [
      { templateId: 'tmpl-review', accepted: true },
      { templateId: 'tmpl-deadline', accepted: false },
    ])
  })
})

describe('UGC content security messages', () => {
  it('maps 422 content security rejection to edit-and-retry copy', () => {
    assert.equal(
      describeApiError({ status: 422, code: 'CONTENT_SECURITY_REJECTED', message: 'blocked' }),
      '文本未通过内容安全检查，请修改后再提交',
    )
    assert.equal(
      describeApiError({ status: 503, code: 'CONTENT_SECURITY_UNAVAILABLE' }),
      '内容安全检查暂时不可用，请稍后重试',
    )
  })
})

describe('MP3 subscribe card targets', () => {
  it('maps review result, teacher queue, comments, and deadline cards', () => {
    assert.equal(
      notificationTarget({ event_type: 'submission.returned', resource_type: 'submission', resource_id: 'sub-1' }),
      '/pages/submissions/detail?id=sub-1',
    )
    assert.equal(
      notificationTarget({ event_type: 'submission.approved', resource_type: 'submission', resource_id: 'sub-1' }),
      '/pages/submissions/detail?id=sub-1',
    )
    assert.equal(
      notificationTarget(
        { event_type: 'submission.created', resource_type: 'submission', resource_id: 'sub-2' },
        'teacher',
      ),
      '/pages/review/index?id=sub-2',
    )
    assert.equal(
      notificationTarget({ event_type: 'comment.created', resource_type: 'submission', resource_id: 'sub-3' }),
      '/pages/submissions/comments?id=sub-3',
    )
    assert.equal(
      notificationTarget({ event_type: 'participant.overdue', resource_type: 'participant', resource_id: 'p-1' }),
      '/pages/tasks/index',
    )
    assert.equal(
      notificationTarget({ event_type: 'participant.deadline_soon', resource_type: 'participant', resource_id: 'p-1' }),
      '/pages/tasks/index',
    )
  })
})
