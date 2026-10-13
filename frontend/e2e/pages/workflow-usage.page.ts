import { expect, type Page } from '@playwright/test'
import { PaperGuardPage } from './paperguard.page'

/** Current list → details paths, shared by the three-role usage journey. */
export class WorkflowUsagePage extends PaperGuardPage {
  constructor(page: Page) { super(page) }

  async openCohort(name: string) {
    const summary = this.page.locator('.screen-summary-row').filter({ hasText: name })
    await expect(this.page.locator('.workflow-sync-state')).toHaveCount(0)
    await expect(summary).toHaveCount(1)
    await this.page.getByRole('tab', { name: '批次列表', exact: true }).click()
    const list = this.page.locator('.screen-collection').filter({ has: this.page.locator('.screen-summary-row') }).first()
    await expect(list).toBeVisible()
    for (let step = 0; !await summary.count() && step < 100; step++) {
      const next = list.getByRole('button', { name: '下一页', exact: true })
      if (!await next.count() || !await next.isEnabled()) break
      await next.click()
    }
    await expect(summary).toBeVisible()
    await summary.click()
    const panel = this.page.getByRole('dialog').locator('.cohort-panel')
    await expect(panel).toBeVisible()
    return panel
  }

  async configureCohort(name: string) {
    const panel = await this.openCohort(name)
    await panel.getByRole('button', { name: '配置', exact: true }).click()
    await expect(this.page.getByRole('dialog').locator('.setup-panel')).toBeVisible()
  }

  async openStudentTask(cohort: string) {
    const summary = this.page.locator('.student-task-summary').filter({ hasText: cohort })
    await expect(summary).toBeVisible()
    await summary.click()
    const task = this.page.getByRole('dialog').locator('.student-task')
    await expect(task).toBeVisible()
    return task
  }

  async openReview(title: string) {
    await this.page.goto('/workflow?panel=submissions')
    const row = this.page.locator('.task-row').filter({ hasText: title })
    await expect(this.page.locator('.workflow-sync-state')).toHaveCount(0)
    await expect(row).toHaveCount(1)
    await this.page.getByRole('tab', { name: '待审批', exact: true }).click()
    await expect(row).toBeVisible()
    await row.click()
    const panel = this.page.getByRole('dialog').locator('.review-panel')
    await expect(panel).toBeVisible()
    return panel
  }

  async openHistory(number: string) {
    const collection = this.page.locator('.screen-collection').filter({ has: this.page.locator('.history-row') }).first()
    const row = this.page.locator('.history-row').filter({ hasText: number })
    for (let step = 0; !await row.count() && step < 100; step++) {
      const next = collection.getByRole('button', { name: '下一页', exact: true })
      if (!await next.count() || !await next.isEnabled()) break
      await next.click()
    }
    await expect(row).toBeVisible()
    await row.click()
  }

  async submitTask(task: ReturnType<Page['locator']>) {
    await task.getByRole('button', { name: '提交预检', exact: true }).click()
    const candidate = this.page.locator('.candidate-box').filter({ has: this.page.getByRole('button', { name: '确认正式提交', exact: true }) })
    await expect(candidate).toBeVisible()
    const warning = candidate.locator('input[type=checkbox]')
    if (await warning.count()) await warning.check()
    await this.waitForMutation('/api/submissions', async () => {
      await candidate.getByRole('button', { name: '确认正式提交', exact: true }).click()
    })
    await expect(this.page.locator('.ok-banner')).toContainText('提交成功')
  }
}
