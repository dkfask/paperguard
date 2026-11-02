<template>
  <section v-if="specification" class="writing-specification" data-test="writing-specification">
    <div class="spec-heading">
      <div><h3>{{ label.title }}</h3><p>{{ label.detail }}</p></div>
      <button v-if="markdown || templateId" class="small" type="button" :disabled="downloading" @click="downloadMarkdown">{{ label.download }}</button>
    </div>
    <p class="spec-counts">{{ specification.rules.length }} {{ label.rules }} · {{ specification.review_items.length }} {{ label.reviews }}</p>
    <p class="spec-note">{{ label.pending }}</p>
    <p v-if="downloadError" role="alert">{{ downloadError }}</p>
    <section v-if="selectedNodes.length" ref="evidencePanel" class="spec-source" aria-live="polite" data-test="specification-source">
      <div class="spec-heading"><h4>{{ label.source }}</h4><button type="button" class="small" @click="selectedNodeIds = []">{{ label.close }}</button></div>
      <article v-for="node in selectedNodes" :key="node.node_id">
        <p>{{ roleLabel(node.role) }} · {{ node.part }}</p>
        <blockquote v-if="node.text">{{ node.text }}</blockquote>
        <p v-else>{{ label.nonText }}</p>
        <details><summary>{{ label.location }}</summary><code>{{ node.path }}</code><pre>{{ JSON.stringify(node.properties, null, 2) }}</pre><pre v-if="Object.keys(node.property_sources || {}).length">{{ JSON.stringify(node.property_sources, null, 2) }}</pre></details>
      </article>
    </section>
    <div v-if="!specification.rules.length" class="spec-note">{{ label.empty }}</div>
    <details v-for="group in groups" :key="group.role" class="spec-group" :open="openGroups.has(group.role)" @toggle="toggleGroup(group.role, $event)">
      <summary>{{ roleLabel(group.role) }} <span>{{ group.rules.length }}</span></summary>
      <div v-if="openGroups.has(group.role)" class="spec-rule-list">
        <article v-for="rule in group.rules" :key="rule.id" class="spec-rule">
          <div class="spec-rule-heading"><strong>{{ propertyLabel(rule.property) }}</strong><span :class="{ 'needs-review': rule.confidence === 'conflict' }">{{ confidenceLabel(rule.confidence) }}</span></div>
          <p>{{ formatValue(rule.expected, rule.property) }}</p>
          <p v-if="rule.scope.region_title" class="spec-note">{{ english ? 'Applies to' : '适用范围' }}：{{ rule.scope.region_title }}</p>
          <p v-if="rule.confidence === 'conflict'" class="spec-note">{{ label.variation }}</p>
          <p v-if="['image', 'table'].includes(group.role)" class="spec-note">{{ label.instance }}</p>
          <div class="spec-evidence-actions">
            <span>{{ rule.supporting_count }}/{{ rule.observed_count }} {{ label.samples }}</span>
            <button class="small" type="button" @click="showEvidence(rule)">{{ label.source }}</button>
          </div>
        </article>
      </div>
    </details>
    <details class="spec-review"><summary>{{ label.reviews }}（{{ specification.review_items.length }}）</summary>
      <ul><li v-for="(item, index) in specification.review_items" :key="`${item.code}:${index}`">{{ item.message }} <button v-if="item.node_ids.length" type="button" class="small" @click="showNodes(item.node_ids)">{{ label.source }}</button></li></ul>
    </details>
    <details class="spec-review"><summary>{{ label.coverage }}</summary><ul><li v-for="item in specification.coverage?.limitations || []" :key="item">{{ item }}</li></ul><code>{{ specification.source_sha256 }}</code></details>
  </section>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { useI18n } from '../i18n'
import { fetchWritingSpecification } from '../api'

const props = defineProps({ specification: { type: Object, default: null }, markdown: { type: String, default: '' }, templateId: { type: String, default: '' } })
const { lang } = useI18n()
const english = computed(() => lang?.value === 'en')
const label = computed(() => english.value ? {
  title: 'Writing requirements extracted from Word', detail: 'Review requirements by content role and inspect their sources.', download: 'Download requirements', rules: 'candidates', reviews: 'items to review',
  pending: 'These observations require confirmation. They have not been enabled as writing or export checks.', empty: 'No reusable formatting was detected. Review the source document.',
  variation: 'Several formats occur in this region. Confirm the requirement and its exceptions.', instance: 'This is the size of one example, not a universal size requirement.', samples: 'supporting samples',
  source: 'View source', close: 'Close', nonText: 'Document object; inspect its properties below.', location: 'Location and inherited formatting', coverage: 'Coverage and source version',
} : {
  title: '从 Word 抽取的撰写规范', detail: '按内容类型查看要求候选，并核对模板中的来源。', download: '下载规范 Markdown', rules: '条候选', reviews: '项待确认',
  pending: '以下规范尚待确认，当前仅供核对，尚未启用为写作或导出检查规则。', empty: '未发现可复用格式，请核对原模板。',
  variation: '此范围存在多种格式，请确认采用的规则与例外。', instance: '这是单个示例的尺寸，不代表所有同类对象都采用该尺寸。', samples: '个支持样本',
  source: '查看来源', close: '关闭', nonText: '这是文档对象，可展开查看其属性。', location: '具体位置与继承来源', coverage: '能力范围与源文件版本',
})
const roles = { body: '正文', cover: '封面', equation: '公式', chart: '原生图表', heading1: '一级标题', heading2: '二级标题', heading3: '三级标题', heading4: '四级标题',
  abstract: '中文摘要正文', abstract_title: '中文摘要标题', english_abstract: '英文摘要正文', english_abstract_title: '英文摘要标题',
  keywords: '关键词', english_keywords: '英文关键词', toc: '目录标题', toc_entry: '目录条目', table_text: '表格文字', table: '表格',
  image: '图片', figure_caption: '图题', table_caption: '表题', section: '页面与分节', header: '页眉', footer: '页脚', references: '参考文献正文',
  references_title: '参考文献标题', acknowledgement: '致谢正文', acknowledgement_title: '致谢标题', appendix: '附录正文', appendix_title: '附录标题', instruction: '模板中的说明' }
const properties = { 'font.eastAsia': '中文字体', 'font.ascii': '西文字体', 'font.hAnsi': '西文扩展字体', 'font.cs': '复杂文字字体', 'font.size': '字号',
  'font.bold': '加粗', 'font.italic': '斜体', 'font.color': '文字颜色', 'font.underline': '下划线', 'paragraph.align': '对齐',
  'paragraph.spacing.line': '行距', 'paragraph.spacing.before': '段前', 'paragraph.spacing.after': '段后',
  'paragraph.indent.first_line': '首行缩进', 'paragraph.indent.hanging': '悬挂缩进', 'paragraph.indent.left': '左缩进', 'paragraph.indent.right': '右缩进',
  'paragraph.keep_next': '与下段同页', 'paragraph.keep_lines': '段内同页', 'paragraph.page_break_before': '段前分页', 'paragraph.widow_control': '孤行控制',
  'table.width': '表格宽度', 'table.layout': '表格布局', 'image.width': '图片显示宽度', 'image.height': '图片显示高度', 'instruction.text': '原文要求',
  'table.tblBorders': '表格边框', 'table.tblCellMar': '单元格内边距', 'table.tblLook': '表格外观区域', 'table.tblStyle': '表格样式',
  'section.pgSz.w': '页面宽度', 'section.pgSz.h': '页面高度', 'section.pgMar.top': '上边距', 'section.pgMar.bottom': '下边距', 'section.pgMar.left': '左边距',
  'section.pgMar.right': '右边距', 'section.pgMar.header': '页眉距离', 'section.pgMar.footer': '页脚距离', 'section.pgMar.gutter': '装订线',
  'section.header_references': '页眉对应关系', 'section.footer_references': '页脚对应关系', 'section.different_first_page': '首页不同', 'section.pgNumType': '页码格式与起始值', 'section.type': '分节方式' }
const sizes = { 42: '初号', 36: '小初', 26: '一号', 24: '小一', 22: '二号', 18: '小二', 16: '三号', 15: '小三', 14: '四号', 12: '小四', 10.5: '五号', 9: '小五' }
const openGroups = ref(new Set(['body']))
const selectedNodeIds = ref([])
const evidencePanel = ref(null)
const downloading = ref(false)
const downloadError = ref('')
const groups = computed(() => {
  const groupsByRole = new Map()
  for (const rule of props.specification?.rules || []) {
    if (!groupsByRole.has(rule.scope.role)) groupsByRole.set(rule.scope.role, [])
    groupsByRole.get(rule.scope.role).push(rule)
  }
  const order = ['body', 'cover', 'abstract_title', 'abstract', 'english_abstract_title', 'english_abstract', 'heading1', 'heading2', 'heading3', 'figure_caption', 'table_caption', 'table_text', 'table', 'image', 'section', 'header', 'footer', 'instruction']
  const propertyOrder = ['font.eastAsia', 'font.ascii', 'font.size', 'paragraph.spacing.line', 'paragraph.indent.first_line']
  return [...groupsByRole].map(([role, rules]) => ({ role, rules: [...rules].sort((a, b) => {
    const rank = property => { const index = propertyOrder.indexOf(property); return index < 0 ? 100 : index }
    return rank(a.property) - rank(b.property)
  }) })).sort((a, b) => (order.includes(a.role) ? order.indexOf(a.role) : 99) - (order.includes(b.role) ? order.indexOf(b.role) : 99))
})
const selectedNodes = computed(() => (props.specification?.nodes || []).filter(n => selectedNodeIds.value.includes(n.node_id)))
function roleLabel(role) { return english.value ? role.replaceAll('_', ' ') : roles[role] || role }
function propertyLabel(property) { return english.value ? property.replaceAll('.', ' ') : properties[property] || property.replaceAll('.', ' ') }
function confidenceLabel(confidence) {
  return (english.value ? { consistent: 'Repeated examples agree', single: 'Single example', conflict: 'Review differences', needs_review: 'Review instruction' } :
    { consistent: '重复样本一致', single: '单个实例', conflict: '格式有差异', needs_review: '说明待确认' })[confidence] || confidence
}
function formatValue(value, property) {
  if (typeof value === 'boolean') return english.value ? (value ? 'Yes' : 'No') : (value ? '是' : '否')
  if (value && typeof value === 'object') {
    if (value.unit) {
      const unit = english.value ? value.unit : ({ pt: '磅', cm: 'cm', character: '字符', line: '行', multiple: '倍', percent: '%' }[value.unit] || value.unit)
      const prefix = !english.value && property === 'font.size' && sizes[value.value] ? `${sizes[value.value]} · ` : ''
      const kind = english.value ? value.kind : ({ auto: '倍数', exact: '固定值', atLeast: '最小值', preferred: '首选宽度', display: '显示尺寸' }[value.kind] || value.kind)
      return `${prefix}${value.value} ${unit}${kind ? `（${kind}）` : ''}`
    }
    if (property === 'table.width' && ['auto', 'nil'].includes(value.kind)) return english.value ? 'Automatic width' : '自动宽度'
    return english.value ? 'See object properties in source' : '见来源中的对象属性'
  }
  if (typeof value === 'string' && value.startsWith('<')) return english.value ? 'See source properties' : '见来源中的对象属性'
  const translated = { justify: '两端对齐', both: '两端对齐', left: '左对齐', right: '右对齐', center: '居中', fixed: '固定布局', autofit: '自动调整布局' }
  return !english.value && translated[value] ? translated[value] : String(value ?? '')
}
function toggleGroup(role, event) {
  if (event.target.open) openGroups.value.add(role)
  else openGroups.value.delete(role)
}
async function showNodes(nodeIds) {
  selectedNodeIds.value = nodeIds
  await nextTick()
  evidencePanel.value?.scrollIntoView?.({ block: 'nearest' })
}
function showEvidence(rule) { return showNodes(rule.evidence_node_ids) }
async function downloadMarkdown() {
  downloading.value = true
  downloadError.value = ''
  let url
  try {
    const markdown = props.markdown || (await fetchWritingSpecification(props.templateId)).markdown
    url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'writing-specification.md'
    link.click()
  } catch (error) {
    downloadError.value = error.message || (english.value ? 'Download failed' : '下载规范失败')
  } finally {
    if (url) URL.revokeObjectURL(url)
    downloading.value = false
  }
}
</script>

<style scoped>
.writing-specification { display: grid; gap: 12px; padding: 14px; border: 1px solid var(--border); border-radius: 10px; min-width: 0; }
.spec-heading, .spec-rule-heading, .spec-evidence-actions { display: flex; justify-content: space-between; align-items: start; gap: 12px; flex-wrap: wrap; }
.spec-heading h3, .spec-heading h4, .spec-rule p, .spec-heading p, .spec-counts, .spec-note { margin: 0; }
.spec-note, .spec-heading p, .spec-evidence-actions { color: var(--on-surface-variant); font-size: 12px; line-height: 1.6; }
.spec-group, .spec-review { border-top: 1px solid var(--border); padding-top: 10px; min-width: 0; }
summary { cursor: pointer; font-weight: 600; }
summary span { color: var(--on-surface-variant); font-weight: 400; }
.spec-rule-list { display: grid; gap: 10px; padding-top: 10px; }
.spec-rule { display: grid; gap: 6px; padding: 10px; background: var(--surface-container-low); border-radius: 8px; overflow-wrap: anywhere; }
.spec-rule-heading span { font-size: 12px; color: var(--on-surface-variant); }
.spec-rule-heading .needs-review { color: var(--error, #b42318); }
.spec-source { padding: 12px; border: 1px solid var(--border); border-radius: 8px; }
.spec-source article + article { border-top: 1px solid var(--border); }
blockquote { border-left: 3px solid var(--border); margin: 8px 0; padding-left: 10px; white-space: pre-wrap; }
pre { font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
code { font-size: 11px; overflow-wrap: anywhere; }
.spec-review ul { padding-left: 20px; display: grid; gap: 8px; }
</style>
