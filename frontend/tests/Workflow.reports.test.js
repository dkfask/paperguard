import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

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

const routeState = vi.hoisted(() => ({
  query: { panel: 'reports' },
  path: '/workflow',
}))

vi.mock('../src/api.js', () => api)
vi.mock('../src/auth.js', () => ({
  auth: { user: { role: 'college_admin', status: 'active', display_name: '学院管理员', username: 'college-admin' } },
}))
vi.mock('../src/i18n', () => ({
  useI18n: () => ({
    t: (key) => key,
    formatDate: () => '09/16 10:00',
  }),
}))
vi.mock('vue-router', () => ({
  RouterLink: { template: '<a><slot /></a>' },
  useRoute: () => routeState,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}))

import Workflow from '../src/views/Workflow.vue'

const cohort = {
  id: 'cohort-1',
  name: '2026 届本科',
  status: 'published',
  academic_year: '2026',
  organization_id: 'college-1',
  member_ids: ['s1'],
  stages: [],
}

describe('Workflow reports', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.fetchMyTasks.mockResolvedValue([])
    api.fetchWorkflowNotifications.mockResolvedValue({ items: [], unread: 0 })
    api.fetchSubmissions.mockResolvedValue([])
    api.fetchProcessCatalog.mockResolvedValue({ participant: [], cohort: [], reference: [] })
    api.fetchProcessRecordInbox.mockResolvedValue([])
    api.fetchCohorts.mockResolvedValue([cohort])
    api.fetchCohort.mockResolvedValue(cohort)
    api.fetchOrganizations.mockResolvedValue([{ id: 'college-1', name: '计算机学院' }])
    api.fetchTemplates.mockResolvedValue([{ id: 'default-gb7713', name: 'GB/T 7713' }])
    api.fetchArchives.mockResolvedValue([])
    api.fetchWorkflowReport.mockResolvedValue({
      member_count: 4,
      state_counts: { submitted: 1, needs_revision: 0, archived: 2 },
      metrics: { on_time_first_submission_rate: 0.8, archive_completion_rate: 0.5 },
    })
  })

  it('renders report rates without crashing', async () => {
    const wrapper = mount(Workflow, {
      global: {
        stubs: {
          AsyncState: true,
          ConfirmDialog: true,
          DeadlineLabel: true,
          MemberImportWizard: true,
          ReasonDialog: true,
          ReviewEvidence: true,
          StageConfigForm: true,
          StatusBadge: true,
          RouterLink: { template: '<a><slot /></a>' },
        },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('80%')
    expect(wrapper.text()).toContain('50%')
    expect(wrapper.text()).not.toContain('errorBoundary.title')
  })
})
