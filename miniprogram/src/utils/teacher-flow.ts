export interface TeacherTask {
  id: string
  state?: string
  student_id?: string
  student_name?: string
  stage_name?: string
  cohort_name?: string
  project_title?: string
  title?: string
  is_late?: boolean
  current_submission?: {
    id?: string
    number?: string
    state?: string
    submitted_at?: string
    review_cycle?: number
  } | null
}

export interface ReviewGateInput {
  submissionState?: string
  blockingOpen: number
  manualChecks: string[]
  confirmations: Record<string, boolean>
  candidateReady: boolean
  blockingGates: number
}

export interface CommentLike {
  id: string
  severity?: string
  status?: string
  body?: string
}

export function isPendingReview(task: TeacherTask): boolean {
  return String(task.state) === 'submitted' && Boolean(task.current_submission?.id)
}

export function isPendingRecheck(task: TeacherTask): boolean {
  return String(task.state) === 'submitted' && Number(task.current_submission?.review_cycle || 1) > 1
}

function bySubmittedAt(a: TeacherTask, b: TeacherTask) {
  return String(a.current_submission?.submitted_at || '').localeCompare(String(b.current_submission?.submitted_at || ''))
}

export function teacherQueue<T extends TeacherTask>(tasks: T[]): { pending: T[]; recheck: T[]; rest: T[] } {
  const pending: T[] = []
  const recheck: T[] = []
  const rest: T[] = []
  for (const task of tasks) {
    if (!isPendingReview(task)) {
      rest.push(task)
      continue
    }
    if (isPendingRecheck(task)) recheck.push(task)
    else pending.push(task)
  }
  pending.sort(bySubmittedAt)
  recheck.sort(bySubmittedAt)
  return { pending, recheck, rest }
}

export interface TeacherTaskAction {
  label: string
  kind: 'review' | 'desktop'
  url?: string
}

/** 待审去审阅/复核；已有决定则打开只读审阅记录，不再误导去电脑端。 */
export function teacherTaskAction(task: TeacherTask): TeacherTaskAction {
  const submissionId = String(task.current_submission?.id || '').trim()
  if (isPendingReview(task) && submissionId) {
    return {
      label: isPendingRecheck(task) ? '去复核' : '去审阅',
      kind: 'review',
      url: `/pages/review/index?id=${submissionId}`,
    }
  }
  if (submissionId) {
    return {
      label: '查看审阅记录',
      kind: 'review',
      url: `/pages/review/index?id=${submissionId}`,
    }
  }
  return { label: '暂无可审阅提交', kind: 'desktop' }
}

export function canDecideReview(submissionState?: string): boolean {
  return String(submissionState || '') === 'submitted'
}

export function defaultRevisionDeadline(days: number, now = new Date()): string {
  const safeDays = Number.isFinite(days) && days > 0 ? Math.floor(days) : 7
  const date = new Date(now.getTime() + safeDays * 86400000)
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function waitDurationLabel(submittedAt: unknown, now = Date.now()): string {
  if (!submittedAt) return '等待时长未知'
  const started = Date.parse(String(submittedAt))
  if (!Number.isFinite(started) || started > now) return '刚刚提交'
  const minutes = Math.max(0, Math.floor((now - started) / 60000))
  if (minutes < 60) return `已等待 ${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `已等待 ${hours} 小时`
  return `已等待 ${Math.floor(hours / 24)} 天`
}

export function canApproveReview(input: ReviewGateInput): { ok: boolean; reason: string } {
  if (String(input.submissionState) !== 'submitted') return { ok: false, reason: '该提交已有最终决定' }
  if (input.blockingOpen > 0) return { ok: false, reason: `存在 ${input.blockingOpen} 条阻断项待确认解决，暂不可通过` }
  if (!input.candidateReady || input.blockingGates > 0) return { ok: false, reason: '自动检查或提交门禁未通过，暂不可通过' }
  const pending = input.manualChecks.filter((name) => input.confirmations[name] !== true)
  if (pending.length) return { ok: false, reason: `完成 ${pending.length} 项人工确认后可通过` }
  return { ok: true, reason: '通过后初稿阶段解锁，终稿阶段进入归档流程' }
}

export function buildReturnPayload(input: { reason: string; requirements: string; deadline: string }): {
  ok: boolean
  reason?: string
  payload?: { decision: 'returned'; reason: string; revision_deadline_at: string }
} {
  const reason = input.reason.trim()
  const requirements = input.requirements.trim()
  const deadline = input.deadline.trim()
  if (!reason) return { ok: false, reason: '退回必须填写原因' }
  if (!requirements) return { ok: false, reason: '退回必须填写修改要求' }
  if (!deadline) return { ok: false, reason: '退回必须选择修订截止' }
  return {
    ok: true,
    payload: {
      decision: 'returned',
      reason: `${reason}\n修改要求：${requirements}`,
      revision_deadline_at: `${deadline}T23:59:00+00:00`,
    },
  }
}

export function groupComments<T extends CommentLike>(comments: T[]): { blocking: T[]; suggestions: T[]; resolved: T[] } {
  const blocking: T[] = []
  const suggestions: T[] = []
  const resolved: T[] = []
  for (const comment of comments) {
    if (comment.status === 'resolved') {
      resolved.push(comment)
      continue
    }
    if (comment.severity === 'blocking') blocking.push(comment)
    else suggestions.push(comment)
  }
  return { blocking, suggestions, resolved }
}

export function commentPayload(input: { body: string; severity: 'blocking' | 'info'; blockId?: string; quotedText?: string }) {
  return {
    body: input.body.trim(),
    severity: input.severity,
    block_id: (input.blockId || '').trim(),
    quoted_text: (input.quotedText || '').trim(),
  }
}
