import { expect, request, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { LoginPage, PaperGuardPage } from './pages/paperguard.page'
import { WorkflowUsagePage } from './pages/workflow-usage.page'
import { UsageAuditPage } from './pages/usage-audit.page'
import { API_BASE_URL } from './document-platform.api'

const DATA_ROOT = process.env.E2E_DATA_ROOT ?? '/Users/a1234/Documents/perpetual/22016022205-王俞尹'
const TEMPLATE_ROOT = process.env.E2E_TEMPLATE_ROOT ?? '/Users/a1234/Documents/perpetual/附件3-2026届毕业设计（论文）相关文档模板'
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD

const files = {
  paper: path.join(DATA_ROOT, '其他过程材料', '22016022205+王俞尹+毕业论文-计软学院-4.7.docx'),
  template: path.join(TEMPLATE_ROOT, '阅读7-成都东软学院本科毕业设计报告样例.docx'),
  policy: path.join(TEMPLATE_ROOT, '阅读4-成都东软学院本科生毕业设计报告（论文）撰写规范.pdf'),
  taskBook: path.join(DATA_ROOT, '22016022205+王俞尹+任务书-计软学院.docx'),
  openingReport: path.join(DATA_ROOT, '22016022205-王俞尹-开题报告-计软学院.docx'),
  cover: path.join(DATA_ROOT, '0-过程性文档封面.doc'),
}

function suffix() {
  return `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
}

function futureLocalDate(hours: number) {
  const date = new Date(Date.now() + hours * 60 * 60 * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function pastLocalDate(hours: number) {
  return futureLocalDate(-hours)
}

async function restoreSeededAdminPassword(replacementPassword: string) {
  const api = await request.newContext({ baseURL: API_BASE_URL })
  try {
    const replacementLogin = await api.post('/api/auth/login', {
      data: { username: 'admin', password: replacementPassword },
    })
    if (!replacementLogin.ok()) {
      // The lifecycle may have failed before the forced-change step. Confirm
      // the original credential is still valid before declaring cleanup done.
      const originalLogin = await api.post('/api/auth/login', {
        data: { username: 'admin', password: ADMIN_PASSWORD },
      })
      if (originalLogin.ok()) return
      throw new Error(`admin cleanup login failed: ${replacementLogin.status()}/${originalLogin.status()}`)
    }

    const login = await replacementLogin.json()
    const restoreResponse = await api.post('/api/auth/change-password', {
      headers: { Authorization: `Bearer ${login.token}` },
      data: {
        current_password: replacementPassword,
        new_password: ADMIN_PASSWORD,
      },
    })
    if (!restoreResponse.ok()) throw new Error(`admin cleanup restore failed: ${restoreResponse.status()}`)
  } finally {
    await api.dispose()
  }
}

async function submitMaterial(page: Page, artifactType: string, title: string, attachment: string) {
  const panel = page.locator('.materials-panel')
  await expect(panel).toBeVisible()
  const compose = panel.locator('.material-compose')
  await compose.locator('select').first().selectOption(artifactType)
  const schemaMarkers: Record<string, RegExp> = {
    task_book: /主要内容、任务及要求/,
    opening_report: /选题背景/,
    final_report: /终稿说明/,
    process_cover: /过程材料清单/,
  }
  await expect(compose.locator('.structured-field, .structured-table-editor').filter({ hasText: schemaMarkers[artifactType] }).first()).toBeVisible()
  await compose.locator('input').first().fill(title)

  for (const field of await compose.locator('.structured-field').all()) {
    const control = field.locator('input, textarea, select').first()
    if (!await control.count()) continue
    const tag = await control.evaluate((element) => element.tagName.toLowerCase())
    if (tag === 'select') {
      const options = await control.locator('option').evaluateAll((items) => items.map((item) => ({ value: item.getAttribute('value') || '', label: item.textContent || '' })).filter((item) => item.value))
      if (options.length) await control.selectOption(options[0].value)
    } else if (await control.getAttribute('type') === 'datetime-local') {
      await control.fill(futureLocalDate(2))
    } else if ((await control.getAttribute('placeholder') || '').includes('JSON')) {
      await control.fill('[]')
    } else {
      await control.fill('测试材料已按学校模板填写，作为浏览器回归证据。')
    }
  }

  for (const table of await compose.locator('.structured-table-editor').all()) {
    const add = table.getByRole('button', { name: /新增一行/ })
    if (await add.count()) await add.click()
    for (const input of await table.locator('input, textarea').all()) {
      await input.fill((await input.getAttribute('type')) === 'number' ? '1' : '测试材料')
    }
  }

  await compose.locator('input[type=file]').setInputFiles(attachment)
  await new PaperGuardPage(page).waitForMutation('/submit', async () => {
    await compose.getByRole('button', { name: /保存并提交材料/ }).click()
  })
  await expect(page.getByRole('status')).toContainText('材料已提交')
}

async function completeRequiredWriterGates(page: Page, thesisTitle: string) {
  const requiredSections = [
    { pattern: /摘\s*要/, text: '本文根据学校毕业设计论文模板完成格式与流程回归测试，验证论文结构、过程材料和导出结果。' },
    { pattern: /Abstract/i, text: 'This regression test validates the thesis workflow, required sections, process materials, and template based export.' },
    { pattern: /关键技术介绍/, text: '本章补充关键技术介绍，用于验证模板要求章节的编辑、保存和检查流程。' },
    { pattern: /系统测试/, text: '本章补充系统测试内容，用于验证完整论文导出前的必填门禁。' },
  ]

  for (const section of requiredSections) {
    await page.getByRole('button', { name: '章节大纲', exact: true }).click()
    const row = page.locator('.nav-row').filter({ hasText: section.pattern }).first()
    await expect(row, `required writer section is visible: ${section.pattern}`).toHaveCount(1)
    await row.click()
    const heading = page.locator('input.heading-input').first()
    await expect(heading).toBeVisible()
    if (!(await heading.inputValue()).trim()) await heading.fill(section.pattern.source)

    let paragraph = page.locator('textarea.para-text').first()
    if (!await paragraph.count()) {
      // 段落新增入口是 BlockList.vue 的插入行首个按钮（class="primary"，文案 t('blockList.addParagraph')）。
      // 旧定位器按「新增段落」查找，但该文案在产品中从不存在（WriterEditor 的按钮是
      // t('writer.addParagraph')＝「添加段落」，且本页面不渲染 WriterEditor 的章节工具条），
      // 属于被 BUG-20261001-01 长期掩盖的 TEST-DEFECT，故按真实结构定位。
      await page.locator('.insert-row button.primary').first().click()
      paragraph = page.locator('textarea.para-text').first()
    }
    await expect(paragraph).toBeVisible()
    await paragraph.fill(section.text)
    await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)
  }

  await page.getByRole('button', { name: '章节大纲', exact: true }).click()
  const coverRow = page.locator('.nav-row').filter({ hasText: /封面/ }).first()
  await expect(coverRow).toHaveCount(1)
  await coverRow.click()
  const titleInput = page.locator('.meta-row').filter({ hasText: /题目/ }).locator('input').first()
  await expect(titleInput).toBeVisible()
  await titleInput.fill(thesisTitle)
  await new PaperGuardPage(page).waitForMutation('/meta', async () => {
    await page.getByRole('button', { name: '保存封面信息' }).click()
  }, 'PUT')
  await expect(page.locator('.writer-save-status')).not.toContainText('未保存')
}

test.describe('PaperGuard browser lifecycle', () => {
  test('admin teacher student complete lifecycle with real thesis files', async ({ browser }, testInfo) => {
    // Includes two submissions, return/revision, and an actual archive worker/download.
    test.setTimeout(300_000)
    test.skip(testInfo.project.name !== 'chromium', 'Full lifecycle is intentionally desktop-only; responsive shell is covered below.')
    if (!ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD is required')
    for (const file of Object.values(files)) expect(fs.existsSync(file), `missing test file: ${file}`).toBeTruthy()

    const run = suffix()
    const teacherUsername = `e2e_teacher_${run}`
    const studentUsername = `e2e_student_${run}`
    const teacherPassword = `Teacher-${run}-2026a`
    const studentPassword = `Student-${run}-2026a`
    const adminReplacementPassword = `Admin-${run}-2026a`
    const teacherDisplay = `使用测试教师-${run}`
    const studentDisplay = `使用测试学生-${run}`
    const schoolName = `使用测试学校-${run}`
    const cohortName = `2026届论文回归批次-${run}`
    const thesisTitle = `基于STM32的卧室智能控制系统的设计与实现-${run}`

    const adminContext = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const teacherContext = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const studentContext = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const admin = await adminContext.newPage()
    const teacher = await teacherContext.newPage()
    const student = await studentContext.newPage()
    let adminPasswordMayHaveChanged = false
    const consoleErrors: string[] = []
    const httpErrors: string[] = []
    const checkpoints: string[] = []
    async function checkpoint(name: string, page?: Page) {
      checkpoints.push(name)
      const file = testInfo.outputPath('usage-checkpoints.json')
      fs.writeFileSync(file, JSON.stringify(checkpoints, null, 2))
      if (page) await page.screenshot({ path: testInfo.outputPath(`${name}.png`), animations: 'disabled', mask: [page.locator('.review-evidence, .history-snapshot')] })
    }
    for (const page of [admin, teacher, student]) {
      page.on('pageerror', (error) => consoleErrors.push(`${page.url()} pageerror: ${error.message}`))
      page.on('console', (message) => {
        if (message.type() !== 'error' || message.text().includes('Failed to load resource')) return
        consoleErrors.push(`${page.url()} console: ${message.text()}`)
      })
      page.on('response', (response) => {
        if (response.status() < 400) return
        // The first export intentionally exercises the HTTP 409 completeness gate.
        if (response.status() === 409 && response.url().includes('/export-jobs?force=false')) return
        httpErrors.push(`${page.url()} response: ${response.status()} ${response.request().method()} ${response.url()}`)
      })
    }

    try {
      await new LoginPage(teacher).createFixtureAccount(teacherUsername, teacherPassword, teacherDisplay, 'teacher')

      adminPasswordMayHaveChanged = true
      await new LoginPage(admin).login('admin', ADMIN_PASSWORD, adminReplacementPassword)
      await admin.goto('/admin/users')
      const directory = new PaperGuardPage(admin)
      const teacherDialog = await directory.openUserDetails(teacherUsername)
      await new PaperGuardPage(admin).waitForMutation('/role-status', async () => {
        await teacherDialog.getByRole('button', { name: '通过认证' }).click()
      }, 'PUT')
      await directory.closeDetails()
      await expect(await directory.userRow(teacherUsername)).toContainText('已认证')
      await checkpoint('administrator-certified-teacher', admin)

      await admin.goto('/organizations')
      await admin.getByRole('button', { name: '新建组织' }).click()
      const orgEditor = admin.getByRole('dialog')
      await new PaperGuardPage(admin).control('名称').fill(schoolName)
      await new PaperGuardPage(admin).waitForMutation('/api/organizations', async () => {
        await orgEditor.getByRole('button', { name: '保存' }).click()
      })
      await expect(admin.getByText(schoolName, { exact: true })).toBeVisible()
      await directory.closeDetails()
      await admin.getByRole('tab', { name: '用户作用域', exact: true }).click()
      const userSelect = admin.locator('.members select').nth(0)
      const scopeSelect = admin.locator('.members select').nth(1)
      await userSelect.selectOption({ label: `${teacherDisplay} · ${teacherUsername}` })
      const scopeOption = scopeSelect.locator('option').filter({ hasText: schoolName }).first()
      const schoolId = await scopeOption.getAttribute('value') || ''
      await scopeSelect.selectOption(schoolId)
      await new PaperGuardPage(admin).waitForMutation('/context', async () => {
        await admin.locator('.members').getByRole('button', { name: '应用' }).click()
      }, 'PUT')

      await new LoginPage(teacher).login(teacherUsername, teacherPassword)
      await teacher.goto('/templates')
      await new PaperGuardPage(teacher).waitForMutation('/templates/import', async () => {
        await teacher.locator('input[type=file]').first().setInputFiles(files.template)
      })
      await expect(teacher.getByText(/识别结果/)).toBeVisible()
      const headingStyles = teacher.getByRole('region', { name: '标题层级', exact: true })
      const otherStyles = teacher.getByRole('region', { name: '其他排版样式', exact: true })
      await expect(headingStyles.locator('summary')).toHaveText(['一级标题(章)', '二级标题(节)', '三级标题(小节)'])
      for (const label of ['英文摘要', '中文关键词', '英文关键词']) await expect(otherStyles.locator('summary').filter({ hasText: label })).toHaveCount(1)
      await expect(teacher.getByRole('dialog')).not.toContainText(/english_abstract|english_keywords|\bkeywords\b/)
      await checkpoint('teacher-imported-style-preview', teacher)
      await new PaperGuardPage(teacher).waitForMutation('/api/templates', async () => {
        await teacher.getByRole('button', { name: '确认并创建模板' }).click()
      })
      await expect(teacher).toHaveURL(/\/templates\/[^/]+\/edit/)
      const importedTemplateId = new URL(teacher.url()).pathname.split('/')[2]
      const importedTemplate = await teacher.request.get(`/api/templates/${importedTemplateId}`, {
        headers: { Authorization: `Bearer ${await teacher.evaluate(() => sessionStorage.getItem('pg_token'))}` },
      })
      expect(importedTemplate.ok()).toBeTruthy()
      const importedDefinition = await importedTemplate.json()
      expect(importedDefinition.sections.find((section: { type: string }) => section.type === 'english_abstract').title).toBe('Abstract')
      const flattenSections = (sections: any[]): any[] => sections.flatMap(section => [section, ...flattenSections(section.children || [])])
      expect(flattenSections(importedDefinition.sections).filter(section => section.type.startsWith('heading') && /abstract|keywords?|关键词/i.test(section.title))).toEqual([])
      await checkpoint('teacher-imported-template')

      await teacher.goto('/policies')
      await teacher.getByRole('button', { name: '新建规范' }).click()
      const policyEditor = teacher.locator('.policy-editor-panel')
      await new PaperGuardPage(teacher).control('规范名称').fill(`使用测试写作规范-${run}`)
      await new PaperGuardPage(teacher).control('版本').fill('2026.1')
      await new PaperGuardPage(teacher).control('说明').fill('真实学校模板回归规范')
      await policyEditor.getByRole('tab', { name: '规范正文', exact: true }).click()
      await policyEditor.locator('textarea.markdown-input').fill('# 论文格式规范\n\n请按照学校模板完成论文。')
      await policyEditor.getByRole('tab', { name: '作用域绑定', exact: true }).click()
      await policyEditor.getByRole('button', { name: '添加绑定', exact: true }).click()
      const binding = policyEditor.locator('.binding-row').first()
      await binding.getByLabel('适用范围', { exact: true }).selectOption('school')
      await binding.getByLabel('适用对象', { exact: true }).selectOption(schoolId)
      await checkpoint('teacher-policy-binding', teacher)
      await policyEditor.getByRole('tab', { name: '基本信息', exact: true }).click()
      await new PaperGuardPage(teacher).waitForMutation('/api/policies', async () => {
        await policyEditor.getByRole('button', { name: '新建规范' }).click()
      })
      // 新建请求返回后页面仍在刷新规范列表，等保存结束再上传，避免刷新覆盖上传反馈。
      await expect(policyEditor.getByRole('button', { name: '保存草稿' })).toBeEnabled()
      await policyEditor.getByRole('tab', { name: '参考文档', exact: true }).click()
      await new PaperGuardPage(teacher).waitForMutation('/documents', async () => {
        await policyEditor.locator('.upload-row input[type=file]').setInputFiles(files.policy)
      })
      await expect(policyEditor.locator('[role=status]')).toContainText('参考文档已上传', { timeout: 30_000 })
      await expect(policyEditor.getByText(path.basename(files.policy))).toBeVisible({ timeout: 30_000 })
      await new PaperGuardPage(teacher).waitForMutation('/publish', async () => {
        await policyEditor.getByRole('button', { name: '发布版本' }).click()
      })
      await expect(policyEditor).toContainText('已发布')
      await checkpoint('teacher-published-policy')

      await new LoginPage(student).createFixtureAccount(studentUsername, studentPassword, studentDisplay, 'student')
      await teacher.goto('/workflow')
      const workflow = new PaperGuardPage(teacher)
      await expect(teacher.locator('.workflow-sync-state')).toHaveCount(0)
      await teacher.getByRole('button', { name: '创建批次', exact: true }).click()
      await workflow.control('批次名称').fill(cohortName)
      const cohortScope = workflow.control('组织范围')
      const cohortScopeOption = cohortScope.locator('option').filter({ hasText: schoolName }).first()
      await cohortScope.selectOption(await cohortScopeOption.getAttribute('value') || '')
      await workflow.control('学年').fill('2026')
      await workflow.waitForMutation('/api/cohorts', async () => {
        await teacher.getByRole('dialog').getByRole('button', { name: '创建批次', exact: true }).click()
      })
      const teacherWorkflow = new WorkflowUsagePage(teacher)
      await teacherWorkflow.configureCohort(cohortName)

      const memberPanel = teacher.locator('.member-quick-add')
      const memberInputs = memberPanel.locator('label input')
      await memberInputs.nth(0).fill(studentUsername)
      await memberInputs.nth(1).fill(teacherUsername)
      await memberInputs.nth(2).fill('浏览器回归首次分配指导关系')
      await workflow.waitForMutation('/members', async () => {
        await memberPanel.getByRole('button', { name: '保存关系' }).click()
      })

      const stageForm = teacher.locator('.stage-config-form')
      const stageInputs = stageForm.locator('label input')
      await stageInputs.nth(0).fill('final')
      await stageInputs.nth(1).fill('终稿阶段')
      await stageInputs.nth(2).fill(pastLocalDate(1))
      await stageInputs.nth(3).fill(futureLocalDate(48))
      await stageInputs.nth(4).fill('7')
      await stageForm.locator('select').selectOption(importedTemplateId)
      await stageInputs.nth(5).fill('')
      await stageInputs.nth(6).fill('')
      await stageForm.locator('input[type=checkbox]').first().check()
      await workflow.waitForMutation('/stages', async () => {
        await stageForm.getByRole('button', { name: '保存阶段草稿' }).click()
      })
      await teacherWorkflow.closeDetails()
      let cohortPanel = await teacherWorkflow.openCohort(cohortName)
      const stageRow = cohortPanel.locator('.stage-row').filter({ hasText: '终稿阶段' })
      await expect(stageRow).toBeVisible()
      await stageRow.getByRole('button', { name: '预览发布' }).click()
      await expect(teacher.locator('.confirm-dialog')).toBeVisible()
      await checkpoint('teacher-stage-before-publish', teacher)
      const confirmPublish = teacher.locator('.confirm-dialog').getByRole('button', { name: '确认发布', exact: true })
      const confirmationReachable = await confirmPublish.evaluate((button) => {
        const r = button.getBoundingClientRect()
        return button.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2))
      })
      expect(confirmationReachable, 'stage confirmation must receive pointer input above its parent drawer').toBe(true)
      await new PaperGuardPage(teacher).waitForMutation('/publish', async () => {
        await confirmPublish.click()
      })
      await expect(teacher.locator('.confirm-dialog')).toHaveCount(0)
      await checkpoint('teacher-stage-after-publish', teacher)
      await expect(teacher.locator('.error-banner'), '阶段发布后应反馈成功而非前端异常').toHaveCount(0)
      await expect(teacher.locator('.ok-banner')).toContainText('阶段已发布')
      await expect(cohortPanel.locator('.stage-row')).toContainText('已发布')
      await teacherWorkflow.closeDetails()
      await checkpoint('teacher-stage-published-without-refresh')

      await new LoginPage(student).login(studentUsername, studentPassword)
      await student.goto('/workflow')
      const studentWorkflow = new WorkflowUsagePage(student)
      let studentTask = await studentWorkflow.openStudentTask(cohortName)
      await studentTask.getByRole('button', { name: '开始写作' }).click()
      const bindModal = student.locator('.modal-mask').filter({ hasText: '绑定阶段主论文' })
      await bindModal.locator('input').fill(thesisTitle)
      await new PaperGuardPage(student).waitForMutation('/stage-participants/', async () => {
        await bindModal.getByRole('button', { name: '创建并绑定' }).click()
      })
      await expect(student.locator('.student-task-summary').filter({ hasText: cohortName })).toContainText(thesisTitle)
      studentTask = await studentWorkflow.openStudentTask(cohortName)
      await expect(studentTask).toContainText(thesisTitle)
      await studentTask.getByRole('button', { name: '确认要求' }).click()
      await expect(student.getByRole('status')).toContainText('已确认')
      await checkpoint('student-bound-and-acknowledged')

      await studentTask.getByRole('button', { name: '过程材料' }).click()
      await submitMaterial(student, 'task_book', '任务书（真实材料）', files.taskBook)
      await submitMaterial(student, 'opening_report', '开题报告（真实材料）', files.openingReport)
      await submitMaterial(student, 'final_report', '终稿论文（真实材料）', files.paper)
      await submitMaterial(student, 'process_cover', '过程性文档封面（DOC 兼容性）', files.cover)
      await checkpoint('student-submitted-materials')
      await student.goto('/workflow')
      const taskForMaterialReadback = await studentWorkflow.openStudentTask(cohortName)
      await taskForMaterialReadback.getByRole('button', { name: '过程材料', exact: true }).click()
      const savedMaterials = student.locator('.materials-panel')
      await expect(savedMaterials.locator('.record-row')).toHaveCount(4)
      const materialDownload = student.waitForEvent('download')
      await savedMaterials.getByRole('button', { name: path.basename(files.taskBook), exact: true }).click()
      const materialPath = await (await materialDownload).path()
      expect(materialPath).toBeTruthy()
      expect(fs.statSync(materialPath!).size).toBeGreaterThan(1000)
      const filledFormDownload = student.waitForEvent('download')
      await savedMaterials.getByRole('button', { name: '导出填充表', exact: true }).first().click()
      const filledFormPath = await (await filledFormDownload).path()
      expect(filledFormPath).toBeTruthy()
      expect(fs.statSync(filledFormPath!).size).toBeGreaterThan(1000)
      await checkpoint('student-materials-reopened-and-downloaded')
      await studentWorkflow.closeDetails()

      await teacher.goto('/workflow')
      const inbox = teacher.locator('.process-inbox')
      await expect(inbox).toBeVisible()
      while (await inbox.locator('.process-inbox-row').count()) {
        await new PaperGuardPage(teacher).waitForMutation('/decision', async () => {
          await inbox.locator('.process-inbox-row').first().getByRole('button', { name: '通过' }).click()
        })
        await teacher.waitForTimeout(200)
      }
      await checkpoint('teacher-approved-materials')

      await student.goto('/workflow')
      const taskAfterMaterials = await studentWorkflow.openStudentTask(cohortName)
      await taskAfterMaterials.getByRole('link', { name: '继续写作' }).click()
      await expect(student).toHaveURL(/\/write\//)
      await expect(student.locator('.writer-shell')).toBeVisible()
      await new PaperGuardPage(student).waitForMutation('/import-docx', async () => {
        await student.locator('input[type=file][accept=".docx"]').setInputFiles(files.paper)
      })
      await expect(student.locator('.import-modal')).toBeVisible()
      await new PaperGuardPage(student).waitForMutation('/import-docx', async () => {
        await student.locator('.import-modal').getByRole('button', { name: '应用到当前草稿' }).click()
      })
      await expect(student.locator('.writer-shell')).toBeVisible()
      await student.getByRole('button', { name: '检查', exact: true }).click()
      await expect(student.locator('.check-panel')).toBeVisible({ timeout: 60_000 })
      await expect(student.locator('.writer-save-status')).not.toContainText('未保存')
      await studentWorkflow.closeDetails()

      // 学生完成模板门禁后正常导出，不依赖强制导出。
      await completeRequiredWriterGates(student, thesisTitle)
      const exporting = student.waitForEvent('download', { timeout: 45_000 })
      await student.locator('.writer-export-action').click()
      const download = await exporting
      expect(download).toBeTruthy()
      const downloadedPath = await download!.path()
      expect(downloadedPath).toBeTruthy()
      expect(fs.statSync(downloadedPath!).size).toBeGreaterThan(10_000)
      await checkpoint('student-exported-docx')

      await student.goto('/workflow')
      const finalTask = await studentWorkflow.openStudentTask(cohortName)
      await studentWorkflow.submitTask(finalTask)
      await checkpoint('student-first-submission', student)
      await student.waitForLoadState('networkidle')

      let reviewPanel = await teacherWorkflow.openReview(thesisTitle)
      await checkpoint('teacher-review-open', teacher)
      const reviewDialog = teacher.getByRole('dialog')
      const keyboardDecisions = new Set<string>()
      for (let step = 0; step < 64 && keyboardDecisions.size < 2; step++) {
        await teacher.keyboard.press('Tab')
        expect(await reviewDialog.evaluate(el => el.contains(document.activeElement))).toBe(true)
        const focused = await teacher.locator(':focus').textContent() || ''
        for (const label of ['退回修改', '通过提交']) if (focused.includes(label)) keyboardDecisions.add(label)
      }
      expect(keyboardDecisions.size, 'both review decisions are reachable by keyboard').toBe(2)
      if (process.env.E2E_AXE_PATH) await new UsageAuditPage(teacher, testInfo).scan('a11y-review', process.env.E2E_AXE_PATH)
      await checkpoint('teacher-review-keyboard-and-scan')
      // Validation and feedback are tested before the actual return.
      await reviewPanel.getByRole('button', { name: '退回修改', exact: true }).click()
      await expect(teacher.locator('.error-banner')).toContainText('退回必须填写原因')
      await reviewPanel.locator('.review-compose textarea').fill('请补充系统测试结论')
      await new PaperGuardPage(teacher).waitForMutation('/comments', async () => {
        await reviewPanel.getByRole('button', { name: '新增批注', exact: true }).click()
      })
      await reviewPanel.locator('.review-decision textarea').fill('请补充系统测试结论后重新提交')
      await new PaperGuardPage(teacher).waitForMutation('/review', async () => {
        await reviewPanel.getByRole('button', { name: '退回修改', exact: true }).click()
      })
      await expect(teacher.locator('.ok-banner')).toContainText('已退回学生修改')
      await checkpoint('teacher-returned-submission')

      await student.goto('/workflow')
      const returnedTask = await studentWorkflow.openStudentTask(cohortName)
      await expect(returnedTask).toContainText('需修改')
      await checkpoint('student-returned-task', student)
      await returnedTask.getByRole('button', { name: '查看记录', exact: true }).click()
      await expect(student.locator('.history-detail'), 'task view-record action must open a visible record').toBeVisible()
      await checkpoint('student-task-history-action', student)
      const returnedHistory = student.locator('.history-detail')
      await expect(returnedHistory).toBeVisible()
      const firstSubmissionNumber = await returnedHistory.locator('h3').first().textContent() || ''
      await expect(returnedHistory, 'student must see the return reason').toContainText('请补充系统测试结论后重新提交')
      await expect(returnedHistory, 'student must see the teacher comment').toContainText('请补充系统测试结论')
      await checkpoint('student-returned-history-feedback', student)
      const feedback = returnedHistory.locator('.submission-feedback')
      await feedback.getByLabel('回复批注', { exact: true }).fill('收到，正在补充系统测试结论。')
      await studentWorkflow.waitForMutation('/response', async () => {
        await feedback.getByRole('button', { name: '发送回复', exact: true }).click()
      })
      await expect(feedback).toContainText('收到，正在补充系统测试结论。')
      await student.goto('/workflow?panel=messages')
      await student.waitForLoadState('networkidle')
      const messages = student.locator('.notification-panel')
      const reviewMessage = messages.locator('.notification-row').filter({ hasText: '论文需要修改' })
      for (let step = 0; !await reviewMessage.count() && step < 100; step++) {
        const next = messages.getByRole('button', { name: '下一页', exact: true })
        if (!await next.count() || !await next.isEnabled()) break
        await next.click()
      }
      await reviewMessage.click()
      await expect(student.locator('.history-detail')).toContainText(firstSubmissionNumber)
      await expect(student.locator('.history-detail')).toContainText('请补充系统测试结论后重新提交')
      await checkpoint('student-notification-feedback', student)
      await student.goto('/workspace')
      await student.waitForLoadState('networkidle')
      const workspaceInbox = student.locator('.workspace-notification-panel')
      const returnMessage = workspaceInbox.locator('.notification-row').filter({ hasText: '论文需要修改' })
      for (let step = 0; !await returnMessage.count() && step < 100; step++) {
        const next = workspaceInbox.getByRole('button', { name: '下一页', exact: true })
        if (!await next.count() || !await next.isEnabled()) break
        await next.click()
      }
      await returnMessage.click()
      await expect(student.locator('.history-detail')).toContainText(firstSubmissionNumber)
      await expect(student.locator('.history-detail')).toContainText('请补充系统测试结论后重新提交')
      await checkpoint('student-workspace-notification-feedback', student)


      await student.goto('/workflow')
      const taskForRevision = await studentWorkflow.openStudentTask(cohortName)
      await taskForRevision.getByRole('link', { name: '继续写作', exact: true }).click()
      await student.getByRole('button', { name: '章节大纲', exact: true }).click()
      const testChapter = student.locator('.nav-row').filter({ hasText: /系统测试/ }).first()
      await testChapter.click()
      const revisedText = '根据教师意见补充系统测试结论：各项测试满足设计目标。'
      await student.locator('textarea.para-text').first().fill(revisedText)
      await expect(student.locator('.writer-save-status')).not.toHaveClass(/pending/)
      await student.reload()
      await student.getByRole('button', { name: '章节大纲', exact: true }).click()
      await testChapter.click()
      await expect(student.locator('textarea.para-text').first()).toHaveValue(revisedText)
      await checkpoint('student-revision-persisted')
      await student.goto('/workflow')
      const resubmitTask = await studentWorkflow.openStudentTask(cohortName)
      await studentWorkflow.submitTask(resubmitTask)
      await checkpoint('student-second-submission')
      await student.goto('/workflow?panel=submissions')
      await expect(student.locator('.screen-collection').filter({ has: student.locator('.history-row') }).first()).toContainText('共 2 项')
      await checkpoint('student-two-submissions', student)
      await studentWorkflow.openHistory(firstSubmissionNumber)
      await expect(student.locator('.history-detail'), 'an older submission must remain viewable after resubmission').toBeVisible()
      await expect(student.locator('.history-detail')).toContainText(firstSubmissionNumber)
      await expect(student.locator('.history-detail')).toContainText('请补充系统测试结论后重新提交')
      await expect(student.locator('.history-detail')).toContainText('收到，正在补充系统测试结论。')
      await checkpoint('student-older-history-action', student)

      reviewPanel = await teacherWorkflow.openReview(thesisTitle)
      await new PaperGuardPage(teacher).waitForMutation('/review', async () => {
        await reviewPanel.getByRole('button', { name: '通过提交' }).click()
      })
      await expect(teacher.locator('.ok-banner')).toContainText('提交已通过')
      await checkpoint('teacher-approved-revision')
      await teacher.waitForLoadState('networkidle')

      await admin.goto('/workflow?panel=archives')
      await expect(admin.locator('.workflow-page')).toBeVisible()
      await expect(admin.locator('.workflow-sync-state')).toHaveCount(0)
      await admin.waitForLoadState('networkidle')
      await expect(admin.locator('body')).toContainText(/生成归档|暂无提交或归档记录/)
      await new PaperGuardPage(admin).waitForMutation('/api/archives', async () => {
        await admin.locator('.archive-row').filter({ hasText: thesisTitle }).getByRole('button', { name: '生成归档', exact: true }).click()
      })
      await checkpoint('administrator-archive-queued')
      await expect.poll(async () => {
        await admin.reload()
        await expect(admin.locator('.workflow-page')).toBeVisible()
        await expect(admin.locator('.workflow-sync-state')).toHaveCount(0)
        return admin.locator('.archive-row').getByRole('button', { name: '下载', exact: true }).count()
      }, { timeout: 60_000, intervals: [1000, 2000, 5000] }).toBe(1)
      const archiveDownload = admin.waitForEvent('download')
      await admin.locator('.archive-row').getByRole('button', { name: '下载', exact: true }).click()
      const archivePath = await (await archiveDownload).path()
      expect(archivePath).toBeTruthy()
      const python = process.env.E2E_PYTHON_BIN || 'python'
      const archiveCheck = JSON.parse(execFileSync(python, ['-c',
        'import zipfile,json,sys,hashlib; z=zipfile.ZipFile(sys.argv[1]); m=json.loads(z.read("manifest.json")); print(json.dumps({"bad_file":z.testzip(),"names":z.namelist(),"hashes_match":all(hashlib.sha256(z.read(f["name"])).hexdigest()==f["sha256"] for f in m["files"])}))',
        archivePath!,
      ], { encoding: 'utf8' }))
      expect(archiveCheck.bad_file).toBeNull()
      expect(archiveCheck.names).toEqual(expect.arrayContaining(['final.docx', 'content.json', 'submission.json', 'reviews.json', 'materials.json', 'manifest.json']))
      expect(archiveCheck.hashes_match).toBe(true)
      await checkpoint('administrator-archive-downloaded-and-verified', admin)
      await admin.goto('/workspace')
      await expect(admin.locator('.workspace-stats')).toBeVisible()
      await admin.locator('.workspace-main').getByRole('tab', { name: '归档任务状态', exact: true }).click()
      const archivedItem = admin.locator('.workspace-archive-row').filter({ hasText: thesisTitle })
      await expect(archivedItem).toContainText(studentDisplay)
      await expect(archivedItem).toContainText('终稿阶段')
      await checkpoint('administrator-named-archive', admin)
      await admin.goto('/workflow?panel=archives')
      await expect(admin.locator('.workflow-sync-state')).toHaveCount(0)
      await expect(admin.getByRole('button', { name: '下载', exact: true })).toBeVisible()

      await expect.soft(admin).toHaveScreenshot('full-lifecycle-admin-shell.png', {
        animations: 'disabled',
        maxDiffPixels: 1000,
        mask: [
          admin.locator('.user-tray'),
          admin.locator('.sidebar-footer'),
          admin.locator('.workflow-summary'),
          admin.locator('.workflow-identity'),
          admin.locator('.cohort-panel h3'),
          admin.locator('.cohort-panel p'),
          admin.locator('.cohort-panel .stage-row'),
          admin.locator('.task-row .task-copy h3'),
          admin.locator('.task-row .task-copy p'),
          admin.locator('.task-row .task-side'),
          admin.locator('.archive-row strong'),
          admin.locator('.archive-row > div > span'),
        ],
      })
      // 软断言保留视觉门禁，同时让后续功能和 console/HTTP 检查继续执行。
      // 2026-10-09 的 UI 改造按用户提供的 Muse 原型更新基线；后续更新仍须视觉复核。
      await student.goto('/workflow')
      await expect(student.locator('.workflow-sync-state')).toHaveCount(0)
      await expect(student.locator('.student-task-summary')).toBeVisible()
      await expect.soft(student).toHaveScreenshot('full-lifecycle-student-shell.png', {
        animations: 'disabled',
        maxDiffPixels: 1000,
        mask: [
          student.locator('.user-tray'),
          student.locator('.sidebar-footer'),
          student.locator('.workflow-summary'),
          student.locator('.workflow-identity'),
          student.locator('.student-task h3'),
          student.locator('.student-task-summary .task-copy'),
          student.locator('.task-summary-state'),
          student.locator('.student-task .task-meta'),
          student.locator('.student-task .task-actions'),
          student.locator('.reference-panel .reference-row small'),
        ],
      })

      await student.goto('/templates')
      await expect(student.locator('.workspace-page')).toBeVisible()
      await expect(student.locator('body')).not.toContainText('undefined')
      await student.waitForLoadState('networkidle')
      await teacher.waitForLoadState('networkidle')
      await admin.waitForLoadState('networkidle')
      await new PaperGuardPage(student).logout()
      await new PaperGuardPage(teacher).logout()
      await new PaperGuardPage(admin).logout()
      expect([...consoleErrors, ...httpErrors], [...consoleErrors, ...httpErrors].join('\n')).toEqual([])
    } finally {
      try {
        if (adminPasswordMayHaveChanged) await restoreSeededAdminPassword(adminReplacementPassword)
      } finally {
        await adminContext.close()
        await teacherContext.close()
        await studentContext.close()
      }
    }
  })

  test('responsive shell keeps login and deep routes visible', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile-chrome', 'This focused viewport check runs on mobile only.')
    await page.goto('/login')
    await expect(page.locator('.stitch-auth-page')).toBeVisible()
    await page.goto('/templates')
    await expect(page.locator('body')).toContainText(/登录|欢迎登录|Available Templates|论文模板/)
    const viewportWidth = await page.evaluate(() => document.documentElement.clientWidth)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewportWidth)
    await expect.soft(page).toHaveScreenshot('responsive-login-mobile.png', {
      animations: 'disabled',
      maxDiffPixels: 800,
      timeout: 15_000,
    })
  })
})
