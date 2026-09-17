import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { materialGateItems, notificationTarget } from '../src/utils/student-flow.ts'
import {
  buildReturnPayload,
  canApproveReview,
  commentPayload,
  defaultRevisionDeadline,
  groupComments,
  canDecideReview,
  teacherQueue,
  teacherTaskAction,
  waitDurationLabel,
} from '../src/utils/teacher-flow.ts'

describe('MP2 teacher queue', () => {
  it('splits pending review, recheck, and the rest', () => {
    const queue = teacherQueue([
      {
        id: 'later',
        state: 'submitted',
        student_name: '李四',
        current_submission: { id: 's2', submitted_at: '2026-09-16T10:00:00Z', review_cycle: 1 },
      },
      {
        id: 'earlier',
        state: 'submitted',
        student_name: '张三',
        current_submission: { id: 's1', submitted_at: '2026-09-16T08:00:00Z', review_cycle: 1 },
      },
      {
        id: 'recheck',
        state: 'submitted',
        student_name: '王五',
        current_submission: { id: 's3', submitted_at: '2026-09-16T09:00:00Z', review_cycle: 2 },
      },
      { id: 'done', state: 'approved', current_submission: { id: 's0' } },
    ])
    assert.deepEqual(queue.pending.map((item) => item.id), ['earlier', 'later'])
    assert.deepEqual(queue.recheck.map((item) => item.id), ['recheck'])
    assert.deepEqual(queue.rest.map((item) => item.id), ['done'])
  })

  it('labels wait duration from submitted_at', () => {
    const now = Date.parse('2026-09-16T12:00:00Z')
    assert.equal(waitDurationLabel('', now), '等待时长未知')
    assert.equal(waitDurationLabel('2026-09-16T11:40:00Z', now), '已等待 20 分钟')
    assert.equal(waitDurationLabel('2026-09-16T09:00:00Z', now), '已等待 3 小时')
    assert.equal(waitDurationLabel('2026-09-13T12:00:00Z', now), '已等待 3 天')
    assert.equal(waitDurationLabel('2026-09-16T13:00:00Z', now), '刚刚提交')
  })

  it('opens decided rest tasks as a read-only review record', () => {
    const decided = teacherTaskAction({
      id: 'done',
      state: 'approved',
      current_submission: { id: 's0' },
    })
    assert.equal(decided.label, '查看审阅记录')
    assert.equal(decided.kind, 'review')
    assert.equal(decided.url, '/pages/review/index?id=s0')
    const pending = teacherTaskAction({
      id: 'pending',
      state: 'submitted',
      current_submission: { id: 's1', review_cycle: 1 },
    })
    assert.equal(pending.label, '去审阅')
    const recheck = teacherTaskAction({
      id: 'recheck',
      state: 'submitted',
      current_submission: { id: 's3', review_cycle: 2 },
    })
    assert.equal(recheck.label, '去复核')
    const empty = teacherTaskAction({ id: 'empty', state: 'in_progress' })
    assert.equal(empty.kind, 'desktop')
    const withdrawn = teacherTaskAction({
      id: 'withdrawn',
      state: 'in_progress',
      current_submission: { id: 's-w', state: 'withdrawn' },
    })
    assert.equal(withdrawn.label, '查看审阅记录')
    assert.equal(withdrawn.url, '/pages/review/index?id=s-w')
    assert.equal(canDecideReview('submitted'), true)
    assert.equal(canDecideReview('returned'), false)
    assert.equal(canDecideReview('approved'), false)
  })
})

describe('MP2 review gates', () => {
  const ready = {
    submissionState: 'submitted',
    blockingOpen: 0,
    manualChecks: ['原创性确认'],
    confirmations: { 原创性确认: true },
    candidateReady: true,
    blockingGates: 0,
  }

  it('blocks approve when automatic checks or comments are open', () => {
    assert.equal(canApproveReview({ ...ready, submissionState: 'returned' }).ok, false)
    assert.match(canApproveReview({ ...ready, blockingOpen: 2 }).reason, /阻断项/)
    assert.match(canApproveReview({ ...ready, candidateReady: false }).reason, /自动检查/)
    assert.match(canApproveReview({ ...ready, blockingGates: 1 }).reason, /提交门禁/)
    assert.match(canApproveReview({ ...ready, confirmations: {} }).reason, /人工确认/)
  })

  it('allows approve only when comments, gates, and confirmations are clear', () => {
    const result = canApproveReview(ready)
    assert.equal(result.ok, true)
    assert.match(result.reason, /通过后/)
  })

  it('requires reason, modification items, and revision deadline to return', () => {
    assert.equal(buildReturnPayload({ reason: '', requirements: '补实验', deadline: '2026-09-23' }).ok, false)
    assert.equal(buildReturnPayload({ reason: '缺基线', requirements: '', deadline: '2026-09-23' }).ok, false)
    assert.equal(buildReturnPayload({ reason: '缺基线', requirements: '补实验', deadline: '' }).ok, false)
    const ok = buildReturnPayload({ reason: '缺基线', requirements: '补实验', deadline: '2026-09-23' })
    assert.equal(ok.ok, true)
    assert.equal(ok.payload?.decision, 'returned')
    assert.equal(ok.payload?.reason, '缺基线\n修改要求：补实验')
    assert.equal(ok.payload?.revision_deadline_at, '2026-09-23T23:59:00+00:00')
  })

  it('defaults revision deadline from stage revision days', () => {
    assert.equal(defaultRevisionDeadline(7, new Date('2026-09-16T08:00:00Z')), '2026-09-23')
  })

  it('lists required materials for the review checks tab', () => {
    const items = materialGateItems([
      { key: 'automatic_checks', passed: true, items: [] },
      {
        key: 'materials',
        passed: false,
        items: [
          { label: '开题报告', passed: true },
          { label: '查重报告', passed: false },
        ],
      },
    ])
    assert.deepEqual(items, [
      { label: '开题报告', passed: true },
      { label: '查重报告', passed: false },
    ])
  })
})

describe('MP2 comments', () => {
  it('groups blocking, suggestions, and resolved comments', () => {
    const grouped = groupComments([
      { id: 'c1', severity: 'blocking', status: 'open' },
      { id: 'c2', severity: 'info', status: 'addressed' },
      { id: 'c3', severity: 'blocking', status: 'resolved' },
    ])
    assert.deepEqual(grouped.blocking.map((item) => item.id), ['c1'])
    assert.deepEqual(grouped.suggestions.map((item) => item.id), ['c2'])
    assert.deepEqual(grouped.resolved.map((item) => item.id), ['c3'])
  })

  it('trims comment payload and keeps blocking|info severity', () => {
    assert.deepEqual(
      commentPayload({ body: '  缺对照  ', severity: 'blocking', blockId: ' p1 ', quotedText: ' 原文 ' }),
      { body: '缺对照', severity: 'blocking', block_id: 'p1', quoted_text: '原文' },
    )
  })

  it('opens teacher submission notifications on the review page', () => {
    assert.equal(
      notificationTarget({ event_type: 'comment.created', resource_type: 'submission', resource_id: 's1' }, 'teacher'),
      '/pages/review/index?id=s1',
    )
    assert.equal(
      notificationTarget({ event_type: 'submission.submitted', resource_type: 'submission', resource_id: 's2' }, 'teacher'),
      '/pages/review/index?id=s2',
    )
  })
})
