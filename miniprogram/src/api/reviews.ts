import { request } from './request'
import type { Comment } from './submissions'

export interface ReviewDecisionPayload {
  decision: 'returned' | 'approved'
  reason: string
  manual_confirmations?: Record<string, boolean>
  revision_deadline_at?: string
}

export interface ReviewResult {
  review: { id: string; decision: string; created_at: string; [key: string]: unknown }
  submission: { id: string; state: string; [key: string]: unknown }
  participant: { state: string; revision_deadline_at?: string; [key: string]: unknown }
}

export const decideSubmission = (submissionId: string, payload: ReviewDecisionPayload) =>
  request<ReviewResult>(`/api/submissions/${submissionId}/reviews`, { method: 'POST', data: { ...payload } })

export const addComment = (
  submissionId: string,
  payload: { body: string; severity: 'blocking' | 'info'; block_id?: string; quoted_text?: string },
) => request<Comment>(`/api/submissions/${submissionId}/comments`, { method: 'POST', data: payload })

export const setCommentStatus = (commentId: string, status: 'open' | 'resolved') =>
  request<Comment>(`/api/comments/${commentId}/status`, { method: 'POST', data: { status } })

export interface VersionContent {
  id: string
  content_snapshot?: {
    sections?: Array<{
      key: string
      title?: string
      paragraphs?: Array<{ id: string; type: string; text?: string }>
    }>
  }
  content_hash?: string
  [key: string]: unknown
}

/** 只读版本快照: 审阅正文按章节懒加载渲染(设计方案 §9 长正文约束)。 */
export const fetchVersionContent = (versionId: string) => request<VersionContent>(`/api/project-versions/${versionId}`)
