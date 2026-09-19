import { expect, type Page } from '@playwright/test'

export class LoginPage {
  constructor(private readonly page: Page) {}

  async login(username: string, password: string, replacementPassword?: string) {
    await this.page.goto('/login?redirect=/')
    await this.page.locator('#username').fill(username)
    await this.page.locator('#password').fill(password)
    await this.page.locator('form.auth-form').getByRole('button').click()

    let requiresPasswordChange = false
    try {
      requiresPasswordChange = await Promise.race([
        this.page.locator('#newPassword').waitFor({ state: 'visible', timeout: 5000 }).then(() => true),
        this.page.waitForURL((url) => !url.pathname.endsWith('/login'), { timeout: 5000 }).then(() => false),
      ])
    } catch {
      // Keep the final URL assertion as the failure signal for invalid login.
    }

    if (replacementPassword && requiresPasswordChange) {
      await this.page.locator('#username').fill(username)
      await this.page.locator('#password').fill(password)
      await this.page.locator('#newPassword').fill(replacementPassword)
      await this.page.locator('#confirmPassword').fill(replacementPassword)
      await this.page.locator('form.auth-form').getByRole('button').click()
      await expect(this.page).toHaveURL(/\/login$/)
      await this.page.locator('#username').fill(username)
      await this.page.locator('#password').fill(replacementPassword)
      await this.page.locator('form.auth-form').getByRole('button').click()
    }

    await expect(this.page).not.toHaveURL(/\/login/)
  }

  async createFixtureAccount(username: string, password: string, displayName: string, role: 'student' | 'teacher') {
    // The browser lifecycle needs deterministic accounts. The UI registration
    // flow requires a real email provider and verification code, so seed only
    // the isolated test database through the compatibility API path here.
    const response = await this.page.request.post('/api/auth/register', {
      data: { username, password, display_name: displayName, role },
    })
    expect(response.status()).toBe(201)
  }
}

export class PaperGuardPage {
  constructor(protected readonly page: Page) {}

  control(label: string) {
    return this.page.locator('label').filter({ hasText: label }).locator('input, select, textarea').first()
  }

  async logout() {
    await this.page.getByRole('button', { name: '退出登录' }).click()
    await expect(this.page).toHaveURL(/\/login/)
  }

  async waitForMutation(urlPart: string, action: () => Promise<void>, method = 'POST') {
    const responsePromise = this.page.waitForResponse((response) => response.url().includes(urlPart)
      && response.request().method() === method
      && response.status() >= 200 && response.status() < 300)
    await action()
    const response = await responsePromise
    await response.finished()
  }
}
