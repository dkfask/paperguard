import { expect, type Page } from '@playwright/test'

export class MidtermTeacherInboxPage {
  constructor(readonly page: Page) {}

  async login(username: string, password: string) {
    await this.page.goto('/login')
    await this.page.getByLabel('用户名').fill(username)
    await this.page.getByLabel('密码').fill(password)
    await this.page.getByRole('button', { name: /登\s*录/ }).click()
    await expect(this.page).toHaveURL(/\/workspace$/)
    await expect(this.page.locator('.workspace-stats')).toBeVisible()
  }

  async openInbox() {
    await this.page.locator('.workspace-main').getByRole('tab', { name: '过程材料', exact: true }).click()
    await this.page.getByRole('link', { name: '打开收件箱' }).click()
    await expect(this.page).toHaveURL(/panel=materials/)
    await expect(this.page.getByRole('heading', { name: '过程材料待审批' }).first()).toBeVisible()
  }

  recordRow(studentId: string) {
    return this.page.locator('.process-inbox-row').filter({ hasText: studentId })
  }

  async openTeacherFields(studentId: string) {
    await this.recordRow(studentId).getByRole('button', { name: '填写意见/评分' }).click()
    const dialog = this.page.getByRole('dialog', { name: '填写中期检查教师字段' })
    await expect(dialog).toBeVisible()
    return dialog
  }

  async saveTeacherFields(studentId: string, opinion: string, score: number) {
    const dialog = await this.openTeacherFields(studentId)
    await dialog.getByLabel('指导教师意见').fill(opinion)
    await dialog.getByLabel('中期评价分数').fill(String(score))
    await dialog.getByRole('button', { name: '保存教师字段' }).click()
    await expect(dialog).not.toBeVisible()
    await expect(this.page.getByText('教师意见和评分已保存')).toBeVisible()
  }

  async expectNoHorizontalOverflow() {
    const width = await this.page.evaluate(() => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
    }))
    expect(width.scroll).toBeLessThanOrEqual(width.client)
  }
}
