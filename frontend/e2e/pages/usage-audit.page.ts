import { expect, type Page, type TestInfo } from '@playwright/test'
import { writeFile } from 'node:fs/promises'

/** Read-only audits; reports contain selectors and visible UI, never storage or credentials. */
export class UsageAuditPage {
  constructor(private readonly page: Page, private readonly info: TestInfo) {}

  async useSession(token: string, user: Record<string, unknown>) {
    await this.page.addInitScript(({ token, user }) => {
      sessionStorage.setItem('pg_token', token)
      sessionStorage.setItem('pg_user', JSON.stringify(user))
      localStorage.setItem('paperguard_lang', 'zh')
    }, { token, user })
  }

  async open(route: string) {
    await this.page.goto(route)
    await expect.poll(() => new URL(this.page.url()).pathname).toBe(route.split('?')[0])
    await expect(this.page.locator('h1').first()).toBeVisible()
    await expect(this.page.locator('.app-error-boundary')).toHaveCount(0)
    await expect(this.page.locator('.workflow-sync-state')).toHaveCount(0)
    await expect(this.page.locator('.async-state.loading')).toHaveCount(0)
  }

  async openThesisDialog() {
    await this.page.getByRole('button', { name: '开始一篇新论文', exact: true }).first().click()
    await expect(this.page.getByRole('dialog')).toBeVisible()
  }

  async snapshot(name: string) {
    const path = this.info.outputPath(`${name}.png`)
    await this.page.screenshot({ path, fullPage: true, animations: 'disabled' })
    await this.info.attach(name, { path, contentType: 'image/png' })
  }

  async scan(name: string, scannerPath: string) {
    await this.page.addScriptTag({ path: scannerPath })
    const report = await this.page.evaluate(async () => {
      const win = window as unknown as { axe: { run: (context: object, options: object) => Promise<any> } }
      const result = await win.axe.run(document.querySelector('[role="dialog"]') || document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
      })
      return {
        engine: result.testEngine,
        violations: result.violations.map((rule: any) => ({
          id: rule.id, impact: rule.impact, description: rule.description, help: rule.help,
          nodes: rule.nodes.map((node: any) => ({ target: node.target, failureSummary: node.failureSummary })),
        })),
        incomplete: result.incomplete.map((rule: any) => ({ id: rule.id, impact: rule.impact, targets: rule.nodes.map((node: any) => node.target) })),
        passedRules: result.passes.length,
      }
    })
    expect(report.passedRules).toBeGreaterThan(0)
    const path = this.info.outputPath(`${name}.json`)
    await writeFile(path, JSON.stringify(report, null, 2))
    await this.info.attach(name, { path, contentType: 'application/json' })
    expect(report.violations, `${name}: WCAG violations after repair`).toEqual([])
    return report
  }

  async checkThesisDialogKeyboard() {
    const dialog = this.page.getByRole('dialog')
    for (const key of ['Tab', 'Shift+Tab']) {
      for (let step = 0; step < 8; step++) {
        await this.page.keyboard.press(key)
        expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true)
      }
    }
    await this.page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(this.page.getByRole('button', { name: '开始一篇新论文', exact: true }).first()).toBeFocused()
  }
}
