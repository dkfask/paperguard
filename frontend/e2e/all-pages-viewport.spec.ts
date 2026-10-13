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
  const layoutDebug = async () => JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('.app-shell-host, .app-shell-root, .app-shell, .app-shell-main, .app-shell-page, .app-shell-page > *, .workspace-layout, .workflow-layout')].map(el => ({ name: el.className, top: el.getBoundingClientRect().top, bottom: el.getBoundingClientRect().bottom, height: el.clientHeight, scrollHeight: el.scrollHeight, scrollTop: el.scrollTop, display: getComputedStyle(el).display, flex: getComputedStyle(el).flex, rows: getComputedStyle(el).gridTemplateRows }))))
  if (geometry.shellScroll.some(area => area.top || area.left)) console.log('Unexpected shell scroll', await layoutDebug())
  expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.width + 1)
  expect(geometry.documentHeight).toBeLessThanOrEqual(geometry.height + 1)
  expect(geometry.scrollY).toBe(0)
  expect(geometry.workAreaScroll).toBe(0)
  for (const area of geometry.shellScroll) expect(area).toEqual({ top: 0, left: 0 })
  const workAreaSelector = '.workspace-layout, .workflow-layout, .workspace-main, .workspace-side, .workflow-main, .workflow-side, .writer-columns, .writer-columns > .sidebar, .writer-columns > .editor-pane, .writer-columns > .writer-context'
  // Measure the final layout after the short pane transition settles.
  await expect.poll(() => page.locator(workAreaSelector).evaluateAll(elements => elements.filter(el =>
    el.getClientRects().length && getComputedStyle(el).display !== 'none' && !el.classList.contains('editor-pane')
    && el.scrollHeight > el.clientHeight + 1
  ).map(el => ({ name: el.className, height: el.clientHeight, contentHeight: el.scrollHeight }))), { message: 'work area clips content' }).toEqual([])
  const workAreas = await page.locator(workAreaSelector).evaluateAll(elements => elements.filter(el => el.getClientRects().length && getComputedStyle(el).display !== 'none').map(el => ({ name: el.className, top: el.getBoundingClientRect().top, bottom: el.getBoundingClientRect().bottom, height: el.clientHeight, contentHeight: el.scrollHeight })))
  for (const area of workAreas) {
    expect(area.height, `${area.name} collapsed`).toBeGreaterThan(24)
    if (!area.name.split(' ').includes('editor-pane')) expect(area.contentHeight, `${area.name} clips content`).toBeLessThanOrEqual(area.height + 1)
    expect(area.top, `${area.name} above viewport`).toBeGreaterThanOrEqual(-1)
    if (area.bottom > geometry.height + 1) console.log('Work area overflow', page.url(), await layoutDebug())
    expect(area.bottom, `${area.name} below viewport`).toBeLessThanOrEqual(geometry.height + 1)
  }
  const fixedSurfaces = page.locator('.app-shell-page .screen-deck-pane.screen-pane-active > section:not(.editor-section), .app-shell-page .screen-deck-pane.screen-pane-active > article, .app-shell-page .inspection-section, .public-shell .screen-deck-pane.screen-pane-active > section, .public-shell .screen-deck-pane.screen-pane-active > aside, .stitch-auth-page, .stitch-auth-main, .first-login-main, .first-login-card, .stitch-auth-card')
  for (const surface of await fixedSurfaces.all()) {
    if (!await surface.isVisible()) continue
    const name = await surface.getAttribute('class')
    await expect.poll(() => surface.evaluate(el => el.scrollHeight - el.clientHeight), { message: `${name} requires vertical scrolling` }).toBeLessThanOrEqual(1)
    expect(await surface.evaluate(el => el.scrollWidth - el.clientWidth), `${name} requires horizontal scrolling`).toBeLessThanOrEqual(1)
    expect(await surface.evaluate(el => el.scrollTop), `${name} scrolled internally`).toBe(0)
  }
  await expect.poll(() => page.locator('.app-shell-page *, .public-shell *').evaluateAll(elements => elements.filter(el =>
    // The persistent chapter outline is an editing navigation surface: its
    // tree scrolls while the outline frame and page remain in the viewport.
    el.getClientRects().length && !el.closest('.editor-pane, .editor-section, textarea, [contenteditable], [data-test="writer-outline"] .tree-nav')
    && ['auto', 'scroll'].includes(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight + 1
  ).map(el => ({ name: el.className, height: el.clientHeight, contentHeight: el.scrollHeight }))), {
    message: 'a main work panel still requires vertical scrolling',
  }).toEqual([])
  return { ...geometry, workAreas }
}
async function controlsReachable(page: Page) {
  const controls = page.locator('.app-shell-page, .public-shell').locator('button:not([disabled]), a[href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary')
  for (const control of await controls.all()) {
    if (!await control.isVisible()) continue
    await control.focus()
    // Textarea focus reveals its caret; the complete field must also be
    // reachable inside the editor without moving a hidden shell layer.
    if (await control.evaluate(el => Boolean(el.closest('.editor-pane, .editor-section, [data-test="writer-outline"] .tree-nav')))) await control.scrollIntoViewIfNeeded()
    await expect(control).toBeInViewport({ ratio: 1 })
    await singleScreen(page)
  }
}
async function verifyCollections(page: Page) {
  for (const collection of await page.locator('.app-shell-page .screen-collection').all()) {
    if (!await collection.isVisible()) continue
    const next = collection.getByRole('button', { name: '下一页', exact: true })
    for (let count = 0; count < 100; count++) {
      const body = collection.locator('.screen-collection-body')
      await expect.poll(() => body.evaluate(el => el.scrollHeight - el.clientHeight), { message: 'page remains clipped after layout settles' }).toBeLessThanOrEqual(1)
      const bounds = await body.evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight }))
      expect(bounds.height, 'paginated list collapsed').toBeGreaterThan(24)
      expect(bounds.scroll, 'current page contains clipped rows').toBeLessThanOrEqual(bounds.height + 1)
      await controlsReachable(page)
      if (!await next.count() || !await next.isEnabled()) break
      await next.click()
      if (count === 99) throw new Error('Pagination did not terminate')
    }
    const previous = collection.getByRole('button', { name: '上一页', exact: true })
    while (await previous.count() && await previous.isEnabled()) await previous.click()
  }
}
async function verifySurfaces(page: Page) {
  await controlsReachable(page)
  await verifyCollections(page)
  for (const deck of await page.locator('.app-shell-page [data-screen-deck], .public-shell [data-screen-deck]').all()) {
    if (!await deck.isVisible()) continue
    const tabs = deck.locator(':scope > .screen-deck-tabs > button')
    for (const tab of await tabs.all()) {
      await tab.click()
      await controlsReachable(page)
      await verifyCollections(page)
    }
    if (await tabs.count()) await tabs.first().click()
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
  let studentThesisId: string
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
      // Use actual published tasks, rather than only empty-state pages. The
      // screenshot regression appeared with three stages at a 916px viewport.
      const checked = async (response: Awaited<ReturnType<typeof api.post>>) => {
        expect(response.ok(), `fixture HTTP ${response.status()}`).toBe(true)
        return response.json()
      }
      const school = await checked(await api.post('/api/organizations', { headers, data: { name: '一屏回归学校', type: 'school' } }))
      for (const account of [teacher, student, administrator]) {
        await checked(await api.put(`/api/organizations/users/${account.user.id}/context`, { headers, data: { organization_context: { school_id: school.id } } }))
      }
      const cohort = await checked(await api.post('/api/cohorts', { headers, data: { name: '一屏回归 · 有任务的论文批次', organization_id: school.id, academic_year: '2026—2027' } }))
      await checked(await api.post(`/api/cohorts/${cohort.id}/members`, { headers, data: { student_id: student.user.id, teacher_id: teacher.user.id } }))
      for (const [index, name] of ['开题报告', '论文初稿', '论文定稿'].entries()) {
        const stage = await checked(await api.post(`/api/cohorts/${cohort.id}/stages`, { headers, data: {
          key: `viewport-${index}`, name, template_id: 'default-gb7713',
          open_at: new Date(Date.now() - 86_400_000).toISOString(),
          deadline_at: new Date(Date.now() + (index + 1) * 14 * 86_400_000).toISOString(),
          is_final: index === 2,
        } }))
        await checked(await api.post(`/api/stages/${stage.id}/publish`, { headers }))
      }
      const studentThesis = await checked(await api.post('/api/projects', { headers: { Authorization: `Bearer ${student.token}` }, data: { template_id: 'default-gb7713', title: '学生一屏回归论文' } }))
      studentThesisId = studentThesis.id
    } finally { await api.dispose() }
  })
  test('every administrator route fits and its controls remain reachable', async ({ page }, info) => {
    test.setTimeout(600_000)
    await session(page, administrator.token, administrator.user)
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    const routes = ['/', '/workspace', '/templates', '/templates/default-gb7713/edit', '/policies', '/organizations', '/cohorts', '/workflow', '/workflow?panel=archives', '/workflow?panel=reports', '/workflow?panel=messages', '/admin/users', '/admin/ai', '/audit', '/operations', '/document-projects', '/document-packages/publish', `/document-projects/${documentId}`, `/write/${thesisId}`, '/layout-missing-page']
    const measurements = []
    for (const viewport of [{ width: 775, height: 804 }, { width: 916, height: 804 }, { width: 1100, height: 640 }, { width: 1280, height: 720 }, { width: 1384, height: 817 }]) {
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
          console.log(`Viewport check: ${route} at ${viewport.width}×${viewport.height}`)
          await verifySurfaces(page)
          if (route === '/workflow?panel=reports') {
            const options = page.locator('.report-filter option').filter({ hasNotText: '选择批次' })
            if (await options.count()) {
              const value = await options.first().getAttribute('value')
              await page.locator('.report-filter select').selectOption(value!)
              await expect(page.locator('.report-grid')).toBeVisible()
              await verifySurfaces(page)
            }
          }
          measurements.push({ route, ...await singleScreen(page) })
        })
        if (route === '/') {
          await page.locator('.workspace-tab').nth(1).click()
          await expect(page.locator('.inspection-section')).toBeVisible()
          await verifySurfaces(page)
          measurements.push({ route, panel: 'inspection', ...await singleScreen(page) })
          await page.getByRole('button', { name: '高校模板对照检查', exact: true }).click()
          await expect(page.locator('.template-upload-card')).toBeVisible()
          await verifySurfaces(page)
          measurements.push({ route, panel: 'template-inspection', ...await singleScreen(page) })
          await page.getByRole('button', { name: '标准规则包检查', exact: true }).click()
          if (viewport.width === 1280) await capture(page, info, 'home-inspection')
        }
      }
    }
    expect(errors).toEqual([])
    const geometryPath = info.outputPath('all-page-geometry.json')
    await writeFile(geometryPath, JSON.stringify(measurements, null, 2))
    await info.attach('all-page-geometry', { path: geometryPath, contentType: 'application/json' })
  })
  test('populated teacher and student routes keep every control reachable', async ({ page, browser }, info) => {
    test.setTimeout(600_000)
    const viewports = [{ width: 775, height: 804 }, { width: 916, height: 804 }, { width: 1100, height: 640 }, { width: 1280, height: 720 }]
    const teacherRoutes = ['/workspace', '/teacher/students', '/document-projects', '/document-packages/publish', '/workflow?panel=review', '/workflow?panel=progress', '/workflow?panel=materials', '/workflow?panel=messages', '/workflow?panel=submissions', '/policies']
    await session(page, teacher.token, teacher.user)
    const measurements = []
    for (const viewport of viewports) {
      await page.setViewportSize(viewport)
      for (const route of teacherRoutes) {
        await page.goto(route)
        await page.waitForLoadState('networkidle')
        expect(new URL(page.url()).pathname).toBe(route.split('?')[0])
        await expect(page.locator('.error-banner')).toHaveCount(0)
        await expect(page.locator('.app-error-boundary')).toHaveCount(0)
        if (route === '/workflow?panel=review') {
          await expect(page.locator('.workflow-task-pages .screen-pager')).toContainText('共 3 项')
          expect(await page.locator('.workflow-main').evaluate(el => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1)
        }
        console.log(`Viewport check: teacher ${route} at ${viewport.width}×${viewport.height}`)
        await test.step(`teacher ${route} at ${viewport.width}×${viewport.height}`, async () => {
          await verifySurfaces(page)
          measurements.push({ role: 'teacher', route, ...await singleScreen(page) })
        })
        if (viewport.width === 916) await capture(page, info, `teacher-${route.replace(/\W/g, '-')}`)
      }
    }
    const context = await browser.newContext()
    try {
      const p = await context.newPage()
      await session(p, student.token, student.user)
      const studentRoutes = ['/workspace', '/', '/document-projects', '/workflow?panel=overview', '/workflow?panel=submissions', '/workflow?panel=materials', '/workflow?panel=messages', '/policies', `/write/${studentThesisId}`]
      for (const viewport of viewports) {
        await p.setViewportSize(viewport)
        for (const route of studentRoutes) {
          await p.goto(route)
          await p.waitForLoadState('networkidle')
          expect(new URL(p.url()).pathname).toBe(route.split('?')[0])
          await expect(p.locator('.error-banner')).toHaveCount(0)
          await expect(p.locator('.app-error-boundary')).toHaveCount(0)
          if (route === '/workspace') {
            await expect(p.locator('details.workspace-drawer')).toHaveCount(3)
            for (const drawer of await p.locator('details.workspace-drawer').all()) {
              await expect(drawer).not.toHaveAttribute('open', '')
              await drawer.locator('summary').click()
              await expect(p.getByRole('dialog')).toBeVisible()
              await p.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).first().click()
            }
          }
          console.log(`Viewport check: student ${route} at ${viewport.width}×${viewport.height}`)
          await test.step(`student ${route} at ${viewport.width}×${viewport.height}`, async () => {
            await verifySurfaces(p)
            measurements.push({ role: 'student', route, ...await singleScreen(p) })
          })
          if (viewport.width === 916) await capture(p, info, `student-${route.replace(/\W/g, '-')}`)
        }
      }
    } finally { await context.close() }
    await info.attach('populated-roles-geometry', { body: JSON.stringify(measurements, null, 2), contentType: 'application/json' })
  })
  test('desktop breakpoint boundaries keep populated work areas inside the shell', async ({ page }, info) => {
    test.setTimeout(180_000)
    await session(page, teacher.token, teacher.user)
    const measurements = []
    for (const viewport of [{ width: 769, height: 640 }, { width: 900, height: 720 }, { width: 901, height: 640 }, { width: 1024, height: 720 }, { width: 1101, height: 640 }, { width: 1440, height: 900 }]) {
      await page.setViewportSize(viewport)
      await page.goto('/workflow?panel=review')
      await expect(page.locator('.workflow-task-pages .screen-pager')).toContainText('共 3 项')
      await verifyCollections(page)
      await controlsReachable(page)
      measurements.push({ route: '/workflow?panel=review', ...await singleScreen(page) })
      await capture(page, info, `review-boundary-${viewport.width}x${viewport.height}`)
    }
    await info.attach('breakpoint-geometry', { body: JSON.stringify(measurements, null, 2), contentType: 'application/json' })
  })
  test('first-login errors remain reachable inside the screen', async ({ page }, info) => {
    await session(page, 'local-first-login-fixture', { id: 'local-onboarding', username: 'local-onboarding', display_name: '本地首次登录测试', role: 'student', status: 'active', onboarding_required: true })
    const errors = ['本地夹具：验证码服务暂不可用。'.repeat(12), '本地夹具：首次登录提交失败。'.repeat(12)]
    await page.route('**/api/auth/email/code', route => route.fulfill({ status: 503, json: { detail: errors[0] } }))
    await page.route('**/api/auth/first-login/complete', route => route.fulfill({ status: 503, json: { detail: errors[1] } }))
    await page.goto('/first-login')
    const fields = page.locator('.first-login-card input')
    await fields.nth(0).fill('local-onboarding@example.test')
    await page.locator('.code-button').click()
    await expect(page.locator('.first-login-card .error-banner')).toBeVisible()
    await page.locator('.first-login-card').getByRole('button', { name: '查看详情' }).click()
    await expect(page.getByRole('dialog').locator('.inline-notice-detail')).toHaveText(errors[0])
    await page.getByRole('dialog').getByRole('button', { name: '关闭' }).click()
    await fields.nth(1).fill('123456')
    const password = `Local-${randomBytes(12).toString('hex')}`
    await fields.nth(2).fill(password)
    await fields.nth(3).fill(password)
    await page.locator('.submit-button').click()
    await expect(page.locator('.first-login-card .error-banner')).toBeVisible()
    for (const viewport of [{ width: 775, height: 640 }, { width: 916, height: 640 }, { width: 1280, height: 720 }]) {
      await page.setViewportSize(viewport)
      await controlsReachable(page)
      await page.locator('.first-login-card').getByRole('button', { name: '查看详情' }).click()
      await expect(page.getByRole('dialog').locator('.inline-notice-detail')).toHaveText(errors[1])
      await expect(page.getByRole('dialog').getByRole('button', { name: '关闭' })).toBeInViewport({ ratio: 1 })
      await page.getByRole('dialog').getByRole('button', { name: '关闭' }).click()
      await expect(fields.nth(0)).toHaveValue('local-onboarding@example.test')
      await expect(fields.nth(1)).toHaveValue('123456')
      await singleScreen(page)
      await capture(page, info, `first-login-error-${viewport.width}x${viewport.height}`)
    }
  })
  test('navigation failure shares the viewport with the page', async ({ page }, info) => {
    await session(page, administrator.token, administrator.user)
    await page.route(/\/src\/views\/Audit\.vue(?:\?|$)/, route => route.abort())
    for (const viewport of [{ width: 775, height: 640 }, { width: 916, height: 640 }, { width: 1280, height: 720 }]) {
      await page.setViewportSize(viewport)
      await page.goto('/admin/ai')
      if (viewport.width < 901) await page.getByRole('button', { name: '打开导航', exact: true }).click()
      await page.locator('#role-sidebar').getByRole('tab', { name: '资源与治理', exact: true }).click()
      await page.locator('.role-sidebar a[href="/audit"]').click()
      await expect(page.locator('.nav-failure')).toBeVisible()
      await page.keyboard.press('Escape')
      await singleScreen(page)
      await controlsReachable(page)
      await capture(page, info, `navigation-failure-${viewport.width}x${viewport.height}`)
      await page.locator('.nav-failure-actions button').last().click()
      await expect(page.locator('.nav-failure')).toHaveCount(0)
      await singleScreen(page)
    }
  })
  test('organization member lists paginate every user inside the screen', async ({ page }) => {
    await session(page, administrator.token, administrator.user)
    const users = Array.from({ length: 18 }, (_, index) => ({ id: `local-scope-${index}`, display_name: `本地成员夹具 ${index + 1}`, username: `local-scope-${index}`, role: 'student', organization_context: {} }))
    await page.route('**/api/auth/admin/users', handler => handler.fulfill({ json: users }))
    for (const viewport of [{ width: 775, height: 640 }, { width: 916, height: 640 }, { width: 1100, height: 640 }, { width: 1280, height: 720 }]) {
      await page.setViewportSize(viewport)
      await page.goto('/organizations')
      await page.waitForLoadState('networkidle')
      await page.locator('.org-page > [data-screen-deck]').getByRole('tab', { name: '用户作用域', exact: true }).click()
      const collection = page.locator('.user-list.screen-collection')
      await expect(collection.locator('.screen-pager')).toContainText('共 18 项')
      const seen = new Set<string>()
      const next = collection.getByRole('button', { name: '下一页', exact: true })
      do {
        await singleScreen(page)
        for (const row of await collection.locator('.user-scope-row').all()) {
          await expect(row).toBeInViewport({ ratio: 1 })
          seen.add((await row.textContent()) || '')
        }
        if (!await next.isEnabled()) break
        await next.click()
      } while (true)
      expect(seen.size).toBe(users.length)
    }
  })
  test('retention summary and configuration drawer fit the screen and preserve edits', async ({ page }, info) => {
    test.setTimeout(180_000)
    await session(page, administrator.token, administrator.user)
    const policy = { draft_days: 30, attachment_days: 365, submission_days: 730, archive_days: 3650, audit_days: 3650, local_cache_days: 7, timezone: 'Asia/Shanghai' }
    let configured = false
    await page.route('**/api/operations/retention', handler => handler.fulfill({ json: { configured, policy: configured ? policy : null } }))
    for (const viewport of [{ width: 775, height: 640 }, { width: 916, height: 640 }, { width: 1100, height: 640 }, { width: 1280, height: 720 }]) {
      await page.setViewportSize(viewport)
      for (configured of [false, true]) {
        await page.goto('/operations')
        await page.waitForLoadState('networkidle')
        await verifySurfaces(page)
        await page.locator('.workspace-side').getByRole('tab').first().click()
        await page.getByRole('button', { name: '配置保留策略', exact: true }).click()
        const dialog = page.getByRole('dialog')
        await expect(dialog).toBeVisible()
        await expect(dialog.getByRole('button', { name: '关闭', exact: true })).toBeInViewport({ ratio: 1 })
        await expect(dialog.getByRole('button', { name: '保存策略', exact: true })).toBeInViewport({ ratio: 1 })
        await dialog.getByLabel('草稿（天）').fill('45')
        await dialog.getByRole('button', { name: '关闭', exact: true }).click()
        await page.getByRole('button', { name: '配置保留策略', exact: true }).click()
        await expect(dialog.getByLabel('草稿（天）')).toHaveValue('45')
        await dialog.getByRole('button', { name: '关闭', exact: true }).click()
        await singleScreen(page)
        await page.getByRole('button', { name: 'English', exact: true }).click()
        await verifySurfaces(page)
        await page.getByRole('button', { name: 'Chinese', exact: true }).click()
      }
    }
    await capture(page, info, 'retention-configured-summary')
  })
  test('all navigation groups fit without scrolling in Chinese and English', async ({ page }, info) => {
    test.setTimeout(180_000)
    for (const account of [administrator, teacher, student]) {
      await session(page, account.token, account.user)
      await page.goto('/workspace')
      await page.waitForLoadState('networkidle')
      for (const viewport of [{ width: 775, height: 640 }, { width: 916, height: 640 }, { width: 1100, height: 640 }, { width: 1280, height: 720 }]) {
        await page.setViewportSize(viewport)
        for (const language of ['zh', 'en']) {
          if (language === 'en') await page.getByRole('button', { name: 'English', exact: true }).click()
          const sidebar = page.locator('#role-sidebar')
          const menu = page.getByRole('button', { name: /^(打开导航|Open navigation)$/ })
          const drawer = !await sidebar.isVisible()
          if (drawer) await menu.click()
          await expect(sidebar).toBeVisible()
          const tabs = sidebar.getByRole('tab')
          const assertSidebar = async () => {
            await expect.poll(() => sidebar.evaluate(el => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1)
            await expect(sidebar.locator('.sidebar-footer')).toBeInViewport({ ratio: 1 })
            for (const link of await sidebar.locator('a').all()) {
              if (!await link.isVisible()) continue
              await link.focus()
              await expect(link).toBeInViewport({ ratio: 1 })
              expect(await sidebar.evaluate(el => el.scrollTop)).toBe(0)
            }
          }
          await assertSidebar()
          for (const tab of await tabs.all()) { await tab.click(); await assertSidebar() }
          if (await tabs.count()) await tabs.first().click()
          if (drawer) await menu.click()
          if (language === 'en') await page.getByRole('button', { name: 'Chinese', exact: true }).click()
        }
      }
      await capture(page, info, `navigation-${account.user.role}`)
    }
  })
  test('public pages and role certification keep controls accessible', async ({ page }, info) => {
    test.setTimeout(180_000)
    for (const viewport of [{ width: 775, height: 640 }, { width: 916, height: 640 }, { width: 1280, height: 720 }]) {
      await page.setViewportSize(viewport)
      for (const route of ['/login', '/register']) {
        await page.goto(route)
        await expect(page.locator('h1')).toBeVisible()
        await verifySurfaces(page)
        await page.getByRole('button', { name: 'English', exact: true }).click()
        await verifySurfaces(page)
        await page.getByRole('button', { name: 'Chinese', exact: true }).click()
        if (route === '/register') {
          const error = '本地夹具：邮箱验证码服务暂不可用，请稍后重试。'.repeat(12)
          await page.route('**/api/auth/email/code', handler => handler.fulfill({ status: 503, json: { detail: error } }))
          await page.locator('#reg-email').fill('local-register@example.test')
          await page.locator('.code-button').click()
          await expect(page.locator('.register-card .error-banner')).toBeVisible()
          await verifySurfaces(page)
          await page.locator('.register-card').getByRole('button', { name: '查看详情' }).click()
          await expect(page.getByRole('dialog').locator('.inline-notice-detail')).toContainText(error)
          await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()
          await expect(page.locator('#reg-email')).toHaveValue('local-register@example.test')
          await singleScreen(page)
        }
        await capture(page, info, `${route.slice(1)}-${viewport.width}x${viewport.height}`)
      }
    }
    await session(page, 'local-certification-fixture', { id: 'local-certification', username: 'local-certification', display_name: '本地身份认证测试', role: 'user', role_application_status: 'unselected' })
    await page.route('**/api/auth/role-certification', route => route.fulfill({ json: { role: 'user', requested_organization_context: {}, application_status: 'unselected', organizations: [{ id: 'local-school', name: '本地学校夹具', type: 'school' }] } }))
    await page.goto('/role-certification')
    await expect(page.locator('.role-certification-page')).toBeVisible()
    for (const viewport of [{ width: 775, height: 640 }, { width: 916, height: 640 }, { width: 1280, height: 720 }]) {
      await page.setViewportSize(viewport)
      await controlsReachable(page)
      await singleScreen(page)
      await capture(page, info, `role-certification-${viewport.width}x${viewport.height}`)
    }
  })
})
