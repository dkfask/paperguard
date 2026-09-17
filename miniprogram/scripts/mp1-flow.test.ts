import assert from 'node:assert/strict'
import { describe, it, beforeEach } from 'node:test'
import { deadlineCountdownLabel, formatDateTime, isDeadlinePassed, shortHash } from '../src/utils/format.ts'
import {
  flattenVersionDiff,
  canWithdrawSubmission,
  effectiveDeadline,
  historyItemNote,
  latestReturnReason,
  materialGateItems,
  submissionTimeline,
  notificationTarget,
  notificationTitle,
  resubmitUrl,
  splitStudentTasks,
  studentPrimaryAction,
  submitRecoveryAction,
  summarizeRequirementItems,
} from '../src/utils/student-flow.ts'
import { idempotencyKey, renewIdempotencyKey } from '../src/utils/idempotency.ts'

const storage = new Map<string, string>()
;(globalThis as { uni?: unknown }).uni = {
  getStorageSync(key: string) {
    return storage.get(key) || ''
  },
  setStorageSync(key: string, value: string) {
    storage.set(key, value)
  },
  removeStorageSync(key: string) {
    storage.delete(key)
  },
}

describe('MP1 student flow', () => {
  it('maps not_started tasks to acknowledge', () => {
    const action = studentPrimaryAction({ id: 'p1', state: 'not_started' })
    assert.equal(action.kind, 'acknowledge')
    assert.equal(action.label, '确认阶段要求')
    assert.equal(action.url, '/pages/requirements/index?id=p1')
  })

  it('summarizes stage policy items and blocks ack without a bound project', () => {
    const unbound = summarizeRequirementItems({ participant: {} })
    assert.equal(unbound.projectBound, false)
    assert.equal(unbound.items.length, 0)
    const bound = summarizeRequirementItems({
      participant: { project_id: 'proj-1', acknowledged_snapshot_hash: '' },
      config: {
        requirement_snapshot: {
          content_hash: 'abc',
          policy_sets: [{ name: '计软学院规范', version: '1' }],
          resolved_items: [{ id: 'r1', description: '正文不少于 8000 字', source: '计软学院规范', source_version: '1' }],
        },
      },
    })
    assert.equal(bound.projectBound, true)
    assert.equal(bound.snapshotHash, 'abc')
    assert.equal(bound.policies[0].name, '计软学院规范')
    assert.equal(bound.items[0].title, '正文不少于 8000 字')
  })

  it('maps in_progress tasks to preflight submit', () => {
    const action = studentPrimaryAction({ id: 'p1', state: 'in_progress' })
    assert.equal(action.kind, 'submit')
    assert.equal(action.url, '/pages/submit/index?participant_id=p1')
  })

  it('maps needs_revision to comments then resubmit url', () => {
    const action = studentPrimaryAction({
      id: 'p1',
      state: 'needs_revision',
      current_submission: { id: 's1' },
    })
    assert.equal(action.kind, 'comments')
    assert.equal(action.url, '/pages/submissions/comments?id=s1')
    assert.equal(resubmitUrl('p1'), '/pages/submit/index?participant_id=p1&revision=1')
  })

  it('maps teacher submitted tasks to review', () => {
    const action = studentPrimaryAction(
      { id: 'p1', state: 'submitted', current_submission: { id: 's9' } },
      'teacher',
    )
    assert.equal(action.kind, 'review')
    assert.equal(action.label, '去审阅')
    assert.equal(action.url, '/pages/review/index?id=s9')
  })

  it('maps teacher decided tasks to the read-only review record', () => {
    const action = studentPrimaryAction(
      { id: 'p1', state: 'approved', current_submission: { id: 's8' } },
      'teacher',
    )
    assert.equal(action.kind, 'review')
    assert.equal(action.label, '查看审阅记录')
    assert.equal(action.url, '/pages/review/index?id=s8')
  })

  it('opens comment notifications on the comments page', () => {
    assert.equal(
      notificationTarget({ event_type: 'comment.created', resource_type: 'submission', resource_id: 's1' }),
      '/pages/submissions/comments?id=s1',
    )
  })

  it('opens returned submissions on the detail page', () => {
    assert.equal(
      notificationTarget({ event_type: 'submission.returned', resource_type: 'submission', resource_id: 's2' }),
      '/pages/submissions/detail?id=s2',
    )
  })

  it('uses notification data.title when message is absent', () => {
    assert.equal(
      notificationTitle({ data: { title: '论文需要修改' } }),
      '论文需要修改',
    )
  })

  it('reads nested version compare sections from the backend contract', () => {
    const lines = flattenVersionDiff({
      diff: { sections: [{ key: 'abstract', status: 'changed' }, { key: 'intro', status: 'added' }] },
    })
    assert.deepEqual(lines.map((line) => line.mark), ['~', '+'])
  })

  it('shows returned review reason and hides it for pending submissions', () => {
    const view = latestReturnReason({
      submission: { state: 'returned' },
      review: { decision: 'returned', reason: '需要补充实验分析', created_at: '2026-09-16T08:00:00Z' },
      participant: { revision_deadline_at: '2026-09-23T08:00:00Z' },
    })
    assert.equal(view?.reason, '需要补充实验分析')
    assert.equal(view?.revisionDeadlineAt, '2026-09-23T08:00:00Z')
    assert.equal(latestReturnReason({ submission: { state: 'submitted' }, review: null }), null)
    assert.equal(latestReturnReason({ submission: { state: 'returned' }, review: { decision: 'returned', reason: '  ' } }), null)
  })

  it('folds approved stages into history and keeps active stages current', () => {
    const split = splitStudentTasks([
      { id: 'a', state: 'in_progress' },
      { id: 'b', state: 'submitted' },
      { id: 'c', state: 'approved' },
      { id: 'd', state: 'archived' },
    ])
    assert.deepEqual(split.current.map((item) => item.id), ['a', 'b'])
    assert.deepEqual(split.history.map((item) => item.id), ['c', 'd'])
  })

  it('allows student withdraw only while the submission is still pending', () => {
    const pending = {
      submission: { state: 'submitted' },
      participant: { state: 'submitted', revision_deadline_at: '2099-01-01T00:00:00Z' },
    }
    assert.equal(canWithdrawSubmission(pending, 'student', Date.parse('2026-09-16T00:00:00Z')), true)
    assert.equal(canWithdrawSubmission(pending, 'teacher', Date.parse('2026-09-16T00:00:00Z')), false)
    assert.equal(canWithdrawSubmission({ ...pending, submission: { state: 'returned' } }, 'student'), false)
    assert.equal(canWithdrawSubmission({
      submission: { state: 'submitted' },
      participant: { state: 'submitted', is_late: true },
    }, 'student'), false)
  })

  it('surfaces modification notes and unresolved blocking counts on history items', () => {
    assert.deepEqual(historyItemNote({ modification_note: '  按意见修订  ', open_blocking_count: 2 }), {
      note: '按意见修订',
      openBlocking: 2,
    })
    assert.deepEqual(historyItemNote({}), { note: '', openBlocking: 0 })
  })

  it('maps submit recovery next actions for deadline, pending, and stale checks', () => {
    const deadline = submitRecoveryAction({ code: 'DEADLINE_PASSED' })
    assert.equal(deadline.kind, 'contact-teacher')
    assert.equal(deadline.url, '/pages/tasks/index')
    const pending = submitRecoveryAction({ code: 'SUBMISSION_ALREADY_PENDING', submissionId: 's9' })
    assert.equal(pending.kind, 'open-pending')
    assert.equal(pending.url, '/pages/submissions/detail?id=s9')
    assert.equal(submitRecoveryAction({ code: 'CHECK_STALE' }).kind, 'repreflight')
    assert.equal(submitRecoveryAction({ status: 409 }).kind, 'repreflight')
    assert.equal(submitRecoveryAction({ code: 'CHECK_PENDING' }).label, '检查未完成，稍后重试')
    assert.equal(submitRecoveryAction({ code: 'CHECK_FAILED' }).label, '检查失败，重新预检')
  })

  it('prefers revision deadline then effective deadline', () => {
    assert.equal(effectiveDeadline({
      revision_deadline_at: '2026-09-23T00:00:00Z',
      effective_deadline_at: '2026-09-20T00:00:00Z',
      initial_deadline_at: '2026-09-10T00:00:00Z',
    }), '2026-09-23T00:00:00Z')
    assert.equal(effectiveDeadline({ effective_deadline_at: '2026-09-20T00:00:00Z' }), '2026-09-20T00:00:00Z')
    assert.equal(effectiveDeadline({}), '')
  })

  it('lists required materials from the materials gate', () => {
    const items = materialGateItems([
      { key: 'automatic_checks', passed: true, blocking: true, items: [] },
      {
        key: 'materials',
        passed: false,
        blocking: true,
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

  it('builds a submission timeline from submit, withdraw, and review events', () => {
    const pending = submissionTimeline({
      submission: { submitted_at: '2026-09-16T08:00:00Z' },
      review: null,
    })
    assert.deepEqual(pending, [
      { title: '正式提交，冻结版本快照', time: '2026-09-16T08:00:00Z', tone: 'blue' },
    ])
    const withdrawn = submissionTimeline({
      submission: { submitted_at: '2026-09-16T08:00:00Z', withdrawn_at: '2026-09-16T09:00:00Z' },
    })
    assert.equal(withdrawn.at(-1)?.title, '学生撤回该提交')
    const returned = submissionTimeline({
      submission: { submitted_at: '2026-09-16T08:00:00Z' },
      review: { decision: 'returned', created_at: '2026-09-16T10:00:00Z' },
    })
    assert.equal(returned.at(-1)?.title, '教师退回')
    const approved = submissionTimeline({
      submission: { submitted_at: '2026-09-16T08:00:00Z' },
      review: { decision: 'approved', created_at: '2026-09-16T11:00:00Z' },
    })
    assert.equal(approved.at(-1)?.title, '教师通过')
  })

  it('formats hashes and late deadlines', () => {
    assert.equal(shortHash('abcdefghijklmnop'), 'abcdef…mnop')
    assert.equal(formatDateTime('2026-09-16T08:00:00+00:00'), '2026-09-16 08:00')
    assert.equal(isDeadlinePassed('2000-01-01T00:00:00Z', Date.parse('2026-09-16T00:00:00Z')), true)
    assert.equal(isDeadlinePassed('2099-01-01T00:00:00Z', Date.parse('2026-09-16T00:00:00Z')), false)
    const now = Date.parse('2026-09-16T12:00:00Z')
    assert.equal(deadlineCountdownLabel('2026-09-16T13:00:00Z', now), '剩余 1 小时')
    assert.equal(deadlineCountdownLabel('2026-09-15T12:00:00Z', now), '已逾期 24 小时')
    assert.equal(deadlineCountdownLabel('2026-09-13T12:00:00Z', now), '已逾期 3 天')
  })
})

describe('submission idempotency', () => {
  beforeEach(() => {
    storage.clear()
    renewIdempotencyKey('submission:p1')
  })

  it('reuses the same key across retries until renew', () => {
    const first = idempotencyKey('submission:p1')
    const second = idempotencyKey('submission:p1')
    assert.equal(first, second)
    assert.ok(first.length >= 8)
    renewIdempotencyKey('submission:p1')
    const third = idempotencyKey('submission:p1')
    assert.notEqual(first, third)
  })
})
