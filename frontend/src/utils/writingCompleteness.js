import { formatReference } from './references.js'

function hasContent(section) {
  return (section.paragraphs || []).some(block => {
    if (block.type === 'image') {
      return Boolean(block.image?.url || (block.image?.smartart && Object.keys(block.image.smartart).length))
    }
    if (block.type === 'table') return (block.table?.cells || []).flat().some(cell => String(cell ?? '').trim())
    return Boolean((block.reference ? formatReference(block.reference) : block.text || '').trim())
  })
}

export function isWritingSectionComplete(section, sections) {
  if (!section) return false
  if (section.type === 'cover' || section.type === 'toc') return true
  if (!(section.title || '').trim()) return false
  if (hasContent(section)) return true
  if (section.type !== 'heading1') return false
  const index = sections.findIndex(item => item.key === section.key)
  if (index < 0) return false
  for (const child of sections.slice(index + 1)) {
    if (child.type === 'heading1') break
    if (['heading2', 'heading3'].includes(child.type) && (child.title || '').trim() && hasContent(child)) return true
  }
  return false
}

export function mergeWritingChecks(report, templateReport) {
  const findings = templateReport.findings.map(finding => ({
    ...finding,
    section_key: finding.location?.section_key,
    source_type: 'template',
    source: templateReport.context.template_name,
    enforce: finding.severity === 'error' ? 'hard' : 'soft',
    fixable: false,
  }))
  const summary = { ...report.summary }
  for (const key of ['total', 'error', 'warning', 'info']) {
    summary[key] = (summary[key] || 0) + (templateReport.summary[key] || 0)
  }
  summary.hard_failures = (summary.hard_failures || 0) + (templateReport.summary.error || 0)
  return { ...report, summary, findings: [...report.findings, ...findings] }
}

export function findHeadingHierarchyIssues(sections = []) {
  const issues = []
  let hasChapter = false
  let hasSection = false
  for (const section of sections) {
    const title = String(section.title || '').trim()
    if (section.type === 'heading1') {
      hasChapter = Boolean(title)
      hasSection = false
    } else if (section.type === 'heading2') {
      if (title && !hasChapter) issues.push({ key: section.key, title, missing: 'heading1' })
      hasSection = Boolean(title) && hasChapter
    } else if (section.type === 'heading3') {
      if (title && !hasSection) issues.push({ key: section.key, title, missing: hasChapter ? 'heading2' : 'heading1and2' })
    } else if (['references', 'acknowledgement', 'appendix'].includes(section.type)) {
      hasChapter = Boolean(title)
      hasSection = false
    } else if (section.type !== 'body') {
      hasChapter = false
      hasSection = false
    }
  }
  return issues
}

export function affectedHeadingDescendants(sections = [], key) {
  const index = sections.findIndex(section => section.key === key)
  const levels = { heading1: 1, heading2: 2, heading3: 3 }
  const parentLevel = levels[sections[index]?.type]
  if (index < 0 || !parentLevel) return []
  const descendants = []
  for (const section of sections.slice(index + 1)) {
    if (section.type === 'body') continue
    const level = levels[section.type]
    if (!level || level <= parentLevel) break
    descendants.push({ key: section.key, title: section.title || section.key })
  }
  return descendants
}
