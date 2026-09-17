export interface TaskLike {
  id: string
  state?: string
  role?: string
  current_submission?: { id?: string; review_cycle?: number } | null
}

export interface NotificationLike {
  id?: string
  event_type?: string
  resource_type?: string
  resource_id?: string
  data?: Record<string, unknown> | null
  title?: unknown
  message?: unknown
}

export interface StudentAction {
  label: string
  kind: 'acknowledge' | 'submit' | 'comments' | 'detail' | 'review' | 'desktop'
  url?: string
}

export function acknowledgeUrl(participantId: string): string {
  return `/pages/requirements/index?id=${encodeURIComponent(participantId)}`
}

export function studentPrimaryAction(task: TaskLike, role = 'student'): StudentAction {
  const state = String(task.state || '')
  const submissionId = task.current_submission?.id || ''
  if (role === 'teacher') {
    if (state === 'submitted' && submissionId) {
      const recheck = Number(task.current_submission?.review_cycle || 1) > 1
      return { label: recheck ? '去复核' : '去审阅', kind: 'review', url: `/pages/review/index?id=${submissionId}` }
    }
    if (submissionId) {
      return { label: '查看审阅记录', kind: 'review', url: `/pages/review/index?id=${submissionId}` }
    }
    return { label: '暂无可审阅提交', kind: 'desktop' }
  }
  if (state === 'not_started') {
    return { label: '确认阶段要求', kind: 'acknowledge', url: acknowledgeUrl(task.id) }
  }
  if (state === 'in_progress') {
    return {
      label: '预检提交',
      kind: 'submit',
      url: `/pages/submit/index?participant_id=${task.id}`,
    }
  }
  if (state === 'needs_revision') {
    if (submissionId) return { label: '查看审阅意见', kind: 'comments', url: `/pages/submissions/comments?id=${submissionId}` }
    return { label: '重新预检提交', kind: 'submit', url: resubmitUrl(task.id) }
  }
  if (submissionId) {
    return { label: state === 'submitted' ? '查看提交详情' : '查看记录', kind: 'detail', url: `/pages/submissions/detail?id=${submissionId}` }
  }
  return { label: '请使用电脑端处理', kind: 'desktop' }
}

export function resubmitUrl(participantId: string): string {
  return `/pages/submit/index?participant_id=${encodeURIComponent(participantId)}&revision=1`
}

export function notificationTitle(item: NotificationLike): string {
  const data = item.data || {}
  return String(item.message || item.title || data.title || data.message || '你有一条新的论文卫士Pro消息')
}

export function notificationTarget(item: NotificationLike, role = 'student'): string | null {
  const eventType = String(item.event_type || '')
  const resourceType = String(item.resource_type || '')
  const resourceId = String(item.resource_id || '')
  if (!resourceId) return null
  if (eventType === 'comment.created' && resourceType === 'submission') {
    if (role === 'teacher') return `/pages/review/index?id=${resourceId}`
    return `/pages/submissions/comments?id=${resourceId}`
  }
  if (resourceType === 'submission') {
    if (role === 'teacher') return `/pages/review/index?id=${resourceId}`
    return `/pages/submissions/detail?id=${resourceId}`
  }
  if (resourceType === 'participant' || eventType === 'participant.extended') {
    return '/pages/tasks/index'
  }
  if (eventType === 'comment.responded') {
    if (role === 'teacher' && resourceType === 'submission') return `/pages/review/index?id=${resourceId}`
    return '/pages/tasks/index'
  }
  return null
}

export function canResubmit(state: unknown): boolean {
  return String(state || '') === 'needs_revision'
}

export const ACTIVE_STUDENT_TASK_STATES = ['not_started', 'in_progress', 'submitted', 'needs_revision']
export const HISTORY_STUDENT_TASK_STATES = ['approved', 'pending_archive', 'archived', 'exited']

export function splitStudentTasks<T extends { state?: string }>(tasks: T[]): { current: T[]; history: T[] } {
  const current: T[] = []
  const history: T[] = []
  for (const task of tasks) {
    const state = String(task.state || '')
    if (HISTORY_STUDENT_TASK_STATES.includes(state)) history.push(task)
    else current.push(task)
  }
  return { current, history }
}

export function canWithdrawSubmission(detail: {
  submission?: { state?: string }
  participant?: { state?: string; student_id?: string; is_late?: boolean; revision_deadline_at?: string; effective_deadline_at?: string }
} | null | undefined, role = 'student', now = Date.now()): boolean {
  if (role !== 'student') return false
  if (String(detail?.submission?.state || '') !== 'submitted') return false
  if (String(detail?.participant?.state || '') !== 'submitted') return false
  const deadline = detail?.participant?.revision_deadline_at || detail?.participant?.effective_deadline_at
  if (detail?.participant?.is_late || isDeadlinePassed(deadline, now)) return false
  return true
}

function isDeadlinePassed(value: unknown, now: number): boolean {
  if (!value) return false
  const parsed = Date.parse(String(value))
  return Number.isFinite(parsed) && parsed < now
}

export function historyItemNote(item: {
  modification_note?: string
  open_blocking_count?: number
}): { note: string; openBlocking: number } {
  return {
    note: String(item.modification_note || '').trim(),
    openBlocking: Number(item.open_blocking_count || 0),
  }
}

export function effectiveDeadline(task: {
  revision_deadline_at?: string
  effective_deadline_at?: string
  initial_deadline_at?: string
} | null | undefined): string {
  return String(task?.revision_deadline_at || task?.effective_deadline_at || task?.initial_deadline_at || '').trim()
}

export function materialGateItems(gates: Array<{
  key?: string
  items?: Array<{ label?: string; passed?: boolean }>
}> | null | undefined): Array<{ label: string; passed: boolean }> {
  const gate = (gates || []).find((item) => item.key === 'materials')
  return (gate?.items || []).map((item) => ({
    label: String(item.label || '材料'),
    passed: Boolean(item.passed),
  }))
}

export interface SubmissionTimelineItem {
  title: string
  time: string
  tone: 'blue' | 'red' | 'green' | 'gray'
}

export function submissionTimeline(detail: {
  submission?: { submitted_at?: string; withdrawn_at?: string }
  review?: { decision?: string; created_at?: string } | null
} | null | undefined): SubmissionTimelineItem[] {
  const items: SubmissionTimelineItem[] = []
  const submittedAt = String(detail?.submission?.submitted_at || '').trim()
  if (submittedAt) {
    items.push({ title: '正式提交，冻结版本快照', time: submittedAt, tone: 'blue' })
  }
  const withdrawnAt = String(detail?.submission?.withdrawn_at || '').trim()
  if (withdrawnAt) {
    items.push({ title: '学生撤回该提交', time: withdrawnAt, tone: 'gray' })
  }
  const review = detail?.review
  const reviewedAt = String(review?.created_at || '').trim()
  if (reviewedAt && review?.decision === 'returned') {
    items.push({ title: '教师退回', time: reviewedAt, tone: 'red' })
  } else if (reviewedAt && review?.decision === 'approved') {
    items.push({ title: '教师通过', time: reviewedAt, tone: 'green' })
  }
  return items
}

export interface ReturnReasonView {
  reason: string
  createdAt: string
  revisionDeadlineAt: string
}

export function latestReturnReason(detail: {
  submission?: { state?: string }
  review?: { decision?: string; reason?: string; created_at?: string } | null
  participant?: { revision_deadline_at?: string }
} | null | undefined): ReturnReasonView | null {
  const state = String(detail?.submission?.state || '')
  const review = detail?.review
  if (state !== 'returned' && review?.decision !== 'returned') return null
  const reason = String(review?.reason || '').trim()
  if (!reason) return null
  return {
    reason,
    createdAt: String(review?.created_at || ''),
    revisionDeadlineAt: String(detail?.participant?.revision_deadline_at || ''),
  }
}

export interface SubmitRecoveryAction {
  kind: 'repreflight' | 'open-pending' | 'contact-teacher' | 'refresh'
  label: string
  url?: string
}

export function submitRecoveryAction(error: unknown): SubmitRecoveryAction {
  const coded = error && typeof error === 'object'
    ? error as { status?: number; code?: string; submissionId?: string; message?: string }
    : {}
  const code = String(coded.code || '')
  const pendingId = String(coded.submissionId || '')
  if (code === 'DEADLINE_PASSED') {
    return { kind: 'contact-teacher', label: '返回任务并联系教师', url: '/pages/tasks/index' }
  }
  if (code === 'SUBMISSION_ALREADY_PENDING') {
    if (pendingId) {
      return { kind: 'open-pending', label: '查看待审核提交', url: `/pages/submissions/detail?id=${pendingId}` }
    }
    return { kind: 'refresh', label: '返回任务列表', url: '/pages/tasks/index' }
  }
  if (code === 'CHECK_PENDING') {
    return { kind: 'repreflight', label: '检查未完成，稍后重试' }
  }
  if (code === 'CHECK_FAILED') {
    return { kind: 'repreflight', label: '检查失败，重新预检' }
  }
  if (code === 'CHECK_STALE' || code === 'REVISION_CONFLICT' || coded.status === 409) {
    return { kind: 'repreflight', label: '重新预检' }
  }
  return { kind: 'refresh', label: '重试' }
}

export function diffSectionTone(status: unknown): { mark: string; tone: string } {
  const key = String(status || '')
  if (key === 'added') return { mark: '+', tone: 'green' }
  if (key === 'removed') return { mark: '-', tone: 'red' }
  if (key === 'changed' || key === 'modified') return { mark: '~', tone: 'orange' }
  return { mark: '=', tone: 'gray' }
}

export function flattenVersionDiff(payload: { sections?: Array<{ key: string; status: string }>; diff?: { sections?: Array<{ key: string; status: string }> } } | null | undefined) {
  const sections = payload?.diff?.sections || payload?.sections || []
  if (!sections.length) return [{ mark: '=', tone: 'gray', text: '两版本文本无结构差异' }]
  return sections.map((section) => {
    const style = diffSectionTone(section.status)
    return { mark: style.mark, tone: style.tone, text: `${section.key}（${section.status}）` }
  })
}

export interface RequirementItemSummary {
  id: string
  title: string
  source: string
}

export function summarizeRequirementItems(detail: {
  participant?: { project_id?: string; acknowledged_snapshot_hash?: string }
  config?: {
    requirement_snapshot?: {
      content_hash?: string
      policy_sets?: Array<{ id?: string; name?: string; version?: string }>
      resolved_items?: Array<{
        id?: string
        description?: string
        title?: string
        source?: string
        source_version?: string
      }>
    }
    requirement_basis_hash?: string
  }
} | null | undefined): {
  projectBound: boolean
  alreadyAcknowledged: boolean
  snapshotHash: string
  policies: Array<{ name: string; version: string }>
  items: RequirementItemSummary[]
} {
  const snapshot = detail?.config?.requirement_snapshot || {}
  const items = (snapshot.resolved_items || []).map((item, index) => ({
    id: String(item.id || `item-${index}`),
    title: String(item.description || item.title || '未命名要求'),
    source: [item.source, item.source_version].filter(Boolean).join(' · '),
  }))
  const policies = (snapshot.policy_sets || []).map((set) => ({
    name: String(set.name || '阶段规范'),
    version: String(set.version || ''),
  }))
  return {
    projectBound: Boolean(detail?.participant?.project_id),
    alreadyAcknowledged: Boolean(detail?.participant?.acknowledged_snapshot_hash),
    snapshotHash: String(snapshot.content_hash || detail?.config?.requirement_basis_hash || ''),
    policies,
    items,
  }
}
