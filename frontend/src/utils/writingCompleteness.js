function hasContent(section) {
  return (section.paragraphs || []).some(block => {
    if (block.type === 'image') return Boolean(block.image?.url)
    if (block.type === 'table') return (block.table?.cells || []).flat().some(cell => String(cell ?? '').trim())
    return Boolean((block.text || '').trim())
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
