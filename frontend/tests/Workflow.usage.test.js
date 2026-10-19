import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
const api = vi.hoisted(() => ({
  addCohortMember: vi.fn(),
  acknowledgeTaskRequirements: vi.fn(),
  bindTaskProject: vi.fn(),
  confirmSubmission: vi.fn(),
  createArchive: vi.fn(),
  createCohort: vi.fn(),
  createCohortProcessRecord: vi.fn(),
  createCohortStage: vi.fn(),
  createParticipantProcessRecord: vi.fn(),
  createSubmissionCandidate: vi.fn(),
  createSubmissionComment: vi.fn(),
  respondSubmissionComment: vi.fn(),
  decideProcessRecord: vi.fn(),
  updateMidtermTeacherFields: vi.fn(),
  downloadProcessRecordAttachment: vi.fn(),
  exportProcessRecord: vi.fn(),
  fetchProcessRecordForm: vi.fn(),
  fetchCohortProcessRecordForm: vi.fn(),
  fetchParticipantScoreSummary: vi.fn(),
  fetchCohort: vi.fn(),
  fetchCohortProcessRecords: vi.fn(),
  fetchCohorts: vi.fn(),
  fetchMyTasks: vi.fn(),
  fetchOrganizations: vi.fn(),
  fetchProjectVersions: vi.fn(),
  fetchSubmission: vi.fn(),
  fetchSubmissions: vi.fn(),
  compareProjectVersions: vi.fn(),
  fetchArchive: vi.fn(),
  fetchArchives: vi.fn(),
  fetchParticipantProcessRecords: vi.fn(),
  fetchParticipantQualification: vi.fn(),
  fetchProcessCatalog: vi.fn(),
  fetchProcessRecordInbox: vi.fn(),
  fetchTemplates: vi.fn(),
  fetchWorkflowNotifications: vi.fn(),
  fetchWorkflowReport: vi.fn(),
  markWorkflowNotificationRead: vi.fn(),
  previewCohortStage: vi.fn(),
  publishCohortStage: vi.fn(),
  createStageVersion: vi.fn(),
  publishStageVersion: vi.fn(),
  reviewSubmission: vi.fn(),
  retryArchive: vi.fn(),
  updateSubmissionCommentStatus: vi.fn(),
  submitProcessRecord: vi.fn(),
  uploadProcessRecordAttachment: vi.fn(),
  downloadArchive: vi.fn(),
  reopenStageParticipant: vi.fn(),
  exitStageParticipant: vi.fn(),
  closeCohort: vi.fn(),
  importCohortMembers: vi.fn(),
  reopenCohort: vi.fn(),
  restoreProjectVersion: vi.fn(),
}))


const identity = vi.hoisted(() => ({ user: { role: 'student', status: 'active' } }))
const route = vi.hoisted(() => ({ query: {}, path: '/workflow' }))
const router = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }))
vi.mock('../src/api.js', () => api)
vi.mock('../src/auth.js', () => ({ auth: identity }))
vi.mock('vue-router', () => ({ RouterLink: { template: '<a><slot /></a>' }, useRoute: () => route, useRouter: () => router }))
import Workflow from '../src/views/Workflow.vue'
let wrapper
const button = text => new DOMWrapper([...document.querySelectorAll('button')].find(el => el.textContent.includes(text)))
const participant = { id: 'participant-1', project_id: 'project-1', project_title: '测试论文', cohort_name: '测试批次', stage_name: '终稿', state: 'needs_revision', current_submission: { id: 'new-submission', number: 'SUB-NEW' } }
const detail = id => ({ submission: { id, submission_number: id === 'old-submission' ? 'SUB-OLD' : 'SUB-NEW', state: 'returned' }, participant, version: { id: 'version-old', source_revision: 2, content_hash: '123456789012', content_snapshot: {} }, review: { decision: 'returned', reason: '请补充实验结果', created_at: '2026-10-11T12:00:00Z' }, comments: [{ id: 'comment-1', submission_id: id, body: '请说明对照条件', severity: 'info', status: 'open', responses: [] }] })
beforeEach(() => {
  vi.clearAllMocks(); identity.user = { role: 'student', status: 'active' }; route.query = {}
  api.fetchMyTasks.mockResolvedValue([participant]); api.fetchSubmissions.mockResolvedValue([{ id: 'old-submission', submission_number: 'SUB-OLD', state: 'returned' }])
  api.fetchWorkflowNotifications.mockResolvedValue({ items: [], unread: 0 }); api.fetchProcessCatalog.mockResolvedValue({ participant: [], cohort: [], reference: [] }); api.fetchProcessRecordInbox.mockResolvedValue([])
  api.fetchSubmission.mockImplementation(async id => detail(id)); api.fetchParticipantProcessRecords.mockResolvedValue([]); api.fetchProjectVersions.mockResolvedValue([{ id: 'version-old', created_at: '2026-10-11' }, { id: 'version-new', created_at: '2026-10-12' }])
  api.fetchOrganizations.mockResolvedValue([]); api.fetchTemplates.mockResolvedValue([]); api.fetchArchives.mockResolvedValue([])
  api.fetchCohorts.mockResolvedValue([])
})
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = '' })
async function open() { wrapper = mount(Workflow, { attachTo: document.body, global: { stubs: { ReviewEvidence: true, StageConfigForm: true, ScreenDeck: { template: '<div><slot name="header" /><slot /></div>' } } } }); await flushPromises() }
describe('workflow usage regressions', () => {
  it.each(['admin', 'college_admin'])('hides teacher fields from the %s material inbox', async role => {
    identity.user = { role, status: 'active' }
    api.fetchProcessRecordInbox.mockResolvedValue([{ id: 'midterm-1', artifact_type: 'midterm_check', artifact_name: '中期检查', pending_approvals: ['teacher'] }])
    await open()
    expect(wrapper.get('.process-inbox').text()).toContain('中期检查')
    expect(wrapper.get('.process-inbox').text()).not.toContain('填写意见/评分')
  })
  it('clears a persisted midterm score and preserves the teacher opinion', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    const record = { id: 'midterm-1', artifact_type: 'midterm_check', revision: 3, data: { advisor_comment: '保留教师意见', score: 88 } }
    api.fetchProcessRecordInbox.mockResolvedValue([record])
    api.updateMidtermTeacherFields.mockResolvedValue({ ...record, revision: 4, data: { advisor_comment: '保留教师意见', score: null } })
    await open(); await button('填写意见/评分').trigger('click'); await flushPromises()
    const dialog = new DOMWrapper(document.querySelector('[role="dialog"]'))
    expect(dialog.element.contains(document.activeElement)).toBe(true)
    await dialog.get('input[type="number"]').setValue('')
    await button('保存教师字段').trigger('click'); await flushPromises()
    expect(api.updateMidtermTeacherFields).toHaveBeenCalledWith('midterm-1', { data: { advisor_comment: '保留教师意见', score: null }, base_revision: 3 })
    expect(document.querySelector('[role="dialog"]')).toBeNull()
  })
  it('opens the task record immediately and shows the persisted review and comment', async () => {
    await open(); await button('测试论文').trigger('click'); await button('查看记录').trigger('click'); await flushPromises()
    expect(api.fetchSubmission).toHaveBeenCalledWith('new-submission')
    expect(document.querySelector('.history-detail').textContent).toContain('请补充实验结果')
    expect(document.querySelector('.history-detail').textContent).toContain('请说明对照条件')
    expect(document.querySelector('.student-task-summary')).toBeTruthy()
    expect(router.replace).toHaveBeenCalledWith({ query: { panel: 'submissions' } })
  })
  it('opens an older submission by its own identity and selects that immutable version', async () => {
    route.query = { panel: 'submissions' }; await open(); await button('SUB-OLD').trigger('click'); await flushPromises()
    expect(api.fetchSubmission).toHaveBeenCalledWith('old-submission')
    expect(document.querySelector('.history-detail h3').textContent).toBe('SUB-OLD')
    expect(document.querySelectorAll('.compare-selects select')[1].value).toBe('version-old')
  })
  it('opens reviewed records from both notifications and direct links', async () => {
    route.query = { panel: 'messages' }
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [{ id: 'event-1', resource_type: 'submission', resource_id: 'old-submission', data: { title: '论文需要修改' } }], unread: 1 })
    await open(); await button('论文需要修改').trigger('click'); await flushPromises()
    expect(api.markWorkflowNotificationRead).toHaveBeenCalledWith('event-1')
    expect(document.querySelector('.history-detail').textContent).toContain('SUB-OLD')
    wrapper.unmount(); route.query = { panel: 'submissions', submission: 'old-submission' }; await open()
    expect(document.querySelector('.history-detail').textContent).toContain('请补充实验结果')
  })
  it('publishes while only the cohort detail is open and refreshes that detail without a reload', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    const stage = { id: 'stage-1', name: '终稿', status: 'draft' }
    const cohort = { id: 'cohort-1', name: '测试批次', member_ids: [], stages: [stage], status: 'draft' }
    api.fetchCohorts.mockResolvedValue([cohort]); api.fetchCohort.mockResolvedValue(cohort)
    api.previewCohortStage.mockResolvedValue({ ok: true, member_snapshot: [] })
    api.publishCohortStage.mockImplementation(async () => { api.fetchCohort.mockResolvedValue({ ...cohort, stages: [{ ...stage, status: 'published' }] }) })
    await open(); await button('测试批次').trigger('click'); await button('预览发布').trigger('click'); await flushPromises()
    await button('确认发布').trigger('click'); await flushPromises()
    expect(api.publishCohortStage).toHaveBeenCalledWith('stage-1')
    expect(document.querySelector('.cohort-panel .stage-row').textContent).toContain('已发布')
    expect(document.querySelector('.error-banner')).toBeNull()
    expect(document.querySelector('.confirm-dialog')).toBeNull()
  })
})
