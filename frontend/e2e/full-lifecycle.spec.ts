import { expect, request, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { LoginPage, PaperGuardPage } from './pages/paperguard.page'
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
    const row = page.locator('.nav-row').filter({ hasText: section.pattern }).first()
    await expect(row, `required writer section is visible: ${section.pattern}`).toHaveCount(1)
    await row.click()
    const heading = page.locator('input.heading-input').first()
    await expect(heading).toBeVisible()
    if (!(await heading.inputValue()).trim()) await heading.fill(section.pattern.source)

    let paragraph = page.locator('textarea.para-text').first()
    if (!await paragraph.count()) {
      await page.getByRole('button', { name: /新增段落/ }).click()
      paragraph = page.locator('textarea.para-text').first()
    }
    await expect(paragraph).toBeVisible()
    await paragraph.fill(section.text)
    await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)
  }

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
    test.setTimeout(180_000)
    test.skip(testInfo.project.name !== 'chromium', 'Full lifecycle is intentionally desktop-only; responsive shell is covered below.')
    if (!ADMIN_PASSWORD) throw new Error('E2E_ADMIN_PASSWORD is required')
    for (const file of Object.values(files)) expect(fs.existsSync(file), `missing test file: ${file}`).toBeTruthy()

    const run = suffix()
    const teacherUsername = `e2e_teacher_${run}`
    const studentUsername = `22016022205_${run}`
    const teacherPassword = `Teacher-${run}-2026a`
    const studentPassword = `Student-${run}-2026a`
    const adminReplacementPassword = `Admin-${run}-2026a`
    const teacherDisplay = `王俞尹回归教师-${run}`
    const studentDisplay = `王俞尹-${run}`
    const schoolName = `王俞尹回归学校-${run}`
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
      await new LoginPage(teacher).register(teacherUsername, teacherPassword, teacherDisplay, 'teacher')

      adminPasswordMayHaveChanged = true
      await new LoginPage(admin).login('admin', ADMIN_PASSWORD, adminReplacementPassword)
      await admin.goto('/admin/users')
      const teacherRow = admin.locator('tr').filter({ hasText: teacherUsername })
      await expect(teacherRow).toBeVisible()
      await new PaperGuardPage(admin).waitForMutation('/role-status', async () => {
        await teacherRow.getByRole('button', { name: '通过认证' }).click()
      }, 'PUT')
      await expect(teacherRow).toContainText('已认证')

      await admin.goto('/organizations')
      await admin.getByRole('button', { name: '新建组织' }).click()
      const orgEditor = admin.locator('section.editor.card')
      await new PaperGuardPage(admin).control('名称').fill(schoolName)
      await new PaperGuardPage(admin).waitForMutation('/api/organizations', async () => {
        await orgEditor.getByRole('button', { name: '保存' }).click()
      })
      await expect(admin.getByText(schoolName, { exact: true })).toBeVisible()
      const userSelect = admin.locator('.members select').nth(0)
      const scopeSelect = admin.locator('.members select').nth(1)
      await userSelect.selectOption({ label: `${teacherDisplay} · ${teacherUsername}` })
      const scopeOption = scopeSelect.locator('option').filter({ hasText: schoolName }).first()
      await scopeSelect.selectOption(await scopeOption.getAttribute('value') || '')
      await new PaperGuardPage(admin).waitForMutation('/context', async () => {
        await admin.locator('.members').getByRole('button', { name: '应用' }).click()
      }, 'PUT')

      await new LoginPage(teacher).login(teacherUsername, teacherPassword)
      await teacher.goto('/templates')
      await new PaperGuardPage(teacher).waitForMutation('/templates/import', async () => {
        await teacher.locator('input[type=file]').first().setInputFiles(files.template)
      })
      await expect(teacher.getByText(/识别结果/)).toBeVisible()
      await new PaperGuardPage(teacher).waitForMutation('/api/templates', async () => {
        await teacher.getByRole('button', { name: '确认并创建模板' }).click()
      })
      await expect(teacher).toHaveURL(/\/templates\/[^/]+\/edit/)

      await teacher.goto('/policies')
      await teacher.getByRole('button', { name: '新建规范' }).click()
      const policyEditor = teacher.locator('.policy-editor-panel')
      await new PaperGuardPage(teacher).control('规范名称').fill(`王俞尹写作规范-${run}`)
      await new PaperGuardPage(teacher).control('版本').fill('2026.1')
      await new PaperGuardPage(teacher).control('说明').fill('真实学校模板回归规范')
      await policyEditor.locator('textarea.markdown-input').fill('# 论文格式规范\n\n请按照学校模板完成论文。')
      await new PaperGuardPage(teacher).waitForMutation('/api/policies', async () => {
        await policyEditor.getByRole('button', { name: '新建规范' }).click()
      })
      await new PaperGuardPage(teacher).waitForMutation('/documents', async () => {
        await policyEditor.locator('.upload-row input[type=file]').setInputFiles(files.policy)
      })
      await expect(policyEditor.locator('[role=status]')).toContainText('参考文档已上传', { timeout: 30_000 })
      await expect(policyEditor.getByText(path.basename(files.policy))).toBeVisible({ timeout: 30_000 })
      await new PaperGuardPage(teacher).waitForMutation('/publish', async () => {
        await policyEditor.getByRole('button', { name: '发布版本' }).click()
      })
      await expect(policyEditor).toContainText('已发布')

      await new LoginPage(student).register(studentUsername, studentPassword, studentDisplay, 'student')
      await teacher.goto('/workflow')
      const workflow = new PaperGuardPage(teacher)
      await workflow.control('批次名称').fill(cohortName)
      const cohortScope = workflow.control('组织范围')
      const cohortScopeOption = cohortScope.locator('option').filter({ hasText: schoolName }).first()
      await cohortScope.selectOption(await cohortScopeOption.getAttribute('value') || '')
      await workflow.control('学年').fill('2026')
      await workflow.waitForMutation('/api/cohorts', async () => {
        await teacher.getByRole('button', { name: '创建批次' }).click()
      })
      const cohortPanel = teacher.locator('.cohort-panel').filter({ hasText: cohortName })
      await expect(cohortPanel).toBeVisible()
      await cohortPanel.getByRole('button', { name: '配置' }).click()

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
      await stageForm.locator('select').selectOption({ index: 0 })
      await stageInputs.nth(5).fill('')
      await stageInputs.nth(6).fill('')
      await stageForm.locator('input[type=checkbox]').first().check()
      await workflow.waitForMutation('/stages', async () => {
        await stageForm.getByRole('button', { name: '保存阶段草稿' }).click()
      })
      const stageRow = cohortPanel.locator('.stage-row').filter({ hasText: '终稿阶段' })
      await expect(stageRow).toBeVisible()
      await stageRow.getByRole('button', { name: '预览发布' }).click()
      await expect(teacher.locator('.modal-mask')).toBeVisible()
      await expect(teacher.locator('.preview-box')).toContainText('可发布')
      await new PaperGuardPage(teacher).waitForMutation('/publish', async () => {
        await teacher.locator('.modal-mask').getByRole('button', { name: '确认发布' }).click()
      })
      await expect(cohortPanel).toContainText('已发布')

      await new LoginPage(student).login(studentUsername, studentPassword)
      await student.goto('/workflow')
      const studentTask = student.locator('.student-task').filter({ hasText: cohortName })
      await expect(studentTask).toBeVisible()
      await studentTask.getByRole('button', { name: '开始写作' }).click()
      const bindModal = student.locator('.modal-mask').filter({ hasText: '绑定阶段主论文' })
      await bindModal.locator('input').fill(thesisTitle)
      await new PaperGuardPage(student).waitForMutation('/stage-participants/', async () => {
        await bindModal.getByRole('button', { name: '创建并绑定' }).click()
      })
      await expect(studentTask).toContainText(thesisTitle)
      await studentTask.getByRole('button', { name: '确认要求' }).click()
      await expect(student.getByRole('status')).toContainText('已确认')

      await studentTask.getByRole('button', { name: '过程材料' }).click()
      await submitMaterial(student, 'task_book', '任务书（真实材料）', files.taskBook)
      await submitMaterial(student, 'opening_report', '开题报告（真实材料）', files.openingReport)
      await submitMaterial(student, 'final_report', '终稿论文（真实材料）', files.paper)
      await submitMaterial(student, 'process_cover', '过程性文档封面（DOC 兼容性）', files.cover)

      await teacher.goto('/workflow')
      const inbox = teacher.locator('.process-inbox')
      await expect(inbox).toBeVisible()
      while (await inbox.locator('.process-inbox-row').count()) {
        await new PaperGuardPage(teacher).waitForMutation('/decision', async () => {
          await inbox.locator('.process-inbox-row').first().getByRole('button', { name: '通过' }).click()
        })
        await teacher.waitForTimeout(200)
      }

      await student.goto('/workflow')
      const taskAfterMaterials = student.locator('.student-task').filter({ hasText: cohortName })
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
      await expect(student.locator('.check-panel')).toBeVisible({ timeout: 60_000 })
      await expect(student.locator('.writer-save-status')).not.toContainText('未保存')

      const exportButton = student.locator('.writer-export-action')
      const firstExport = student.waitForEvent('download', { timeout: 45_000 }).catch(() => null)
      await exportButton.click()
      let download = await firstExport
      if (!download && await student.locator('.missing-panel').count()) {
        const forcedExport = student.waitForEvent('download', { timeout: 45_000 })
        await student.locator('.missing-panel').getByRole('button', { name: /仍要导出/ }).click()
        download = await forcedExport
      }
      expect(download).toBeTruthy()
      const downloadedPath = await download!.path()
      expect(downloadedPath).toBeTruthy()
      expect(fs.statSync(downloadedPath!).size).toBeGreaterThan(10_000)

      await completeRequiredWriterGates(student, thesisTitle)

      await student.goto('/workflow')
      const finalTask = student.locator('.student-task').filter({ hasText: cohortName })
      await finalTask.getByRole('button', { name: '提交预检' }).click()
      await expect(student.locator('.candidate-box')).toBeVisible()
      const warning = student.locator('.candidate-box input[type=checkbox]')
      if (await warning.count()) await warning.check()
      await new PaperGuardPage(student).waitForMutation('/api/submissions', async () => {
        await student.locator('.candidate-box').getByRole('button', { name: '确认正式提交' }).click()
      })
      await expect(student.locator('.ok-banner')).toContainText('提交成功')
      await student.waitForLoadState('networkidle')

      await teacher.goto('/workflow?panel=submissions')
      const reviewTask = teacher.locator('.task-row').filter({ hasText: thesisTitle })
      await expect(reviewTask).toBeVisible()
      await reviewTask.click()
      await expect(teacher.locator('.review-panel')).toBeVisible()
      await new PaperGuardPage(teacher).waitForMutation('/review', async () => {
        await teacher.locator('.review-panel').getByRole('button', { name: '通过提交' }).click()
      })
      await expect(teacher.locator('.ok-banner')).toContainText('提交已通过')
      await teacher.waitForLoadState('networkidle')

      await admin.goto('/workflow?panel=archives')
      await expect(admin.locator('.workflow-page')).toBeVisible()
      await expect(admin.locator('.workflow-sync-state')).toHaveCount(0)
      await admin.waitForLoadState('networkidle')
      await expect(admin.locator('body')).toContainText(/生成归档|暂无提交或归档记录/)
      await expect(admin).toHaveScreenshot('full-lifecycle-admin-shell.png', {
        animations: 'disabled',
        maxDiffPixels: 1000,
        mask: [
          admin.locator('.user-tray'),
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
      await expect(student).toHaveScreenshot('full-lifecycle-student-shell.png', {
        animations: 'disabled',
        maxDiffPixels: 1000,
        mask: [
          student.locator('.user-tray'),
          student.locator('.workflow-summary'),
          student.locator('.workflow-identity'),
          student.locator('.student-task h3'),
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
    await expect(page).toHaveScreenshot('responsive-login-mobile.png', {
      animations: 'disabled',
      maxDiffPixels: 800,
      timeout: 15_000,
    })
  })
})
