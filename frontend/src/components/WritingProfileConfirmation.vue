<template>
  <section v-if="template.source_docx && template.id" class="profile-confirmation" data-test="writing-profile-confirmation">
    <h3>{{ L('确认规范与母版填充', 'Confirm rules and master layout') }}</h3>
    <p v-if="template.source_execution?.profile_sha256" class="published">{{ L('已发布，新的学生项目会冻结此版本：', 'Published. New projects freeze this version: ') }}{{ template.version }}</p>
    <p>{{ L('正文在母版对应章节内填充；封面、分节和页眉页脚保留。保存修改后，先预览实际执行格式，再确认发布。', 'Content fills mapped regions while the cover, sections and headers remain. Save changes, preview the execution plan, then publish.') }}</p>
    <p v-if="dirty" role="status">{{ L('有尚未保存的修改，请先保存草稿。', 'Save your changes before preparing a preview.') }}</p>
    <details v-if="conflicts.length"><summary>{{ L('选择存在差异的格式', 'Resolve observed variations') }} ({{ conflicts.length }})</summary>
      <label v-for="rule in conflicts" :key="rule.id" class="rule-choice">
        <span>{{ rule.scope.region_title }} · {{ contentRoleLabel(rule.scope.role) }} · {{ propertyLabel(rule.property) }}</span>
        <select :value="selected[rule.id] || JSON.stringify(rule.expected)" @change="choose(rule.id, $event.target.value)">
          <option :value="JSON.stringify(rule.expected)">{{ L('建议：', 'Suggested: ') }}{{ valueLabel(rule.expected) }}</option>
          <option v-for="(alternative, i) in rule.alternatives" :key="i" :value="JSON.stringify(alternative.expected)">{{ valueLabel(alternative.expected) }}</option>
        </select>
      </label>
    </details>
    <label class="ack"><input v-model="removeUnmatchedToc" type="checkbox" />{{ L('如果目录条目没有对应正文标题，允许清理这些失配项；系统会在预览中列出具体条目。', 'Allow removal of TOC entries without a matching body heading. The preview will list each removed entry.') }}</label>
    <label v-if="!candidate" class="ack"><input v-model="preserveManuscriptStructure" data-test="manuscript-structure-policy" type="checkbox" :disabled="busy" @change="proposal = null; accepted = false" />{{ L('导入论文时保留原论文的完整章节层级、标题和顺序；此 Word 提供各级标题、正文及图表版式。', 'Keep imported manuscript headings, levels and order. Use this Word master for heading, text and object styles.') }}</label>
    <label class="ack"><input v-model="bodyPageNumberEnabled" data-test="body-page-number-enabled" type="checkbox" :disabled="busy" @change="invalidatePageNumber" />{{ L('按确认的规则设置正文至论文末尾的页码', 'Apply the confirmed page-number rule from body to end') }}</label>
    <div v-if="bodyPageNumberEnabled" data-test="body-page-number-settings">
      <label class="rule-choice">{{ L('页码字体', 'Page-number font') }}<input v-model.trim="bodyPageNumberFont" maxlength="128" @input="invalidatePageNumber" /></label>
      <label class="rule-choice">{{ L('页码字号（磅）', 'Page-number size (pt)') }}<input v-model.number="bodyPageNumberSize" type="number" min="6" max="36" step="0.5" @input="invalidatePageNumber" /></label>
    </div>
    <label class="ack"><input v-model="coverPaginationEnabled" data-test="cover-pagination-enabled" type="checkbox" :disabled="busy" @change="invalidatePageNumber" />{{ L('按确认的封面规则检查多余空白页', 'Check cover pages against the confirmed blank-page rule') }}</label>
    <div v-if="coverPaginationEnabled" data-test="cover-pagination-settings">
      <label class="ack"><input v-model="coverBoundaryNewPage" data-test="cover-boundary-new-page" type="checkbox" :disabled="busy" @change="invalidatePageNumber" />{{ L('首个内容章节必须从新的一页开始', 'Start the first content section on a new page') }}</label>
      <label class="rule-choice">{{ L('模板明确要求留白的页面：填写相对首个内容章节向前的页数，多个数字用逗号分隔；无留白则不填。', 'Intentional blank pages: enter offsets before the first content section, separated by commas; leave empty for none.') }}
        <input v-model.trim="coverBlankOffsets" data-test="cover-blank-offsets" placeholder="1, 2" :disabled="busy" @input="invalidatePageNumber" />
      </label>
      <p v-if="!coverOffsetsValid" class="error" role="alert">{{ L('请输入不重复的 1–63 整数，用逗号分隔。', 'Enter distinct integers from 1 to 63, separated by commas.') }}</p>
    </div>
    <button data-test="writing-profile-prepare" type="button" class="small" :disabled="busy || dirty || !coverOffsetsValid" @click="prepare">{{ L('预览执行配置', 'Preview execution plan') }}</button>
    <div v-if="proposal" class="execution-preview" data-test="writing-profile-plan">
      <p>{{ L('以下格式来自源稿和已选项。母版中的单个示例尺寸不会作为所有新图片或表格的固定尺寸。', 'The following styles use source evidence and your choices. Individual example dimensions do not become universal object dimensions.') }}</p>
      <p v-if="proposal.trials">{{ L('普通正文和长正文的母版试填检查已通过。', 'Normal and long-content trial checks passed.') }}</p>
      <p v-else role="status">{{ L('请先确认数字字体规则，再重新预览并执行试填检查。', 'Confirm the digit-font rule, then preview again to run the trials.') }}</p>
      <details v-if="plan.heading_semantic_corrections?.length" open data-test="heading-semantic-corrections">
        <summary>{{ L('根据母版编号修正章节层级', 'Heading levels corrected from source numbering') }} ({{ plan.heading_semantic_corrections.length }})</summary>
        <ul><li v-for="item in plan.heading_semantic_corrections" :key="item.key">{{ item.title }} · {{ L('二级调整为三级，归入', 'Level 2 changed to level 3 under') }} {{ plan.headings.find(heading => heading.key === item.parent_key)?.title }}</li></ul>
      </details>
      <details v-if="plan.removed_toc_entries?.length"><summary>{{ L('将清理的失配目录项', 'Unmatched TOC entries to remove') }} ({{ plan.removed_toc_entries.length }})</summary><ul><li v-for="(entry, i) in plan.removed_toc_entries" :key="i">{{ entry }}</li></ul></details>
      <p v-if="plan.cover_pagination" data-test="cover-pagination-preview">{{ L('封面检查：首个内容章节', 'Cover check: first content section') }} {{ plan.headings.find(heading => heading.key === plan.cover_pagination.boundary_key)?.title }} · {{ L('允许留白的相对位置', 'Allowed blank-page offsets') }}: {{ plan.cover_pagination.allowed_blank_offsets.join(', ') || L('无', 'none') }}</p>
      <details><summary>{{ L('封面字段', 'Cover fields') }} ({{ plan.fields.length }})</summary>
        <details v-for="field in plan.fields" :key="field.key"><summary>{{ field.key }}：{{ fontLabel(field.properties) }}</summary><dl><template v-for="(value, key) in field.properties" :key="key"><dt>{{ propertyLabel(key) }}</dt><dd>{{ valueLabel(value) }}</dd></template></dl></details>
      </details>
      <details><summary>{{ L('章节与正文格式', 'Sections and body styles') }} ({{ plan.headings.length }})</summary>
        <details v-for="heading in plan.headings" :key="heading.key"><summary>{{ heading.title }}：{{ fontLabel(heading.properties) }}</summary><dl><template v-for="(value, key) in heading.properties" :key="key"><dt>{{ propertyLabel(key) }}</dt><dd>{{ valueLabel(value) }}</dd></template></dl>
          <div v-if="heading.inline" class="inline-label-settings" :data-test="`inline-label-${heading.key}`">
            <label class="rule-choice">{{ L('成品中的关键词标签', 'Keyword label in output') }}
              <input :value="inlineLabel(heading).text" maxlength="64" @input="setInlineLabel(heading, 'text', $event.target.value)" />
            </label>
            <label class="rule-choice">{{ L('标签后的分隔符', 'Separator after label') }}
              <select :value="inlineLabel(heading).separator" @change="setInlineLabel(heading, 'separator', $event.target.value)">
                <option :value="heading.separator">{{ JSON.stringify(heading.separator) }}</option>
                <option v-if="heading.separator !== '：'" value="：">{{ L('中文冒号', 'Full-width colon') }}</option>
                <option v-if="heading.separator !== ': '" value=": ">{{ L('英文冒号和空格', 'Colon and space') }}</option>
                <option v-if="heading.separator !== ':'" value=":">{{ L('英文冒号', 'Colon') }}</option>
              </select>
            </label>
            <label class="ack"><input type="checkbox" :checked="inlineLabel(heading).bold" @change="setInlineLabel(heading, 'bold', $event.target.checked)" />{{ L('标签及冒号加粗', 'Bold label and colon') }}</label>
          </div>
        </details>
      </details>
      <details><summary>{{ L('新增图表与表题、图题的格式', 'Styles for new objects and captions') }}</summary>
        <details v-for="(properties, role) in plan.object_properties" :key="role"><summary>{{ objectLabel(role) }}：{{ fontLabel(properties) }}</summary><dl><template v-for="(value, key) in properties" :key="key"><dt>{{ propertyLabel(key) }}</dt><dd>{{ valueLabel(value) }}</dd></template></dl></details>
      </details>
      <details v-if="plan.digit_font_review?.candidates?.length" open data-test="digit-font-review">
        <summary>{{ L('数字字体要求', 'Digit-font requirement') }}</summary>
        <label v-for="rule in plan.digit_font_review.candidates" :key="rule.rule_id" class="ack content-rule">
          <input v-model="confirmedDigitFontRules" type="checkbox" :value="rule.rule_id" />
          <span><strong>{{ L('各级标题和正文', 'Headings and body') }}</strong> · {{ rule.source_text }}<small>{{ rule.source_part }} · {{ rule.source_path }}</small></span>
        </label>
      </details>
      <details v-if="plan.content_rule_review" data-test="content-rule-review">
        <summary>{{ L('Word 中明确的内容要求', 'Explicit content requirements') }} ({{ plan.content_rule_review.candidates?.length || 0 }})</summary>
        <p>{{ L('请确认适用的内容要求。文献数量与时效仅作提示，其他要求按对应规则执行。', 'Confirm applicable content requirements. Reference counts and age are advisory.') }}</p>
        <div v-for="rule in plan.content_rule_review.candidates || []" :key="rule.rule_id">
          <label class="ack content-rule">
            <input v-model="confirmedContentRules" type="checkbox" :value="rule.rule_id" />
            <span><strong>{{ rule.region_title || rule.target }}</strong> · {{ rule.source_text }}<small>{{ rule.checker }} · {{ rule.source_path }}</small></span>
          </label>
          <label v-if="rule.requires_defense_date_field && confirmedContentRules.includes(rule.rule_id)" class="rule-choice">
            {{ L('选择封面中表示答辩日期的字段', 'Select the cover field containing the defense date') }}
            <select v-model="defenseDateFieldKey" data-test="defense-date-field">
              <option value="">{{ L('请选择', 'Select') }}</option>
              <option v-for="field in template.meta_fields || []" :key="field.key" :value="field.key">{{ field.label }} ({{ field.key }})</option>
            </select>
          </label>
        </div>
        <p v-if="!contentSelectionMatchesPreview" role="status">{{ L('内容要求选择已变化，请重新预览后再发布。', 'Content-rule choices changed. Preview again before publishing.') }}</p>
        <details v-if="plan.content_rule_review.manual_items?.length"><summary>{{ L('保留人工核查的原文要求', 'Instructions retained for manual review') }} ({{ plan.content_rule_review.manual_items.length }})</summary>
          <ul><li v-for="item in plan.content_rule_review.manual_items" :key="item.rule_id"><strong>{{ item.region_title || L('未定位章节', 'Unmapped section') }}</strong> · {{ item.source_text }}</li></ul>
        </details>
      </details>
      <label class="ack"><input v-model="accepted" type="checkbox" />{{ L('已核对原文要求、差异项和上述执行格式；采用此配置并冻结章节结构。', 'I reviewed the source instructions, variations and execution styles and accept the frozen structure.') }}</label>
      <label class="ack"><input v-model="removeInstructions" type="checkbox" />{{ L('从学生成品移除已识别的模板填写说明和示例正文。', 'Remove identified filling instructions and example body content from student output.') }}</label>
      <label class="ack"><input v-model="acceptObjects" type="checkbox" />{{ L('采用上述图表样式和按章编号；新增图片适应版心并保持比例，表格适应版心并重复表头。未从示例推断全局固定大小。', 'Accept the displayed object styles and chapter-based captions. Images fit the text area and retain their ratio; tables fit and repeat their header. These are explicit layout defaults.') }}</label>
      <p v-if="!inlineLabelsMatchPreview" role="status">{{ L('关键词标签已修改，请重新预览后再发布。', 'Keyword labels changed. Preview again before publishing.') }}</p>
      <p>{{ L('正式交付前须通过系统的排版与成品验证。', 'Final delivery requires system layout and output verification.') }}</p>
      <button data-test="writing-profile-publish" type="button" class="small primary" :disabled="busy || dirty || !accepted || !removeInstructions || !acceptObjects || !contentSelectionMatchesPreview || !inlineLabelsMatchPreview || (plan.removed_toc_entries?.length && !removeUnmatchedToc)" @click="publish">{{ L('确认并发布此版本', 'Confirm and publish version') }}</button>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { proposeWritingProfile, publishWritingProfile, prepareCandidateWritingProfile } from '../api'
import { useI18n } from '../i18n'
const props = defineProps({ template: { type: Object, required: true }, dirty: Boolean, candidate: {type:Object,default:null} })
const emit = defineEmits(['published'])
const { lang } = useI18n()
const L = (zh, en) => lang.value === 'zh' ? zh : en
const busy = ref(false), error = ref(''), proposal = ref(null), selected = ref({})
const accepted = ref(false), removeInstructions = ref(false), acceptObjects = ref(false), removeUnmatchedToc = ref(false)
const confirmedContentRules = ref([...(props.template.source_execution?.confirmed_content_rule_ids || [])])
const confirmedDigitFontRules = ref([...(props.template.source_execution?.confirmed_digit_font_rule_ids || [])])
const inlineLabelOverrides = ref({ ...(props.template.source_execution?.inline_label_overrides || {}) })
const defenseDateFieldKey = ref(props.template.source_execution?.defense_date_field_key || '')
const preserveManuscriptStructure = ref(!!props.template.source_execution?.preserve_manuscript_structure)
const bodyPageNumberEnabled = ref(!!props.template.source_execution?.body_page_number)
const bodyPageNumberFont = ref(props.template.source_execution?.body_page_number?.font || 'Times New Roman')
const bodyPageNumberSize = ref(props.template.source_execution?.body_page_number?.size_pt || 9)
const coverPaginationEnabled = ref(!!props.template.source_execution?.cover_pagination)
const coverBoundaryNewPage = ref(props.template.source_execution?.cover_pagination?.require_boundary_new_page ?? true)
const coverBlankOffsets = ref((props.template.source_execution?.cover_pagination?.allowed_blank_offsets || []).join(', '))
const coverOffsetsValid = computed(() => {
  if (!coverPaginationEnabled.value || !coverBlankOffsets.value) return true
  const parts = coverBlankOffsets.value.split(/[,，]/).map(value => value.trim())
  return parts.every(value => /^[1-9]\d*$/.test(value) && Number(value) <= 63) && new Set(parts.map(Number)).size === parts.length
})
const conflicts = computed(() => (props.template.writing_specification?.rules || []).filter(r => r.confidence === 'conflict' && (r.property.startsWith('font.') || r.property.startsWith('paragraph.'))))
const plan = computed(() => proposal.value?.template.source_execution)
function canonicalInlineLabels(labels) {
  return JSON.stringify(Object.fromEntries(Object.entries(labels || {}).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => [key, { text: value.text, separator: value.separator, bold: value.bold ?? null }])))
}
const inlineLabelsMatchPreview = computed(() => canonicalInlineLabels(inlineLabelOverrides.value) === canonicalInlineLabels(plan.value?.inline_label_overrides))
function inlineLabel(heading) {
  return inlineLabelOverrides.value[heading.key] || { text: heading.display_label || heading.title, separator: heading.separator, bold: !!heading.title_properties?.['font.bold'] }
}
function setInlineLabel(heading, field, value) {
  inlineLabelOverrides.value = { ...inlineLabelOverrides.value, [heading.key]: { ...inlineLabel(heading), [field]: value } }
  accepted.value = false
}
const contentSelectionMatchesPreview = computed(() => {
  const current = [...confirmedContentRules.value].sort()
  const previewed = [...(plan.value?.confirmed_content_rule_ids || [])].sort()
  const digits = [...confirmedDigitFontRules.value].sort()
  const previewedDigits = [...(plan.value?.confirmed_digit_font_rule_ids || [])].sort()
  const requiredDigits = plan.value?.digit_font_review?.candidates || []
  return current.length === previewed.length && current.every((id, index) => id === previewed[index]) && defenseDateFieldKey.value === (plan.value?.defense_date_field_key || '') && digits.length === previewedDigits.length && digits.every((id, index) => id === previewedDigits[index]) && requiredDigits.every((rule) => digits.includes(rule.rule_id))
})
function valueLabel(value) { return typeof value === 'object' ? `${value.value ?? ''} ${{pt:L('磅','pt'),line:L('行','lines'),multiple:L('倍','multiple'),character:L('字符','characters')}[value.unit] ?? value.unit ?? JSON.stringify(value)}` : typeof value === 'boolean' ? (value ? L('是','Yes') : L('否','No')) : String(value) }
function propertyLabel(key) {
  const labels = {'font.eastAsia':'中文字体','font.ascii':'西文字体','font.hAnsi':'西文扩展字体','font.cs':'复杂文字字体','font.size':'字号','font.bold':'加粗','font.italic':'斜体','font.color':'颜色','paragraph.align':'对齐','paragraph.spacing.line':'行距','paragraph.spacing.before':'段前间距','paragraph.spacing.after':'段后间距','paragraph.indent.first_line':'首行缩进','paragraph.indent.left':'左缩进','paragraph.indent.right':'右缩进','paragraph.indent.hanging':'悬挂缩进','paragraph.page_break_before':'段前分页','paragraph.keep_next':'与下段同页','paragraph.keep_lines':'段中不分页','paragraph.widow_control':'孤行控制'}
  return lang.value === 'zh' ? labels[key] || '其他格式属性' : key.replaceAll('.', ' ').replaceAll('_', ' ')
}
function contentRoleLabel(role) {
  const labels = {
    body: '正文', cover: '封面', cover_title: '封面标题', cover_meta: '封面信息',
    abstract: '中文摘要正文', abstract_title: '中文摘要标题', english_abstract: '英文摘要正文',
    english_abstract_title: '英文摘要标题', keywords: '中文关键词', english_keywords: '英文关键词',
    toc: '目录标题', toc_entry: '目录条目', heading1: '一级标题', heading2: '二级标题',
    heading3: '三级标题', heading4: '四级标题', figure_caption: '图题', table_caption: '表题',
    table_text: '表格文字', table: '表格', image: '图片', equation: '公式', chart: '原生图表',
    section: '页面与分节', header: '页眉', footer: '页脚', references: '参考文献正文',
    references_title: '参考文献标题', acknowledgement: '致谢正文', acknowledgement_title: '致谢标题',
    appendix: '附录正文', appendix_title: '附录标题', instruction: '模板中的说明',
  }
  return lang.value === 'zh' ? labels[role] || '其他内容类型' : String(role || '').replaceAll('_', ' ')
}
function objectLabel(role) { return contentRoleLabel(role) }
function fontLabel(p) { return `${p['font.eastAsia'] || ''} / ${p['font.ascii'] || ''} · ${valueLabel(p['font.size'] || {})} · ${L('行距', 'line')} ${valueLabel(p['paragraph.spacing.line'] || {})}` }
function choose(id, value) { selected.value[id] = value; proposal.value = null; accepted.value = false }
function invalidatePageNumber() { proposal.value = null; accepted.value = false }
function confirmation() { return { expected_specification_sha256: props.template.writing_specification?.specification_sha256 || '', overrides: Object.fromEntries(Object.entries(selected.value).map(([k,v]) => [k,JSON.parse(v)])), confirmed_content_rule_ids: [...confirmedContentRules.value], confirmed_digit_font_rule_ids: [...confirmedDigitFontRules.value], inline_label_overrides: { ...inlineLabelOverrides.value }, body_page_number: bodyPageNumberEnabled.value ? { font: bodyPageNumberFont.value, size_pt: bodyPageNumberSize.value } : null, cover_pagination: coverPaginationEnabled.value ? { allowed_blank_offsets: coverBlankOffsets.value ? coverBlankOffsets.value.split(/[,，]/).map(value => Number(value.trim())).sort((a, b) => a - b) : [], require_boundary_new_page: coverBoundaryNewPage.value } : null, defense_date_field_key: defenseDateFieldKey.value, remove_unmatched_toc_entries: removeUnmatchedToc.value, preserve_manuscript_structure: preserveManuscriptStructure.value } }
async function prepare() {
  busy.value = true; error.value = ''; proposal.value = null
  try { proposal.value = props.candidate ? await prepareCandidateWritingProfile(props.candidate,confirmation()) : await proposeWritingProfile(props.template.id, confirmation()); accepted.value = removeInstructions.value = acceptObjects.value = false }
  catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function publish() {
  busy.value = true; error.value = ''
  try {
    const reviewed = { ...confirmation(), proposal_id: proposal.value.proposal_id || '', accept_recommended: accepted.value, remove_template_instructions: removeInstructions.value, accept_new_object_defaults: acceptObjects.value }
    const result = props.candidate ? await prepareCandidateWritingProfile(props.candidate,reviewed,false) : await publishWritingProfile(props.template.id, reviewed)
    emit('published', result); proposal.value = null
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
watch(() => [props.template.id, props.template.updated_at, props.dirty], () => {
  proposal.value = null; accepted.value = false
  confirmedContentRules.value = [...(props.template.source_execution?.confirmed_content_rule_ids || [])]
  confirmedDigitFontRules.value = [...(props.template.source_execution?.confirmed_digit_font_rule_ids || [])]
  inlineLabelOverrides.value = { ...(props.template.source_execution?.inline_label_overrides || {}) }
  defenseDateFieldKey.value = props.template.source_execution?.defense_date_field_key || ''
  preserveManuscriptStructure.value = !!props.template.source_execution?.preserve_manuscript_structure
  bodyPageNumberEnabled.value = !!props.template.source_execution?.body_page_number
  bodyPageNumberFont.value = props.template.source_execution?.body_page_number?.font || 'Times New Roman'
  bodyPageNumberSize.value = props.template.source_execution?.body_page_number?.size_pt || 9
  coverPaginationEnabled.value = !!props.template.source_execution?.cover_pagination
  coverBoundaryNewPage.value = props.template.source_execution?.cover_pagination?.require_boundary_new_page ?? true
  coverBlankOffsets.value = (props.template.source_execution?.cover_pagination?.allowed_blank_offsets || []).join(', ')
})
</script>

<style scoped>
.profile-confirmation { border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin: 16px 0; }
.profile-confirmation p { overflow-wrap: anywhere; }
.published { color: var(--primary); }
.rule-choice { display: grid; gap: 6px; margin: 12px 0; }
.rule-choice select { min-width: 0; max-width: 100%; }
.execution-preview { margin-top: 16px; }
.execution-preview details { margin: 8px 0; }
.execution-preview dl { display: grid; grid-template-columns: minmax(80px, 1fr) minmax(0, 2fr); gap: 5px 12px; font-size: 13px; }
.execution-preview dd { margin: 0; overflow-wrap: anywhere; }
.content-rule { align-items: flex-start; }
.content-rule span { display: grid; gap: 4px; }
.content-rule small { color: var(--muted); overflow-wrap: anywhere; }
.ack { display: flex; align-items: flex-start; gap: 8px; margin: 12px 0; }
.ack input { flex-shrink: 0; margin-top: 4px; }
.error { color: var(--error, #b91c1c); }
</style>
