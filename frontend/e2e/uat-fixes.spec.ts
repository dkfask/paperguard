import { randomBytes } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { LoginPage, PaperGuardPage } from './pages/paperguard.page'
import { API_BASE_URL } from './document-platform.api'

class UatFixPage extends PaperGuardPage {
  constructor(page: Page) { super(page) }

  async verifyWriter(projectId: string, title: string, output: string) {
    await this.page.goto(`/write/${projectId}`)
    await expect(this.page.locator('.writer-command-title strong')).toHaveText(title)
    await expect(this.page.locator('.meta-row').filter({ hasText: '论文题目' }).locator('input')).toHaveValue(title)
    const downloading = this.page.waitForEvent('download', { timeout: 90_000 })
    await this.page.getByRole('button', { name: '导出', exact: true }).click()
    const download = await downloading
    expect(await download.failure()).toBeNull()
    expect(download.suggestedFilename()).toContain(title)
    await download.saveAs(output)
  }

  async verifyReport(cohortId: string) {
    await this.page.goto('/workflow?panel=reports')
    await this.page.locator('.report-filter select').selectOption(cohortId)
    await expect(this.page.locator('.report-metrics strong')).toHaveText(['100%', '50%'])
  }
}

class FixtureApi {
  constructor(private readonly api: APIRequestContext) {}

  async call(method: 'get' | 'post' | 'put', path: string, token: string, data?: object, status = 200) {
    const response = await this.api[method](path, {
      headers: token ? { Authorization: `Bearer ${token}` } : {}, data,
    })
    expect(response.status(), `${method} ${path}`).toBe(status)
    return response.json()
  }

  async readyProject(student: string, participant: any, title: string) {
    const bound = await this.call('post', `/api/stage-participants/${participant.id}/project`, student, { title })
    let project = bound.project
    const meta = Object.fromEntries(project.template_snapshot.meta_fields.map((field: any) => [
      field.key, field.key === 'title' ? title : `合成${field.label}`,
    ]))
    project = await this.call('put', `/api/projects/${project.id}/meta`, student, { base_revision: project.revision, meta })
    for (const section of project.sections.filter((section: any) => section.required)) {
      project = await this.call('put', `/api/projects/${project.id}/section/${section.key}`, student, {
        base_revision: project.revision,
        paragraphs: [{ id: `${section.key}-uat`, text: `合成回归正文 ${section.key}，验证标题审批和报表。` }],
      })
    }
    return project
  }
}

test('UAT fixes: final topic approval reaches the writer and Word, and a two-stage report shows 100% and 50%', async ({ page, request }, info) => {
  test.setTimeout(180_000)
  // This suite provisions fixtures and runs a real local worker; fail closed for hosted targets.
  expect(['127.0.0.1', 'localhost']).toContain(new URL(API_BASE_URL).hostname)
  const stamp = randomBytes(5).toString('hex')
  const password = `Fixture-${randomBytes(16).toString('hex')}`
  const adminPassword = process.env.E2E_ADMIN_PASSWORD
  expect(adminPassword).toBeTruthy()
  const api = new FixtureApi(request)
  const admin = (await api.call('post', '/api/auth/login', '', { username: 'admin', password: adminPassword })).token
  const tokens: string[] = [admin]
  try {
    const teacherName = `uat_teacher_${stamp}`, studentName = `uat_student_${stamp}`
    const teacher = await api.call('post', '/api/auth/register', '', { username: teacherName, password, role: 'teacher', display_name: '合成回归教师' }, 201)
    await api.call('put', `/api/auth/admin/users/${teacher.id}/role-status`, admin, { role: 'teacher', status: 'active' })
    const student = await api.call('post', '/api/auth/register', '', { username: studentName, password, role: 'student', display_name: '合成回归学生' }, 201)
    const teacherToken = (await api.call('post', '/api/auth/login', '', { username: teacherName, password })).token
    const studentToken = (await api.call('post', '/api/auth/login', '', { username: studentName, password })).token
    tokens.push(teacherToken, studentToken)
    const organization = await api.call('post', '/api/organizations', admin, { id: `uat-fix-${stamp}`, name: '合成回归学院', type: 'school' }, 201)
    const cohort = await api.call('post', '/api/cohorts', admin, { name: `合成双阶段-${stamp}`, organization_id: organization.id }, 201)
    await api.call('post', `/api/cohorts/${cohort.id}/members`, admin, { student_id: student.id, teacher_id: teacher.id }, 201)
    const now = Date.now(), stages: any[] = []
    for (const index of [1, 2]) {
      const stage = await api.call('post', `/api/cohorts/${cohort.id}/stages`, admin, {
        key: `final${index}`, name: `合成阶段${index}`, is_final: true,
        open_at: new Date(now - 60_000).toISOString(), deadline_at: new Date(now + 86_400_000).toISOString(),
      }, 201)
      await api.call('post', `/api/stages/${stage.id}/publish`, admin)
      stages.push(stage)
    }
    const tasks = await api.call('get', '/api/my-tasks', studentToken)
    const participants = stages.map(stage => tasks.find((p: any) => p.stage_id === stage.id))
    const projects: any[] = []
    for (const [index, participant] of participants.entries()) projects.push(await api.readyProject(studentToken, participant, `旧题目${index}-${stamp}`))
    const topic = await api.call('post', `/api/stage-participants/${participants[1].id}/process-records`, studentToken, {
      artifact_type: 'topic_change', data: { original_topic: projects[1].title, new_topic: `审批新题目-${stamp}`, reason: '合成审批回归' },
    }, 201)
    await api.call('post', `/api/process-records/${topic.id}/submit`, studentToken)
    await api.call('post', `/api/process-records/${topic.id}/decisions`, teacherToken, { decision: 'approved' })
    for (const [index, participant] of participants.entries()) {
      await api.call('post', `/api/stage-participants/${participant.id}/acknowledge`, studentToken, {})
      const candidate = await api.call('post', '/api/submission-candidates', studentToken, { participant_id: participant.id }, 201)
      expect(candidate.status).toBe('ready')
      const submitted = await api.call('post', '/api/submissions', studentToken, {
        candidate_id: candidate.id, idempotency_key: `uat-${stamp}-${index}`, warning_acknowledged: true,
      })
      await api.call('post', `/api/submissions/${submitted.submission.id}/reviews`, teacherToken, { decision: 'approved' })
      if (index === 0) {
        const archive = await api.call('post', '/api/archives', admin, { submission_id: submitted.submission.id }, 202)
        await expect.poll(async () => (await api.call('get', `/api/archives/${archive.id}`, admin)).status, { timeout: 60_000 }).toBe('succeeded')
      }
    }
    const approved = await api.call('post', `/api/process-records/${topic.id}/decisions`, admin, { decision: 'approved' })
    expect(approved.state).toBe('approved')
    const report = await api.call('get', `/api/reports/cohorts/${cohort.id}`, admin)
    expect(report.metrics.on_time_first_submission_rate).toBe(1)
    expect(report.metrics.archive_completion_rate).toBe(0.5)
    expect(report.member_count).toBe(1)
    expect(report.participant_count).toBe(2)

    const login = new LoginPage(page), ui = new UatFixPage(page)
    await login.login(studentName, password)
    const output = info.outputPath('approved-topic.docx'), newTitle = `审批新题目-${stamp}`
    await ui.verifyWriter(projects[1].id, newTitle, output)
    const text = execFileSync(process.env.E2E_PYTHON_BIN || 'python3.12', ['-c',
      'from docx import Document; import sys; print("\\n".join(p.text for p in Document(sys.argv[1]).paragraphs))', output], { encoding: 'utf8' })
    expect(text).toContain(newTitle)
    expect(text).not.toContain(projects[1].title)
    await page.screenshot({ path: info.outputPath('approved-title-writer.png'), fullPage: true })
    await ui.logout()
    await login.login('admin', adminPassword!)
    await ui.verifyReport(cohort.id)
    await page.locator('.report-panel').screenshot({ path: info.outputPath('corrected-report.png') })
    await ui.logout()
    await info.attach('uat-fix-results', { body: JSON.stringify({ cohort_id: cohort.id, project_id: projects[1].id,
      approved_title: newTitle, first_submission_rate: report.metrics.on_time_first_submission_rate,
      archive_completion_rate: report.metrics.archive_completion_rate }), contentType: 'application/json' })
  } finally {
    for (const token of tokens) await api.call('post', '/api/auth/logout', token)
  }
})
