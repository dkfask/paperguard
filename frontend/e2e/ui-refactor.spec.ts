import { randomBytes } from 'node:crypto'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { LoginPage } from './pages/paperguard.page'

async function studentSession(page: Page) {
  const stamp = randomBytes(6).toString('hex')
  const login = new LoginPage(page)
  const password = `Local-${randomBytes(12).toString('hex')}`
  await login.createFixtureAccount(`ui_${stamp}`, password, '界面回归学生', 'student')
  await login.login(`ui_${stamp}`, password)
}

async function capture(page: Page, info: TestInfo, name: string) {
  const path = info.outputPath(`${name}.png`)
  await page.screenshot({ path, fullPage: true })
  await info.attach(name, { path, contentType: 'image/png' })
}

async function assertContentFits(page: Page) {
  const bounds = await page.locator('.app-shell-main').evaluate(el => ({ right: el.getBoundingClientRect().right, width: window.innerWidth }))
  expect(bounds.right).toBeLessThanOrEqual(bounds.width + 1)
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(1)
}

test('workspace errors, retry and panel navigation remain consistent on direct entry', async ({ page }, info) => {
  await studentSession(page)
  await page.route('**/api/my-tasks', route => route.fulfill({ status: 503, json: { detail: '测试夹具：任务服务暂不可用' } }))
  await page.goto('/workspace')
  await expect(page.locator('.async-state.error')).toContainText('任务服务暂不可用')
  await expect(page.locator('.workspace-stats')).toHaveCount(0)
  await page.unroute('**/api/my-tasks')
  await page.locator('.async-state.error').getByRole('button', { name: '重试' }).click()
  await expect(page.locator('.workspace-stats')).toBeVisible()
  await assertContentFits(page)
  await capture(page, info, 'student-workspace')

  const drawers = page.locator('details.workspace-drawer')
  await expect(drawers).toHaveCount(3)
  await expect(page.locator('details.workspace-drawer[open]')).toHaveCount(0)
  await expect(drawers.first().locator('.workspace-drawer-body')).toBeHidden()
  await capture(page, info, 'student-drawers-collapsed')
  await drawers.first().locator('summary').focus()
  await page.keyboard.press('Enter')
  await expect(drawers.first()).toHaveAttribute('open', '')
  await expect(drawers.first().locator('.async-state.empty')).toBeVisible()
  await drawers.nth(1).locator('summary').click()
  await expect(drawers.nth(1)).toHaveAttribute('open', '')
  await expect(drawers.first()).toHaveAttribute('open', '')
  await expect(drawers.nth(1).getByRole('link', { name: /查看详情/ })).toHaveAttribute('href', '/workflow?panel=progress')
  await drawers.nth(2).locator('summary').focus()
  await page.keyboard.press('Space')
  await expect(drawers.nth(2)).toHaveAttribute('open', '')
  await expect(drawers.nth(2).getByRole('link', { name: /全部记录/ })).toHaveAttribute('href', '/workflow?panel=submissions')
  await assertContentFits(page)
  await capture(page, info, 'student-drawers-expanded')
  await page.keyboard.press('Space')
  await expect(drawers.nth(2)).not.toHaveAttribute('open')
  await expect(drawers.nth(2).locator('.workspace-drawer-body')).toBeHidden()

  if (info.project.name === 'mobile-chrome') {
    await page.getByRole('button', { name: '打开导航' }).click()
    await expect(page.locator('.role-sidebar')).toBeVisible()
    await capture(page, info, 'mobile-navigation')
  }
  await page.locator('.sidebar-link').filter({ hasText: '过程材料' }).click()
  await expect(page).toHaveURL(/panel=materials/)
  await expect(page.locator('h1')).toHaveText('过程材料')
  await expect(page).toHaveTitle('过程材料 · PaperGuard')
  await expect(page.locator('.sidebar-link[aria-current="page"]')).toHaveCount(1)
  await page.reload()
  await expect(page.locator('h1')).toHaveText('过程材料')
  await assertContentFits(page)
  await capture(page, info, 'student-materials')
  await page.getByRole('button', { name: 'English', exact: true }).click()
  await expect(page.locator('h1')).toHaveText('Process Materials')
  await expect(page).toHaveTitle('Process Materials · PaperGuard')
  await assertContentFits(page)
})

test('writer keeps its draft, editor instance and selection when panels and viewport change', async ({ page }, info) => {
  await studentSession(page)
  await page.goto('/')
  await page.locator('.create-thesis-bar input').fill('界面回归论文 · 验证长标题在桌面与手机工具栏中正确截断且保存状态保持可见')
  await page.locator('.create-thesis-bar button.primary').click()
  await expect(page).toHaveURL(/\/write\//)
  await page.locator('.nav-row').filter({ hasText: /摘\s*要/ }).first().click()
  if (!await page.locator('textarea.para-text').count()) {
    await page.locator('.insert-row button.primary').first().click()
  }
  const editor = page.locator('textarea.para-text').first()
  const draft = '中文写作回归：界面调整应保持正文、选区与保存状态。'
  await editor.fill(draft)
  await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)
  await editor.evaluate(el => el.setSelectionRange(2, 8))
  const handle = await editor.elementHandle()
  await page.getByRole('button', { name: '检查', exact: true }).first().click()
  await expect(editor).toHaveValue(draft)
  expect(await editor.evaluate((el, original) => el === original, handle)).toBe(true)
  expect(await editor.evaluate(el => [el.selectionStart, el.selectionEnd])).toEqual([2, 8])
  const originalViewport = page.viewportSize()!
  await page.setViewportSize({ width: 760, height: 900 })
  await expect(editor).toHaveValue(draft)
  expect(await editor.evaluate((el, original) => el === original, handle)).toBe(true)
  await page.setViewportSize(originalViewport)
  if (info.project.name === 'chromium') {
    // A resized textarea gives the page enough content to exercise scrolling.
    await editor.evaluate(el => { el.style.height = '1000px' })
    for (const width of [1280, 840]) {
      await page.setViewportSize({ width, height: 500 })
      await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
      const panes = width === 1280 ? ['.writer-columns > .sidebar', '.writer-context'] : ['.writer-columns > .sidebar']
      const before = await Promise.all(panes.map(selector => page.locator(selector).evaluate(el => el.getBoundingClientRect().top)))
      await page.evaluate(() => window.scrollTo({ top: 260, behavior: 'instant' }))
      const scroll = await page.evaluate(() => window.scrollY)
      expect(scroll).toBeGreaterThan(200)
      for (const [index, selector] of panes.entries()) {
        const after = await page.locator(selector).evaluate(el => el.getBoundingClientRect().top)
        expect(after).toBeCloseTo(before[index] - scroll, 0)
      }
    }
    await editor.evaluate(el => { el.style.height = '' })
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }))
    await page.setViewportSize(originalViewport)
  }
  await assertContentFits(page)
  await capture(page, info, 'writer-shell')
  await page.reload()
  await page.locator('.nav-row').filter({ hasText: /摘\s*要/ }).first().click()
  await expect(page.locator('textarea.para-text').first()).toHaveValue(draft)

  await page.getByRole('button', { name: '返回我的论文', exact: true }).click()
  const remove = page.locator('.thesis-row').getByRole('button', { name: '删除', exact: true })
  await remove.click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true)
  await capture(page, info, 'confirmation-dialog')
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(remove).toBeFocused()
  await capture(page, info, 'thesis-list')
})

test('writer retains a failed Chinese draft and retries the same content', async ({ page }) => {
  await studentSession(page)
  await page.goto('/')
  await page.locator('.create-thesis-bar input').fill('保存失败回归论文')
  await page.locator('.create-thesis-bar button.primary').click()
  await expect(page).toHaveURL(/\/write\//)
  await page.locator('.nav-row').filter({ hasText: /摘\s*要/ }).first().click()
  if (!await page.locator('textarea.para-text').count()) await page.locator('.insert-row button.primary').first().click()
  await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)
  const editor = page.locator('textarea.para-text').first()
  const draft = '中文组合输入与保存失败回归：重试必须保留这份草稿。'
  await page.route('**/api/projects/*/section/*', route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 503, json: { detail: '测试夹具：保存暂不可用' } }) : route.continue())
  // Synthetic composition verifies DOM continuity; native OS input-method UAT remains separate.
  await editor.dispatchEvent('compositionstart', { data: '' })
  const original = await editor.elementHandle()
  await page.getByRole('button', { name: '检查', exact: true }).first().click()
  expect(await editor.evaluate((el, handle) => el === handle, original)).toBe(true)
  await editor.dispatchEvent('compositionend', { data: draft })
  await editor.fill(draft)
  await expect(page.locator('.save-failure')).toBeVisible()
  await expect(editor).toHaveValue(draft)
  await expect(page.locator('.writer-save-status')).toHaveClass(/pending/)
  await page.unroute('**/api/projects/*/section/*')
  await page.locator('.save-failure').getByRole('button').click()
  await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)
  await expect(page.locator('.save-failure')).toHaveCount(0)
  await page.reload()
  await page.locator('.nav-row').filter({ hasText: /摘\s*要/ }).first().click()
  await expect(page.locator('textarea.para-text').first()).toHaveValue(draft)
})
