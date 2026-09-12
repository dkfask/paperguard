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
  })

  test('published package opens an editor from its parsed section schema', async ({ page }) => {
    const { editorPage } = await openEditor(page, 'schema')
    await expect(page.locator('.snapshot-list')).toContainText('generic-docx-v1')
    await expect(page.locator('.block-label')).toContainText('paragraph')
    expect(await editorPage.firstContentValue()).toBe('')
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
  })
})
