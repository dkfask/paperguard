import { expect, test, type Page } from '@playwright/test'

async function cachedIdentity(page: Page, user: Record<string, unknown>) {
  await page.addInitScript(user => {
    sessionStorage.setItem('pg_token', 'local-pilot-browser-fixture')
    sessionStorage.setItem('pg_user', JSON.stringify(user))
    localStorage.setItem('paperguard_lang', 'zh')
  }, user)
}

for (const role of ['college_admin', 'reviewer', 'unknown']) {
  test(`cached ${role} identity cannot enter business deep links`, async ({ page }) => {
    await cachedIdentity(page, { id: 'closed-fixture', username: 'closed-fixture', role, status: 'active' })
    const requests: string[] = []
    await page.route(url => url.pathname.startsWith('/api/'), route => {
      requests.push(new URL(route.request().url()).pathname)
      return route.fulfill({ status: 403, json: { detail: { code: 'ROLE_NOT_OPEN' } } })
    })
    for (const path of ['/cohorts', '/audit', '/document-projects', '/admin/users']) {
      await page.goto(path)
      await expect(page).toHaveURL(/\/login$/)
      await expect(page.locator('#username')).toBeVisible()
      expect(await page.evaluate(() => sessionStorage.getItem('pg_token'))).toBeNull()
      expect(await page.evaluate(() => sessionStorage.getItem('pg_user'))).toBeNull()
      await expect(page.locator('.sidebar-link, .sidebar-tool')).toHaveCount(0)
    }
    expect(requests).toEqual([])
  })
}

for (const state of ['unselected', 'pending', 'rejected', 'approved']) {
  test(`user with ${state} application stays in certification until approval refresh`, async ({ page }, info) => {
    let current: Record<string, unknown> = {
      id: 'certification-fixture', username: 'certification-fixture', display_name: '本地认证测试',
      role: 'user', status: 'active', role_application_status: state,
    }
    await cachedIdentity(page, current)
    const businessRequests: string[] = [], unexpected: string[] = [], runtimeErrors: string[] = []
    page.on('pageerror', error => runtimeErrors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), route => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/auth/me') return route.fulfill({ json: current })
      if (path === '/api/auth/role-certification') {
        if (route.request().method() === 'POST') {
          expect(route.request().postDataJSON()).toEqual({ role: 'teacher', organization_context: { school_id: 'pilot-school' } })
          current = { ...current, requested_role: 'teacher', role_application_status: 'pending' }
          return route.fulfill({ json: current })
        }
        return route.fulfill({ json: {
          role: 'user', application_status: state, requested_organization_context: {},
          organizations: [{ id: 'pilot-school', name: '本地试点学校', type: 'school' }],
        } })
      }
      businessRequests.push(path)
      if (['/api/my-tasks', '/api/process-records/inbox', '/api/document-projects', '/api/document-packages'].includes(path)) {
        return route.fulfill({ json: [] })
      }
      if (path === '/api/notifications') return route.fulfill({ json: { items: [], unread: 0 } })
      unexpected.push(path)
      return route.fulfill({ status: 404, json: { detail: 'Unexpected pilot fixture request' } })
    })

    await page.goto('/document-projects')
    await expect(page).toHaveURL(/\/role-certification$/)
    await expect(page.locator('.role-option')).toHaveCount(2)
    await expect(page.locator('.role-certification-page')).not.toContainText('社会用户')
    await expect(page.locator('.certification-actions button.primary')).toBeEnabled()
    expect(businessRequests).toEqual([])
    await page.locator('.role-option').filter({ hasText: '教师' }).click()
    await page.locator('.organization-fields select').first().selectOption('pilot-school')
    await page.getByRole('button', { name: '确认并继续', exact: true }).click()
    await expect(page.getByRole('status')).toContainText('申请已提交')
    await expect(page).toHaveURL(/\/role-certification$/)
    expect(businessRequests).toEqual([])
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
    if (state === 'pending') await page.screenshot({ path: info.outputPath('pilot-certification-pending.png') })

    current = { ...current, role: 'teacher', role_application_status: 'approved' }
    await page.getByRole('button', { name: '刷新审核状态', exact: true }).click()
    await expect(page).toHaveURL(/\/workspace$/)
    await expect(page.locator('h1')).toHaveText('教师待办')
    await expect(page.locator('.workspace-stats')).toBeVisible()
    expect(await page.evaluate(() => JSON.parse(sessionStorage.getItem('pg_user') || '{}').role)).toBe('teacher')
    if (!await page.locator('#role-sidebar').isVisible()) await page.getByRole('button', { name: /打开导航/ }).click()
    await page.locator('#role-sidebar').getByRole('tab', { name: '个人写作', exact: true }).click()
    await page.locator('#role-sidebar').getByRole('link', { name: '我的通用文档', exact: true }).click()
    await expect(page).toHaveURL(/\/document-projects$/)
    await expect(page.locator('h1')).toHaveText('我的通用文档')
    await expect(page.locator('.personal-template-action')).toBeVisible()
    await expect(page.locator('.error-banner')).toHaveCount(0)
    expect(unexpected).toEqual([])
    expect(runtimeErrors).toEqual([])
  })
}
