import { randomBytes } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, test } from '@playwright/test'
import { LoginPage } from './pages/paperguard.page'

test('student completes template writing, checks and downloads a formatted Word file', async ({ page }, info) => {
  test.setTimeout(120_000)
  const stamp = randomBytes(6).toString('hex')
  const username = `writing_${stamp}`
  const password = `Local-${randomBytes(16).toString('hex')}`
  const login = new LoginPage(page)
  await login.createFixtureAccount(username, password, '模板写作回归学生', 'student')
  await login.login(username, password)
  await page.goto('/')
  const title = `模板约束最小闭环-${stamp}`
  await page.getByRole('button', { name: '开始一篇新论文', exact: true }).click()
  await page.locator('.create-thesis-form select').selectOption('default-gb7713')
  await page.locator('.create-thesis-form input').fill(title)
  const creating = page.waitForResponse(response => response.url().endsWith('/api/projects') && response.request().method() === 'POST')
  await page.locator('.create-thesis-form button.primary').click()
  const created = await creating
  expect(created.status()).toBe(201)
  const project = await created.json()
  await expect(page.locator('.writer-shell')).toBeVisible()
  await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)

  // Incomplete work is blocked through the actual export button.
  await page.getByRole('button', { name: '导出', exact: true }).click()
  await expect(page.locator('.missing-panel')).toContainText('封面')
  await expect(page.locator('.missing-panel button')).toHaveCount(1)
  await page.locator('.missing-panel button').click()

  for (const field of project.template_snapshot.meta_fields) {
    await page.locator('.meta-row').filter({ hasText: field.label }).locator('input').fill(field.key === 'title' ? title : `回归${field.label}`)
  }
  await page.getByRole('button', { name: /保存封面信息/ }).click()
  await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)

  const required = project.sections.filter(section => section.required)
  const texts = new Map<string, string>()
  for (const section of required) {
    await page.locator('.nav-title').filter({ hasText: section.title }).first().click()
    const text = section.type.startsWith('english')
      ? `Synthetic writing regression content for ${section.key}.`
      : `模板闭环正文：${section.key}。本段通过网页填写，验证自动保存、格式检查和模板导出。`
    texts.set(section.key, text)
    await page.locator('textarea.para-text').first().fill(text)
    await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)
  }
  await expect(page.locator('.stats-value')).toHaveText(`${required.length}/${required.length}`)

  // Delete every paragraph in a required section; a title alone must not pass.
  const abstract = required.find(section => section.type === 'abstract')!
  await page.locator('.nav-title').filter({ hasText: abstract.title }).click()
  await page.locator('.para-block .para-toolbar button.danger').click()
  await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)
  await expect(page.locator('.stats-value')).toHaveText(`${required.length - 1}/${required.length}`)
  const checkingEmpty = page.waitForResponse(response => response.url().endsWith(`/api/projects/${project.id}/template-check`) && response.request().method() === 'GET')
  await page.getByRole('button', { name: '检查', exact: true }).first().click()
  const emptyCheck = await (await checkingEmpty).json()
  expect(emptyCheck.findings.some(finding => finding.rule_id.endsWith('abstract.empty'))).toBe(true)
  await expect(page.locator('.check-list')).toContainText('模板要求的章节尚未填写: 摘要')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await page.getByRole('button', { name: '导出', exact: true }).click()
  await expect(page.locator('.missing-panel')).toContainText('摘要')
  await page.locator('.missing-panel button').click()
  await page.locator('.insert-row button.primary').first().click()
  await page.locator('textarea.para-text').first().fill(texts.get(abstract.key)!)
  await expect(page.locator('.writer-save-status')).not.toHaveClass(/pending/)

  await page.reload()
  await page.locator('.nav-title').filter({ hasText: abstract.title }).click()
  await expect(page.locator('textarea.para-text').first()).toHaveValue(texts.get(abstract.key)!)
  const checking = page.waitForResponse(response => response.url().endsWith(`/api/projects/${project.id}/template-check`) && response.request().method() === 'GET')
  await page.getByRole('button', { name: '检查', exact: true }).first().click()
  const check = await (await checking).json()
  expect(check.findings.filter(finding => finding.rule_id.startsWith('TPL.') && finding.severity === 'error')).toEqual([])
  await expect(page.locator('.writer-review-action')).toBeEnabled()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)

  const downloading = page.waitForEvent('download')
  await page.getByRole('button', { name: '导出', exact: true }).click()
  const download = await downloading
  expect(download.suggestedFilename()).toContain(title)
  const file = info.outputPath('template-writing.docx')
  await download.saveAs(file)
  const localPython = resolve('../.venv/bin/python')
  const python = process.env.E2E_PYTHON_BIN || (existsSync(localPython) ? localPython : 'python3.12')
  const inspected = JSON.parse(execFileSync(python, ['-c', `
import json, sys
from docx import Document
from docx.oxml.ns import qn
doc = Document(sys.argv[1])
text = '\\n'.join(p.text for p in doc.paragraphs)
body = next(p for p in doc.paragraphs if p.text.startswith('模板闭环正文：ch1。'))
run = body.runs[0]
heading = next(p for p in doc.paragraphs if p.text == '第一章 绪论')
section = doc.sections[-1]
print(json.dumps({'text': text, 'body_size': run.font.size.pt,
 'body_font_zh': run._element.rPr.rFonts.get(qn('w:eastAsia')),
 'body_spacing': body.paragraph_format.line_spacing,
 'heading_style': heading.style.name,
 'margins': [round(getattr(section, k).cm, 2) for k in ['top_margin','bottom_margin','left_margin','right_margin']],
 'toc': 'TOC' in doc.element.xml,
 'page': any('PAGE' in s.footer._element.xml for s in doc.sections)}))
`, file], { encoding: 'utf8' }))
  for (const text of texts.values()) expect(inspected.text).toContain(text)
  expect(inspected.text).toContain(title)
  expect(inspected.body_size).toBe(project.template_snapshot.styles.body.size_pt)
  expect(inspected.body_font_zh).toBe(project.template_snapshot.styles.body.font_zh)
  expect(inspected.body_spacing).toBe(project.template_snapshot.styles.body.line_spacing)
  expect(inspected.heading_style).toBe('Heading 1')
  expect(inspected.margins).toEqual([2.54, 2.54, 3.17, 3.17])
  expect(inspected.toc).toBe(true)
  expect(inspected.page).toBe(true)
  await info.attach('template-writing.docx', { path: file, contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
  await page.screenshot({ path: info.outputPath('writing-complete.png'), fullPage: true })
})
