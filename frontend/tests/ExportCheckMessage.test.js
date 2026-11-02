import { describe, expect, it } from 'vitest'
import { exportCheckMessage } from '../src/utils/exportCheckMessage'

describe('export failure guidance', () => {
  it('explains unapplied source template overrides', () => {
    const message = exportCheckMessage({ findings: [{ rule: 'capability.anchor_format_overrides', object: 'template' }] })
    expect(message).toContain('Word 源模板')
    expect(message).toContain('重新导入')
  })

  it('identifies the affected paragraph without printing manuscript values', () => {
    const message = exportCheckMessage({ findings: [{ rule: 'format.font_size', object: 'section:ch1/paragraph:2/run:1', expected: 'private manuscript', actual: 'private text' }] })
    expect(message).toContain('章节“ch1”，第 3 段')
    expect(message).toContain('格式与模板不一致')
    expect(message).not.toContain('private')
  })

  it('keeps errors without a checker report unchanged', () => {
    expect(exportCheckMessage(null)).toBe('')
    expect(exportCheckMessage({ findings: [] })).toBe('')
  })
})
