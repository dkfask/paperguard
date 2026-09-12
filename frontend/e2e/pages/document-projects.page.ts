import { expect, type Page } from '@playwright/test'

export class DocumentProjectsPage {
  constructor(private readonly page: Page) {}

  async goto() {
    await this.page.goto('/document-projects')
    await expect(this.page.getByRole('heading', { name: /通用文档工作台|Document workspace/ })).toBeVisible()
  }

  private packageCard(templateName: string, specName: string) {
    return this.page.locator('.package-card').filter({
      has: this.page.getByRole('heading', { name: templateName, exact: true }),
    }).filter({ hasText: specName })
  }

  async expectPublishedPackage(templateName: string, specName: string) {
    await expect(this.packageCard(templateName, specName)).toBeVisible()
  }

  async startProject(templateName: string, specName: string, title: string): Promise<string> {
    const card = this.packageCard(templateName, specName)
    await expect(card).toBeVisible()
    await card.getByRole('textbox', { name: /项目名称|Project title/ }).fill(title)
    await card.getByRole('button', { name: /开始填写|Start filling/ }).click()
    await expect(this.page).toHaveURL(/\/document-projects\/[^/]+$/)
    return this.page.url().split('/').pop() ?? ''
  }
}
