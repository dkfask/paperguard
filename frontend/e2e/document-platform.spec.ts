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

  test('manager publishes a package from the browser workflow', async ({ page }) => {
    await page.addInitScript(({ token, user }) => {
      sessionStorage.setItem('pg_token', token)
      sessionStorage.setItem('pg_user', JSON.stringify(user))
    }, { token: publishedPackage.managerToken, user: publishedPackage.managerUser })
    await page.goto('/document-packages/publish')
    await expect(page.locator('h1')).toContainText('发布通用文档包')

    await page.locator('[data-test="template-file"]').setInputFiles(TEMPLATE_PATH)
    await page.locator('[data-test="template-parse"]').click()
    await expect(page.locator('[data-test="template-confirm"]')).toBeEnabled()
    await page.locator('[data-test="template-candidate-name"]').fill('通用项目申请书')
    const bodySection = page.locator('[data-test="template-section-body"]')
    await bodySection.locator('[data-test="template-section-kind"]').selectOption('heading2')
    await bodySection.locator('[data-test="template-section-order"]').fill('1')
    await bodySection.locator('[data-test="template-section-style"]').selectOption('body')
    await bodySection.locator('[data-test="template-section-repeatable"]').check()
    await bodySection.locator('[data-test="template-section-allow-add"]').check()
    await bodySection.locator('[data-test="template-section-allow-delete"]').check()
    await page.locator('[data-test="template-document-mode"]').selectOption('hybrid')
    await page.locator('[data-test="template-export-strategy"]').selectOption('source_template')
    const categoryReview = page.locator('[data-test="template-field-category"]')
    await categoryReview.locator('select').selectOption('select')
    await categoryReview.locator('[data-test="template-field-options"]').fill('A, B')
    await page.locator('[data-test="template-confirm"]').click()
    await expect(page.locator('[data-test="template-confirm"]')).toBeDisabled()

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
    await page.locator('[data-test="spec-confirm"]').click()
    await expect(page.locator('[data-test="spec-confirm"]')).toBeDisabled()

    await page.locator('input[placeholder="例如 grant-application"]').fill('e2e-ui-report')
    await expect(page.locator('[data-test="publish"]')).toBeEnabled()
    await page.locator('[data-test="publish"]').click()
    await expect(page.locator('.success-panel')).toContainText('文档包发布成功')
    await expect(page.locator('.success-panel')).toContainText(specName)

    const account = await createStudent(api, 'browser-full-chain')
    const studentPage = await page.context().newPage()
    await new LoginPage(studentPage).login(account.username, account.password)
    const projectsPage = new DocumentProjectsPage(studentPage)
    await projectsPage.goto()
    await projectsPage.expectPublishedPackage('通用项目申请书', specName)
    await projectsPage.startProject('通用项目申请书', specName, account.projectTitle)
    const editorPage = new DocumentProjectEditorPage(studentPage)
    await editorPage.expectLoaded('通用项目申请书')
    await expect(studentPage.locator('[data-test="add-block-body"]')).toBeVisible()
    await expect(studentPage.locator('[data-test^="delete-block-"]')).toBeVisible()
    const summary = `字段摘要 ${account.projectTitle}`
    const body = `可导出的通用文档内容 ${account.projectTitle}`
    await editorPage.fillFieldAndWaitForSave('summary', summary)
    await studentPage.locator('[data-test="document-field-category"]').selectOption('A')
    await editorPage.fillFirstContentAndWaitForSave(body)
    await editorPage.reload('通用项目申请书')
    expect(await editorPage.firstContentValue()).toBe(body)
    await expect(studentPage.locator('[data-test="document-field-summary"]')).toHaveValue(summary)
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
    const projectId = studentPage.url().split('/').pop() as string
    const reopened = await api.get(`/api/document-projects/${projectId}`, {
      headers: { Authorization: `Bearer ${studentToken}` },
    })
    expect(reopened.ok()).toBeTruthy()
    expect((await reopened.json()).field_values.summary).toBe(summary)

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
    await studentPage.close()
  })

  test('published package opens an editor from its parsed section schema', async ({ page }) => {
    const { editorPage } = await openEditor(page, 'schema')
    await expect(page.locator('.snapshot-list')).toContainText('generic-docx-v1')
    await expect(page.locator('.block-label')).toContainText('paragraph')
    expect(await editorPage.firstContentValue()).toBe('')
  })

  test('dynamic editor renders field and content block schemas', async ({ page }) => {
    const { editorPage } = await openEditor(page, 'dynamic-schema')
    await expect(page.locator('[data-test="document-field-summary"]')).toHaveJSProperty('tagName', 'TEXTAREA')
    await expect(page.locator('[data-test="document-field-category"]')).toHaveJSProperty('tagName', 'SELECT')
    await page.locator('[data-test="document-field-summary"]').fill('结构化字段内容')
    await page.locator('[data-test="document-field-category"]').selectOption('A')

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
