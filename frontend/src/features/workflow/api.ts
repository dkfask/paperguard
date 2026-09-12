import {
  acknowledgeTaskRequirements,
  bindTaskProject,
  createSubmissionCandidate,
  fetchMyTasks,
  fetchProcessRecordInbox,
  fetchSubmissions,
  fetchWorkflowJobs,
  fetchWorkflowNotifications,
  fetchWorkflowReport,
  reviewSubmission,
  submitProcessRecord,
} from '../../api.js'

export const workflowApi = {
  tasks: () => fetchMyTasks(),
  notifications: () => fetchWorkflowNotifications(),
  processInbox: () => fetchProcessRecordInbox(),
  report: (cohortId: string | number) => fetchWorkflowReport(cohortId),
  submissions: () => fetchSubmissions(),
  jobs: () => fetchWorkflowJobs(),
  bindProject: (participantId: string | number, payload: Record<string, unknown> = {}) => bindTaskProject(participantId, payload),
  acknowledgeRequirements: (participantId: string | number, snapshotHash = '') => acknowledgeTaskRequirements(participantId, snapshotHash),
  createCandidate: (payload: Record<string, unknown>) => createSubmissionCandidate(payload),
  submitProcessRecord: (recordId: string | number) => submitProcessRecord(recordId),
  reviewSubmission: (submissionId: string | number, payload: Record<string, unknown>) => reviewSubmission(submissionId, payload),
}
