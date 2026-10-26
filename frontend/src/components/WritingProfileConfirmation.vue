<template>
  <section v-if="template.source_docx && template.id" class="profile-confirmation" data-test="writing-profile-confirmation">
    <h3>{{ L('确认规范与母版填充', 'Confirm rules and master layout') }}</h3>
    <p v-if="template.source_execution?.profile_sha256" class="published">{{ L('已发布，新的学生项目会冻结此版本：', 'Published. New projects freeze this version: ') }}{{ template.version }}</p>
    <p>{{ L('正文在母版对应章节内填充；封面、分节和页眉页脚保留。保存修改后，先预览实际执行格式，再确认发布。', 'Content fills mapped regions while the cover, sections and headers remain. Save changes, preview the execution plan, then publish.') }}</p>
    <p v-if="dirty" role="status">{{ L('有尚未保存的修改，请先保存草稿。', 'Save your changes before preparing a preview.') }}</p>
    <details v-if="conflicts.length"><summary>{{ L('选择存在差异的格式', 'Resolve observed variations') }} ({{ conflicts.length }})</summary>
      <label v-for="rule in conflicts" :key="rule.id" class="rule-choice">
        <span>{{ rule.scope.region_title }} · {{ rule.scope.role }} · {{ rule.property }}</span>
        <select :value="selected[rule.id] || JSON.stringify(rule.expected)" @change="choose(rule.id, $event.target.value)">
          <option :value="JSON.stringify(rule.expected)">{{ L('建议：', 'Suggested: ') }}{{ valueLabel(rule.expected) }}</option>
          <option v-for="(alternative, i) in rule.alternatives" :key="i" :value="JSON.stringify(alternative.expected)">{{ valueLabel(alternative.expected) }}</option>
        </select>
      </label>
    </details>
    <label class="ack"><input v-model="removeUnmatchedToc" type="checkbox" />{{ L('如果目录条目没有对应正文标题，允许清理这些失配项；系统会在预览中列出具体条目。', 'Allow removal of TOC entries without a matching body heading. The preview will list each removed entry.') }}</label>
    <label v-if="!candidate" class="ack"><input v-model="preserveManuscriptStructure" data-test="manuscript-structure-policy" type="checkbox" :disabled="busy" @change="proposal = null; accepted = false" />{{ L('导入论文时保留原论文的完整章节层级、标题和顺序；此 Word 提供各级标题、正文及图表版式。', 'Keep imported manuscript headings, levels and order. Use this Word master for heading, text and object styles.') }}</label>
    <button type="button" class="small" :disabled="busy || dirty" @click="prepare">{{ L('预览执行配置', 'Preview execution plan') }}</button>
    <div v-if="proposal" class="execution-preview" data-test="writing-profile-plan">
      <p>{{ L('以下格式来自源稿和已选项。母版中的单个示例尺寸不会作为所有新图片或表格的固定尺寸。', 'The following styles use source evidence and your choices. Individual example dimensions do not become universal object dimensions.') }}</p>
      <p v-if="proposal.trials">{{ L('普通正文和长正文的母版试填检查已通过。', 'Normal and long-content trial checks passed.') }}</p>
      <details v-if="plan.removed_toc_entries?.length"><summary>{{ L('将清理的失配目录项', 'Unmatched TOC entries to remove') }} ({{ plan.removed_toc_entries.length }})</summary><ul><li v-for="(entry, i) in plan.removed_toc_entries" :key="i">{{ entry }}</li></ul></details>
      <details><summary>{{ L('封面字段', 'Cover fields') }} ({{ plan.fields.length }})</summary>
        <details v-for="field in plan.fields" :key="field.key"><summary>{{ field.key }}：{{ fontLabel(field.properties) }}</summary><dl><template v-for="(value, key) in field.properties" :key="key"><dt>{{ propertyLabel(key) }}</dt><dd>{{ valueLabel(value) }}</dd></template></dl></details>
      </details>
      <details><summary>{{ L('章节与正文格式', 'Sections and body styles') }} ({{ plan.headings.length }})</summary>
        <details v-for="heading in plan.headings" :key="heading.key"><summary>{{ heading.title }}：{{ fontLabel(heading.properties) }}</summary><dl><template v-for="(value, key) in heading.properties" :key="key"><dt>{{ propertyLabel(key) }}</dt><dd>{{ valueLabel(value) }}</dd></template></dl></details>
      </details>
      <details><summary>{{ L('新增图表与表题、图题的格式', 'Styles for new objects and captions') }}</summary>
        <details v-for="(properties, role) in plan.object_properties" :key="role"><summary>{{ objectLabel(role) }}：{{ fontLabel(properties) }}</summary><dl><template v-for="(value, key) in properties" :key="key"><dt>{{ propertyLabel(key) }}</dt><dd>{{ valueLabel(value) }}</dd></template></dl></details>
      </details>
      <details v-if="plan.content_rule_review" data-test="content-rule-review">
        <summary>{{ L('Word 中明确的内容要求', 'Explicit content requirements') }} ({{ plan.content_rule_review.candidates?.length || 0 }})</summary>
        <p>{{ L('只有明确且可自动判断的要求经教师勾选后才会成为提交硬性检查；其他说明继续保留为人工核查。', 'Only explicit, machine-checkable requirements you select become submission gates. Other instructions remain for manual review.') }}</p>
        <label v-for="rule in plan.content_rule_review.candidates || []" :key="rule.rule_id" class="ack content-rule">
          <input v-model="confirmedContentRules" type="checkbox" :value="rule.rule_id" />
          <span><strong>{{ rule.region_title || rule.target }}</strong> · {{ rule.source_text }}<small>{{ rule.checker }} · {{ rule.source_path }}</small></span>
        </label>
        <p v-if="!contentSelectionMatchesPreview" role="status">{{ L('内容要求选择已变化，请重新预览后再发布。', 'Content-rule choices changed. Preview again before publishing.') }}</p>
        <details v-if="plan.content_rule_review.manual_items?.length"><summary>{{ L('保留人工核查的原文要求', 'Instructions retained for manual review') }} ({{ plan.content_rule_review.manual_items.length }})</summary>
          <ul><li v-for="item in plan.content_rule_review.manual_items" :key="item.rule_id"><strong>{{ item.region_title || L('未定位章节', 'Unmapped section') }}</strong> · {{ item.source_text }}</li></ul>
        </details>
      </details>
      <label class="ack"><input v-model="accepted" type="checkbox" />{{ L('已核对原文要求、差异项和上述执行格式；采用此配置并冻结章节结构。', 'I reviewed the source instructions, variations and execution styles and accept the frozen structure.') }}</label>
      <label class="ack"><input v-model="removeInstructions" type="checkbox" />{{ L('从学生成品移除已识别的模板填写说明和示例正文。', 'Remove identified filling instructions and example body content from student output.') }}</label>
      <label class="ack"><input v-model="acceptObjects" type="checkbox" />{{ L('采用上述图表样式和按章编号；新增图片适应版心并保持比例，表格适应版心并重复表头。未从示例推断全局固定大小。', 'Accept the displayed object styles and chapter-based captions. Images fit the text area and retain their ratio; tables fit and repeat their header. These are explicit layout defaults.') }}</label>
      <p>{{ L('成品仍需在 Word/WPS 中核对分页、字体替换及更新目录。', 'Final pagination, font substitution and refreshed fields require Word/WPS acceptance.') }}</p>
      <button type="button" class="small primary" :disabled="busy || dirty || !accepted || !removeInstructions || !acceptObjects || !contentSelectionMatchesPreview || (plan.removed_toc_entries?.length && !removeUnmatchedToc)" @click="publish">{{ L('确认并发布此版本', 'Confirm and publish version') }}</button>
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
const preserveManuscriptStructure = ref(!!props.template.source_execution?.preserve_manuscript_structure)
const conflicts = computed(() => (props.template.writing_specification?.rules || []).filter(r => r.confidence === 'conflict' && (r.property.startsWith('font.') || r.property.startsWith('paragraph.'))))
const plan = computed(() => proposal.value?.template.source_execution)
const contentSelectionMatchesPreview = computed(() => {
  const current = [...confirmedContentRules.value].sort()
  const previewed = [...(plan.value?.confirmed_content_rule_ids || [])].sort()
  return current.length === previewed.length && current.every((id, index) => id === previewed[index])
})
function valueLabel(value) { return typeof value === 'object' ? `${value.value ?? ''} ${{pt:L('磅','pt'),line:L('行','lines'),multiple:L('倍','multiple'),character:L('字符','characters')}[value.unit] ?? value.unit ?? JSON.stringify(value)}` : typeof value === 'boolean' ? (value ? L('是','Yes') : L('否','No')) : String(value) }
function propertyLabel(key) {
  const labels = {'font.eastAsia':'中文字体','font.ascii':'西文字体','font.hAnsi':'西文扩展字体','font.cs':'复杂文字字体','font.size':'字号','font.bold':'加粗','font.italic':'斜体','font.color':'颜色','paragraph.align':'对齐','paragraph.spacing.line':'行距','paragraph.spacing.before':'段前间距','paragraph.spacing.after':'段后间距','paragraph.indent.first_line':'首行缩进','paragraph.indent.left':'左缩进','paragraph.indent.right':'右缩进','paragraph.indent.hanging':'悬挂缩进','paragraph.page_break_before':'段前分页','paragraph.keep_next':'与下段同页','paragraph.keep_lines':'段中不分页','paragraph.widow_control':'孤行控制'}
  return lang.value === 'zh' ? labels[key] || key : key.replaceAll('.', ' ').replaceAll('_', ' ')
}
function objectLabel(role) { return lang.value === 'zh' ? {figure_caption:'图题',table_caption:'表题',table_text:'表格文字'}[role] || role : role.replaceAll('_',' ') }
function fontLabel(p) { return `${p['font.eastAsia'] || ''} / ${p['font.ascii'] || ''} · ${valueLabel(p['font.size'] || {})} · ${L('行距', 'line')} ${valueLabel(p['paragraph.spacing.line'] || {})}` }
function choose(id, value) { selected.value[id] = value; proposal.value = null; accepted.value = false }
function confirmation() { return { expected_specification_sha256: props.template.writing_specification?.specification_sha256 || '', overrides: Object.fromEntries(Object.entries(selected.value).map(([k,v]) => [k,JSON.parse(v)])), confirmed_content_rule_ids: [...confirmedContentRules.value], remove_unmatched_toc_entries: removeUnmatchedToc.value, preserve_manuscript_structure: preserveManuscriptStructure.value } }
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
  preserveManuscriptStructure.value = !!props.template.source_execution?.preserve_manuscript_structure
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
