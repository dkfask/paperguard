export const PARTICIPANT_STATE_LABELS: Record<string, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  submitted: '待审核',
  needs_revision: '需修改',
  approved: '已通过',
  pending_archive: '待归档',
  archived: '已归档',
  exited: '已退出',
}

export const PARTICIPANT_STATE_TONES: Record<string, 'blue' | 'orange' | 'green' | 'gray' | 'red'> = {
  not_started: 'gray',
  in_progress: 'blue',
  submitted: 'blue',
  needs_revision: 'orange',
  approved: 'green',
  pending_archive: 'blue',
  archived: 'green',
  exited: 'gray',
}

export const SUBMISSION_STATE_LABELS: Record<string, string> = {
  submitted: '待审核',
  returned: '已退回',
  approved: '已通过',
  withdrawn: '已撤回',
}

export const SUBMISSION_STATE_TONES: Record<string, 'blue' | 'red' | 'green' | 'gray'> = {
  submitted: 'blue',
  returned: 'red',
  approved: 'green',
  withdrawn: 'gray',
}

export const EVENT_TYPE_LABELS: Record<string, string> = {
  'submission.returned': '审核退回',
  'submission.approved': '审核通过',
  'comment.created': '新批注',
  'comment.responded': '批注回复',
  'participant.extended': '截止延期',
}

export function formatDateTime(value: unknown): string {
  if (!value) return ''
  return String(value).replace('T', ' ').replace(/\+\d{2}:\d{2}$/, '').slice(0, 16)
}

export function isDeadlinePassed(value: unknown, now = Date.now()): boolean {
  if (!value) return false
  const parsed = Date.parse(String(value))
  return Number.isFinite(parsed) && parsed < now
}

export function deadlineCountdownLabel(value: unknown, now = Date.now()): string {
  if (!value) return ''
  const parsed = Date.parse(String(value))
  if (!Number.isFinite(parsed)) return ''
  const delta = parsed - now
  const minutes = Math.floor(Math.abs(delta) / 60000)
  if (delta < 0) {
    if (minutes < 60) return `已逾期 ${minutes} 分钟`
    const hours = Math.floor(minutes / 60)
    if (hours < 48) return `已逾期 ${hours} 小时`
    return `已逾期 ${Math.floor(hours / 24)} 天`
  }
  if (minutes < 60) return `剩余 ${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `剩余 ${hours} 小时`
  return `剩余 ${Math.floor(hours / 24)} 天`
}

export function shortHash(value: unknown): string {
  const text = String(value || '')
  if (!text) return '—'
  return text.length > 12 ? `${text.slice(0, 6)}…${text.slice(-4)}` : text
}

export function participantStateLabel(state: unknown): string {
  const key = String(state || '')
  return PARTICIPANT_STATE_LABELS[key] || '进行中'
}

export function participantStateTone(state: unknown) {
  const key = String(state || '')
  return PARTICIPANT_STATE_TONES[key] || 'blue'
}

export function submissionStateLabel(state: unknown): string {
  const key = String(state || '')
  return SUBMISSION_STATE_LABELS[key] || key || '—'
}

export function submissionStateTone(state: unknown) {
  const key = String(state || '')
  return SUBMISSION_STATE_TONES[key] || 'blue'
}

export function eventTypeLabel(eventType: unknown): string {
  const key = String(eventType || '')
  return EVENT_TYPE_LABELS[key] || '系统消息'
}
