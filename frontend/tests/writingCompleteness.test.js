import { describe, expect, it } from 'vitest'
import { affectedHeadingDescendants, findHeadingHierarchyIssues, isWritingSectionComplete, mergeWritingChecks } from '../src/utils/writingCompleteness'

describe('template writing completion', () => {
  it('keeps template failures visible alongside rule and policy findings', () => {
    const report = { summary: { total: 1, warning: 1, hard_failures: 0, not_checked: 2 }, findings: [{ rule_id: 'rule-1' }], not_checked: [{ id: 'manual' }] }
    const merged = mergeWritingChecks(report, {
      summary: { total: 1, error: 1 }, context: { template_name: '学校模板' },
      findings: [{ rule_id: 'TPL.abstract.empty', severity: 'error', location: { section_key: 'abstract' } }],
    })
    expect(merged.summary).toMatchObject({ total: 2, error: 1, warning: 1, hard_failures: 1, not_checked: 2 })
    expect(merged.findings[1]).toMatchObject({ source_type: 'template', section_key: 'abstract', fixable: false })
    expect(merged.not_checked).toEqual(report.not_checked)
    expect(report.summary.total).toBe(1)
  })

  it('requires content even after all paragraphs have been deleted', () => {
    const section = { key: 'abstract', type: 'abstract', title: '摘要', paragraphs: [] }
    expect(isWritingSectionComplete(section, [section])).toBe(false)
    section.paragraphs.push({ type: 'paragraph', text: '  ' })
    expect(isWritingSectionComplete(section, [section])).toBe(false)
    section.paragraphs[0].text = '真实摘要正文'
    expect(isWritingSectionComplete(section, [section])).toBe(true)
  })

  it('accepts child text for a chapter without counting empty child titles', () => {
    const sections = [
      { key: 'ch1', type: 'heading1', title: '第一章', paragraphs: [] },
      { key: 'ch1_1', type: 'heading2', title: '1.1 背景', paragraphs: [] },
      { key: 'ch2', type: 'heading1', title: '第二章', paragraphs: [{ type: 'paragraph', text: '下一章的正文' }] },
    ]
    expect(isWritingSectionComplete(sections[0], sections)).toBe(false)
    sections[1].paragraphs.push({ type: 'paragraph', text: '本章正文' })
    expect(isWritingSectionComplete(sections[0], sections)).toBe(true)
    sections[0].title = ' '
    expect(isWritingSectionComplete(sections[0], sections)).toBe(false)
  })

  it('requires actual media content and accepts a numeric zero in a table', () => {
    const section = { key: 'body', type: 'body', title: '正文', paragraphs: [{ type: 'table', table: { cells: [['']] } }] }
    expect(isWritingSectionComplete(section, [section])).toBe(false)
    section.paragraphs[0].table.cells[0][0] = 0
    expect(isWritingSectionComplete(section, [section])).toBe(true)
  })
})

describe('heading hierarchy', () => {
  it('reports missing parents, resets at special sections, and allows appendix subsections', () => {
    const sections = [
      { key: 'orphan', type: 'heading2', title: '无上级' },
      { key: 'child', type: 'heading3', title: '无有效上级' },
      { key: 'chapter', type: 'heading1', title: '第一章' },
      { key: 'jump', type: 'heading3', title: '跳级' },
      { key: 'section', type: 'heading2', title: '小节' },
      { key: 'nested', type: 'heading3', title: '正常子节' },
      { key: 'refs', type: 'references', title: '参考文献' },
      { key: 'after-refs', type: 'heading2', title: '参考文献小节' },
      { key: 'new-abstract', type: 'abstract', title: '摘要' },
      { key: 'after-abstract', type: 'heading2', title: '无父节' },
      { key: 'appendix', type: 'appendix', title: '附录' },
      { key: 'appendix-section', type: 'heading2', title: '附录小节' },
      { key: 'blank', type: 'heading2', title: '  ' },
      { key: 'after-blank', type: 'heading3', title: '空标题之后' },
    ]
    expect(findHeadingHierarchyIssues(sections).map(item => item.key)).toEqual([
      'orphan', 'child', 'jump', 'after-abstract', 'after-blank',
    ])
  })

  it('lists descendants affected by changing a parent without consuming the next peer', () => {
    const sections = [
      { key: 'chapter', type: 'heading1', title: '第一章' },
      { key: 'section', type: 'heading2', title: '1.1' },
      { key: 'child-a', type: 'heading3', title: '1.1.1' },
      { key: 'child-b', type: 'heading3', title: '1.1.2' },
      { key: 'next', type: 'heading2', title: '1.2' },
      { key: 'next-child', type: 'heading3', title: '1.2.1' },
    ]
    expect(affectedHeadingDescendants(sections, 'section').map(item => item.key)).toEqual(['child-a', 'child-b'])
  })
})
