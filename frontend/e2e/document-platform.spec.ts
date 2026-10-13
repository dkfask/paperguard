import { execFileSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import { expect, request, test, type APIRequestContext, type Page } from '@playwright/test'
import { DocumentProjectEditorPage } from './pages/document-project-editor.page'
import { DocumentProjectsPage } from './pages/document-projects.page'
import { LoginPage } from './pages/paperguard.page'
import {
  createStudent,
  API_BASE_URL,
  TEMPLATE_PATH,
  publishFixturePackage,
  type PublishedPackage,
  type StudentAccount,
} from './document-platform.api'

test.describe('generic document platform', () => {
  let api: APIRequestContext
  let publishedPackage: PublishedPackage

  test.beforeAll(async () => {
    api = await request.newContext({ baseURL: API_BASE_URL })
    publishedPackage = await publishFixturePackage(api)
  })

  test.afterAll(async () => {
    await api?.dispose()
  })

  async function openEditor(page: Page, testName: string) {
    const account: StudentAccount = await createStudent(api, testName)
    const loginPage = new LoginPage(page)
    await loginPage.login(account.username, account.password)
    const projectsPage = new DocumentProjectsPage(page)
    await projectsPage.goto()
    await projectsPage.expectPublishedPackage(publishedPackage.templateName, publishedPackage.specName)
    await projectsPage.startProject(publishedPackage.templateName, publishedPackage.specName, account.projectTitle)
    const editorPage = new DocumentProjectEditorPage(page)
    await editorPage.expectLoaded(publishedPackage.templateName)
    return { account, editorPage }
  }

  function assertAnchoredDocx(filePath: string, expectedSummary: string, expectedBody: string) {
    const documentXml = execFileSync('unzip', ['-p', filePath, 'word/document.xml'], { encoding: 'utf8' })
    const headerXml = execFileSync('unzip', ['-p', filePath, 'word/header1.xml'], { encoding: 'utf8' })
    const footerXml = execFileSync('unzip', ['-p', filePath, 'word/footer1.xml'], { encoding: 'utf8' })
    expect(documentXml).toContain(expectedSummary)
    expect(documentXml).toContain(expectedBody)
    expect(documentXml).toContain('静态模板内容：此段必须在导出文件中保留。')
    expect(documentXml).toContain('PaperGuard fixture')
    expect(documentXml).not.toContain('{{summary}}')
    expect(documentXml).not.toContain('{{section:body}}')
    expect(headerXml).toContain('固定页眉：PaperGuard 通用模板')
    expect(footerXml).toContain('固定页脚：模板版本 1')
  }

  async function expectScreenFits(page: Page) {
    if ((page.viewportSize()?.width || 0) < 769) return
    const bounds = await page.evaluate(() => ({
      width: innerWidth, height: innerHeight,
      documentWidth: document.documentElement.scrollWidth,
      documentHeight: document.documentElement.scrollHeight,
      scroll: scrollY,
    }))
    expect(bounds.documentWidth).toBeLessThanOrEqual(bounds.width + 1)
    expect(bounds.documentHeight).toBeLessThanOrEqual(bounds.height + 1)
    expect(bounds.scroll).toBe(0)
    const drawer = page.getByRole('dialog')
    if (await drawer.isVisible()) {
      await expect(drawer).toBeInViewport({ ratio: 1 })
      await expect(drawer.getByRole('button', { name: /^(关闭|Close)$/ })).toBeInViewport({ ratio: 1 })
    } else {
      for (const control of await page.locator('main button, main input, main select, main a').all()) {
        if (await control.isVisible()) await expect(control).toBeInViewport({ ratio: 1 })
      }
    }
  }

  test('teacher saves a personal template and writes without publishing it to students', async ({ page, browser }, info) => {
    if ((page.viewportSize()?.width || 0) >= 769) await page.setViewportSize({ width: 1100, height: 640 })
    await page.addInitScript(({ token, user }) => {
      sessionStorage.setItem('pg_token', token)
      sessionStorage.setItem('pg_user', JSON.stringify(user))
    }, { token: publishedPackage.managerToken, user: publishedPackage.managerUser })
    await page.goto('/document-packages/publish')
    await expect(page.locator('h1')).toContainText('设置自用文档模板')
    await expectScreenFits(page)

    await page.locator('[data-test="template-file"]').setInputFiles(TEMPLATE_PATH)
    await page.locator('[data-test="template-parse"]').click()
    await expect(page.locator('[data-test="template-confirm"]')).toBeEnabled()
    await expectScreenFits(page)
    await page.locator('[data-test="template-candidate-name"]').fill('通用项目申请书')
    const bodySection = page.locator('[data-test="template-section-body"]')
    await bodySection.locator('[data-test="template-section-title"]').fill('项目正文')
    await expect(bodySection.locator('select, input')).toHaveCount(0)
    await page.locator('.template-fields summary').click()
    const categoryReview = page.locator('[data-test="template-field-category"]')
    await categoryReview.locator('select').selectOption('select')
    await categoryReview.locator('[data-test="template-field-options"]').fill('A, B')
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expectScreenFits(page)
    await page.getByRole('tab', { name: /^上传文档规范/ }).click()
    await page.getByRole('tab', { name: /^上传 (Word|DOCX) 模板$/ }).click()
    await page.locator('[data-test="template-review"]').click()
    await expect(page.locator('[data-test="template-candidate-name"]')).toHaveValue('通用项目申请书')
    await expect(bodySection.locator('[data-test="template-section-title"]')).toHaveValue('项目正文')
    await page.locator('[data-test="template-confirm"]').click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.locator('.candidate-summary')).toContainText('已确认')
    await expectScreenFits(page)
    await page.getByRole('tab', { name: /^上传文档规范/ }).click()

    const specName = `E2E UI 规范 ${Date.now()}`
    const spec = `---
name: ${specName}
version: '1'
rules:
  - id: body-minimum-ui
    type: auto
    target: body
    checker: word_count
    params:
      min: 1
    severity: warning
    enforce: soft
    description: 正文至少包含 1 个字符
    source: UI E2E fixture
---

# UI E2E 规范
`
    await page.locator('[data-test="spec-file"]').setInputFiles({
      name: 'ui-e2e-spec.md',
      mimeType: 'text/markdown',
      buffer: Buffer.from(spec, 'utf8'),
    })
    await page.locator('[data-test="spec-parse"]').click()
    await expect(page.locator('[data-test="spec-confirm"]')).toBeEnabled()
    await expectScreenFits(page)
    await page.getByLabel('规则参数 JSON', { exact: true }).fill('{broken')
    await page.locator('[data-test="spec-confirm"]').click()
    await expect(page.getByRole('dialog').getByRole('alert')).toBeVisible()
    await page.getByLabel('规则参数 JSON', { exact: true }).fill('{"min":1}')
    await page.locator('[data-test="spec-confirm"]').click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.locator('.candidate-summary').last()).toContainText('已确认')
    await expectScreenFits(page)
    await page.getByRole('tab', { name: '保存自用模板', exact: true }).click()

    await page.getByLabel('文档类型', { exact: true }).selectOption('grant-application')
    await expect(page.locator('[data-test="publish"]')).toBeEnabled()
    const savedResponse = page.waitForResponse(response => response.url().endsWith('/api/document-packages') && response.request().method() === 'POST')
    await page.locator('[data-test="publish"]').click()
    const savedBundle = await (await savedResponse).json()
    expect(savedBundle.package.metadata.usage).toBe('personal')
    expect(savedBundle.package.published_by).toBe(publishedPackage.managerUser.id)
    await expect(page.locator('.success-panel')).toContainText('自用模板已保存')
    await expect(page.locator('.success-panel')).toContainText(specName)
    await expectScreenFits(page)
    await page.screenshot({ path: info.outputPath('personal-template-saved.png') })

    const account = await createStudent(api, 'browser-full-chain')
    const writingPage = page
    const projectsPage = new DocumentProjectsPage(writingPage)
    await projectsPage.goto()
    await projectsPage.expectPublishedPackage('通用项目申请书', specName)
    const personalSetup = page.locator('.personal-template-action')
    await expect(personalSetup).toBeInViewport({ ratio: 1 })
    expect(await personalSetup.evaluate(el => getComputedStyle(el).whiteSpace)).toBe('nowrap')
    await expectScreenFits(page)
    await page.screenshot({ path: info.outputPath('personal-writing-templates.png') })
    await projectsPage.startProject('通用项目申请书', specName, account.projectTitle)
    const editorPage = new DocumentProjectEditorPage(writingPage)
    await editorPage.expectLoaded('通用项目申请书')
    const summary = `字段摘要 ${account.projectTitle}`
    const body = `可导出的通用文档内容 ${account.projectTitle}`
    await editorPage.fillFieldAndWaitForSave('summary', summary)
    await writingPage.locator('[data-test="document-field-category"]').selectOption('A')
    await editorPage.fillFirstContentAndWaitForSave(body)
    await editorPage.reload('通用项目申请书')
    expect(await editorPage.firstContentValue()).toBe(body)
    await editorPage.showFields()
    await expect(writingPage.locator('[data-test="document-field-summary"]')).toHaveValue(summary)
    await editorPage.validate()
    await editorPage.expectNoFindings()
    const download = await editorPage.exportDocx()
    const filePath = await download.path()
    expect(filePath).toBeTruthy()
    assertAnchoredDocx(filePath as string, summary, body)

    const studentLogin = await api.post('/api/auth/login', {
      data: { username: account.username, password: account.password },
    })
    const studentToken = (await studentLogin.json()).token
    const projectId = writingPage.url().split('/').pop() as string
    const reopened = await api.get(`/api/document-projects/${projectId}`, {
      headers: { Authorization: `Bearer ${publishedPackage.managerToken}` },
    })
    expect(reopened.ok()).toBeTruthy()
    const reopenedProject = await reopened.json()
    expect(reopenedProject.field_values.summary).toBe(summary)
    expect(reopenedProject.owner_id).toBe(publishedPackage.managerUser.id)
    const studentHeaders = { Authorization: `Bearer ${studentToken}` }
    const studentPackages = await api.get('/api/document-packages', { headers: studentHeaders })
    expect((await studentPackages.json()).some((item: any) => item.package.id === savedBundle.package.id)).toBe(false)
    expect((await api.get(`/api/document-packages/${savedBundle.package.id}`, { headers: studentHeaders })).status()).toBe(404)
    expect((await api.post('/api/document-projects', { headers: studentHeaders, data: { package_id: savedBundle.package.id } })).status()).toBe(404)
    const studentContext = await browser.newContext()
    try {
      const studentView = await studentContext.newPage()
      await new LoginPage(studentView).login(account.username, account.password)
      await studentView.goto('/document-projects')
      await expect(studentView.locator('h1')).toBeVisible()
      await expect(studentView.locator('.package-card').filter({ hasText: specName })).toHaveCount(0)
    } finally { await studentContext.close() }

    const other = await createStudent(api, 'browser-isolation')
    const otherLogin = await api.post('/api/auth/login', {
      data: { username: other.username, password: other.password },
    })
    const forbidden = await api.get(`/api/document-projects/${projectId}`, {
      headers: { Authorization: `Bearer ${(await otherLogin.json()).token}` },
    })
    expect(forbidden.status()).toBe(404)
    const audit = await api.get('/api/audit', {
      headers: { Authorization: `Bearer ${publishedPackage.adminToken}` },
      params: { resource_id: projectId },
    })
    expect(audit.ok()).toBeTruthy()
    const actions = new Set((await audit.json()).map((event: { action: string }) => event.action))
    expect(actions.has('document.project.created')).toBeTruthy()
    expect(actions.has('document.project.updated')).toBeTruthy()
    expect(actions.has('document.project.validated')).toBeTruthy()
    expect(actions.has('document.project.rendered')).toBeTruthy()
    await writingPage.goto('/templates')
    await expect(writingPage.locator('h1')).toHaveText('发布论文模板')
    await page.screenshot({ path: info.outputPath('student-guidance-templates.png') })
    const sidebar = writingPage.locator('#role-sidebar')
    if (!await sidebar.isVisible()) await writingPage.getByRole('button', { name: /打开导航/ }).click()
    await sidebar.getByRole('tab', { name: '学生指导', exact: true }).click()
    await expect(sidebar.getByRole('link', { name: '发布论文模板', exact: true })).toHaveAttribute('href', '/templates')
    await sidebar.getByRole('tab', { name: '个人写作', exact: true }).click()
    await expect(sidebar.getByRole('link', { name: '设置自用文档模板', exact: true })).toHaveAttribute('href', '/document-packages/publish')
    await page.screenshot({ path: info.outputPath('teacher-personal-navigation.png') })
  })

  test('published package opens an editor from its parsed section schema', async ({ page }) => {
    const { editorPage } = await openEditor(page, 'schema')
    await page.getByRole('button', { name: /^(版本快照|Version snapshot)$/ }).click()
    await expect(page.locator('.snapshot-list')).toContainText('generic-docx-v1')
    await page.getByRole('dialog').getByRole('button', { name: /^(关闭|Close)$/ }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.locator('.block-label')).toContainText('段落')
    expect(await editorPage.firstContentValue()).toBe('')
  })

  test('document editor uses the available width for fields and writing', async ({ page }, info) => {
    test.setTimeout(120_000)
    const { editorPage } = await openEditor(page, 'writing-layout')
    const viewports = info.project.name === 'chromium'
      ? [{ width: 1100, height: 640 }, { width: 1600, height: 900 }, { width: 916, height: 804 }, { width: 775, height: 804 }]
      : [{ width: 390, height: 844 }]
    for (const viewport of viewports) {
      await page.setViewportSize(viewport)
      await editorPage.showFields()
      await expect(page.locator('[data-test="document-field-summary"]').locator('..')).toContainText('摘要')
      await expect(page.locator('[data-test="document-field-category"]').locator('..')).toContainText('类别')
      await expect(page.locator('.editor-subtitle')).not.toContainText('e2e-report')
      await page.screenshot({ path: info.outputPath(`fields-${viewport.width}.png`), animations: 'disabled' })
      const main = await page.locator('.editor-main').boundingBox()
      const layout = await page.locator('.editor-layout').boundingBox()
      expect(main!.width / layout!.width).toBeGreaterThan(0.95)
      const summary = await page.locator('[data-test="document-field-summary"]').boundingBox()
      expect(summary!.width / main!.width).toBeGreaterThan(0.85)
      await editorPage.showContent()
      await expect(page.locator('.writing-section h2')).toHaveText('正文')
      await expect(page.locator('[data-test^="block-kind-"]').first().locator('option:checked')).toHaveText('段落')
      await page.screenshot({ path: info.outputPath(`writing-${viewport.width}.png`), animations: 'disabled' })
      const writing = await page.locator('.content-block').first().getByRole('textbox').boundingBox()
      expect(writing!.width / main!.width).toBeGreaterThan(0.85)
      expect(writing!.height).toBeGreaterThanOrEqual(180)
      await expectScreenFits(page)
    }
    await page.getByRole('button', { name: 'English', exact: true }).click()
    await editorPage.showFields()
    await expect(page.locator('[data-test="document-field-summary"]').locator('..')).toContainText('Summary')
    await page.screenshot({ path: info.outputPath('fields-en.png'), animations: 'disabled' })
    await editorPage.showContent()
    await expect(page.locator('.writing-section h2')).toHaveText('Body')
    await expect(page.locator('[data-test^="block-kind-"]').first().locator('option:checked')).toHaveText('Paragraph')
    await page.screenshot({ path: info.outputPath('writing-en.png'), animations: 'disabled' })
    await expectScreenFits(page)
  })

  test('dynamic editor renders field and content block schemas', async ({ page }) => {
    const { editorPage } = await openEditor(page, 'dynamic-schema')
    await editorPage.showFields()
    await expect(page.locator('[data-test="document-field-summary"]')).toHaveJSProperty('tagName', 'TEXTAREA')
    await expect(page.locator('[data-test="document-field-category"]')).toHaveJSProperty('tagName', 'SELECT')
    await page.locator('[data-test="document-field-summary"]').fill('结构化字段内容')
    await page.locator('[data-test="document-field-category"]').selectOption('A')

    await editorPage.showContent()
    const saved = page.waitForResponse((response) => response.url().includes('/api/document-projects/')
      && response.request().method() === 'PUT'
      && response.status() === 200)
    await page.locator('[data-test^="block-kind-"]').first().selectOption('table')
    await page.getByLabel(/表格单元格 1-1|Table cell 1-1/).fill('表格单元格')
    await saved
    await expect(page.locator('[data-test="document-table-editor"]')).toBeVisible()
    await expect(page.getByLabel(/表格单元格 1-1|Table cell 1-1/)).toHaveValue('表格单元格')
  })

  test('content is autosaved and survives an editor reload', async ({ page }) => {
    const { account, editorPage } = await openEditor(page, 'autosave')
    const content = `已保存的通用文档内容 ${account.projectTitle}`
    await editorPage.fillFirstContentAndWaitForSave(content)
    await editorPage.reload(publishedPackage.templateName)
    expect(await editorPage.firstContentValue()).toBe(content)
  })

  test('deterministic validation reports an empty required body', async ({ page }) => {
    const { editorPage } = await openEditor(page, 'validation')
    await editorPage.validate()
    await editorPage.expectFinding('正文至少包含 1 个字符')
  })

  test('valid content exports as a DOCX download', async ({ page }) => {
    const { account, editorPage } = await openEditor(page, 'export')
    await editorPage.fillFieldAndWaitForSave('summary', `字段摘要 ${account.projectTitle}`)
    await page.locator('[data-test="document-field-category"]').selectOption('A')
    await editorPage.fillFirstContentAndWaitForSave(`可导出的通用文档内容 ${account.projectTitle}`)
    await editorPage.validate()
    await editorPage.expectNoFindings()
    const download = await editorPage.exportDocx()
    expect(download.suggestedFilename()).toBe(`${account.projectTitle}.docx`)
    const filePath = await download.path()
    expect(filePath).toBeTruthy()
    const bytes = await readFile(filePath as string)
    expect(bytes.subarray(0, 2).toString('ascii')).toBe('PK')
    expect(bytes.length).toBeGreaterThan(0)
    assertAnchoredDocx(filePath as string, `字段摘要 ${account.projectTitle}`, `可导出的通用文档内容 ${account.projectTitle}`)
  })
})
