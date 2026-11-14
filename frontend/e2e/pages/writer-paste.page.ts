import { expect, type Locator, type Page } from '@playwright/test'

/** Browser integration coverage using synthetic clipboard events, not the OS clipboard. */
export class WriterPastePage {
  constructor(private readonly page: Page) {}

  private async pasteSample(mixed: boolean) {
    const paragraph = this.page.locator('textarea.para-text').first()
    await paragraph.focus()
    const handled = await paragraph.evaluate(async (element, includeTable) => {
      const input = element as HTMLTextAreaElement
      input.setSelectionRange(input.value.length, input.value.length)
      const transfer = new DataTransfer()
      const urls = includeTable ? ['/icon-shield.png', '/favicon-64.png'] : ['/favicon-192.png']
      for (const [index, url] of urls.entries()) {
        const response = await fetch(url)
        if (!response.ok) throw new Error(`clipboard fixture unavailable: ${url}`)
        transfer.items.add(new File([await response.blob()], `figure-${index}.png`, { type: 'image/png' }))
      }
      if (includeTable) {
        transfer.setData('text/html', '<p>粘贴混合内容</p><img src="first.png"><img src="second.png"><table><tr><td>参数甲</td><td>值甲</td></tr><tr><td>参数乙</td><td>值乙</td></tr></table>')
        transfer.setData('text/plain', '粘贴混合内容\n参数甲\t值甲\n参数乙\t值乙')
      }
      const event = new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: transfer })
      input.dispatchEvent(event)
      return event.defaultPrevented
    }, mixed)
    expect(handled).toBe(true)
  }

  private figures() {
    return this.page.locator('.para-block').filter({ has: this.page.locator('.image-block') })
  }

  private async expectValues(locator: Locator, values: string[]) {
    await expect.poll(() => locator.evaluateAll(
      elements => elements.map(element => (element as HTMLInputElement).value),
    )).toEqual(values)
  }

  async exerciseMixedPaste() {
    await expect(this.figures()).toHaveCount(1)
    await this.pasteSample(true)
    await expect(this.figures()).toHaveCount(3)
    await expect(this.figures().locator('.image-preview img')).toHaveCount(3)
    await this.figures().nth(0).locator('.caption-input').fill('粘贴图甲')
    await this.figures().nth(1).locator('.caption-input').fill('粘贴图乙')
    await this.figures().nth(1).getByRole('button', { name: '↑', exact: true }).click()
    await this.expectValues(this.figures().locator('.caption-input'), ['粘贴图乙', '粘贴图甲', '系统结构示意图'])
    await this.figures().nth(1).getByRole('button', { name: '✖', exact: true }).click()
    await expect(this.figures()).toHaveCount(2)
    await this.pasteSample(false)
    await expect(this.figures()).toHaveCount(3)
    await this.figures().nth(0).locator('.caption-input').fill('粘贴图丙')
    await this.assertPersisted()
  }

  async assertPersisted() {
    await this.expectValues(this.figures().locator('.caption-input'), ['粘贴图丙', '粘贴图乙', '系统结构示意图'])
    await expect(this.figures().locator('.caption-num')).toHaveText(['图 4.1', '图 4.2', '图 4.3'])
    await this.expectValues(this.page.locator('.table-block').first().locator('.cell-grid input'), ['参数甲', '值甲', '参数乙', '值乙'])
    await expect(this.page.locator('textarea.para-text').first()).toHaveValue(/粘贴混合内容/)
    await expect(this.page.locator('.writer-save-status')).not.toHaveClass(/pending/)
  }
}
