import { expect, test, type Page } from '@playwright/test'

// Visual fixtures run in an isolated browser context; no API writes reach the server.
const candidate = {
  candidate_id: 'visual-template', name: '论文模板', version: '1', status: 'needs_confirmation', issues: [],
  definition: {
    mode: 'structured_document', export_strategy: 'auto', fields: [], styles: { heading1: {} },
    sections: [{ key: 'ch1', title: '第一章 研究背景、国内外技术进展以及论文整体设计方案与主要研究工作的详细说明',
      kind: 'heading1', order: 3, style_ref: 'heading1', required: true, allow_user_add: false,
      allow_user_delete: false, repeatable: false,
      children: [{ key: 'ch1_1', title: '国内外研究现状与关键技术的比较分析', kind: 'heading2', order: 0,
        children: [{ key: 'ch1_1_1', title: '具体方法与实现细节', kind: 'heading3', children: [] }] }],
    }],
  },
}

async function visualSession(page: Page) {
  await page.addInitScript(() => {
    sessionStorage.setItem('pg_token', 'visual-fixture')
    sessionStorage.setItem('pg_user', JSON.stringify({ id: 'visual-admin', username: 'visual-admin', role: 'admin', status: 'active' }))
    localStorage.setItem('paperguard_lang', 'zh')
  })
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (!path.startsWith('/api/')) { await route.continue(); return }
    let json: unknown = []
    if (path === '/api/templates') json = [{ id: 'visual-template', name: '学位论文模板（GB/T 7713）', section_count: 3 }]
    if (path === '/api/packs') json = ['default']
    if (path === '/api/templates/parse') json = candidate
    if (path === '/api/templates/parse/confirm') json = { ...route.request().postDataJSON(), status: 'confirmed' }
    if (path === '/api/auth/admin/users') json = [{ id: 'visual-student', username: 'visual-student', display_name: '界面验证学生', role: 'student', status: 'active' }]
    if (path === '/api/templates/import') json = {
      filename: '学位论文格式模板.docx', style_names: ['Heading 1', 'Heading 2', 'Heading 3'],
      styles: Object.fromEntries(['heading1', 'heading2', 'heading3', 'english_abstract', 'keywords', 'english_keywords'].map(key => [key, {
        font_zh: '黑体', font_en: 'Times New Roman', size_pt: 14, bold: true, line_spacing: 1.5,
        space_before_pt: 6, space_after_pt: 6, first_line_indent_chars: 0, align: 'left',
      }])),
    }
    await route.fulfill({ json })
  })
}

async function fitsDialog(page: Page) {
  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog).toBeInViewport({ ratio: 1 })
  await expect.poll(() => dialog.evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(1)
  const bounds = await dialog.boundingBox()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1)
  await expect(dialog.getByRole('button', { name: /^(关闭|Close)$/ })).toBeInViewport({ ratio: 1 })
}

for (const viewport of [{ width: 1280, height: 800 }, { width: 900, height: 600 }, { width: 390, height: 844 }]) {
  test(`short forms fit their content and preserve failed creation at ${viewport.width}`, async ({ page }, info) => {
    await page.setViewportSize(viewport)
    await visualSession(page)
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/projects', route => route.request().method() === 'POST'
      ? route.fulfill({ status: 503, json: { detail: '测试夹具：创建暂时失败，请重试' } })
      : route.fulfill({ json: [] }))
    await page.goto('/')
    const trigger = page.getByRole('button', { name: '开始一篇新论文', exact: true })
    await trigger.click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('button', { name: '新建论文', exact: true })).toBeEnabled()
    await page.getByLabel('论文题目', { exact: true }).fill('测试论文')
    await fitsDialog(page)
    expect((await dialog.boundingBox())!.height).toBeLessThan(viewport.height * .8)
    await page.screenshot({ path: info.outputPath('new-thesis.png') })
    await dialog.getByRole('button', { name: '新建论文', exact: true }).click()
    await expect(dialog.getByRole('alert')).toContainText('创建暂时失败')
    await expect(page.getByLabel('论文题目', { exact: true })).toHaveValue('测试论文')
    await fitsDialog(page)
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(trigger).toBeFocused()

    await page.goto('/audit')
    await page.getByRole('button', { name: '筛选记录', exact: true }).click()
    await fitsDialog(page)
    expect((await dialog.boundingBox())!.height).toBeLessThan(viewport.height * .9)
    await page.screenshot({ path: info.outputPath('audit-filter.png') })
    await page.keyboard.press('Escape')
    await page.goto('/organizations')
    await page.getByRole('button', { name: '新建组织', exact: true }).click()
    await fitsDialog(page)
    await page.keyboard.press('Escape')
    await page.goto('/admin/users')
    await page.locator('tbody').getByRole('button').first().click()
    await fitsDialog(page)
    expect((await dialog.boundingBox())!.height).toBeLessThan(viewport.height * .8)
    expect(errors).toEqual([])
  })

  test(`template previews show complete headings without crowded technical controls at ${viewport.width}`, async ({ page }, info) => {
    await page.setViewportSize(viewport)
    await visualSession(page)
    await page.goto('/document-packages/publish')
    await page.locator('[data-test="template-file"]').setInputFiles({ name: 'outline.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: Buffer.from('visual fixture') })
    await page.locator('[data-test="template-parse"]').click()
    await fitsDialog(page)
    const row = page.locator('[data-test="template-section-ch1"]')
    await expect(row.getByRole('textbox')).toHaveValue(candidate.definition.sections[0].title)
    await expect(row).toContainText('一级标题')
    await expect(page.locator('[data-test="template-section-ch1_1"]')).toContainText('二级标题')
    await expect(page.locator('[data-test="template-section-ch1_1_1"]')).toContainText('三级标题')
    await expect(row.locator('select, input')).toHaveCount(0)
    await expect.poll(() => row.getByRole('textbox').evaluate(el => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1)
    await page.screenshot({ path: info.outputPath('section-preview.png') })
    const confirmation = page.waitForRequest(request => request.url().endsWith('/templates/parse/confirm'))
    await page.locator('[data-test="template-confirm"]').click()
    expect((await confirmation).postDataJSON().definition).toEqual(candidate.definition)
    await expect(page.getByRole('dialog')).toHaveCount(0)

    await page.goto('/templates')
    await page.locator('input[type="file"]').setInputFiles({ name: 'styles.docx', mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', buffer: Buffer.from('visual fixture') })
    await expect(page.locator('.style-preview-item')).toHaveCount(6)
    await expect(page.locator('.style-preview-item[open]')).toHaveCount(0)
    const headings = page.getByRole('region', { name: '标题层级', exact: true })
    const otherStyles = page.getByRole('region', { name: '其他排版样式', exact: true })
    await expect(headings.locator('summary')).toHaveText(['一级标题(章)', '二级标题(节)', '三级标题(小节)'])
    await expect(otherStyles.locator('summary')).toHaveText(['英文摘要', '中文关键词', '英文关键词'])
    await expect(page.getByRole('dialog')).not.toContainText(/english_abstract|english_keywords|\bkeywords\b/)
    await fitsDialog(page)
    await page.screenshot({ path: info.outputPath('style-preview.png'), animations: 'disabled' })
    await page.locator('.style-preview-item summary').first().click()
    await expect(page.getByRole('dialog')).toContainText('Times New Roman')
    await fitsDialog(page)
    await page.screenshot({ path: info.outputPath('style-details.png'), animations: 'disabled' })
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click()
    await page.getByRole('button', { name: 'English', exact: true }).click()
    await page.getByRole('button', { name: 'IMPORT PREVIEW', exact: true }).click()
    await expect(page.getByRole('region', { name: 'Other formatting styles', exact: true }).locator('summary')).toHaveText(['English Abstract', 'Chinese Keywords', 'English Keywords'])
    await fitsDialog(page)
  })
}
