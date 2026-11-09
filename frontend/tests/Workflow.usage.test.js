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
  extendTaskDeadline: vi.fn(),
  updateMidtermTeacherFields: vi.fn(),
  downloadProcessRecordAttachment: vi.fn(),
  downloadSubmissionDocument: vi.fn(),
  downloadSubmissionPdf: vi.fn(),
  downloadSubmissionMaterial: vi.fn(),
  exportProcessRecord: vi.fn(),
  fetchProcessRecordForm: vi.fn(),
  fetchCohortProcessRecordForm: vi.fn(),
  fetchParticipantScoreSummary: vi.fn(),
  fetchCohort: vi.fn(),
  fetchCohortProcessRecords: vi.fn(),
  fetchCohorts: vi.fn(),
  fetchMyTasks: vi.fn(),
  fetchWorkflowCapabilities: vi.fn(),
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
  withdrawSubmission: vi.fn(),
  submitProcessRecord: vi.fn(),
  uploadProcessRecordAttachment: vi.fn(),
  downloadArchive: vi.fn(),
  getPrivateImageUrl: vi.fn(),
  getSubmissionImageUrl: vi.fn(),
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
  api.fetchWorkflowCapabilities.mockResolvedValue({ m2_workflow_enabled: true })
  api.fetchMyTasks.mockResolvedValue([participant]); api.fetchSubmissions.mockResolvedValue([{ id: 'old-submission', submission_number: 'SUB-OLD', state: 'returned' }])
  api.fetchWorkflowNotifications.mockResolvedValue({ items: [], unread: 0 }); api.fetchProcessCatalog.mockResolvedValue({ participant: [], cohort: [], reference: [] }); api.fetchProcessRecordInbox.mockResolvedValue([])
  api.fetchSubmission.mockImplementation(async id => detail(id)); api.fetchParticipantProcessRecords.mockResolvedValue([]); api.fetchProjectVersions.mockResolvedValue([{ id: 'version-old', created_at: '2026-10-11' }, { id: 'version-new', created_at: '2026-10-12' }])
  api.fetchOrganizations.mockResolvedValue([]); api.fetchTemplates.mockResolvedValue([]); api.fetchArchives.mockResolvedValue([])
  api.fetchCohorts.mockResolvedValue([])
})
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ''; vi.unstubAllGlobals() })
async function open(stubReview = true) { wrapper = mount(Workflow, { attachTo: document.body, global: { stubs: { ...(stubReview ? { ReviewEvidence: true } : {}), StageConfigForm: true, ScreenDeck: { template: '<div><slot name="header" /><slot /></div>' } } } }); await flushPromises() }
describe('workflow usage regressions', () => {
  it('prevents repeated pre-checks and hides an older candidate when a new pre-check fails', async () => {
    let resolveFirst
    api.createSubmissionCandidate.mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
    await open()
    await button('测试论文').trigger('click')
    const precheck = button('提交预检')
    await precheck.trigger('click')
    expect(precheck.element.disabled).toBe(true)
    expect(precheck.text()).toContain('预检中')
    await precheck.trigger('click')
    expect(api.createSubmissionCandidate).toHaveBeenCalledTimes(1)
    resolveFirst({ id: 'candidate-1', status: 'ready', gates: [], check_report: {} })
    await flushPromises()
    expect(document.body.textContent).toContain('确认正式提交')
    api.createSubmissionCandidate.mockRejectedValueOnce(new Error('预检失败'))
    await button('提交预检').trigger('click')
    await flushPromises()
    expect(document.querySelector('[role=dialog] [role=alert]')?.textContent).toContain('预检失败')
    expect(document.body.textContent).not.toContain('确认正式提交')
    expect(button('提交预检').element.disabled).toBe(false)
  })
  it('opens the archive list from a workflow message', async () => {
    route.query = { panel: 'messages' }
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [{
      id: 'event-archive', resource_type: 'archive', resource_id: 'archive-1',
      data: { title: '终稿归档暂未完成' },
    }], unread: 1 })
    api.markWorkflowNotificationRead.mockResolvedValue({})
    await open()
    await wrapper.get('.notification-row').trigger('click')
    await flushPromises()
    expect(router.replace).toHaveBeenCalledWith({ query: { panel: 'archives' } })
  })

  it('shows the submitted image in the student history using that submission identity', async () => {
    vi.stubGlobal('URL', { revokeObjectURL: vi.fn() })
    api.getSubmissionImageUrl.mockResolvedValue('blob:submitted-image')
    api.fetchSubmission.mockImplementation(async id => ({
      ...detail(id), version: { ...detail(id).version, content_snapshot: { sections: [
        { key: 'chapter', paragraphs: [{ id: 'figure-1', type: 'image', image: { caption: '研究结果' } }] },
      ] } },
    }))
    route.query = { panel: 'submissions' }
    await open(false); await button('SUB-OLD').trigger('click'); await flushPromises()
    expect(api.getSubmissionImageUrl).toHaveBeenCalledWith('old-submission', 'figure-1')
    expect(document.querySelector('.history-detail img')?.getAttribute('src')).toBe('blob:submitted-image')
  })

  it('updates an earlier comment after the student replies from the current submission', async () => {
    route.query = { panel: 'submissions' }
    api.fetchSubmissions.mockResolvedValue([{ id: 'new-submission', submission_number: 'SUB-NEW', state: 'returned' }])
    api.fetchSubmission.mockImplementation(async id => ({ ...detail(id), prior_unresolved_comments: [{
      id: 'prior-comment', submission_id: 'old-submission', source_submission_number: 'SUB-OLD',
      source_section_title: '研究方法', source_text: '原稿内容', block_id: 'old-block',
      body: '补充数据来源', severity: 'blocking', status: 'open', responses: [],
    }] }))
    api.respondSubmissionComment.mockResolvedValue({ id: 'prior-comment', submission_id: 'old-submission',
      block_id: 'old-block', body: '补充数据来源', severity: 'blocking', status: 'addressed',
      responses: [{ id: 'reply-1', body: '已补充来源', created_at: '2026-10-30T12:00:00Z' }] })
    await open(); await button('SUB-NEW').trigger('click'); await flushPromises()
    const feedback = new DOMWrapper(document.querySelector('[data-test="student-prior-feedback"]'))
    await feedback.get('textarea').setValue('已补充来源')
    await feedback.get('form').trigger('submit'); await flushPromises()
    expect(api.respondSubmissionComment).toHaveBeenCalledWith('prior-comment', '已补充来源')
    expect(feedback.text()).toContain('已补充来源')
    expect(feedback.text()).toContain('SUB-OLD')
    expect(feedback.text()).toContain('原稿内容')
  })

  it('lets the teacher extend an archived task and compares against its active revision deadline', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    api.fetchMyTasks.mockResolvedValue([{
      ...participant, state: 'archived', effective_deadline_at: '2030-01-01T00:00:00Z',
      revision_deadline_at: '2030-02-01T00:00:00Z',
    }])
    api.extendTaskDeadline.mockResolvedValue({})
    await open(); await button('测试论文').trigger('click'); await flushPromises()
    await button('延期').trigger('click')
    const deadline = new DOMWrapper(document.querySelector('input[aria-label="新的截止时间"]'))
    const reason = new DOMWrapper(document.querySelector('input[placeholder="延期原因"]'))
    await reason.setValue('安排重新提交')
    await deadline.setValue('2030-01-15T12:00')
    await button('确认延期').trigger('click'); await flushPromises()
    expect(api.extendTaskDeadline).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('新的截止时间必须晚于当前截止时间')
    await deadline.setValue('2030-03-01T12:00')
    await button('确认延期').trigger('click'); await flushPromises()
    expect(api.extendTaskDeadline).toHaveBeenCalledWith('participant-1', {
      effective_deadline_at: new Date('2030-03-01T12:00').toISOString(), reason: '安排重新提交',
    })
  })

  it.each([
    ['teacher', true],
    ['admin', false],
    ['college_admin', false],
  ])('shows the correction panel for the %s role only when allowed', async (role, canReopen) => {
    identity.user = { role, status: 'active' }
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'pending_archive' }])
    await open()
    const panel = wrapper.find('.correction-panel')
    expect(panel.exists()).toBe(canReopen)
    if (canReopen) {
      const actions = panel.findAll('button').map(item => item.text())
      expect(actions.some(text => text.includes('重开'))).toBe(true)
      expect(actions.some(text => text.includes('退出'))).toBe(false)
    }
  })

  it('lets the student download only a deliverable archive from submission history', async () => {
    route.query = { panel: 'submissions' }
    api.fetchSubmissions.mockResolvedValue([{ id: 'new-submission', participant_id: participant.id, submission_number: 'SUB-NEW', state: 'approved' }])
    api.fetchArchives.mockResolvedValue([
      { id: 'archive-ready', submission_id: 'new-submission', status: 'succeeded', download_url: '/api/archives/archive-ready/download', manual_review: { status: 'not_required' } },
      { id: 'archive-pending', submission_id: 'new-submission', status: 'queued' },
      { id: 'archive-review', submission_id: 'new-submission', status: 'succeeded', download_url: '/api/archives/archive-review/download', manual_review: { status: 'required' } },
    ])
    api.downloadArchive.mockResolvedValue()
    await open()
    const archives = wrapper.get('[data-test="student-archives"]')
    expect(archives.text()).toContain('论文归档')
    expect(archives.text()).toContain('排队中')
    expect(archives.text()).toContain('Word/WPS 核查待办，归档已可下载')
    expect(archives.findAll('button')).toHaveLength(2)
    await archives.findAll('button')[0].trigger('click')
    await flushPromises()
    expect(api.downloadArchive).toHaveBeenCalledOnce()
    expect(api.downloadArchive).toHaveBeenCalledWith('archive-ready')
  })

  it('keeps archive download available while showing the Word/WPS review todo', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchArchives.mockResolvedValue([
      { id: 'archive-ready', status: 'succeeded', download_url: '/api/archives/archive-ready/download', manual_review: { status: 'not_required' } },
      { id: 'archive-unchecked', status: 'succeeded', download_url: '/api/archives/archive-unchecked/download', manual_review: { status: 'required' } },
    ])
    api.downloadArchive.mockResolvedValue()
    await open()
    const archives = wrapper.get('[data-test="teacher-archives"]')
    expect(archives.text()).toContain('Word/WPS 核查待办，归档已可下载')
    expect(archives.findAll('button')).toHaveLength(2)
    await archives.findAll('button')[0].trigger('click')
    await flushPromises()
    expect(api.downloadArchive).toHaveBeenCalledWith('archive-ready')
    expect(api.downloadArchive).toHaveBeenCalledWith('archive-ready')
    await archives.findAll('button')[1].trigger('click')
    await flushPromises()
    expect(api.downloadArchive).toHaveBeenCalledWith('archive-unchecked')
  })

  it('keeps student submission history available when archive loading fails', async () => {
    route.query = { panel: 'submissions' }
    api.fetchArchives.mockRejectedValue(new Error('归档列表加载失败'))
    await open()
    expect(wrapper.text()).toContain('SUB-OLD')
    expect(wrapper.get('[role="alert"]').text()).toContain('归档列表加载失败')
    expect(wrapper.find('[data-test="student-archives"]').exists()).toBe(false)
  })

  it('lets the student withdraw a pending submission and refreshes its status', async () => {
    route.query = { panel: 'submissions' }
    let state = 'submitted'
    api.fetchSubmissions.mockResolvedValue([{ id: 'new-submission', submission_number: 'SUB-NEW', state }])
    api.fetchSubmission.mockImplementation(async id => ({
      ...detail(id), submission: { ...detail(id).submission, state },
    }))
    api.withdrawSubmission.mockImplementation(async () => {
      state = 'withdrawn'
      api.fetchSubmissions.mockResolvedValue([{ id: 'new-submission', submission_number: 'SUB-NEW', state }])
    })

    await open(); await button('SUB-NEW').trigger('click'); await flushPromises()
    await button('撤回提交').trigger('click'); await flushPromises()
    expect(api.withdrawSubmission).not.toHaveBeenCalled()
    await button('确认撤回').trigger('click'); await flushPromises()

    expect(api.withdrawSubmission).toHaveBeenCalledWith('new-submission')
    expect(document.body.textContent).toContain('已撤回')
    expect([...document.querySelectorAll('button')].some(item => item.textContent.includes('撤回提交'))).toBe(false)
  })

  it('does not offer withdrawal for an already returned submission', async () => {
    route.query = { panel: 'submissions' }
    await open(); await button('SUB-OLD').trigger('click'); await flushPromises()
    expect([...document.querySelectorAll('button')].some(item => item.textContent.includes('撤回提交'))).toBe(false)
  })

  it('refreshes the submission when a teacher decision wins the withdrawal race', async () => {
    route.query = { panel: 'submissions' }
    let state = 'submitted'
    api.fetchSubmissions.mockImplementation(async () => [{ id: 'new-submission', submission_number: 'SUB-NEW', state }])
    api.fetchSubmission.mockImplementation(async id => ({
      ...detail(id), submission: { ...detail(id).submission, state },
    }))
    api.withdrawSubmission.mockImplementation(async () => {
      state = 'returned'
      throw new Error('只有待审核提交可以撤回')
    })

    await open(); await button('SUB-NEW').trigger('click'); await flushPromises()
    await button('撤回提交').trigger('click'); await button('确认撤回').trigger('click'); await flushPromises()

    expect(document.querySelector('.history-detail [role="alert"]').textContent).toContain('只有待审核提交可以撤回')
    expect(document.querySelector('.history-detail').textContent).toContain('已退回')
    expect([...document.querySelectorAll('button')].some(item => item.textContent.includes('撤回提交'))).toBe(false)
  })

  it('refreshes an open student task after acknowledging requirements and submitting', async () => {
    const initial = { ...participant, state: 'in_progress', current_submission: null }
    const acknowledged = { ...initial, acknowledged_snapshot_hash: 'requirements-hash' }
    const submitted = { ...acknowledged, state: 'submitted', current_submission: { id: 'submission-1', number: 'SUB-1' } }
    api.fetchMyTasks.mockResolvedValue([initial])
    api.acknowledgeTaskRequirements.mockImplementation(async () => { api.fetchMyTasks.mockResolvedValue([acknowledged]) })
    api.createSubmissionCandidate.mockResolvedValue({
      id: 'candidate-1', status: 'ready', warning_count: 0,
      manual_review: { status: 'required' },
      gates: [{ key: 'office_review_todo', label: 'Word/WPS 核查待办', passed: false, blocking: false, detail: '自动检查已通过；此待办不影响正式提交。' }],
    })
    api.confirmSubmission.mockImplementation(async () => { api.fetchMyTasks.mockResolvedValue([submitted]) })

    await open(); await button('测试论文').trigger('click')
    await button('确认要求').trigger('click'); await flushPromises()
    expect(document.querySelector('[role="dialog"]').textContent).not.toContain('确认要求')
    await button('提交预检').trigger('click'); await flushPromises()
    expect(document.querySelector('[role="dialog"]').textContent).toContain('Word/WPS 核查待办')
    expect(document.querySelector('[role="dialog"]').textContent).toContain('此待办不影响正式提交')
    await button('确认正式提交').trigger('click'); await flushPromises()

    const dialog = document.querySelector('[role="dialog"]')
    expect(dialog.textContent).toContain('待审核')
    expect(dialog.textContent).toContain('SUB-1')
    expect(dialog.textContent).not.toContain('提交预检')
    expect(dialog.textContent).not.toContain('尚无正式提交')
  })
  it('retries the same submission with the original request identity after a lost response', async () => {
    const initial = { ...participant, state: 'in_progress', current_submission: null }
    const submitted = { ...initial, state: 'submitted', current_submission: { id: 'submission-1', number: 'SUB-1' } }
    api.fetchMyTasks.mockResolvedValue([initial])
    api.createSubmissionCandidate.mockResolvedValue({ id: 'candidate-1', status: 'ready', gates: [], warning_count: 1 })
    api.confirmSubmission.mockRejectedValueOnce(new Error('提交结果未收到')).mockImplementationOnce(async () => {
      api.fetchMyTasks.mockResolvedValue([submitted])
    })

    await open(); await button('测试论文').trigger('click')
    await button('提交预检').trigger('click'); await flushPromises()
    const warning = new DOMWrapper(document.querySelector('.candidate-box input[type="checkbox"]'))
    await warning.setValue(true)
    await button('确认正式提交').trigger('click'); await flushPromises()

    expect(api.confirmSubmission).toHaveBeenCalledTimes(1)
    expect(document.querySelector('[role=dialog] [role=alert]')?.textContent).toContain('提交结果未收到')
    const originalRequest = api.confirmSubmission.mock.calls[0][0]
    expect(originalRequest).toMatchObject({ candidate_id: 'candidate-1', warning_acknowledged: true })
    await button('确认正式提交').trigger('click'); await flushPromises()

    expect(api.confirmSubmission).toHaveBeenCalledTimes(2)
    expect(api.confirmSubmission.mock.calls[1][0]).toEqual(originalRequest)
    expect(document.querySelector('[role="dialog"]').textContent).toContain('待审核')
    expect(document.body.textContent).not.toContain('提交结果未收到')
  })

  it('reports a confirmed submission when refreshing its task fails', async () => {
    api.fetchMyTasks.mockResolvedValueOnce([{ ...participant, state: 'in_progress', current_submission: null }])
      .mockRejectedValueOnce(new Error('刷新任务失败'))
    api.createSubmissionCandidate.mockResolvedValue({ id: 'candidate-1', status: 'ready', gates: [], warning_count: 0 })
    api.confirmSubmission.mockResolvedValue({})

    await open(); await button('测试论文').trigger('click')
    await button('提交预检').trigger('click'); await flushPromises()
    await button('确认正式提交').trigger('click'); await flushPromises()

    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(document.body.textContent).toContain('提交已成功，但工作台信息未完全刷新，请点击“刷新工作台”查看。')
    expect(document.body.textContent).toContain('刷新任务失败')
  })

  it('uses a new request identity when the student prepares a new candidate', async () => {
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'in_progress', current_submission: null }])
    api.createSubmissionCandidate
      .mockResolvedValueOnce({ id: 'candidate-first', status: 'ready', gates: [], warning_count: 0 })
      .mockResolvedValueOnce({ id: 'candidate-second', status: 'ready', gates: [], warning_count: 0 })
    api.confirmSubmission.mockRejectedValueOnce(new Error('预检结果已过期')).mockResolvedValueOnce({})

    await open(); await button('测试论文').trigger('click')
    await button('提交预检').trigger('click'); await flushPromises()
    await button('确认正式提交').trigger('click'); await flushPromises()
    await button('提交预检').trigger('click'); await flushPromises()
    await button('确认正式提交').trigger('click'); await flushPromises()

    const [first, second] = api.confirmSubmission.mock.calls.map(([request]) => request)
    expect(first.candidate_id).toBe('candidate-first')
    expect(second.candidate_id).toBe('candidate-second')
    expect(second.idempotency_key).not.toBe(first.idempotency_key)
  })
  it('opens the pending submission from the teacher review entry', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'submitted' }])
    api.fetchSubmission.mockImplementation(async id => ({ ...detail(id), submission: { ...detail(id).submission, state: 'submitted' } }))

    await open(); await button('测试论文').trigger('click'); await flushPromises()

    expect(api.fetchSubmission).toHaveBeenCalledWith('new-submission')
    expect(document.querySelector('.review-panel').textContent).toContain('退回修改')
    expect(document.querySelector('.student-task-summary')).toBeNull()
  })
  it('downloads the Word file belonging to the displayed submission', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'submitted' }])
    api.fetchSubmission.mockImplementation(async id => ({
      ...detail(id), submission: { ...detail(id).submission, state: 'submitted' },
      candidate: { artifact: { docx_sha256: 'checked-hash' } },
    }))
    api.downloadSubmissionDocument.mockResolvedValue()

    await open(false); await button('测试论文').trigger('click'); await flushPromises()
    await new DOMWrapper(document.querySelector('[data-test="download-submission-document"]')).trigger('click')
    await flushPromises()
    expect(api.downloadSubmissionDocument).toHaveBeenCalledWith('new-submission')
    expect(document.body.textContent).toContain('提交版 Word 已开始下载')
  })
  it('downloads an optional PDF for the displayed submission', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchWorkflowCapabilities.mockResolvedValue({ m2_workflow_enabled: true, optional_pdf_enabled: true })
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'submitted' }])
    api.fetchSubmission.mockImplementation(async id => ({
      ...detail(id), submission: { ...detail(id).submission, state: 'submitted' },
      candidate: { artifact: { docx_sha256: 'checked-hash' } },
    }))
    api.downloadSubmissionPdf.mockResolvedValue()
    await open(false); await button('测试论文').trigger('click'); await flushPromises()
    await new DOMWrapper(document.querySelector('[data-test="download-submission-pdf"]')).trigger('click')
    await flushPromises()
    expect(api.downloadSubmissionPdf).toHaveBeenCalledWith('new-submission')
    expect(api.downloadSubmissionDocument).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('提交版 PDF 已开始下载')
  })
  it('uses frozen submission materials and keeps return available when they cannot be verified', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'submitted' }])
    let available = false
    api.fetchSubmission.mockImplementation(async id => ({
      ...detail(id), submission: { ...detail(id).submission, state: 'submitted' },
      config: { required_materials: ['task_book'] },
      submitted_materials_available: available,
      submitted_materials: available ? [{ id: 'material-1', artifact_name: '提交时的任务书', state: 'submitted',
        attachments: [{ id: 'file-1', name: '任务书.txt' }] }] : [],
    }))
    api.fetchParticipantProcessRecords.mockResolvedValue([{ id: 'live-material', artifact_name: '后来修改的任务书' }])
    api.downloadSubmissionMaterial.mockResolvedValue()

    await open(false); await button('测试论文').trigger('click'); await flushPromises()

    expect(document.querySelector('.review-panel')).toBeTruthy()
    expect(document.querySelector('[data-test="review-materials-error"]').textContent).toContain('本次提交的必交材料不可用')
    expect(button('通过提交').element.disabled).toBe(true)
    expect(button('退回修改').element.disabled).toBe(false)
    available = true
    await button('重新加载材料').trigger('click'); await flushPromises()
    expect(document.querySelector('[data-test="review-materials-error"]')).toBeNull()
    expect(button('通过提交').element.disabled).toBe(false)
    expect(document.querySelector('.review-materials').textContent).toContain('提交时的任务书')
    expect(document.querySelector('.review-materials').textContent).not.toContain('后来修改的任务书')
    expect(api.fetchParticipantProcessRecords).not.toHaveBeenCalled()
    await button('任务书.txt').trigger('click'); await flushPromises()
    expect(api.downloadSubmissionMaterial).toHaveBeenCalledWith('new-submission', 'file-1', '任务书.txt')
  })
  it('shows student feedback even when the version list cannot be loaded', async () => {
    route.query = { panel: 'submissions' }
    api.fetchProjectVersions.mockRejectedValue(new Error('网络错误'))

    await open(); await button('SUB-OLD').trigger('click'); await flushPromises()

    const history = document.querySelector('.history-detail')
    expect(history.textContent).toContain('请补充实验结果')
    expect(history.textContent).toContain('请说明对照条件')
    expect(history.textContent).toContain('版本列表加载失败')
    expect(history.querySelector('textarea')).toBeTruthy()
  })
  it('shows unresolved feedback from earlier submissions and lets the teacher resolve it', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'submitted' }])
    let resolved = false
    api.fetchSubmission.mockImplementation(async id => ({
      ...detail(id), submission: { ...detail(id).submission, state: 'submitted' },
      prior_unresolved_comments: resolved ? [] : [{
        id: 'prior-comment', submission_id: 'old-submission', source_submission_number: 'SUB-OLD',
        source_section_title: '绪论', source_text: '修改前的正文', block_id: 'deleted-block',
        body: '请补充研究背景', severity: 'blocking', status: 'addressed',
        responses: [{ id: 'reply-1', body: '已补充说明', created_at: '2026-10-11' }],
      }],
    }))
    api.updateSubmissionCommentStatus.mockImplementation(async () => { resolved = true })

    await open(); await button('测试论文').trigger('click'); await flushPromises()
    const feedback = new DOMWrapper(document.querySelector('[data-test="prior-feedback"]'))
    expect(feedback.text()).toContain('SUB-OLD')
    expect(feedback.text()).toContain('修改前的正文')
    expect(feedback.text()).toContain('已补充说明')
    expect(feedback.text()).toContain('当前稿中无法找到原批注位置')
    expect(button('通过提交').element.disabled).toBe(true)
    expect(document.querySelector('#review-blocking-feedback').textContent).toContain('1 条阻断意见未解决')
    await feedback.get('button').trigger('click'); await flushPromises()
    expect(api.updateSubmissionCommentStatus).toHaveBeenCalledWith('prior-comment', 'resolved')
    expect(document.querySelector('[data-test="prior-feedback"]')).toBeNull()
    expect(button('通过提交').element.disabled).toBe(false)
    expect(document.querySelector('#review-blocking-feedback')).toBeNull()
  })
  it('prevents approval while a blocking comment on the current submission is unresolved', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'submitted' }])
    api.fetchSubmission.mockImplementation(async id => ({
      ...detail(id), submission: { ...detail(id).submission, state: 'submitted' },
      comments: [{ id: 'current-comment', severity: 'blocking', status: 'open', body: '补充数据' }],
    }))

    await open(); await button('测试论文').trigger('click'); await flushPromises()
    expect(button('通过提交').element.disabled).toBe(true)
    expect(button('退回修改').element.disabled).toBe(false)
    expect(document.querySelector('#review-blocking-feedback').textContent).toContain('1 条阻断意见未解决')
  })
  it('submits a comment attached to the selected snapshot block', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'submitted' }])
    api.fetchSubmission.mockImplementation(async id => ({ ...detail(id), submission: { ...detail(id).submission, state: 'submitted' },
      version: { ...detail(id).version, content_snapshot: { sections: [{ key: 'method', title: '研究方法', paragraphs: [{ id: 'p1', type: 'paragraph', text: '实验结果' }] }] } } }))
    api.createSubmissionComment.mockResolvedValue({})
    await open(false); await button('测试论文').trigger('click'); await flushPromises()
    await new DOMWrapper(document.querySelector('[data-test="select-review-block"]')).trigger('click')
    expect(document.querySelector('.comment-target').textContent).toContain('研究方法')
    await new DOMWrapper(document.querySelector('textarea[aria-label="新增批注"]')).setValue('补充数据来源')
    await button('新增批注').trigger('click'); await flushPromises()
    expect(api.createSubmissionComment).toHaveBeenCalledWith('new-submission', { body: '补充数据来源', severity: 'info', block_id: 'p1' })
    expect(document.querySelector('.comment-target')).toBeNull()
  })
  it('submits the selected text and its exact position in the submitted version', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'submitted' }])
    api.fetchSubmission.mockImplementation(async id => ({ ...detail(id), submission: { ...detail(id).submission, state: 'submitted' },
      version: { ...detail(id).version, content_snapshot: { sections: [{ key: 'method', title: '研究方法', paragraphs: [{ id: 'p1', type: 'paragraph', text: '结果😀结果' }] }] } } }))
    api.createSubmissionComment.mockResolvedValue({})
    await open(false); await button('测试论文').trigger('click'); await flushPromises()
    const paragraph = document.querySelector('.snapshot-block p')
    const range = document.createRange()
    range.setStart(paragraph.firstChild, 4)
    range.setEnd(paragraph.firstChild, 6)
    const selection = window.getSelection()
    selection.removeAllRanges(); selection.addRange(range)
    await new DOMWrapper(paragraph).trigger('mouseup')
    await new DOMWrapper(document.querySelector('[data-test="select-review-text"]')).trigger('click')
    await new DOMWrapper(document.querySelector('textarea[aria-label="新增批注"]')).setValue('核对结果')
    await button('新增批注').trigger('click'); await flushPromises()
    expect(api.createSubmissionComment).toHaveBeenCalledWith('new-submission', {
      body: '核对结果', severity: 'info', block_id: 'p1', quoted_text: '结果', text_start: 3, text_end: 5,
    })
    selection.removeAllRanges()
  })
  it('shows the quoted passage beside a saved teacher comment', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review' }
    api.fetchMyTasks.mockResolvedValue([{ ...participant, state: 'submitted' }])
    api.fetchSubmission.mockImplementation(async id => ({ ...detail(id), submission: { ...detail(id).submission, state: 'submitted' },
      comments: [{ id: 'c1', block_id: 'p1', quoted_text: '实验结果', body: '核对数据', severity: 'info', status: 'open' }] }))
    await open(false); await button('测试论文').trigger('click'); await flushPromises()
    expect(document.querySelector('.comment-list blockquote').textContent).toBe('实验结果')
  })
  it('keeps student tasks available while M2 actions and direct archive links are closed', async () => {
    api.fetchWorkflowCapabilities.mockResolvedValue({ m2_workflow_enabled: false })
    await open(); await button('测试论文').trigger('click'); await flushPromises()
    expect(document.querySelector('.student-task')).toBeTruthy()
    expect(document.body.textContent).not.toContain('提交预检')
    expect(document.body.textContent).not.toContain('提交与归档')
    expect(api.createSubmissionCandidate).not.toHaveBeenCalled()
    wrapper.unmount(); route.query = { panel: 'archives' }; await open()
    expect(wrapper.text()).toContain('论文提交、审核和归档尚未开放')
    expect(wrapper.find('.archive-panel').exists()).toBe(false)
  })
  it('hides teacher fields from the admin material inbox', async () => {
    const role = 'admin'
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
  it('opens feedback from a message even when marking it as read fails', async () => {
    route.query = { panel: 'messages' }
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [{ id: 'event-1', resource_type: 'submission', resource_id: 'old-submission', data: { title: '论文需要修改' } }], unread: 1 })
    api.markWorkflowNotificationRead.mockRejectedValueOnce(new Error('消息状态保存失败'))
    await open(); await button('论文需要修改').trigger('click'); await flushPromises()
    expect(api.fetchSubmission).toHaveBeenCalledWith('old-submission')
    expect(document.querySelector('.history-detail').textContent).toContain('请补充实验结果')
    expect(document.querySelector('.history-detail').textContent).toContain('消息状态保存失败')
  })
  it('opens feedback without waiting for a stalled read request', async () => {
    route.query = { panel: 'messages' }
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [{ id: 'event-1', resource_type: 'submission', resource_id: 'old-submission', data: { title: '论文需要修改' } }], unread: 1 })
    api.markWorkflowNotificationRead.mockImplementationOnce(() => new Promise(() => {}))
    await open(); await button('论文需要修改').trigger('click'); await flushPromises()
    expect(api.fetchSubmission).toHaveBeenCalledWith('old-submission')
    expect(document.querySelector('.history-detail').textContent).toContain('请补充实验结果')
  })
  it('does not open a submission from a message when the workflow is unavailable', async () => {
    route.query = { panel: 'messages' }
    api.fetchWorkflowCapabilities.mockResolvedValue({ m2_workflow_enabled: false })
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [{ id: 'event-1', resource_type: 'submission', resource_id: 'old-submission', data: { title: '论文需要修改' } }], unread: 1 })
    await open(); await button('论文需要修改').trigger('click'); await flushPromises()
    expect(api.fetchSubmission).not.toHaveBeenCalled()
    expect(document.querySelector('.history-detail')).toBeNull()
    expect(document.body.textContent).toContain('论文提交、审核和归档尚未开放')
  })
  it('opens the specific submission from a teacher notification link only when workflow is enabled', async () => {
    identity.user = { role: 'teacher', status: 'active' }
    route.query = { panel: 'review', submission: 'new-submission' }
    api.fetchSubmission.mockImplementation(async id => ({ ...detail(id), submission: { ...detail(id).submission, state: 'submitted' } }))
    await open(); await flushPromises()
    expect(api.fetchSubmission).toHaveBeenCalledWith('new-submission')
    expect(document.querySelector('.review-panel').textContent).toContain('SUB-NEW')
    wrapper.unmount()
    api.fetchSubmission.mockClear()
    api.fetchWorkflowCapabilities.mockResolvedValue({ m2_workflow_enabled: false })
    await open(); await flushPromises()
    expect(api.fetchSubmission).not.toHaveBeenCalled()
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
