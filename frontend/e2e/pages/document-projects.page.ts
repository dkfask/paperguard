import { expect, type Page } from '@playwright/test'

export class DocumentProjectsPage {
  constructor(private readonly page: Page) {}

  async goto() {
    await this.page.goto('/document-projects')
    await expect(this.page.getByRole('heading', { name: /通用文档工作台|Document workspace|我的通用文档|My documents/ })).toBeVisible()
  }

  private packageCard(templateName: string, specName: string) {
    return this.page.locator('.package-card').filter({
      has: this.page.getByRole('heading', { name: templateName, exact: true }),
    }).filter({ hasText: specName })
  }

  async expectPublishedPackage(templateName: string, specName: string) {
    await expect(this.page.locator('.package-card').first()).toBeVisible()
    const collection = this.page.locator('.package-grid .screen-collection')
    for (let pageIndex = 0; pageIndex < 100; pageIndex++) {
      await expect.poll(() => collection.evaluate(el => el.getAnimations({ subtree: true }).filter(animation => animation.playState === 'running').length)).toBe(0)
      if (await this.packageCard(templateName, specName).isVisible()) return
      const next = collection.getByRole('button', { name: /^(下一页|Next page)$/ })
      if (!await next.count() || !await next.isEnabled()) break
      await next.click()
    }
    await expect(this.packageCard(templateName, specName)).toBeVisible()
  }

  async startProject(templateName: string, specName: string, title: string): Promise<string> {
    await this.expectPublishedPackage(templateName, specName)
    const card = this.packageCard(templateName, specName)
    await expect(card).toBeVisible()
    await card.getByRole('textbox', { name: /项目名称|Project title/ }).fill(title)
    await card.getByRole('button', { name: /开始填写|Start filling|开始写作|Start writing/ }).click()
    await expect(this.page).toHaveURL(/\/document-projects\/[^/]+$/)
    return this.page.url().split('/').pop() ?? ''
  }
}
