import { expect, type Page } from '@playwright/test'

export class LoginPage {
  constructor(readonly page: Page) {}

  async goto() {
    await this.page.goto('/login')
  }

  async expectVisible() {
    await expect(this.page.getByRole('heading', { name: /欢迎登录 PaperGuard/ })).toBeVisible()
    await expect(this.page.getByRole('button', { name: /登\s*录/ })).toBeVisible()
  }

  async expectRedirectedFrom(path: string) {
    await expect(this.page).toHaveURL(/\/login\?redirect=/)
    expect(new URL(this.page.url()).searchParams.get('redirect')).toBe(path)
  }
}
