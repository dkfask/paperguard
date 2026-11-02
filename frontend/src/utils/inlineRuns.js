function formatOf(run) {
  return { bold: Boolean(run.bold), italic: Boolean(run.italic), underline: Boolean(run.underline),
    highlight: run.highlight || null, vert_align: run.vert_align || null }
}

function sameFormat(left, right) {
  return ['bold', 'italic', 'underline', 'highlight', 'vert_align'].every(key => left[key] === right[key])
}

export function reconcileInlineRuns(runs, previousText, nextText) {
  if (!Array.isArray(runs) || !runs.length) return runs || []
  if (runs.map(run => run.text).join('') !== previousText) throw new Error('inline-run-mismatch')
  const oldChars = Array.from(previousText)
  const newChars = Array.from(nextText)
  let prefix = 0
  while (prefix < oldChars.length && prefix < newChars.length && oldChars[prefix] === newChars[prefix]) prefix += 1
  let suffix = 0
  while (suffix < oldChars.length - prefix && suffix < newChars.length - prefix
    && oldChars[oldChars.length - 1 - suffix] === newChars[newChars.length - 1 - suffix]) suffix += 1

  const pieces = []
  const append = (run, text) => {
    if (!text) return
    const next = { ...formatOf(run), text }
    const last = pieces.at(-1)
    if (last && sameFormat(last, next)) last.text += text
    else pieces.push(next)
  }
  const copyRange = (start, end) => {
    let offset = 0
    for (const run of runs) {
      const chars = Array.from(run.text)
      const from = Math.max(start - offset, 0)
      const to = Math.min(end - offset, chars.length)
      if (from < to) append(run, chars.slice(from, to).join(''))
      offset += chars.length
    }
  }
  copyRange(0, prefix)
  let offset = 0
  let insertionRun = runs[0]
  const insertionPosition = Math.max(0, prefix - 1)
  for (const run of runs) {
    offset += Array.from(run.text).length
    if (insertionPosition < offset) { insertionRun = run; break }
  }
  append(insertionRun, newChars.slice(prefix, newChars.length - suffix).join(''))
  copyRange(oldChars.length - suffix, oldChars.length)
  if (pieces.length > 500) throw new Error('too-many-runs')
  return pieces
}
