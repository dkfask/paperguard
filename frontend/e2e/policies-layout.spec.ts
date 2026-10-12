import { randomBytes } from 'node:crypto'
import { expect, request, test, type APIRequestContext, type Page } from '@playwright/test'
import { API_BASE_URL, createStudent } from './document-platform.api'
import { UsageAuditPage } from './pages/usage-audit.page'

type Session = { token: string; user: Record<string, unknown> }

test.describe('writing standards layout and states', () => {
  let api: APIRequestContext
  let admin: Session
  let teacher: Session
  let student: Session

  test.beforeAll(async () => {
    api = await request.newContext({ baseURL: API_BASE_URL })
    const response = await api.post('/api/auth/login', {
      data: { username: 'admin', password: process.env.E2E_ADMIN_PASSWORD },
    })
    expect(response.ok()).toBeTruthy()
    const seed = await response.json()
    // The bootstrap account intentionally requires a first-login password
    // change. Use a separately registered, active administrator for UI tests.
    const adminAccount = await createStudent(api, 'policies-admin')
    const firstLogin = await api.post('/api/auth/login', { data: { username: adminAccount.username, password: adminAccount.password } })
    expect(firstLogin.ok()).toBeTruthy()
    const initialAdmin = await firstLogin.json()
    const promotion = await api.put(`/api/auth/admin/users/${initialAdmin.user.id}/role-status`, {
      headers: { Authorization: `Bearer ${seed.token}` }, data: { role: 'admin', status: 'active' },
    })
    expect(promotion.ok()).toBeTruthy()
    const adminLogin = await api.post('/api/auth/login', { data: { username: adminAccount.username, password: adminAccount.password } })
    expect(adminLogin.ok()).toBeTruthy()
    admin = await adminLogin.json()
    const username = `policy-layout-${randomBytes(8).toString('hex')}`
    const password = `Local7${randomBytes(18).toString('hex')}`
    const registration = await api.post('/api/auth/register', {
      data: { username, password, display_name: '规范布局测试教师', role: 'teacher' },
    })
    expect(registration.status()).toBe(201)
    const user = await registration.json()
    const activation = await api.put(`/api/auth/admin/users/${user.id}/role-status`, {
      headers: { Authorization: `Bearer ${admin.token}` }, data: { role: 'teacher', status: 'active' },
    })
    expect(activation.ok()).toBeTruthy()
    const login = await api.post('/api/auth/login', { data: { username, password } })
    expect(login.ok()).toBeTruthy()
    teacher = await login.json()
    const account = await createStudent(api, 'policy-layout')
    const studentLogin = await api.post('/api/auth/login', { data: { username: account.username, password: account.password } })
    expect(studentLogin.ok()).toBeTruthy()
    student = await studentLogin.json()
  })

  test.afterAll(async () => { await api?.dispose() })

  async function open(page: Page, session: Session) {
    await page.goto('/login')
    await page.evaluate(({ token, user }) => {
      sessionStorage.setItem('pg_token', token)
      sessionStorage.setItem('pg_user', JSON.stringify(user))
    }, session)
    await page.goto('/policies')
    await expect(page.locator('h1')).toHaveText('写作规范')
  }

  async function expectFits(page: Page) {
    const bounds = await page.evaluate(() => ({
      width: innerWidth, height: innerHeight,
      documentWidth: document.documentElement.scrollWidth,
      documentHeight: document.documentElement.scrollHeight,
    }))
    expect(bounds.documentWidth).toBeLessThanOrEqual(bounds.width + 1)
    if (bounds.width > 768) expect(bounds.documentHeight).toBeLessThanOrEqual(bounds.height + 1)
    for (const control of await page.locator('main button, main a').all()) {
      if (await control.isVisible() && bounds.width > 768) await expect(control).toBeInViewport({ ratio: 1 })
    }
  }

  async function expectFullWidth(page: Page) {
    const panel = await page.locator('.policy-list-panel').boundingBox()
    const layout = await page.locator('.policy-layout').boundingBox()
    expect(panel).not.toBeNull()
    expect(layout).not.toBeNull()
    expect(panel!.width / layout!.width).toBeGreaterThanOrEqual(0.95)
  }

  test('teacher empty state uses the available width and offers one set of actions', async ({ page }, info) => {
    // Deliberately empty only the list response, keeping authentication and the
    // page shell real. Save/publish are exercised separately against the API.
    await page.route('**/api/policies', route => route.fulfill({ json: [] }))
    await open(page, teacher)
    await expect(page.getByRole('heading', { name: '暂无可用规范' })).toBeVisible()
    const sizes = info.project.name === 'chromium'
      ? [{ width: 1100, height: 640 }, { width: 1600, height: 900 }, { width: 916, height: 804 }, { width: 775, height: 804 }]
      : [{ width: 390, height: 844 }]
    for (const size of sizes) {
      await page.setViewportSize(size)
      await page.screenshot({ path: info.outputPath(`empty-${size.width}.png`), animations: 'disabled', fullPage: true })
      await expectFullWidth(page)
      await expectFits(page)
    }
    await expect(page.getByRole('heading', { name: '可用规范', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '新建规范', exact: true })).toHaveCount(1)
    await expect(page.getByRole('button', { name: 'AI 导入规范', exact: true })).toHaveCount(1)
    await expect(page.getByRole('button', { name: '刷新', exact: true })).toHaveCount(1)
    await expect(page.locator('.policy-empty-state')).toContainText('创建规范或导入已有要求')
    if (process.env.E2E_AXE_PATH) await new UsageAuditPage(page, info).scan('teacher-empty-accessibility', process.env.E2E_AXE_PATH)
    await page.getByRole('button', { name: '新建规范', exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true })).toBeInViewport({ ratio: 1 })
    for (let index = 0; index < 8; index++) {
      await page.keyboard.press('Tab')
      expect(await page.getByRole('dialog').evaluate(el => el.contains(document.activeElement))).toBeTruthy()
    }
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.getByRole('button', { name: '新建规范', exact: true })).toBeFocused()
  })

  test('administrator and student see appropriate empty guidance, including English', async ({ page }, info) => {
    await page.route('**/api/policies', route => route.fulfill({ json: [] }))
    await open(page, admin)
    await expect(page.getByRole('button', { name: '新建规范', exact: true })).toHaveCount(1)
    await expect(page.locator('.policy-empty-state')).toContainText('创建规范或导入已有要求')
    if (process.env.E2E_AXE_PATH) await new UsageAuditPage(page, info).scan('admin-empty-accessibility', process.env.E2E_AXE_PATH)
    await open(page, student)
    await expect(page.getByRole('heading', { name: '暂无可用规范' })).toBeVisible()
    await expect(page.getByRole('button', { name: '新建规范', exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'AI 导入规范', exact: true })).toHaveCount(0)
    await expect(page.locator('.policy-empty-state')).toContainText('联系指导教师或管理员')
    await expectFullWidth(page)
    await expectFits(page)
    if (process.env.E2E_AXE_PATH) await new UsageAuditPage(page, info).scan('student-empty-accessibility', process.env.E2E_AXE_PATH)
    await page.screenshot({ path: info.outputPath('student-empty.png'), animations: 'disabled', fullPage: true })
    await page.getByRole('button', { name: 'English', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'No standards available' })).toBeVisible()
    await expect(page.locator('.policy-empty-state')).toContainText('Contact your supervisor or administrator')
    await expectFits(page)
  })

  test('failed loading shows a recoverable error instead of an empty list', async ({ page }) => {
    let fail = true
    await page.route('**/api/policies', route => route.fulfill(fail
      ? { status: 503, json: { detail: '规范服务暂不可用' } }
      : { json: [] }))
    await open(page, teacher)
    await expect(page.getByRole('heading', { name: '规范加载失败' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '暂无可用规范' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '重新加载', exact: true })).toHaveCount(1)
    await expectFits(page)
    fail = false
    await page.getByRole('button', { name: '重新加载', exact: true }).click()
    await expect(page.getByRole('heading', { name: '暂无可用规范' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '规范加载失败' })).toHaveCount(0)
  })

  test('teacher creates and publishes a standard; administrators page the list; students read published details', async ({ page }, info) => {
    if (info.project.name === 'chromium') await page.setViewportSize({ width: 1100, height: 640 })
    await open(page, teacher)
    await page.getByRole('button', { name: '新建规范', exact: true }).click()
    const editor = page.locator('.policy-editor-panel')
    const name = '本科毕业论文写作要求与研究方法说明（指导学生使用，2026 修订版）'
    await editor.getByLabel('规范名称', { exact: true }).fill(name)
    await editor.getByRole('tab', { name: '规范正文', exact: true }).click()
    await editor.locator('textarea.markdown-input').fill('# 写作要求\n\n请说明研究方法与结果。')
    await editor.getByRole('button', { name: '新建规范', exact: true }).click()
    await expect(editor.getByRole('button', { name: '保存草稿', exact: true })).toBeEnabled()
    const id = await page.evaluate(async () => {
      const r = await fetch('/api/policies', { headers: { Authorization: `Bearer ${sessionStorage.getItem('pg_token')}` } })
      return (await r.json()).find((p: { name: string }) => p.name.startsWith('本科毕业论文写作要求')).id
    })
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()
    const row = page.locator('.policy-list-item').filter({ hasText: name })
    await expect(row).toBeVisible()
    await expectFullWidth(page)
    await expectFits(page)
    await row.click()
    await editor.getByRole('button', { name: '发布版本', exact: true }).click()
    await expect(editor.getByRole('button', { name: '创建新版本', exact: true })).toBeEnabled()
    const published = await api.get(`/api/policies/${id}`, { headers: { Authorization: `Bearer ${teacher.token}` } })
    expect(published.ok()).toBeTruthy()
    expect((await published.json()).status).toBe('published')
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()
    await expect(page.locator('.async-state.loading')).toHaveCount(0)
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await page.getByRole('button', { name: '刷新', exact: true }).click()
    await expect(row).toBeVisible()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await page.screenshot({ path: info.outputPath('teacher-list.png'), animations: 'disabled', fullPage: true })

    const headers = { Authorization: `Bearer ${admin.token}` }
    const publishedName = '学校论文写作规范（学生可查看）'
    for (let index = 0; index < 15; index++) {
      const creation = await api.post('/api/policies', { headers, data: {
        name: index ? `规范布局分页测试 ${index}` : publishedName,
        level: 'school', version: '2026.1', markdown_content: '学校论文写作要求',
      } })
      expect(creation.status()).toBe(201)
      if (!index) {
        const created = await creation.json()
        expect((await api.post(`/api/policies/${created.id}/publish`, { headers })).ok()).toBeTruthy()
      }
    }
    await open(page, admin)
    await expect(page.locator('.policy-list-item').first()).toBeVisible()
    await expectFullWidth(page)
    await expectFits(page)
    await expect(page.getByRole('heading', { name: '可用规范', exact: true })).toHaveCount(1)
    if (info.project.name === 'chromium') {
      const first = await page.locator('.policy-list-item').first().innerText()
      await expect(page.getByRole('button', { name: '下一页', exact: true })).toBeEnabled()
      await page.getByRole('button', { name: '下一页', exact: true }).click()
      await expect(page.locator('.policy-list-item').first()).not.toHaveText(first)
      await expectFits(page)
    }
    await page.screenshot({ path: info.outputPath('admin-list.png'), animations: 'disabled', fullPage: true })
    if (process.env.E2E_AXE_PATH) await new UsageAuditPage(page, info).scan('admin-list-accessibility', process.env.E2E_AXE_PATH)
    await open(page, student)
    await page.locator('.policy-list-item').filter({ hasText: publishedName }).first().click()
    await expect(editor.getByLabel('规范名称', { exact: true })).toBeDisabled()
    await expect(editor.getByRole('button', { name: '保存草稿', exact: true })).toHaveCount(0)
    await expect(editor.getByRole('button', { name: '发布版本', exact: true })).toHaveCount(0)
    await expect(page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true })).toBeInViewport({ ratio: 1 })
  })
})
