const MEDIA_TYPES = new Set(['image', 'table', 'equation'])
const MARKS = new Set(['bold', 'italic', 'underline', 'pgHighlight', 'pgSuperscript', 'pgSubscript'])
let fallbackId = 0

export function newParagraphId() {
  const random = globalThis.crypto?.randomUUID?.()
  return random ? `p-${random}` : `p-${Date.now()}-${++fallbackId}-${Math.random().toString(36).slice(2)}`
}

function marksFor(run, block) {
  const marks = []
  for (const name of ['bold', 'italic', 'underline']) {
    if (run[name] || block[name]) marks.push({ type: name })
  }
  const highlight = run.highlight ?? block.highlight
  if (highlight) {
    if (typeof highlight !== 'string') throw new Error('invalid-highlight')
    marks.push({ type: 'pgHighlight', attrs: { color: highlight } })
  }
  const vertical = run.vert_align ?? block.vert_align
  if (vertical && !['superscript', 'subscript'].includes(vertical)) throw new Error('invalid-vertical-alignment')
  if (vertical === 'superscript') marks.push({ type: 'pgSuperscript' })
  if (vertical === 'subscript') marks.push({ type: 'pgSubscript' })
  return marks
}

function textNodes(text, marks) {
  const nodes = []
  const lines = String(text).split('\n')
  lines.forEach((line, index) => {
    if (index) nodes.push({ type: 'hardBreak', ...(marks.length ? { marks } : {}) })
    if (line) nodes.push({ type: 'text', text: line, ...(marks.length ? { marks } : {}) })
  })
  return nodes
}

function paragraphNode(block) {
  const text = String(block.text || '')
  const runs = Array.isArray(block.inline_runs) && block.inline_runs.length ? block.inline_runs : [{ text }]
  if (runs.map(run => run.text).join('') !== text) throw new Error('inline-run-mismatch')
  return {
    type: 'paragraph',
    attrs: { blockId: block.id || null },
    content: runs.flatMap(run => textNodes(run.text, marksFor(run, block))),
  }
}

export function blocksToSectionDocument(blocks) {
  const content = (blocks || []).map(block => {
    if (!block.type || block.type === 'paragraph') return paragraphNode(block)
    if (!MEDIA_TYPES.has(block.type) || !block.id) throw new Error('unsupported-block')
    const title = block[block.type]?.caption || block.equation?.alt_text || ''
    return { type: 'paperMedia', attrs: { blockId: block.id, kind: block.type, title } }
  })
  return { type: 'doc', content: content.length ? content : [{ type: 'paragraph', attrs: { blockId: null } }] }
}

function runFor(node) {
  const run = { text: node.type === 'hardBreak' ? '\n' : node.text, bold: false, italic: false,
    underline: false, highlight: null, vert_align: null }
  if (typeof run.text !== 'string') throw new Error('invalid-text')
  for (const mark of node.marks || []) {
    if (!MARKS.has(mark.type)) throw new Error('unsupported-mark')
    if (['bold', 'italic', 'underline'].includes(mark.type)) run[mark.type] = true
    if (mark.type === 'pgHighlight') {
      if (typeof mark.attrs?.color !== 'string' || !mark.attrs.color) throw new Error('invalid-highlight')
      run.highlight = mark.attrs.color
    }
    if (mark.type === 'pgSuperscript' || mark.type === 'pgSubscript') {
      const value = mark.type === 'pgSuperscript' ? 'superscript' : 'subscript'
      if (run.vert_align && run.vert_align !== value) throw new Error('invalid-vertical-alignment')
      run.vert_align = value
    }
  }
  return run
}

function sameFormat(a, b) {
  return ['bold', 'italic', 'underline', 'highlight', 'vert_align'].every(key => a[key] === b[key])
}

function normalizedRuns(nodes) {
  const runs = []
  for (const node of nodes || []) {
    if (!['text', 'hardBreak'].includes(node.type)) throw new Error('unsupported-inline')
    const next = runFor(node)
    if (!next.text) continue
    const previous = runs.at(-1)
    if (previous && sameFormat(previous, next)) previous.text += next.text
    else runs.push(next)
  }
  return runs
}

function sameRuns(left, right) {
  return left.length === right.length && left.every((run, index) =>
    run.text === right[index].text && sameFormat(run, right[index]))
}

export function sectionDocumentText(document) {
  return (document?.content || []).filter(node => node.type === 'paragraph')
    .map(node => (node.content || []).map(item => item.type === 'hardBreak' ? '\n' : item.text || '').join(''))
    .join('\n\n')
}

export function legacyTextToSectionDocument(text, originalBlocks) {
  const blocks = originalBlocks || []
  const unsafe = blocks.some(block => MEDIA_TYPES.has(block.type) || block.bold || block.italic || block.underline
    || block.highlight || block.vert_align || block.inline_runs?.length)
  if (unsafe) throw new Error('legacy-rich-draft')
  const parts = String(text || '').replace(/\r\n?/g, '\n').split(/\n\s*\n+/)
    .map(part => part.trim()).filter(Boolean)
  const remaining = [...blocks]
  const converted = parts.map(part => {
    const matches = remaining.filter(block => block.text === part)
    const previous = matches.length === 1 ? matches[0] : null
    if (previous) remaining.splice(remaining.indexOf(previous), 1)
    return previous || { id: newParagraphId(), type: 'paragraph', text: part }
  })
  return blocksToSectionDocument(converted)
}

export function sectionDocumentToBlocks(document, originalBlocks) {
  if (document?.type !== 'doc' || !Array.isArray(document.content)) throw new Error('invalid-document')
  const original = new Map((originalBlocks || []).map(block => [block.id, block]))
  const used = new Set()
  return document.content.map(node => {
    const id = node.attrs?.blockId
    const previous = id ? original.get(id) : null
    if (node.type === 'paperMedia') {
      if (!previous || !MEDIA_TYPES.has(previous.type) || previous.type !== node.attrs?.kind || used.has(id)) {
        throw new Error('invalid-media')
      }
      used.add(id)
      return { ...previous }
    }
    if (node.type !== 'paragraph') throw new Error('unsupported-block')
    if (previous && previous.type && previous.type !== 'paragraph') throw new Error('invalid-paragraph')
    const runs = normalizedRuns(node.content)
    if (runs.length > 500) throw new Error('too-many-runs')
    const text = runs.map(run => run.text).join('')
    const keepId = Boolean(previous && !used.has(id))
    if (keepId) {
      used.add(id)
      if (sameRuns(runs, normalizedRuns(paragraphNode(previous).content))) return { ...previous }
    }
    const blockId = keepId ? id : newParagraphId()
    used.add(blockId)
    const hasFormatting = runs.some(run => run.bold || run.italic || run.underline || run.highlight || run.vert_align)
    return { ...(previous && blockId === id ? previous : {}), id: blockId, type: 'paragraph', text,
      bold: false, italic: false, underline: false, highlight: null, vert_align: null,
      inline_runs: hasFormatting ? runs : [] }
  })
}
