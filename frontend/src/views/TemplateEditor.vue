<template>
  <div class="workspace-page resource-page template-editor-page">
    <section class="workspace-hero">
      <div>
        <p class="workspace-kicker">{{ t('editor.kicker') }}</p>
        <h1>{{ isNew ? t('templates.new') : t('templates.edit') }}</h1>
        <p>{{ t('editor.description') }}</p>
      </div>
      <div class="resource-hero-actions">
        <button class="small primary" type="button" :disabled="saving || loading || !!loadError" @click="save">
          <span class="material-symbols-outlined" aria-hidden="true">save</span>{{ saving ? t('editor.saving') : t('editor.save') }}
        </button>
        <button class="small" type="button" @click="$router.push('/templates')">
          <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>{{ t('editor.back') }}
        </button>
      </div>
    </section>

    <div v-if="loadError" class="error-banner" role="alert">{{ loadError }}</div>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div v-if="saved" class="saved-banner" role="status" aria-live="polite"><span class="material-symbols-outlined" aria-hidden="true">check_circle</span>{{ t('editor.saved') }}{{ savedAt ? ` · ${savedAt}` : '' }}</div>

    <section class="panel template-editor-panel">
      <div class="panel-heading template-editor-heading">
        <div>
          <p class="workspace-kicker">{{ t('editor.kicker') }}</p>
          <h2>{{ t('editor.title') }}</h2>
          <p>{{ t('editor.description') }}</p>
        </div>
        <span class="material-symbols-outlined panel-icon" aria-hidden="true">tune</span>
      </div>

      <div v-if="loading" class="editor-loading">{{ t('editor.loading') }}</div>
    <!-- 基本信息 -->
    <div v-else class="template-basic-form">
      <div class="template-basic-grid">
        <label>{{ t('editor.templateId') }}
          <input v-model="tpl.id" :disabled="!isNew" :placeholder="t('editor.templateIdPlaceholder')" />
        </label>
        <label>{{ t('editor.templateName') }}
          <input v-model="tpl.name" :placeholder="t('editor.templateNamePlaceholder')" />
        </label>
      </div>
      <label class="template-description-field">{{ t('editor.descriptionLabel') }}
        <input v-model="tpl.description" :placeholder="t('editor.descriptionPlaceholder')" />
      </label>
      <div class="structure-edit-toggle">
        <label>
          <input type="checkbox" v-model="tpl.allow_structure_edit" />
          <span>{{ t('editor.allowStructure') }}</span>
        </label>
        <p>{{ t('editor.allowStructureDetail') }}</p>
      </div>

    <!-- 写作规范绑定 -->
    <h3 class="editor-section-title"><span class="material-symbols-outlined" aria-hidden="true">rule</span>{{ t('editor.policyBinding') }}</h3>
    <div class="policy-binding">
      <label>{{ t('editor.templateVersion') }}
        <input v-model="tpl.version" :placeholder="t('editor.templateVersionPlaceholder')" />
      </label>
      <label>{{ t('editor.rulePacks') }}
        <input :value="(tpl.rule_packs || []).join(',')" @input="setRulePacks($event.target.value)"
               :placeholder="t('editor.rulePacksPlaceholder')" />
      </label>
      <label class="policy-select-label">{{ t('editor.requirements') }}
        <select v-model="tpl.requirement_set_ids" multiple>
          <option v-for="policy in policyOptions" :key="policy.id" :value="policy.id">
            {{ policy.name }} · v{{ policy.version }}
          </option>
        </select>
      </label>
      <p class="field-hint">
        {{ t('editor.requirementsDetail') }}
      </p>
    </div>

    <!-- 封面字段 -->
    <h3 class="editor-section-title"><span class="material-symbols-outlined" aria-hidden="true">badge</span>{{ t('editor.coverFields') }}</h3>
    <div v-for="(f, i) in tpl.meta_fields" :key="i" class="meta-field-row">
      <label>{{ t('editor.fieldKey') }}
        <input v-model="f.key" :placeholder="t('editor.fieldKeyPlaceholder')" />
      </label>
      <label>{{ t('editor.displayName') }}
        <input v-model="f.label" :placeholder="t('editor.displayNamePlaceholder')" />
      </label>
      <label class="meta-field-required"><input type="checkbox" v-model="f.required" /> {{ t('editor.required') }}</label>
      <button class="small danger" type="button" @click="tpl.meta_fields.splice(i, 1)"><span class="material-symbols-outlined" aria-hidden="true">delete</span>{{ t('common.remove') }}</button>
    </div>
    <button class="small" type="button" @click="tpl.meta_fields.push({ key: '', label: '', required: true })"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('editor.addCoverField') }}</button>

    <!-- 章节结构 -->
    <h3 class="editor-section-title"><span class="material-symbols-outlined" aria-hidden="true">account_tree</span>{{ t('editor.sections') }}</h3>
    <div class="tree">
      <section-node v-for="node in tpl.sections" :key="node.key" :node="node"
                    @remove="removeTopLevel(node.key)" />
    </div>
    <button class="small" type="button" @click="addSection()"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('editor.addTopSection') }}</button>

    <!-- 样式规范 -->
    <h3 class="editor-section-title"><span class="material-symbols-outlined" aria-hidden="true">format_paint</span>{{ t('editor.styles') }}</h3>
    <p style="color: var(--muted); font-size: 13px; margin-bottom: 12px">
      {{ t('editor.stylesDetail') }}
    </p>
    <div v-for="key in stylesKeys" :key="key" class="style-block">
      <button class="style-block-heading" type="button" @click="toggleStyle(key)">
        <span style="font-weight: 600">{{ styleLabel(key) }}</span>
        <span class="rule-id"><span class="material-symbols-outlined" aria-hidden="true">{{ collapsed[key] ? 'expand_more' : 'expand_less' }}</span>{{ collapsed[key] ? t('editor.expand') : t('editor.collapse') }}</span>
      </button>
      <div v-show="!collapsed[key]" class="style-grid">
        <label>{{ t('editor.chineseFont') }} <input v-model="tpl.styles[key].font_zh" /></label>
        <label>{{ t('editor.westernFont') }} <input v-model="tpl.styles[key].font_en" /></label>
        <label>{{ t('editor.sizePt') }} <input type="number" step="0.5" v-model.number="tpl.styles[key].size_pt" /></label>
        <label>{{ t('templates.lineSpacing') }} <input type="number" step="0.1" v-model.number="tpl.styles[key].line_spacing" /></label>
        <label>{{ t('editor.beforePt') }} <input type="number" step="1" v-model.number="tpl.styles[key].space_before_pt" /></label>
        <label>{{ t('editor.afterPt') }} <input type="number" step="1" v-model.number="tpl.styles[key].space_after_pt" /></label>
        <label>{{ t('editor.indentChars') }} <input type="number" step="0.5" v-model.number="tpl.styles[key].first_line_indent_chars" /></label>
        <label>{{ t('editor.align') }}
          <select v-model="tpl.styles[key].align">
            <option value="left">{{ t('editor.left') }}</option>
            <option value="center">{{ t('editor.center') }}</option>
            <option value="right">{{ t('editor.right') }}</option>
            <option value="justify">{{ t('editor.justify') }}</option>
          </select>
        </label>
        <label class="checkbox"><input type="checkbox" v-model="tpl.styles[key].bold" /> {{ t('templates.bold') }}</label>
        <label class="checkbox"><input type="checkbox" v-model="tpl.styles[key].italic" /> {{ t('editor.italic') }}</label>
        <label class="checkbox"><input type="checkbox" v-model="tpl.styles[key].underline" /> {{ t('editor.underline') }}</label>
        <label class="checkbox"><input type="checkbox" v-model="tpl.styles[key].page_break_before" /> {{ t('editor.pageBreak') }}</label>
        <div class="style-preview">
          <span>{{ t('editor.preview') }}:</span>
          <span class="preview-text" :style="previewStyle(tpl.styles[key])">
            {{ styleLabel(key) }}示例 — 汉字 Abc 123
          </span>
        </div>
      </div>
    </div>
    </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchTemplate, fetchPolicies, saveTemplate } from '../api'
import SectionNode from '../components/SectionNode.vue'
import { useI18n } from '../i18n'

const route = useRoute()
const router = useRouter()
const { t, formatDate } = useI18n()
const tplId = computed(() => String(route.params.id || ''))
const isNew = computed(() => tplId.value === 'new')

const STYLE_KEYS = {
  cover_title: 'templates.style.coverTitle', cover_meta: 'templates.style.coverMeta', abstract: 'section.abstract', english_abstract: 'section.englishAbstract',
  keywords: 'section.keywords', english_keywords: 'section.englishKeywords', toc: 'section.toc', heading1: 'section.heading1', heading2: 'section.heading2', heading3: 'section.heading3', body: 'section.body', references: 'section.references', acknowledgement: 'section.acknowledgement', appendix: 'section.appendix', figure_caption: 'templates.style.figureCaption', table_caption: 'templates.style.tableCaption', table_cell: 'templates.style.tableCell',
}
const DEFAULT_STYLE = () => ({
  font_zh: '宋体', font_en: 'Times New Roman', size_pt: 12,
  bold: false, italic: false, underline: false, align: 'justify',
  line_spacing: 1.5, space_before_pt: 0, space_after_pt: 0,
  first_line_indent_chars: 0, page_break_before: false,
})

const tpl = ref({
  id: '', name: '', description: '',
  allow_structure_edit: true,
  version: '1', rule_packs: [], requirement_set_ids: [],
  sections: [], styles: {}, meta_fields: [],
})
const policyOptions = ref([])
const stylesKeys = ref([])
const collapsed = ref({})
const error = ref('')
const saved = ref(false)
const savedAt = ref('')
const saving = ref(false)
const loadError = ref('')
const loading = ref(true)

onMounted(async () => {
  loading.value = true
  loadError.value = ''
  if (isNew.value) {
    // 新模板: 以默认样式为起点, 一个空结构
    tpl.value.id = ''
    tpl.value.name = ''
    tpl.value.styles = {
      cover_title: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 22, bold: true, align: 'center' },
      cover_meta: { ...DEFAULT_STYLE(), size_pt: 16, align: 'center', line_spacing: 2 },
      abstract: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 18, bold: true, align: 'center', page_break_before: true },
      toc: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 18, bold: true, align: 'center', page_break_before: true },
      heading1: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 18, bold: true, align: 'center', page_break_before: true },
      heading2: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 16, bold: true, align: 'left' },
      heading3: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 14, bold: true, align: 'left' },
      body: { ...DEFAULT_STYLE(), first_line_indent_chars: 2 },
      references: { ...DEFAULT_STYLE(), page_break_before: true },
      acknowledgement: { ...DEFAULT_STYLE(), page_break_before: true },
      appendix: { ...DEFAULT_STYLE(), page_break_before: true },
    }
  } else {
    try {
      tpl.value = await fetchTemplate(tplId.value)
    } catch (e) {
      loadError.value = e.message || t('common.operationFailed')
    }
  }
  try { policyOptions.value = await fetchPolicies() } catch { /* 学生或旧后端可无规范列表 */ }
  stylesKeys.value = Object.keys(tpl.value.styles)
  for (const k of stylesKeys.value) collapsed.value[k] = true
  loading.value = false
})

function setRulePacks(value) {
  tpl.value.rule_packs = value.split(',').map((item) => item.trim()).filter(Boolean)
}

function addSection() {
  const used = new Set()
  function collect(nodes) {
    for (const node of nodes || []) {
      if (node.key) used.add(node.key)
      collect(node.children)
    }
  }
  collect(tpl.value.sections)
  let n = tpl.value.sections.length + 1
  let key = 'ch' + n
  while (used.has(key)) key = 'ch' + (++n)
  tpl.value.sections.push({ key, type: 'heading1', title: t('templates.newSection'), required: true, children: [] })
}

function removeTopLevel(key) {
  const index = tpl.value.sections.findIndex((node) => node.key === key)
  if (index >= 0) tpl.value.sections.splice(index, 1)
}

function toggleStyle(k) { collapsed.value[k] = !collapsed.value[k] }

function styleLabel(k) { return t(STYLE_KEYS[k] || k) }

function previewStyle(s) {
  const alignMap = { left: 'left', center: 'center', right: 'right', justify: 'justify' }
  return {
    fontFamily: `'${s.font_zh}', '${s.font_en}', sans-serif`,
    fontSize: s.size_pt + 'pt',
    fontWeight: s.bold ? 'bold' : 'normal',
    fontStyle: s.italic ? 'italic' : 'normal',
    textDecoration: s.underline ? 'underline' : 'none',
    textAlign: alignMap[s.align] || 'left',
    lineHeight: s.line_spacing,
    textIndent: s.first_line_indent_chars ? (s.first_line_indent_chars * s.size_pt) + 'pt' : '0',
    margin: (s.space_before_pt || 0) + 'pt 0 ' + (s.space_after_pt || 0) + 'pt',
  }
}

function validateTemplate() {
  if (!isNew.value && !tpl.value.id.trim()) return t('editor.idRequired')
  if (!tpl.value.name.trim()) return t('editor.nameRequired')
  const keys = new Set()
  let invalid = ''
  function walk(nodes) {
    for (const node of nodes || []) {
      if (!node.key?.trim()) { invalid = t('editor.sectionKeyRequired'); return }
      if (keys.has(node.key)) { invalid = t('editor.duplicateSectionKey', { key: node.key }); return }
      if (!node.title?.trim()) { invalid = t('editor.sectionTitleRequired', { key: node.key }); return }
      keys.add(node.key)
      walk(node.children)
      if (invalid) return
    }
  }
  walk(tpl.value.sections)
  if (invalid) return invalid
  const metaKeys = new Set()
  for (const field of tpl.value.meta_fields) {
    if (!field.key?.trim() || !field.label?.trim()) return t('editor.coverFieldRequired')
    if (metaKeys.has(field.key)) return t('editor.duplicateFieldKey', { key: field.key })
    metaKeys.add(field.key)
  }
  return ''
}

async function save() {
  saving.value = true; error.value = ''; saved.value = false
  const validationError = validateTemplate()
  if (validationError) {
    error.value = validationError
    saving.value = false
    return
  }
  try {
    const result = await saveTemplate(tpl.value, isNew.value)
    tpl.value = result
    saved.value = true
    savedAt.value = formatDate(new Date(), { hour: '2-digit', minute: '2-digit' })
    if (isNew.value) router.replace('/templates/' + result.id + '/edit')
  } catch (e) { error.value = e.message || t('common.operationFailed') }
  finally { saving.value = false }
}
</script>

<style scoped>
.saved-banner { color: #15803d; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; }
.style-block { border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; margin-bottom: 10px; }
.style-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; padding: 12px 0; }
.style-grid label { font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
.style-grid input, .style-grid select {
  padding: 6px; border: 1px solid var(--border); border-radius: 6px; font-size: 14px; color: var(--text);
}
.style-grid label.checkbox { flex-direction: row; align-items: center; gap: 6px; }
.style-preview { grid-column: 1 / -1; display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--muted); }
.preview-text {
  flex: 1; background: #fff; border: 1px dashed var(--border); border-radius: 6px;
  padding: 8px 12px; color: #1f2937; min-height: 32px;
}
.head-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.policy-binding {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px;
  padding: 14px; background: var(--surface-container-low); border: 1px solid var(--border);
  border-radius: 8px;
}
.policy-binding label { display: flex; flex-direction: column; gap: 5px; color: var(--muted); font-size: 12px; }
.policy-binding input, .policy-binding select {
  width: 100%; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px;
  background: var(--card); color: var(--on-surface); font: inherit; font-size: 13px;
}
.policy-select-label { grid-column: 1 / -1; }
.policy-select-label select { min-height: 96px; }
.field-hint { grid-column: 1 / -1; color: var(--muted); font-size: 12px; }
.template-editor-heading {
  margin-bottom: 18px;
}

.template-editor-panel {
  padding-bottom: 22px;
}

.editor-loading {
  min-height: 180px;
  display: grid;
  place-items: center;
  border: 1px dashed var(--border);
  color: var(--on-surface-variant);
  font-size: 13px;
}

.saved-banner {
  display: flex;
  align-items: center;
  gap: 7px;
  border-radius: var(--radius-md);
  margin-bottom: 14px;
}

.saved-banner .material-symbols-outlined {
  font-size: 17px;
}

.template-basic-form {
  min-width: 0;
}

.template-basic-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.template-basic-grid label,
.template-description-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--on-surface-variant);
  font-size: 12px;
}

.template-basic-grid input,
.template-description-field input {
  width: 100% !important;
  padding: 8px 10px !important;
  border-radius: var(--radius-sm) !important;
  background: var(--surface-container-lowest);
  color: var(--on-surface);
  font: inherit;
}

.template-description-field {
  margin-top: 12px;
}

.structure-edit-toggle {
  margin-top: 16px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-container-low);
}

.structure-edit-toggle label {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: var(--on-surface);
  cursor: pointer;
  font-size: 13px;
  font-weight: 600;
}

.structure-edit-toggle input {
  flex: 0 0 auto;
  margin-top: 3px;
}

.structure-edit-toggle p {
  margin: 5px 0 0 24px !important;
  color: var(--on-surface-variant) !important;
  font-size: 12px !important;
  line-height: 1.5;
}

.editor-section-title {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 28px 0 12px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  color: var(--on-surface);
  font-size: 16px;
}

.editor-section-title .material-symbols-outlined {
  color: var(--primary);
  font-size: 19px;
}

.meta-field-row {
  display: grid;
  grid-template-columns: minmax(150px, .75fr) minmax(180px, 1fr) auto auto;
  gap: 10px;
  align-items: end;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
}

.meta-field-row label:not(.meta-field-required) {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--on-surface-variant);
  font-size: 12px;
}

.meta-field-row input {
  width: 100% !important;
  padding: 8px 10px !important;
  border-radius: var(--radius-sm) !important;
  background: var(--surface-container-lowest);
  color: var(--on-surface);
  font: inherit;
}

.meta-field-required {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding-bottom: 9px;
  color: var(--on-surface-variant);
  font-size: 12px;
  white-space: nowrap;
}

.style-block {
  padding: 0;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--surface-container-lowest);
}

.style-block-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 11px 14px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--on-surface);
  text-align: left;
}

.style-block-heading:hover:not(:disabled) {
  background: var(--surface-container-low);
}

.style-block-heading .rule-id {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.style-block-heading .material-symbols-outlined {
  font-size: 17px;
}

.style-grid {
  padding: 14px;
  border-top: 1px solid var(--border);
  background: var(--surface-container-low);
}

.style-grid input,
.style-grid select {
  width: 100%;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  border-color: var(--border);
  background: var(--surface-container-lowest);
  color: var(--on-surface);
  font: inherit;
  font-size: 13px;
}

.style-preview {
  align-items: flex-start;
  flex-direction: column;
  gap: 6px;
}

.preview-text {
  width: 100%;
  background: var(--surface-container-lowest);
}

@media (max-width: 700px) {
  .template-basic-grid {
    grid-template-columns: 1fr;
  }

  .meta-field-row {
    grid-template-columns: 1fr 1fr;
  }

  .meta-field-required,
  .meta-field-row > button {
    padding-bottom: 0;
  }
}

@media (max-width: 640px) {
  .policy-binding { grid-template-columns: 1fr; }
  .policy-select-label, .field-hint { grid-column: auto; }
}
</style>
