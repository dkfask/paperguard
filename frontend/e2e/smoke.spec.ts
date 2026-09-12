import { expect, test } from '@playwright/test'
import { AppPage } from './pages/app.page'
import { LoginPage } from './pages/login.page'

test.describe('PaperGuard frontend smoke', () => {
  test('TC-UI-01: unauthenticated users see the login surface', async ({ page }) => {
    const loginPage = new LoginPage(page)
    await loginPage.goto()
    await loginPage.expectVisible()
  })

  test('TC-UI-02: direct protected routes redirect to login', async ({ page }) => {
    const appPage = new AppPage(page)
    const loginPage = new LoginPage(page)
    const protectedPath = '/write/does-not-exist'

    await appPage.gotoProtected(protectedPath)
    await loginPage.expectVisible()
    await loginPage.expectRedirectedFrom(protectedPath)
  })

  test('TC-UI-03: mobile login has no horizontal overflow', async ({ page }) => {
    const loginPage = new LoginPage(page)
    const appPage = new AppPage(page)
    await loginPage.goto()
    await loginPage.expectVisible()

    const width = await appPage.bodyWidth()
    expect(width.scroll).toBeLessThanOrEqual(width.client)
  })
})
