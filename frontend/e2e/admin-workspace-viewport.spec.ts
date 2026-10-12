import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test'

type FixtureOptions = { crowded?: boolean; empty?: boolean; role?: string; failLoad?: boolean; pauseLoad?: boolean }

async function dashboardFixture(page: Page, options: FixtureOptions = {}) {
  await page.addInitScript(role => {
    sessionStorage.setItem('pg_token', 'local-layout-fixture')
    sessionStorage.setItem('pg_user', JSON.stringify({ id: 'layout-admin', username: 'layout-admin', display_name: '管理员', role, status: 'active' }))
    localStorage.setItem('paperguard_lang', 'zh')
  }, options.role || 'admin')
  const cohorts = Array.from({ length: options.empty ? 0 : options.crowded ? 8 : 1 }, (_, i) => ({
    id: `layout-cohort-${i}`, name: options.crowded ? `本地布局验证批次 ${i + 1} · 长名称与较多数据的可达性检查` : '示例批次（本地布局验证）',
    academic_year: '2026', status: 'published', member_ids: ['fixture-student-1', 'fixture-student-2'],
  }))
  const archives = Array.from({ length: options.empty ? 0 : options.crowded ? 10 : 1 }, (_, i) => ({
    id: `layout-archive-${i}`, submission_id: `fixture-submission-${i}`, submission_number: `示例提交 ${i + 1}`,
    status: 'queued', created_at: '2026-10-10T01:00:00Z',
  }))
  const items = Array.from({ length: options.crowded ? 12 : 0 }, (_, i) => ({
    id: `layout-notification-${i}`, data: { title: `本地测试消息 ${i + 1} · 检查列表与键盘访问`, event_type: 'review' },
    created_at: '2026-10-10T01:00:00Z', read_at: null,
  }))
  let resumeLoad = () => {}
  const loadGate = options.pauseLoad ? new Promise<void>(resolve => { resumeLoad = resolve }) : Promise.resolve()
  let failLoad = Boolean(options.failLoad)
  const unexpected: string[] = [], runtimeErrors: string[] = []
  page.on('pageerror', error => runtimeErrors.push(error.message))
  page.on('console', message => {
    if (message.type() === 'error' && !message.text().startsWith('Failed to load resource:')) runtimeErrors.push(message.text())
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/cohorts') await loadGate
    if (path === '/api/cohorts' && failLoad) return route.fulfill({ status: 503, json: { detail: '本地夹具：批次服务暂不可用' } })
    if (path === '/api/cohorts') return route.fulfill({ json: cohorts })
    if (path === '/api/archives') return route.fulfill({ json: archives })
    if (path === '/api/notifications') return route.fulfill({ json: { items, unread: items.length } })
    if (path.startsWith('/api/reports/cohorts/')) return route.fulfill({ json: {
      cohort_name: cohorts[0]?.name, member_count: 2, state_counts: { submitted: 1, needs_revision: 0, archived: 0 },
      metrics: { on_time_first_submission_rate: .5, archive_completion_rate: 0 },
    } })
    if (/^\/api\/notifications\/[^/]+\/read$/.test(path)) return route.fulfill({ status: 503, json: { detail: '本地夹具：消息标记失败' } })
    unexpected.push(path)
    return route.fulfill({ status: 404, json: { detail: `未定义布局夹具：${path}` } })
  })
  return { unexpected, runtimeErrors, resumeLoad, retry: () => { failLoad = false } }
}

async function assertSingleScreen(page: Page) {
  const geometry = await page.evaluate(() => ({ viewport: { width: innerWidth, height: innerHeight }, document: {
    width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight,
  }, scrollY }))
  expect(geometry.document.width).toBeLessThanOrEqual(geometry.viewport.width + 1)
  expect(geometry.document.height).toBeLessThanOrEqual(geometry.viewport.height + 1)
  expect(geometry.scrollY).toBe(0)
  for (const panel of await page.locator('.admin-workspace .panel').all()) {
    if (!await panel.isVisible()) continue
    const bounds = await panel.boundingBox()
    expect(bounds).not.toBeNull()
    expect(bounds!.height).toBeGreaterThan(45)
    expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(geometry.viewport.height + 1)
    await expect(panel.locator('.panel-heading')).toBeInViewport({ ratio: 1 })
  }
  return geometry
}

async function assertCollectionRowsFit(collection: Locator) {
  // Pagination measures the available area after the pane opens. Wait for its
  // short row transition before taking a list of locators at fixed positions.
  await expect.poll(() => collection.evaluate(el => el.getAnimations({ subtree: true }).filter(animation => animation.playState === 'running').length)).toBe(0)
  await expect.poll(() => collection.locator('.screen-collection-body').evaluate(el => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1)
  for (const row of await collection.locator('.screen-collection-body > *').all()) await expect(row).toBeInViewport({ ratio: 1 })
}

async function capture(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(`${name}.png`)
  await page.screenshot({ path, fullPage: true })
  await info.attach(name, { path, contentType: 'image/png' })
}

test('manager dashboard and every panel fit one desktop viewport', async ({ page }, info) => {
  const fixture = await dashboardFixture(page)
  await page.setViewportSize({ width: 1384, height: 817 })
  await page.goto('/workspace')
  await expect(page.locator('.workspace-stats')).toBeVisible()
  const geometries = []
  for (const viewport of [{ width: 1384, height: 817 }, { width: 1280, height: 720 }, { width: 1440, height: 900 }, { width: 1920, height: 1080 }]) {
    await page.setViewportSize(viewport)
    geometries.push(await assertSingleScreen(page))
    await expect(page.locator('.workspace-main > .screen-deck-tabs > button')).toHaveCount(4)
    for (const tab of await page.locator('.workspace-main > .screen-deck-tabs > button').all()) {
      await tab.click()
      await assertSingleScreen(page)
      for (const collection of await page.locator('.workspace-main .screen-deck-pane:not([hidden]) .screen-collection').all()) await assertCollectionRowsFit(collection)
    }
    await page.locator('.workspace-hero').hover()
    await page.mouse.wheel(0, 1000)
    expect(await page.evaluate(() => scrollY)).toBe(0)
    await page.locator('.workspace-main > .screen-deck-tabs > button').nth(2).click()
    for (const metric of await page.locator('.workspace-report-grid > div').all()) await expect(metric).toBeInViewport({ ratio: 1 })
    await expect(page.locator('.workspace-report-note')).toBeInViewport({ ratio: 1 })
    await page.locator('.workspace-main > .screen-deck-tabs > button').nth(3).click()
    await expect(page.locator('.workspace-archive-row').first()).toBeInViewport({ ratio: 1 })
    if (viewport.width === 1384 || viewport.width === 1440 || viewport.width === 1280) {
      await capture(page, info, `admin-workspace-${viewport.width}x${viewport.height}`)
    }
  }
  await page.locator('.role-sidebar').getByRole('tab', { name: '管理工具', exact: true }).click()
  await page.locator('.sidebar-tool').last().focus()
  await expect(page.locator('.sidebar-tool').last()).toBeInViewport({ ratio: 1 })
  expect(await page.evaluate(() => scrollY)).toBe(0)
  expect(fixture.unexpected).toEqual([])
  expect(fixture.runtimeErrors).toEqual([])
  await info.attach('viewport-geometry', { body: JSON.stringify(geometries, null, 2), contentType: 'application/json' })
})

test('long lists stay keyboard accessible inside cards and English labels fit', async ({ page }, info) => {
  const fixture = await dashboardFixture(page, { crowded: true })
  await page.setViewportSize({ width: 1280, height: 720 })
  await page.goto('/workspace')
  await assertSingleScreen(page)
  for (const index of [1, 3]) {
    await page.locator('.workspace-main > .screen-deck-tabs > button').nth(index).click()
    const collection = page.locator('.workspace-main .screen-deck-pane:not([hidden]) .screen-collection')
    const next = collection.getByRole('button', { name: '下一页', exact: true })
    expect(await next.isEnabled()).toBe(true)
    do {
      await assertCollectionRowsFit(collection)
      await assertSingleScreen(page)
      if (!await next.isEnabled()) break
      await next.click()
    } while (true)
  }
  const inbox = page.locator('.workspace-notification-panel')
  await inbox.locator('.workspace-text-link').focus()
  await expect(inbox.locator('.workspace-text-link')).toBeInViewport({ ratio: 1 })
  await inbox.locator('.notification-row').first().click()
  await expect(page.locator('.error-banner')).toContainText('消息标记失败')
  await assertSingleScreen(page)
  await page.getByRole('button', { name: 'English', exact: true }).click()
  await expect(page.locator('h1')).toHaveText('School dashboard')
  await assertSingleScreen(page)
  expect(fixture.unexpected).toEqual([])
  expect(fixture.runtimeErrors).toEqual([])
  await capture(page, info, 'crowded-dashboard-english')
})

test('empty and retry states fit a desktop screen for college administrators', async ({ page }) => {
  const fixture = await dashboardFixture(page, { empty: true, failLoad: true, role: 'college_admin' })
  await page.setViewportSize({ width: 1384, height: 817 })
  await page.goto('/workspace')
  await expect(page.locator('.async-state.error')).toContainText('批次服务暂不可用')
  await assertSingleScreen(page)
  fixture.retry()
  await page.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.locator('.workspace-stats')).toBeVisible()
  await expect(page.locator('.workspace-report-panel')).toHaveCount(0)
  await page.locator('.workspace-main > .screen-deck-tabs > button').nth(1).click()
  await expect(page.locator('.workspace-cohorts-panel .async-state.empty')).toBeVisible()
  await page.locator('.workspace-main > .screen-deck-tabs > button').nth(2).click()
  await expect(page.locator('.workspace-archives-panel .async-state.empty')).toBeVisible()
  await assertSingleScreen(page)
  expect(fixture.unexpected).toEqual([])
  expect(fixture.runtimeErrors).toEqual([])
})

test('narrow screens keep all content reachable with the mobile navigation', async ({ page }, info) => {
  const fixture = await dashboardFixture(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/workspace')
  await expect(page.locator('.workspace-stats')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeGreaterThan(844)
  await page.locator('.workspace-main > .screen-deck-tabs > button').nth(3).click()
  await page.locator('.workspace-archives-panel').scrollIntoViewIfNeeded()
  await expect(page.locator('.workspace-archives-panel .panel-heading')).toBeInViewport({ ratio: 1 })
  await page.getByRole('button', { name: '打开导航', exact: true }).click()
  await expect(page.locator('.role-sidebar')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('.role-sidebar')).toBeHidden()
  expect(fixture.unexpected).toEqual([])
  expect(fixture.runtimeErrors).toEqual([])
  await capture(page, info, 'admin-workspace-mobile')
})

test('loading reserves the viewport until dashboard data arrives', async ({ page }) => {
  const fixture = await dashboardFixture(page, { pauseLoad: true })
  await page.setViewportSize({ width: 1384, height: 817 })
  await page.goto('/workspace')
  await expect(page.locator('.async-state.loading')).toBeVisible()
  await expect(page.locator('.workspace-stats')).toHaveCount(0)
  await assertSingleScreen(page)
  fixture.resumeLoad()
  await expect(page.locator('.workspace-stats')).toBeVisible()
  await assertSingleScreen(page)
  expect(fixture.unexpected).toEqual([])
  expect(fixture.runtimeErrors).toEqual([])
})
