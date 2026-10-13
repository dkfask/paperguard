import { expect, type Download, type Page } from '@playwright/test'

export class DocumentProjectEditorPage {
  constructor(private readonly page: Page) {}

  private firstContentBlock() {
    return this.page.locator('.content-block').first().getByRole('textbox')
  }

  private async revealPanelFor(selector: string) {
    const target = this.page.locator(selector).first()
    const tabId = await target.evaluate(el => el.closest('[role="tabpanel"]')?.getAttribute('aria-labelledby'))
    if (tabId) await this.page.locator(`[id="${tabId}"]`).click()
  }

  async showContent() {
    await this.revealPanelFor('.content-block')
  }

  async showFields() {
    await this.revealPanelFor('.document-field')
  }

  async expectLoaded(templateName: string) {
    await expect(this.page.locator('.title-input')).toBeVisible()
    await expect(this.page.locator('.editor-subtitle')).toContainText(templateName)
    await this.showContent()
    await expect(this.firstContentBlock()).toBeVisible()
  }

  async fillFirstContentAndWaitForSave(text: string) {
    await this.showContent()
    const saved = this.page.waitForResponse((response) => response.url().includes('/api/document-projects/')
      && response.request().method() === 'PUT'
      && response.status() === 200)
    await this.firstContentBlock().fill(text)
    await saved
    await expect(this.page.getByRole('status')).toContainText(/已保存|Saved/)
    await expect(this.page.getByRole('dialog')).toHaveCount(0)
  }

  async fillFieldAndWaitForSave(key: string, value: string) {
    await this.showFields()
    const saved = this.page.waitForResponse((response) => response.url().includes('/api/document-projects/')
      && response.request().method() === 'PUT'
      && response.status() === 200)
    await this.page.locator(`[data-test="document-field-${key}"]`).fill(value)
    await saved
    await expect(this.page.getByRole('status')).toContainText(/已保存|Saved/)
    await expect(this.page.getByRole('dialog')).toHaveCount(0)
  }

  async reload(templateName: string) {
    await this.page.reload()
    await this.expectLoaded(templateName)
  }

  async firstContentValue() {
    await this.showContent()
    return this.firstContentBlock().inputValue()
  }

  async validate() {
    const validated = this.page.waitForResponse((response) => response.url().includes('/validate')
      && response.request().method() === 'POST'
      && response.status() === 200)
    await this.page.getByRole('button', { name: /检查当前内容|Check current content/ }).click()
    await validated
    await expect(this.page.locator('.validation-panel')).toBeVisible()
  }

  async expectFinding(description: string) {
    await expect(this.page.locator('.finding-list')).toContainText(description)
  }

  async expectNoFindings() {
    await expect(this.page.locator('.validation-panel')).toContainText(/未发现问题|No issues found/)
  }

  async exportDocx(): Promise<Download> {
    const drawer = this.page.getByRole('dialog')
    if (await drawer.isVisible()) {
      await drawer.getByRole('button', { name: /^(关闭|Close)$/ }).click()
      await expect(drawer).toHaveCount(0)
    }
    const download = this.page.waitForEvent('download')
    await this.page.getByRole('button', { name: /导出 DOCX|Export DOCX/ }).click()
    return download
  }
}
