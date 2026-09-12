import { type Page } from '@playwright/test'

export class AppPage {
  constructor(readonly page: Page) {}

  async gotoProtected(path: string) {
    await this.page.goto(path)
  }

  async bodyWidth() {
    return this.page.evaluate(() => ({ client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }))
  }
}
