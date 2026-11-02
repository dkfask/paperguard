import { describe, expect, it } from 'vitest'
import { blocksToSectionDocument, sectionDocumentText, sectionDocumentToBlocks } from '../src/utils/sectionDocument.js'

function sample() {
  return [
    { id: 'p1', type: 'paragraph', text: '温度 H2O', bold: false, inline_runs: [
      { text: '温度 H', bold: true, italic: false, underline: false, highlight: null, vert_align: null },
      { text: '2', bold: false, italic: false, underline: false, highlight: null, vert_align: 'subscript' },
      { text: 'O', bold: false, italic: true, underline: true, highlight: 'yellow', vert_align: null },
    ] },
    { id: 'image1', type: 'image', image: { url: '/media/figure.png', caption: '实验装置', width_cm: 6 } },
    { id: 'p2', type: 'paragraph', text: '图片后\n第二行', bold: true },
    { id: 'table1', type: 'table', table: { rows: 1, cols: 1, cells: [['数据']], source_xml: '<source />' } },
    { id: 'p3', type: 'paragraph', text: '最后一段' },
  ]
}

describe('structured section editor', () => {
  it('round-trips unchanged text, formatting, media metadata and order', () => {
    const blocks = sample()
    const document = blocksToSectionDocument(blocks)
    expect(document.content.map(node => node.type)).toEqual(['paragraph', 'paperMedia', 'paragraph', 'paperMedia', 'paragraph'])
    expect(sectionDocumentToBlocks(document, blocks)).toEqual(blocks)
    expect(sectionDocumentText(document)).toBe('温度 H2O\n\n图片后\n第二行\n\n最后一段')
  })

  it('keeps the original block id and unaffected formatting after a text edit', () => {
    const blocks = sample()
    const document = blocksToSectionDocument(blocks)
    document.content[0].content[0].text = '温度与湿度 H'
    const result = sectionDocumentToBlocks(document, blocks)
    expect(result.map(block => block.id)).toEqual(['p1', 'image1', 'p2', 'table1', 'p3'])
    expect(result[0].text).toBe('温度与湿度 H2O')
    expect(result[0].inline_runs).toMatchObject([
      { text: '温度与湿度 H', bold: true },
      { text: '2', vert_align: 'subscript' },
      { text: 'O', italic: true, underline: true, highlight: 'yellow' },
    ])
    expect(result[1]).toEqual(blocks[1])
    expect(result[3]).toEqual(blocks[3])
  })

  it('assigns a new id to a split or pasted paragraph and follows visible media order', () => {
    const blocks = sample()
    const document = blocksToSectionDocument(blocks)
    const split = structuredClone(document.content[0])
    split.content = [{ type: 'text', text: '新增段落' }]
    document.content.splice(1, 0, split)
    const result = sectionDocumentToBlocks(document, blocks)
    expect(result[0].id).toBe('p1')
    expect(result[1].id).not.toBe('p1')
    expect(result[1].text).toBe('新增段落')
    expect(result.slice(2).map(block => block.id)).toEqual(['image1', 'p2', 'table1', 'p3'])
    expect(new Set(result.map(block => block.id)).size).toBe(result.length)
  })

  it('rejects forged or repeated media and unsupported inline content', () => {
    const blocks = sample()
    const document = blocksToSectionDocument(blocks)
    document.content.push(structuredClone(document.content[1]))
    expect(() => sectionDocumentToBlocks(document, blocks)).toThrow('invalid-media')
    document.content.pop()
    document.content[0].content[0].marks = [{ type: 'unrecognized' }]
    expect(() => sectionDocumentToBlocks(document, blocks)).toThrow('unsupported-mark')
  })

  it('does not discard inconsistent saved inline formatting', () => {
    expect(() => blocksToSectionDocument([{ id: 'p1', type: 'paragraph', text: '正文', inline_runs: [{ text: '其他' }] }]))
      .toThrow('inline-run-mismatch')
  })
})
