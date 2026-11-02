const EMPTY_FORMAT = { bold: false, italic: false, underline: false, highlight: null, vert_align: null }
const FORMAT_KEYS = Object.keys(EMPTY_FORMAT)

function sameFormat(left, right) {
  return FORMAT_KEYS.every(key => left[key] === right[key])
}

export function appendClipboardRun(runs, run) {
  if (!run.text) return
  const normalized = { ...EMPTY_FORMAT, ...run }
  const last = runs.at(-1)
  if (last && sameFormat(last, normalized)) last.text += normalized.text
  else runs.push(normalized)
}

export function clipboardImageFiles(clipboard) {
  const items = Array.from(clipboard?.items || [])
    .filter(item => item.kind === 'file' && item.type?.startsWith('image/'))
    .map(item => item.getAsFile?.()).filter(Boolean)
  if (items.length) return items
  return Array.from(clipboard?.files || []).filter(file => file.type?.startsWith('image/'))
}

function imageFromDataUrl(source, index) {
  const match = String(source).match(/^data:(image\/(?:png|jpeg|gif|webp))(;base64)?,(.*)$/is)
  if (!match || typeof File === 'undefined') return null
  try {
    const [, mime, base64, payload] = match
    const bytes = base64
      ? Uint8Array.from(atob(payload), character => character.charCodeAt(0))
      : new TextEncoder().encode(decodeURIComponent(payload))
    return new File([bytes], `pasted-image-${index + 1}.${mime.split('/')[1]}`, { type: mime })
  } catch {
    return null
  }
}

function highlightColor(value) {
  const color = String(value || '').toLowerCase().replace(/\s+/g, '')
  if (!color || ['transparent', 'none', 'rgba(0,0,0,0)'].includes(color)) return null
  const colors = {
    yellow: 'yellow', '#ffff00': 'yellow', '#ff0': 'yellow', 'rgb(255,255,0)': 'yellow',
    lime: 'green', '#00ff00': 'green', 'rgb(0,255,0)': 'green',
    cyan: 'cyan', '#00ffff': 'cyan', 'rgb(0,255,255)': 'cyan',
    magenta: 'magenta', '#ff00ff': 'magenta', 'rgb(255,0,255)': 'magenta',
    red: 'red', '#ff0000': 'red', 'rgb(255,0,0)': 'red',
    blue: 'blue', '#0000ff': 'blue', 'rgb(0,0,255)': 'blue',
  }
  if (!colors[color]) throw new Error('unsupported-paste')
  return colors[color]
}

function elementFormat(element, inherited) {
  const tag = element.tagName.toLowerCase()
  const style = element.style
  const format = { ...inherited }
  if (['b', 'strong'].includes(tag)) format.bold = true
  if (['i', 'em'].includes(tag)) format.italic = true
  if (tag === 'u') format.underline = true
  if (tag === 'sup') format.vert_align = 'superscript'
  if (tag === 'sub') format.vert_align = 'subscript'
  if (tag === 'mark') format.highlight = highlightColor(element.getAttribute('data-pg-highlight') || 'yellow')

  const weight = style?.fontWeight?.toLowerCase()
  if (weight) {
    if (['bold', 'bolder'].includes(weight) || (Number(weight) >= 600)) format.bold = true
    else if (['normal', 'lighter'].includes(weight) || (Number(weight) > 0 && Number(weight) <= 500)) format.bold = false
    else throw new Error('unsupported-paste')
  }
  const italic = style?.fontStyle?.toLowerCase()
  if (italic) {
    if (['italic', 'oblique'].includes(italic)) format.italic = true
    else if (italic === 'normal') format.italic = false
    else throw new Error('unsupported-paste')
  }
  const decoration = `${style?.textDecoration || ''} ${style?.textDecorationLine || ''}`.toLowerCase()
  if (decoration.includes('line-through') || decoration.includes('overline')) throw new Error('unsupported-paste')
  if (decoration.includes('underline')) format.underline = true
  if (decoration.trim() === 'none') format.underline = false
  const vertical = style?.verticalAlign?.toLowerCase()
  if (vertical) {
    if (vertical === 'super') format.vert_align = 'superscript'
    else if (vertical === 'sub') format.vert_align = 'subscript'
    else if (vertical === 'baseline') format.vert_align = null
    else throw new Error('unsupported-paste')
  }
  if (style?.display === 'none' || style?.visibility === 'hidden') throw new Error('unsupported-paste')
  if (style?.backgroundColor) format.highlight = highlightColor(style.backgroundColor)
  return format
}

function cellText(cell) {
  if (cell.querySelector('table, img, br, object, svg, math, a[href]')) throw new Error('unsupported-paste')
  if (cell.querySelectorAll('p, div').length > 1) throw new Error('unsupported-paste')
  for (const element of cell.querySelectorAll('*')) {
    if (!['span', 'p', 'div', 'font'].includes(element.tagName.toLowerCase())) throw new Error('unsupported-paste')
    if (!sameFormat(elementFormat(element, EMPTY_FORMAT), EMPTY_FORMAT)) throw new Error('unsupported-paste')
  }
  if (!sameFormat(elementFormat(cell, EMPTY_FORMAT), EMPTY_FORMAT)) throw new Error('unsupported-paste')
  return (cell.textContent || '').replace(/\u00a0/g, ' ')
}

function htmlTable(table) {
  if (table.querySelector('table')) throw new Error('unsupported-paste')
  const rows = Array.from(table.rows || []).filter(row => row.closest('table') === table)
  const matrix = rows.map(row => Array.from(row.cells).map(cell => {
    if (Number(cell.getAttribute('rowspan') || '1') !== 1 || Number(cell.getAttribute('colspan') || '1') !== 1) {
      throw new Error('unsupported-paste')
    }
    return cellText(cell)
  }))
  if (!matrix.length || matrix.some(row => !row.length || row.length !== matrix[0].length)) throw new Error('unsupported-paste')
  return matrix
}

function rejectUnresolvedStyles(document) {
  const semanticProperties = /(?:^|;)\s*(?:font-weight|font-style|text-decoration(?:-line)?|vertical-align|background(?:-color)?|display|visibility)\s*:/i
  for (const style of document.querySelectorAll('style')) {
    const css = (style.textContent || '').replace(/\/\*[\s\S]*?\*\//g, '')
    if (/@(?:media|supports|layer)\b/i.test(css) && semanticProperties.test(css.replace(/[{}]/g, ';'))) {
      throw new Error('unsupported-paste')
    }
    for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = match[1].trim()
      if (selector.startsWith('@') || !semanticProperties.test(match[2])) continue
      try {
        if (document.body.matches(selector) || document.body.querySelector(selector)) throw new Error('unsupported-paste')
      } catch {
        throw new Error('unsupported-paste')
      }
    }
  }
}

function parseHtml(html, files) {
  const document = new DOMParser().parseFromString(html, 'text/html')
  rejectUnresolvedStyles(document)
  const items = []
  let runs = []
  const flush = () => {
    if (runs.length) items.push({ type: 'paragraph', runs })
    runs = []
  }
  const addText = (text, format) => appendClipboardRun(runs, { ...format, text: text.replace(/\u00a0/g, ' ') })
  const walk = (node, format, inBlock = false) => {
    if (node.nodeType === 3) {
      const text = node.nodeValue || ''
      if (!inBlock && !runs.length && !text.trim()) return
      addText(text, format)
      return
    }
    if (node.nodeType !== 1) return
    const tag = node.tagName.toLowerCase()
    if (tag === 'style') return
    if (tag === 'o:p' && !node.textContent.trim()) return
    if (tag === 'img') {
      flush()
      items.push({ type: 'image', source: node.getAttribute('src') || '' })
      return
    }
    if (tag === 'table') {
      flush()
      items.push({ type: 'table', matrix: htmlTable(node) })
      return
    }
    if (tag === 'br') { addText('\n', format); return }
    if (!['p', 'div', 'span', 'font', 'b', 'strong', 'i', 'em', 'u', 'sup', 'sub', 'mark'].includes(tag)) {
      throw new Error('unsupported-paste')
    }
    const nextFormat = elementFormat(node, format)
    const block = ['p', 'div'].includes(tag)
    if (block) flush()
    const start = items.length
    for (const child of node.childNodes) walk(child, nextFormat, block || inBlock)
    if (block) {
      flush()
      if (tag === 'p' && items.length === start) items.push({ type: 'paragraph', runs: [] })
    }
  }
  for (const child of document.body.childNodes) walk(child, EMPTY_FORMAT)
  flush()

  const images = items.filter(item => item.type === 'image')
  const missing = []
  images.forEach((image, index) => {
    image.file = imageFromDataUrl(image.source, index)
    if (!image.file) missing.push(index)
  })
  if (missing.length) {
    if (files.length === missing.length) missing.forEach((index, position) => { images[index].file = files[position] })
    else if (files.length === images.length) missing.forEach(index => { images[index].file = files[index] })
    else throw new Error('unsupported-paste')
  }
  return items
}

function tabularText(text) {
  if (!text.includes('\t')) return null
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  if (lines.at(-1) === '') lines.pop()
  const matrix = lines.map(line => line.split('\t'))
  if (!matrix.length || matrix.some(row => row.length !== matrix[0].length) || matrix[0].length < 2) return null
  return matrix
}

export function parseClipboardContent(clipboard) {
  if (!clipboard) return { native: true, items: [] }
  const html = clipboard.getData?.('text/html') || ''
  const text = clipboard.getData?.('text/plain') || ''
  const files = clipboardImageFiles(clipboard)
  if (html) {
    const items = parseHtml(html, files)
    if (items.length) return { native: false, items }
    if (text) return { native: true, items: [] }
  }
  if (text && files.length) throw new Error('unsupported-paste')
  if (files.length) return { native: false, items: files.map(file => ({ type: 'image', file })) }
  const matrix = tabularText(text)
  if (matrix) return { native: false, items: [{ type: 'table', matrix }] }
  return { native: true, items: [] }
}

export function sliceParagraphRuns(block, start, end) {
  const text = String(block.text || '')
  const original = block.inline_runs?.length ? block.inline_runs : [{ text }]
  if (original.map(run => run.text).join('') !== text) throw new Error('inline-run-mismatch')
  const result = []
  let offset = 0
  for (const run of original) {
    const value = String(run.text || '')
    const from = Math.max(0, start - offset)
    const to = Math.min(value.length, end - offset)
    if (from < to) appendClipboardRun(result, {
      text: value.slice(from, to),
      bold: Boolean(block.bold || run.bold), italic: Boolean(block.italic || run.italic),
      underline: Boolean(block.underline || run.underline), highlight: run.highlight ?? block.highlight ?? null,
      vert_align: run.vert_align ?? block.vert_align ?? null,
    })
    offset += value.length
  }
  return result
}
