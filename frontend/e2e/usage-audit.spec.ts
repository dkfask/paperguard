import { expect, request, test } from '@playwright/test'
import { API_BASE_URL, createStudent, publishFixturePackage } from './document-platform.api'
import { UsageAuditPage } from './pages/usage-audit.page'
import { PaperGuardPage } from './pages/paperguard.page'

const scannerPath = process.env.E2E_AXE_PATH ?? ''

async function isolatedAdministrator(api: Awaited<ReturnType<typeof request.newContext>>, seedToken: string) {
  const account = await createStudent(api, 'audit-admin')
  const first = await api.post('/api/auth/login', { data: { username: account.username, password: account.password } })
  expect(first.ok()).toBe(true)
  const initial = await first.json()
  const promotion = await api.put(`/api/auth/admin/users/${initial.user.id}/role-status`, {
    headers: { Authorization: `Bearer ${seedToken}` }, data: { role: 'admin', status: 'active' },
  })
  expect(promotion.ok()).toBe(true)
  const login = await api.post('/api/auth/login', { data: { username: account.username, password: account.password } })
  expect(login.ok()).toBe(true)
  return login.json()
}

// Scan results are review evidence, not a claim that every reported violation passed.
test('TC-04-07: collect accessibility and UI evidence for three roles and a short form', async ({ browser }, info) => {
  test.setTimeout(120_000)
  test.skip(info.project.name !== 'chromium', 'Desktop scans; mobile geometry is covered in dedicated browser cases.')
  test.skip(!scannerPath, 'Optional accessibility collector requires E2E_AXE_PATH; no audit is claimed when absent.')
  const api = await request.newContext({ baseURL: API_BASE_URL })
  try {
    const bundle = await publishFixturePackage(api)
    const registered = await createStudent(api, 'ui-audit')
    const login = await api.post('/api/auth/login', { data: { username: registered.username, password: registered.password } })
    expect(login.ok()).toBe(true)
    const student = await login.json()
    // Use an isolated active administrator; the seeded account deliberately
    // requires first-login password replacement and redirects protected routes.
    const activeAdmin = await isolatedAdministrator(api, bundle.adminToken)
    const profiles = [
      { name: 'admin', token: activeAdmin.token, user: activeAdmin.user },
      { name: 'teacher', token: bundle.managerToken, user: bundle.managerUser },
      { name: 'student', token: student.token, user: student.user },
    ]
    for (const profile of profiles) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
      try {
        const page = await context.newPage()
        const audit = new UsageAuditPage(page, info)
        await audit.open('/login')
        if (profile.name === 'admin') {
          await audit.snapshot('audit-login')
          await audit.scan('a11y-login', scannerPath)
        }
        await audit.useSession(profile.token, profile.user)
        await audit.open('/workspace')
        await audit.snapshot(`audit-${profile.name}-workspace`)
        await audit.scan(`a11y-${profile.name}-workspace`, scannerPath)
        await audit.open('/workflow')
        await audit.snapshot(`audit-${profile.name}-workflow`)
        await audit.scan(`a11y-${profile.name}-workflow`, scannerPath)
        if (profile.name === 'teacher') {
          const title = await page.locator('h1').textContent() || ''
          await expect(page.getByRole('heading', { name: title, exact: true })).toHaveCount(1)
          const opener = page.getByRole('button', { name: '创建批次', exact: true })
          await opener.click()
          const dialog = page.getByRole('dialog')
          const bounds = await dialog.boundingBox()
          expect(bounds?.width).toBeLessThanOrEqual(448)
          expect(bounds?.height).toBeLessThan(600)
          await audit.snapshot('audit-create-cohort')
          await audit.scan('a11y-create-cohort', scannerPath)
          for (const key of ['Tab', 'Shift+Tab']) {
            for (let index = 0; index < 8; index++) {
              await page.keyboard.press(key)
              expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true)
            }
          }
          await page.keyboard.press('Escape')
          await expect(dialog).toHaveCount(0)
          await expect(opener).toBeFocused()
        }

        if (profile.name === 'student') {
          await audit.open('/')
          await audit.openThesisDialog()
          await audit.snapshot('audit-create-thesis')
          await audit.scan('a11y-create-thesis', scannerPath)
          await audit.checkThesisDialogKeyboard()
        }
        await page.setViewportSize({ width: 390, height: 844 })
        await audit.open('/workspace')
        await audit.snapshot(`audit-${profile.name}-mobile`)
        if (profile.name === 'admin') await expect(page.locator('.workspace-operation-card').first()).toBeInViewport({ ratio: 1 })
      } finally { await context.close() }
    }
  } finally { await api.dispose() }
})

test('TC-01-02: certification dialog reflects the completed decision', async ({ page }, info) => {
  test.skip(info.project.name !== 'chromium', 'One real desktop decision; mobile geometry covered separately.')
  const api = await request.newContext({ baseURL: API_BASE_URL })
  try {
    const bundle = await publishFixturePackage(api)
    const administrator = await isolatedAdministrator(api, bundle.adminToken)
    const pending = await createStudent(api, 'pending-teacher')
    const initialLogin = await api.post('/api/auth/login', { data: { username: pending.username, password: pending.password } })
    expect(initialLogin.ok()).toBe(true)
    const initial = await initialLogin.json()
    const pendingRole = await api.put(`/api/auth/admin/users/${initial.user.id}/role-status`, {
      headers: { Authorization: `Bearer ${bundle.adminToken}` }, data: { role: 'teacher', status: 'pending' },
    })
    expect(pendingRole.ok()).toBe(true)
    const audit = new UsageAuditPage(page, info)
    await audit.useSession(administrator.token, administrator.user)
    await audit.open('/admin/users')
    const directory = new PaperGuardPage(page)
    const dialog = await directory.openUserDetails(pending.username)
    await audit.snapshot('certification-before')
    await directory.waitForMutation('/role-status', async () => {
      await dialog.getByRole('button', { name: '通过认证', exact: true }).click()
    }, 'PUT')
    // Verify persistence separately: reloading the list resets pagination;
    // changing a page behind an open modal would be an invalid user action.
    const persisted = await api.get('/api/auth/admin/users', { headers: { Authorization: `Bearer ${administrator.token}` } })
    expect(persisted.ok()).toBe(true)
    const users = await persisted.json()
    expect(users.find((user: any) => user.username === pending.username)?.status).toBe('active')
    await expect(page.locator('.async-state.loading')).toHaveCount(0)
    await audit.snapshot('certification-after')
    await expect(dialog).toContainText('已认证')
    await expect(dialog.getByRole('button', { name: '通过认证', exact: true })).toHaveCount(0)
  } finally { await api.dispose() }
})
