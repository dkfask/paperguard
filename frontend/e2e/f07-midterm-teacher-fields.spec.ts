import { randomBytes } from 'node:crypto'
import { expect, test, type APIRequestContext } from '@playwright/test'
import { E2E_ADMIN_PASSWORD } from './document-platform.api'
import { MidtermTeacherInboxPage } from './pages/midterm-teacher-inbox.page'

async function readJson(response: Awaited<ReturnType<APIRequestContext['get']>>, label: string) {
  const body = await response.json().catch(() => ({}))
  if (!response.ok()) throw new Error(`${label}: ${response.status()} ${JSON.stringify(body)}`)
  return body
}

test('TC-F07-01: 学生提交中期字段后，指派教师填写意见和评分并审批', async ({ page, request }) => {
  if (!E2E_ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD is required')
  const suffix = randomBytes(4).toString('hex')
  const adminLogin = await readJson(await request.post('/api/auth/login', {
    data: { username: 'admin', password: E2E_ADMIN_PASSWORD },
  }), 'admin login')
  const adminHeaders = { Authorization: `Bearer ${adminLogin.token}` }

  const teacherName = `f07_teacher_${suffix}`
  const teacherPassword = `F07!Aa9-${randomBytes(5).toString('hex')}`
  const teacher = await readJson(await request.post('/api/auth/register', {
    data: { username: teacherName, password: teacherPassword, role: 'teacher', display_name: 'F07 合成指导教师' },
  }), 'register teacher')
  await readJson(await request.put(`/api/auth/admin/users/${teacher.id}/role-status`, {
    headers: adminHeaders, data: { role: 'teacher', status: 'active' },
  }), 'activate teacher')
  const teacherLogin = await readJson(await request.post('/api/auth/login', {
    data: { username: teacherName, password: teacherPassword },
  }), 'teacher login')
  const teacherHeaders = { Authorization: `Bearer ${teacherLogin.token}` }

  const studentName = `f07_student_${suffix}`
  const studentPassword = `F07!Aa9-${randomBytes(5).toString('hex')}`
  const student = await readJson(await request.post('/api/auth/register', {
    data: { username: studentName, password: studentPassword, role: 'student', display_name: 'F07 合成学生' },
  }), 'register student')
  const studentLogin = await readJson(await request.post('/api/auth/login', {
    data: { username: studentName, password: studentPassword },
  }), 'student login')
  const studentHeaders = { Authorization: `Bearer ${studentLogin.token}` }

  const organization = await readJson(await request.post('/api/organizations', {
    headers: adminHeaders,
    data: { id: `f07-school-${suffix}`, name: 'F07 合成测试学院', type: 'school' },
  }), 'create organization')
  const cohort = await readJson(await request.post('/api/cohorts', {
    headers: adminHeaders,
    data: { name: `F07 合成测试批次 ${suffix}`, organization_id: organization.id, academic_year: '2027' },
  }), 'create cohort')
  await readJson(await request.post(`/api/cohorts/${cohort.id}/members`, {
    headers: adminHeaders, data: { student_id: student.id, teacher_id: teacher.id },
  }), 'assign student and teacher')
  const openAt = new Date(Date.now() - 60_000).toISOString()
  const deadline = new Date(Date.now() + 86_400_000).toISOString()
  const stage = await readJson(await request.post(`/api/cohorts/${cohort.id}/stages`, {
    headers: adminHeaders,
    data: { key: 'midterm', name: '中期检查', open_at: openAt, deadline_at: deadline, required_materials: [] },
  }), 'create stage')
  await readJson(await request.post(`/api/stages/${stage.id}/publish`, { headers: adminHeaders }), 'publish stage')
  const tasks = await readJson(await request.get('/api/my-tasks', { headers: studentHeaders }), 'load student tasks')
  const participant = tasks.find((item: { student_id: string }) => item.student_id === student.id)
  expect(participant).toBeTruthy()

  const form = await readJson(await request.get(`/api/stage-participants/${participant.id}/process-records/form/midterm_check`, {
    headers: studentHeaders,
  }), 'load student midterm form')
  expect(form.schema.fields.map((field: { key: string }) => field.key)).toContain('progress')
  expect(form.schema.fields.map((field: { key: string }) => field.key)).not.toContain('advisor_comment')
  expect(form.schema.fields.map((field: { key: string }) => field.key)).not.toContain('score')

  const studentData = {
    title: 'F07 合成中期检查', student_id: `S-${suffix}`, student_name: 'F07 合成学生',
    topic: `F07 中期题目 ${suffix}`, progress: '完成合成需求分析和主体实现',
    problems: '性能边界仍需验证', next_plan: '补充压力测试并整理结果',
  }
  const record = await readJson(await request.post(`/api/stage-participants/${participant.id}/process-records`, {
    headers: studentHeaders,
    data: { artifact_type: 'midterm_check', structured_form: true, data: studentData },
  }), 'create student midterm record')
  const submitted = await readJson(await request.post(`/api/process-records/${record.id}/submit`, {
    headers: studentHeaders,
  }), 'submit student midterm record')
  expect(submitted.state).toBe('submitted')

  const inbox = await readJson(await request.get('/api/process-records/inbox', { headers: teacherHeaders }), 'load teacher inbox')
  expect(inbox.map((item: { id: string }) => item.id)).toContain(record.id)

  const teacherInboxPage = new MidtermTeacherInboxPage(page)
  await teacherInboxPage.login(teacherName, teacherPassword)
  await page.goto('/workspace')
  await teacherInboxPage.openInbox()
  await expect(teacherInboxPage.recordRow(studentData.student_id)).toBeVisible()
  await teacherInboxPage.expectNoHorizontalOverflow()
  await teacherInboxPage.saveTeacherFields(studentData.student_id, '按计划完成阶段目标，建议继续验证边界情况。', 88)
  await teacherInboxPage.expectNoHorizontalOverflow()

  const saved = await readJson(await request.get(`/api/process-records/${record.id}`, { headers: teacherHeaders }), 'read saved midterm record')
  expect(saved.state).toBe('submitted')
  expect(saved.data).toMatchObject({
    ...studentData,
    advisor_comment: '按计划完成阶段目标，建议继续验证边界情况。',
    score: 88,
  })
  const dialog = await teacherInboxPage.openTeacherFields(studentData.student_id)
  await teacherInboxPage.expectNoHorizontalOverflow()
  await expect(dialog.getByLabel('指导教师意见')).toHaveValue('按计划完成阶段目标，建议继续验证边界情况。')
  await expect(dialog.getByLabel('中期评价分数')).toHaveValue('88')
  expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true)
  await page.keyboard.press('Escape')
  await expect(dialog).not.toBeVisible()
  await expect(teacherInboxPage.recordRow(studentData.student_id).getByRole('button', { name: '填写意见/评分' })).toBeFocused()

  await teacherInboxPage.saveTeacherFields(studentData.student_id, saved.data.advisor_comment, '')
  const cleared = await readJson(await request.get(`/api/process-records/${record.id}`, { headers: teacherHeaders }), 'read cleared midterm score')
  expect(cleared.data).toEqual({ ...saved.data, score: null })

  await teacherInboxPage.recordRow(studentData.student_id).getByRole('button', { name: '通过' }).click()
  await expect(teacherInboxPage.recordRow(studentData.student_id)).toHaveCount(0)
  const approved = await readJson(await request.get(`/api/process-records/${record.id}`, { headers: teacherHeaders }), 'verify approved midterm record')
  expect(approved.state).toBe('approved')
  expect(approved.data).toEqual(cleared.data)
})
