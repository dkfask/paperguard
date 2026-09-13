import { request } from './request'

export interface Gate {
  key: string
  label: string
  passed: boolean
  blocking: boolean
  detail: string | string[]
  items?: Array<{ label: string; passed: boolean }>
}

export interface CheckReport {
  summary?: Record<string, number>
  findings?: Array<Record<string, unknown>>
  not_checked?: string[]
}

export interface Candidate {
  id: string
  participant_id: string
  project_id: string
  project_version_id: string
  source_revision: number
  content_hash: string
  check_status: string
  check_report: CheckReport
  gates: Gate[]
  warning_count: number
  status: 'ready' | 'blocked' | string
  created_at: string
  check_error_code?: string
  modification_note?: string
}

export interface CommentResponseItem {
  id: string
  author_id: string
  body: string
  created_at: string
}

export interface Comment {
  id: string
  body: string
  severity: 'blocking' | 'suggestion' | string
  block_id: string
  quoted_text: string
  status: 'open' | 'addressed' | 'resolved' | string
  responses: CommentResponseItem[]
  created_at: string
}

export interface Submission {
  id: string
  submission_number: string
  state: 'submitted' | 'returned' | 'approved' | 'withdrawn' | string
  review_cycle: number
  submitted_at: string
  participant_id: string
  project_version_id?: string
  prior_submission_id?: string
  [key: string]: unknown
}

export interface SubmissionDetail {
  submission: Submission
  participant: Record<string, unknown> & { revision_deadline_at?: string; effective_deadline_at?: string }
  stage: Record<string, unknown> & { name?: string }
  config: Record<string, unknown>
  version: Record<string, unknown> & { content_hash?: string }
  candidate: Candidate | null
  comments: Comment[]
}

/** 预检: 冻结候选版本并运行门禁检查(同步返回, 检查失败时 check_status=failed)。 */
export const createCandidate = (participantId: string, materials: string[] = [], modificationNote = '') =>
  request<Candidate>('/api/submission-candidates', {
    method: 'POST',
    data: { participant_id: participantId, materials, modification_note: modificationNote },
  })

export const getCandidate = (candidateId: string) => request<Candidate>(`/api/submission-candidates/${candidateId}`)

/** 确认正式提交: 幂等键相同则返回同一结果(设计方案 §6.1)。 */
export const confirmSubmission = (candidateId: string, idempotencyKey: string, warningAcknowledged: boolean) =>
  request<Submission>('/api/submissions', {
    method: 'POST',
    data: { candidate_id: candidateId, idempotency_key: idempotencyKey, warning_acknowledged: warningAcknowledged },
  })

export const fetchSubmission = (id: string) => request<SubmissionDetail>(`/api/submissions/${id}`)
export const fetchSubmissions = () => request<Submission[]>('/api/submissions')

/** 学生回复批注: 批注状态转为 addressed, 等待教师确认解决。 */
export const respondComment = (commentId: string, body: string) =>
  request<Comment>(`/api/comments/${commentId}/response`, { method: 'POST', data: { body } })

export interface VersionDiff {
  sections?: Array<{ key: string; status: string; before: unknown; after: unknown }>
  [key: string]: unknown
}

export const compareVersions = (projectId: string, leftVersionId: string, rightVersionId: string) =>
  request<VersionDiff>(`/api/projects/${projectId}/versions/compare?left=${leftVersionId}&right=${rightVersionId}`)
