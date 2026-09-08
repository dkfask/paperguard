export function splitSectionText(text) {
  return String(text || '')
    .replace(/\r\n?/g, '\n')
    .split(/\n\s*\n+/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
}

export function createTextBlocks(text, sectionKey) {
  const prefix = String(sectionKey || 'section').replace(/[^a-zA-Z0-9_-]/g, '_')
  return splitSectionText(text).map((paragraph, index) => ({
    id: `${prefix}-p-${Date.now()}-${index}`,
    type: 'paragraph',
    text: paragraph,
    bold: false,
    italic: false,
    underline: false,
  }))
}

export function textBlocksToSectionText(paragraphs) {
  return (paragraphs || [])
    .filter((block) => block.type === 'paragraph')
    .map((block) => String(block.text || '').trim())
    .filter(Boolean)
    .join('\n\n')
}

export function mergeTextBlocksWithMedia(textBlocks, existingBlocks) {
  const existing = existingBlocks || []
  const originalTextCount = existing.filter((block) => block.type === 'paragraph').length
  if (!originalTextCount) return [...textBlocks, ...existing.filter((block) => block.type !== 'paragraph')]

  const result = []
  let writtenTextCount = 0
  let originalTextBefore = 0
  for (const block of existing) {
    if (block.type === 'paragraph') {
      originalTextBefore += 1
      continue
    }
    const targetTextCount = Math.round((originalTextBefore / originalTextCount) * textBlocks.length)
    result.push(...textBlocks.slice(writtenTextCount, targetTextCount))
    writtenTextCount = targetTextCount
    result.push(block)
  }
  result.push(...textBlocks.slice(writtenTextCount))
  return result
}
