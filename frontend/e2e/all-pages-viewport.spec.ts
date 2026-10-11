import { randomBytes } from 'node:crypto'
import { writeFile } from 'node:fs/promises'
import { expect, request, test, type Page, type TestInfo } from '@playwright/test'
import { API_BASE_URL, createStudent, publishFixturePackage } from './document-platform.api'

// Fixture writes are confined to the disposable playwright database.
async function session(page: Page, token: string, user: Record<string, unknown>) {
  await page.addInitScript(({ token, user }) => {
    sessionStorage.setItem('pg_token', token)
    sessionStorage.setItem('pg_user', JSON.stringify(user))
    localStorage.setItem('paperguard_lang', 'zh')
  }, { token, user })
}
async function singleScreen(page: Page) {
  await page.evaluate(() => window.scrollTo(0, 1000))
  const geometry = await page.evaluate(() => ({ width: innerWidth, height: innerHeight,
    documentWidth: document.documentElement.scrollWidth, documentHeight: document.documentElement.scrollHeight,
    scrollY, workAreaScroll: document.querySelector('.app-shell-page')?.scrollTop || 0,
    shellScroll: [...document.querySelectorAll('.app-shell-host, .app-shell-root, .app-shell, .app-shell-main, .app-shell-page, .public-shell')].map(el => ({ top: el.scrollTop, left: el.scrollLeft })) }))
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.width + 1)
  expect(geometry.documentHeight).toBeLessThanOrEqual(geometry.height + 1)
  expect(geometry.scrollY).toBe(0)
  expect(geometry.workAreaScroll).toBe(0)
  for (const area of geometry.shellScroll) expect(area).toEqual({ top: 0, left: 0 })
  return geometry
}
async function controlsReachable(page: Page) {
  const controls = page.locator('.app-shell-page').locator('button:not([disabled]), a[href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary')
  for (const control of await controls.all()) {
    if (!await control.isVisible()) continue
    await control.focus()
    // Textarea focus reveals its caret; the complete field must also be
    // reachable by scrolling its panel without moving a hidden shell layer.
    await control.scrollIntoViewIfNeeded()
    await expect(control).toBeInViewport({ ratio: 1 })
    await singleScreen(page)
  }
}
async function capture(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(`${name}.png`)
  await page.screenshot({ path })
  await info.attach(name, { path, contentType: 'image/png' })
}

test.describe('all-page desktop viewport', () => {
  test.skip(({ isMobile }) => isMobile, 'Mobile is covered by ui-refactor and admin-workspace-viewport.')
  let administrator: { token: string; user: Record<string, unknown> }
  let teacher: { token: string; user: Record<string, unknown> }
  let student: { token: string; user: Record<string, unknown> }
  let thesisId: string
  let documentId: string
  test.beforeAll(async () => {
    const api = await request.newContext({ baseURL: API_BASE_URL })
    try {
      const bundle = await publishFixturePackage(api)
      teacher = { token: bundle.managerToken, user: bundle.managerUser }
      const account = await createStudent(api, 'desktop-layout-admin')
      const registered = await api.post('/api/auth/login', { data: { username: account.username, password: account.password } })
      expect(registered.ok()).toBe(true)
      const initial = await registered.json()
      const promotion = await api.put(`/api/auth/admin/users/${initial.user.id}/role-status`, { headers: { Authorization: `Bearer ${bundle.adminToken}` }, data: { role: 'admin', status: 'active' } })
      expect(promotion.ok()).toBe(true)
      const login = await api.post('/api/auth/login', { data: { username: account.username, password: account.password } })
      expect(login.ok()).toBe(true)
      administrator = await login.json()
      const headers = { Authorization: `Bearer ${administrator.token}` }
      const thesis = await api.post('/api/projects', { headers, data: { template_id: 'default-gb7713', title: '一屏布局测试论文' } })
      expect(thesis.ok()).toBe(true)
      thesisId = (await thesis.json()).id
      const document = await api.post('/api/document-projects', { headers, data: { package_id: bundle.packageId, title: '一屏布局测试文档' } })
      expect(document.ok()).toBe(true)
      documentId = (await document.json()).id
      const account2 = await createStudent(api, 'desktop-layout-student')
      const login2 = await api.post('/api/auth/login', { data: { username: account2.username, password: account2.password } })
      expect(login2.ok()).toBe(true)
      student = await login2.json()
    } finally { await api.dispose() }
  })
  test('every administrator route fits and its controls remain reachable', async ({ page }, info) => {
    test.setTimeout(180_000)
    await session(page, administrator.token, administrator.user)
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    const routes = ['/', '/workspace', '/templates', '/templates/default-gb7713/edit', '/policies', '/organizations', '/cohorts', '/workflow', '/workflow?panel=archives', '/workflow?panel=reports', '/workflow?panel=notifications', '/admin/users', '/admin/ai', '/audit', '/operations', '/document-projects', '/document-packages/publish', `/document-projects/${documentId}`, `/write/${thesisId}`, '/layout-missing-page']
    const measurements = []
    for (const viewport of [{ width: 1280, height: 720 }, { width: 1384, height: 817 }]) {
      await page.setViewportSize(viewport)
      for (const route of routes) {
        await page.goto(route)
        await page.waitForLoadState('networkidle')
        expect(new URL(page.url()).pathname).toBe(route.split('?')[0])
        await expect(page.locator('.app-shell-page > *').first()).toBeVisible()
        await expect(page.locator('.app-error-boundary')).toHaveCount(0)
        await expect(page.locator('.error-banner')).toHaveCount(0)
        if (viewport.width === 1280) await capture(page, info, route.replace(/[^a-z0-9]/gi, '-') || 'home')
        await test.step(`${route} at ${viewport.width}×${viewport.height}`, async () => {
          await controlsReachable(page)
          measurements.push({ route, ...await singleScreen(page) })
        })
        if (route === '/') {
          await page.locator('.workspace-tab').nth(1).click()
          await expect(page.locator('.inspection-section')).toBeVisible()
          await controlsReachable(page)
          measurements.push({ route, panel: 'inspection', ...await singleScreen(page) })
          if (viewport.width === 1280) await capture(page, info, 'home-inspection')
        }
      }
    }
    expect(errors).toEqual([])
    const geometryPath = info.outputPath('all-page-geometry.json')
    await writeFile(geometryPath, JSON.stringify(measurements, null, 2))
    await info.attach('all-page-geometry', { path: geometryPath, contentType: 'application/json' })
  })
  test('teacher and student lower panels remain reachable', async ({ page, browser }, info) => {
    await page.setViewportSize({ width: 1280, height: 720 })
    await session(page, teacher.token, teacher.user)
    for (const route of ['/workspace', '/teacher/students']) {
      await page.goto(route)
      await page.waitForLoadState('networkidle')
      await expect(page.locator('.error-banner')).toHaveCount(0)
      await controlsReachable(page)
      await singleScreen(page)
      await capture(page, info, `teacher-${route.replace(/\W/g, '-')}`)
    }
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 } })
    try {
      const p = await context.newPage()
      await session(p, student.token, student.user)
      await p.goto('/workspace')
      await expect(p.locator('details.workspace-drawer')).toHaveCount(3)
      for (const drawer of await p.locator('details.workspace-drawer').all()) await drawer.locator('summary').click()
      await controlsReachable(p)
      await singleScreen(p)
      await capture(p, info, 'student-expanded')
    } finally { await context.close() }
  })
  test('first-login errors remain reachable inside the screen', async ({ page }, info) => {
    await page.setViewportSize({ width: 1280, height: 720 })
    await session(page, 'local-first-login-fixture', { id: 'local-onboarding', username: 'local-onboarding', display_name: '本地首次登录测试', role: 'student', status: 'active', onboarding_required: true })
    const errors = ['本地夹具：验证码服务暂不可用。'.repeat(12), '本地夹具：首次登录提交失败。'.repeat(12)]
    await page.route('**/api/auth/email/code', route => route.fulfill({ status: 503, json: { detail: errors[0] } }))
    await page.route('**/api/auth/first-login/complete', route => route.fulfill({ status: 503, json: { detail: errors[1] } }))
    await page.goto('/first-login')
    const fields = page.locator('.first-login-card input')
    await fields.nth(0).fill('local-onboarding@example.test')
    await page.locator('.code-button').click()
    await expect(page.locator('.first-login-card .error-banner')).toHaveText(errors[0])
    await fields.nth(1).fill('123456')
    const password = `Local-${randomBytes(12).toString('hex')}`
    await fields.nth(2).fill(password)
    await fields.nth(3).fill(password)
    await page.locator('.submit-button').click()
    await expect(page.locator('.first-login-card .error-banner')).toHaveText(errors[1])
    await controlsReachable(page)
    expect(await page.locator('.stitch-auth-page').evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true)
    await capture(page, info, 'first-login-error')
  })
  test('navigation failure shares the viewport with the page', async ({ page }, info) => {
    await page.setViewportSize({ width: 1280, height: 720 })
    await session(page, administrator.token, administrator.user)
    await page.goto('/admin/ai')
    await page.route(/\/src\/views\/Audit\.vue(?:\?|$)/, route => route.abort())
    await page.locator('.role-sidebar a[href="/audit"]').click()
    await expect(page.locator('.nav-failure')).toBeVisible()
    await singleScreen(page)
    await controlsReachable(page)
    await capture(page, info, 'navigation-failure')
    await page.locator('.nav-failure-actions button').last().click()
    await expect(page.locator('.nav-failure')).toHaveCount(0)
    await singleScreen(page)
  })
  test('public pages and role certification keep controls accessible', async ({ page }, info) => {
    await page.setViewportSize({ width: 1280, height: 720 })
    for (const route of ['/login', '/register']) {
      await page.goto(route)
      await expect(page.locator('h1')).toBeVisible()
      for (const control of await page.locator('.public-shell input, .public-shell button, .public-shell a[href]').all()) {
        if (!await control.isVisible() || await control.isDisabled()) continue
        await control.focus()
        await expect(control).toBeInViewport({ ratio: 1 })
      }
      await singleScreen(page)
      await capture(page, info, route.slice(1))
    }
    await session(page, 'local-certification-fixture', { id: 'local-certification', username: 'local-certification', display_name: '本地身份认证测试', role: 'user', role_application_status: 'unselected' })
    await page.route('**/api/auth/role-certification', route => route.fulfill({ json: { role: 'user', requested_organization_context: {}, application_status: 'unselected', organizations: [{ id: 'local-school', name: '本地学校夹具', type: 'school' }] } }))
    await page.goto('/role-certification')
    await expect(page.locator('.role-certification-page')).toBeVisible()
    await controlsReachable(page)
    await singleScreen(page)
    await capture(page, info, 'role-certification')
  })
})
