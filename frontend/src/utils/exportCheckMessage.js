/** Explain the public checker projection without exposing manuscript values. */
export function exportCheckMessage(report) {
  const finding = report?.findings?.[0]
  if (!finding) return ''
  const rule = String(finding.rule || '')
  const object = String(finding.object || '')
  const figure = object.match(/figure:([^/]+)/)
  const section = object.match(/section:([^/]+)/)
  const paragraph = object.match(/paragraph:(\d+)/)
  const location = [
    figure ? `图 ${figure[1]}` : '',
    section ? `章节“${section[1]}”` : (!figure ? '文档' : ''),
    paragraph ? `第 ${Number(paragraph[1]) + 1} 段` : '',
  ].filter(Boolean).join('，')
  let advice
  if (rule === 'capability.anchor_format_overrides') {
    advice = '额外格式尚未应用到源模板。请导师在 Word 源模板中落实格式要求后重新导入。'
  } else if (rule === 'master.smartart_label_size') {
    advice = '图中标注文字字号大于图题字号，违反“图中标注符号和文字的字号不大于图题的字号”要求。请在 Word 中将图内标注调至不大于图题字号（如五号/10.5pt）后重新导入或提交。'
  } else if (rule.includes('chart')) {
    advice = '包含当前无法核验的 Office 图表。请导师调整模板或选择受支持的模板。'
  } else if (rule.startsWith('capability.')) {
    advice = '包含当前无法核验的格式或内容。请导师调整模板后重试。'
  } else if (rule.startsWith('format.')) {
    advice = '实际文件格式与模板不一致。请重新导出；若再次出现，请导师检查该处模板样式。'
  } else if (rule.startsWith('content.') || rule.startsWith('structure.')) {
    advice = '内容、顺序或结构未通过核对。请检查对应章节内容和模板后重新导出。'
  } else {
    advice = '无法证明导出文件符合模板。请重新导出；若再次出现，请导师检查模板。'
  }
  return `${location}：${advice}`
}
