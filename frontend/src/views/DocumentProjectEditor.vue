<template>
  <div class="editor-page">
    <div v-if="loading" class="editor-state">{{ t('common.loading') }}</div>
    <div v-else-if="error && !editor" class="editor-state error-banner" role="alert">{{ error }}</div>
    <template v-else>
      <header class="editor-header">
        <div>
          <RouterLink to="/document-projects" class="back-link">
            <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>
            {{ t('documentProjects.back') }}
          </RouterLink>
          <input v-model="draft.title" class="title-input" :aria-label="t('documentProjects.projectTitle')" maxlength="300" @input="scheduleSave" />
          <p class="editor-subtitle">{{ editor.template.name }} · {{ editor.package.document_type_id }} · {{ editor.project.template_version_id }}</p>
        </div>
        <div class="editor-actions">
          <button class="validate-action" type="button" :disabled="validating || saving || exporting" @click="runValidation">
            <span class="material-symbols-outlined" aria-hidden="true">fact_check</span>
            {{ validating ? t('documentProjects.validating') : t('documentProjects.validate') }}
          </button>
          <button class="export-action" type="button" :disabled="validating || saving || exporting" @click="exportProject">
            <span class="material-symbols-outlined" aria-hidden="true">download</span>
            {{ exporting ? t('documentProjects.exporting') : t('documentProjects.export') }}
          </button>
          <div class="save-status" :class="{ saving }" role="status" aria-live="polite">
            <span class="status-dot" :class="saving ? 'working' : 'ok'"></span>
            {{ saving ? t('documentProjects.saving') : t('documentProjects.saved', { revision: draft.revision }) }}
          </div>
        </div>
      </header>

      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

      <section v-if="validation" class="validation-panel" aria-live="polite">
        <div class="validation-heading">
          <div><p class="workspace-kicker">{{ t('documentProjects.validationEyebrow') }}</p><h2>{{ t('documentProjects.validationTitle') }}</h2></div>
          <strong :class="validation.findings.length ? 'has-findings' : 'clean'">{{ validation.findings.length ? t('documentProjects.issueCount', { count: validation.findings.length }) : t('documentProjects.noFindings') }}</strong>
        </div>
        <ul v-if="validation.findings.length" class="finding-list">
          <li v-for="finding in validation.findings" :key="`${finding.rule_id}:${finding.section_key}`" :class="finding.severity">
            <span class="finding-severity">{{ finding.severity }}</span>
            <span>{{ finding.message }}</span>
            <small v-if="finding.section_key">{{ finding.section_key }}</small>
          </li>
        </ul>
        <p v-if="validation.not_checked.length" class="not-checked">{{ t('documentProjects.notChecked', { count: validation.not_checked.length }) }}</p>
      </section>

      <div class="editor-layout">
        <aside class="editor-aside">
          <div class="aside-block">
            <p class="workspace-kicker">{{ t('documentProjects.snapshot') }}</p>
            <dl class="snapshot-list">
              <div><dt>{{ t('documentProjects.templateVersion') }}</dt><dd>{{ draft.template_version_id }}</dd></div>
              <div><dt>{{ t('documentProjects.specVersion') }}</dt><dd>{{ draft.spec_version_id }}</dd></div>
              <div><dt>{{ t('documentProjects.renderer') }}</dt><dd>{{ draft.renderer_version }}</dd></div>
              <div><dt>{{ t('documentProjects.ruleEngine') }}</dt><dd>{{ draft.rule_engine_version }}</dd></div>
            </dl>
          </div>
          <div class="aside-block">
            <p class="workspace-kicker">{{ t('documentProjects.rules') }}</p>
            <ul class="rule-list">
              <li v-for="rule in editor.spec_version.rules" :key="rule.id">
                <span class="rule-kind" :class="rule.kind">{{ rule.kind }}</span>
                <span>{{ rule.description || rule.id }}</span>
              </li>
            </ul>
          </div>
        </aside>

        <main class="editor-main">
          <section v-if="editor.editor_schema.fields.length" class="editor-section">
            <div class="section-heading"><div><p class="workspace-kicker">{{ t('documentProjects.fieldsEyebrow') }}</p><h2>{{ t('documentProjects.fieldsTitle') }}</h2></div></div>
            <div class="field-grid">
              <label v-for="field in editor.editor_schema.fields" :key="field.key" class="document-field">
                <span>{{ field.label || field.key }} <em v-if="field.required">{{ t('documentProjects.required') }}</em></span>
                <textarea v-if="isMultilineField(field)" :data-test="`document-field-${field.key}`" v-model="draft.field_values[field.key]" rows="3" @input="scheduleSave"></textarea>
                <select v-else-if="isChoiceField(field)" :data-test="`document-field-${field.key}`" v-model="draft.field_values[field.key]" @change="scheduleSave">
                  <option value="">{{ t('documentProjects.selectPlaceholder') }}</option>
                  <option v-for="option in fieldOptions(field)" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
                <input v-else-if="field.type === 'checkbox'" :data-test="`document-field-${field.key}`" v-model="draft.field_values[field.key]" type="checkbox" @change="scheduleSave" />
                <input v-else :data-test="`document-field-${field.key}`" v-model="draft.field_values[field.key]" :type="nativeFieldType(field)" :min="field.constraints?.min" :max="field.constraints?.max" :maxlength="field.constraints?.max_length" @input="scheduleSave" />
              </label>
            </div>
          </section>

          <section v-for="section in editableSections" :key="section.key" class="editor-section">
            <div class="section-heading">
              <div><p class="workspace-kicker">{{ section.kind }}</p><h2>{{ section.title || section.key }}</h2></div>
              <button class="icon-action" type="button" :title="t('documentProjects.addBlock')" :aria-label="t('documentProjects.addBlock')" @click="addBlock(section.key)">
                <span class="material-symbols-outlined" aria-hidden="true">add</span>
              </button>
            </div>
            <div class="block-list">
              <article v-for="(block, index) in draft.sections[section.key]" :key="block.id" class="content-block">
                <div class="block-label">
                  <span>{{ t('documentProjects.block') }} {{ index + 1 }}</span>
                  <select :data-test="`block-kind-${block.id}`" v-model="block.kind" :aria-label="t('documentProjects.blockType')" @change="normalizeBlock(block); scheduleSave()">
                    <option v-for="kind in blockKinds(section)" :key="kind" :value="kind">{{ kind }}</option>
                  </select>
                </div>
                <textarea v-if="isTextBlock(block)" v-model="block.text" :aria-label="`${section.title || section.key} ${index + 1}`" rows="7" @input="scheduleSave"></textarea>
                <div v-else-if="isImageBlock(block)" class="block-fields">
                  <label>{{ t('documentProjects.imageUpload') }}<input type="file" accept="image/png,image/jpeg,image/gif" :disabled="uploadingBlockId === block.id" @change="uploadBlockImage(block, $event)" /></label>
                  <label>{{ t('documentProjects.imageUrl') }}<input v-model="block.data.url" type="text" readonly /></label>
                  <label>{{ t('documentProjects.imageCaption') }}<input v-model="block.data.caption" type="text" @input="scheduleSave" /></label>
                </div>
                <div v-else-if="isTableBlock(block)" class="table-editor">
                  <div class="table-editor-toolbar">
                    <label>{{ t('documentProjects.tableRows') }}<input v-model.number="block.data.rows" type="number" min="1" max="100" @change="resizeTable(block); scheduleSave()" /></label>
                    <label>{{ t('documentProjects.tableColumns') }}<input v-model.number="block.data.cols" type="number" min="1" max="30" @change="resizeTable(block); scheduleSave()" /></label>
                  </div>
                  <div class="table-editor-scroll">
                    <table data-test="document-table-editor">
                      <tbody>
                        <tr v-for="(row, rowIndex) in block.data.cells" :key="rowIndex">
                          <td v-for="(_, colIndex) in row" :key="colIndex"><input v-model="block.data.cells[rowIndex][colIndex]" :aria-label="`${t('documentProjects.tableCell')} ${rowIndex + 1}-${colIndex + 1}`" @input="scheduleSave" /></td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <label>{{ t('documentProjects.tableCaption') }}<input v-model="block.data.caption" type="text" @input="scheduleSave" /></label>
                </div>
                <div v-else-if="isEquationBlock(block)" class="block-fields">
                  <label>{{ t('documentProjects.equationText') }}<textarea v-model="block.data.omml" rows="4" @input="scheduleSave"></textarea></label>
                  <label>{{ t('documentProjects.equationAltText') }}<input v-model="block.data.alt_text" type="text" @input="scheduleSave" /></label>
                </div>
                <textarea v-else v-model="block.text" :aria-label="`${section.title || section.key} ${index + 1}`" rows="7" @input="scheduleSave"></textarea>
              </article>
            </div>
          </section>
        </main>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../i18n'
import { getDocumentProjectEditor, renderDocumentProject, updateDocumentProject, uploadDocumentProjectImage, validateDocumentProject } from '../api.js'

const route = useRoute()
const { t } = useI18n()
const editor = ref(null)
const draft = reactive({
  title: '',
  revision: 0,
  template_version_id: '',
  spec_version_id: '',
  renderer_version: '',
  rule_engine_version: '',
  field_values: {},
  sections: {},
})
const loading = ref(true)
const saving = ref(false)
const validating = ref(false)
const exporting = ref(false)
const validation = ref(null)
const error = ref('')
const uploadingBlockId = ref('')
let saveTimer = null

const editableSections = computed(() => (editor.value?.editor_schema.sections || []).filter((section) => draft.sections[section.key]))

function applyProject(project) {
  draft.title = project.title || ''
  draft.revision = project.revision
  draft.template_version_id = project.template_version_id || ''
  draft.spec_version_id = project.spec_version_id || ''
  draft.renderer_version = project.renderer_version || ''
  draft.rule_engine_version = project.rule_engine_version || ''
  draft.field_values = JSON.parse(JSON.stringify(project.field_values || {}))
  draft.sections = JSON.parse(JSON.stringify(project.sections || {}))
  for (const blocks of Object.values(draft.sections)) {
    for (const block of blocks) normalizeBlock(block)
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    editor.value = await getDocumentProjectEditor(route.params.id)
    applyProject(editor.value.project)
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(save, 500)
}

async function save() {
  saveTimer = null
  saving.value = true
  error.value = ''
  try {
    const updated = await updateDocumentProject(route.params.id, {
      title: draft.title,
      field_values: draft.field_values,
      sections: draft.sections,
      base_revision: draft.revision,
    })
    applyProject(updated)
    await runValidation()
  } catch (exception) {
    error.value = exception.message
  } finally {
    saving.value = false
  }
}

async function runValidation() {
  validating.value = true
  error.value = ''
  try {
    validation.value = await validateDocumentProject(route.params.id)
  } catch (exception) {
    error.value = exception.message
  } finally {
    validating.value = false
  }
}

async function exportProject() {
  exporting.value = true
  error.value = ''
  try {
    await renderDocumentProject(route.params.id)
  } catch (exception) {
    error.value = exception.message
  } finally {
    exporting.value = false
  }
}

function addBlock(sectionKey) {
  const id = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
  const section = (editor.value?.editor_schema.sections || []).find(item => item.key === sectionKey)
  const kind = blockKinds(section)[0] || 'paragraph'
  draft.sections[sectionKey].push({ id, kind, text: '', data: {}, order: draft.sections[sectionKey].length })
  normalizeBlock(draft.sections[sectionKey][draft.sections[sectionKey].length - 1])
  scheduleSave()
}

function isMultilineField(field) {
  return ['textarea', 'multiline', 'richtext'].includes(String(field.type || '').toLowerCase())
}

function isChoiceField(field) {
  return ['select', 'enum'].includes(String(field.type || '').toLowerCase())
}

function nativeFieldType(field) {
  const type = String(field.type || 'text').toLowerCase()
  return ['date', 'number', 'email', 'url'].includes(type) ? type : 'text'
}

function fieldOptions(field) {
  const raw = field.constraints?.options || field.constraints?.enum || []
  return Array.isArray(raw) ? raw.map(option => typeof option === 'object'
    ? { value: String(option.value ?? option.key ?? ''), label: String(option.label ?? option.value ?? option.key ?? '') }
    : { value: String(option), label: String(option) }) : []
}

function blockKinds(section) {
  const configured = section?.content_schema?.block_kinds
  return Array.isArray(configured) && configured.length ? configured : ['paragraph', 'image', 'table', 'equation']
}

function isTextBlock(block) {
  return ['paragraph', 'text', 'richtext'].includes(String(block.kind || 'paragraph').toLowerCase())
}

function isImageBlock(block) {
  return ['image', 'figure'].includes(String(block.kind || '').toLowerCase())
}

function isTableBlock(block) {
  return ['table', 'grid'].includes(String(block.kind || '').toLowerCase())
}

function isEquationBlock(block) {
  return ['equation', 'formula'].includes(String(block.kind || '').toLowerCase())
}

function normalizeBlock(block) {
  if (!block.data || typeof block.data !== 'object') block.data = {}
  if (isTableBlock(block)) {
    block.data.rows = Math.max(1, Math.min(100, Number(block.data.rows) || 1))
    block.data.cols = Math.max(1, Math.min(30, Number(block.data.cols) || 1))
    resizeTable(block)
  }
  if (isImageBlock(block)) {
    block.data.url = String(block.data.url || block.data.path || '')
    block.data.caption = String(block.data.caption || '')
  }
  if (isEquationBlock(block)) {
    block.data.omml = String(block.data.omml || '')
    block.data.alt_text = String(block.data.alt_text || block.text || '')
  }
}

function resizeTable(block) {
  if (!isTableBlock(block)) return
  const rows = Math.max(1, Math.min(100, Number(block.data.rows) || 1))
  const cols = Math.max(1, Math.min(30, Number(block.data.cols) || 1))
  const existing = Array.isArray(block.data.cells) ? block.data.cells : []
  block.data.cells = Array.from({ length: rows }, (_, rowIndex) => Array.from({ length: cols }, (_, colIndex) => String(existing[rowIndex]?.[colIndex] ?? '')))
  block.data.rows = rows
  block.data.cols = cols
}

async function uploadBlockImage(block, event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  uploadingBlockId.value = block.id
  error.value = ''
  try {
    const uploaded = await uploadDocumentProjectImage(route.params.id, file)
    block.data.url = uploaded.url
    scheduleSave()
  } catch (exception) {
    error.value = exception.message
  } finally {
    uploadingBlockId.value = ''
  }
}

onMounted(load)
onBeforeUnmount(() => { if (saveTimer) clearTimeout(saveTimer) })
</script>

<style scoped>
.editor-page { max-width: 1280px; margin: 0 auto; padding: 22px 20px 56px; }
.editor-state { display: grid; place-items: center; min-height: 50vh; color: var(--on-surface-variant); }
.editor-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; padding: 10px 0 22px; border-bottom: 1px solid var(--border); }
.back-link { display: inline-flex; align-items: center; gap: 5px; color: var(--primary); font-size: 13px; text-decoration: none; }
.back-link .material-symbols-outlined { font-size: 18px; }
.title-input { display: block; width: min(720px, 100%); margin: 14px 0 6px; padding: 0; border: 0; outline: 0; background: transparent; color: var(--on-surface); font-size: 30px; font-weight: 700; }
.editor-subtitle { margin: 0; color: var(--on-surface-variant); font-size: 13px; }
.editor-actions { display: flex; align-items: center; gap: 14px; flex: none; }
.validate-action { display: inline-flex; align-items: center; gap: 6px; min-height: 34px; padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--primary); cursor: pointer; font: inherit; font-size: 12px; }
.validate-action:hover:not(:disabled) { border-color: var(--primary); }
.validate-action:disabled { cursor: wait; opacity: .6; }
.validate-action .material-symbols-outlined { font-size: 18px; }
.export-action { display: inline-flex; align-items: center; gap: 6px; min-height: 34px; padding: 7px 10px; border: 1px solid var(--primary); border-radius: 6px; background: var(--primary); color: var(--on-primary, #fff); cursor: pointer; font: inherit; font-size: 12px; }
.export-action:hover:not(:disabled) { filter: brightness(.94); }
.export-action:disabled { cursor: wait; opacity: .6; }
.export-action .material-symbols-outlined { font-size: 18px; }
.save-status { display: flex; align-items: center; gap: 7px; color: var(--on-surface-variant); font-size: 12px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success, #2f7d4a); }
.status-dot.working { background: var(--tertiary, #a56a00); }
.editor-layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 30px; padding-top: 26px; }
.editor-aside { align-self: start; position: sticky; top: 20px; display: grid; gap: 22px; }
.aside-block { padding-bottom: 18px; border-bottom: 1px solid var(--border); }
.snapshot-list { display: grid; gap: 10px; margin: 12px 0 0; }
.snapshot-list div { display: grid; gap: 3px; }
.snapshot-list dt { color: var(--on-surface-variant); font-size: 11px; }
.snapshot-list dd { margin: 0; font-size: 12px; overflow-wrap: anywhere; }
.rule-list { display: grid; gap: 9px; margin: 12px 0 0; padding: 0; list-style: none; }
.rule-list li { display: flex; gap: 7px; align-items: flex-start; font-size: 12px; line-height: 1.45; }
.rule-kind { flex: none; padding: 2px 5px; border-radius: 4px; background: color-mix(in srgb, var(--primary) 12%, var(--surface)); color: var(--primary); font-size: 10px; }
.rule-kind.manual { background: color-mix(in srgb, var(--tertiary) 13%, var(--surface)); color: var(--tertiary); }
.editor-main { min-width: 0; }
.validation-panel { padding: 18px 20px; margin: 22px 0 0; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.validation-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.validation-heading h2 { margin: 3px 0 0; font-size: 18px; }
.validation-heading strong { font-size: 12px; font-weight: 600; }
.validation-heading .clean { color: var(--success, #2f7d4a); }
.validation-heading .has-findings { color: var(--error, #b3261e); }
.finding-list { display: grid; gap: 8px; margin: 16px 0 0; padding: 0; list-style: none; }
.finding-list li { display: flex; align-items: baseline; gap: 8px; padding: 9px 10px; border-left: 3px solid var(--tertiary, #a56a00); background: color-mix(in srgb, var(--tertiary) 7%, var(--surface)); font-size: 13px; }
.finding-list li.error { border-left-color: var(--error, #b3261e); }
.finding-list li.info { border-left-color: var(--primary); }
.finding-severity { flex: none; font-size: 10px; font-weight: 700; text-transform: uppercase; }
.finding-list small { margin-left: auto; color: var(--on-surface-variant); font-size: 11px; }
.not-checked { margin: 14px 0 0; color: var(--on-surface-variant); font-size: 12px; }
.editor-section { padding-bottom: 30px; margin-bottom: 26px; border-bottom: 1px solid var(--border); }
.section-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 14px; }
.section-heading h2 { margin: 3px 0 0; font-size: 20px; }
.field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.document-field { display: grid; gap: 6px; color: var(--on-surface); font-size: 13px; font-weight: 600; }
.document-field em { color: var(--tertiary); font-size: 11px; font-style: normal; font-weight: 400; }
.document-field input, .document-field textarea, .content-block textarea, .block-fields input, .block-fields textarea, .table-editor input { width: 100%; box-sizing: border-box; padding: 10px 11px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--on-surface); font: inherit; font-weight: 400; resize: vertical; }
.document-field input[type="checkbox"] { width: auto; padding: 0; accent-color: var(--primary); }
.block-fields, .table-editor { display: grid; gap: 10px; }
.block-fields label, .table-editor label { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 12px; }
.table-editor-toolbar { display: flex; flex-wrap: wrap; gap: 10px; }
.table-editor-toolbar label { min-width: 96px; }
.table-editor-toolbar input { width: 96px; }
.table-editor-scroll { overflow-x: auto; }
.table-editor table { width: 100%; min-width: 360px; border-collapse: collapse; }
.table-editor td { padding: 4px; border: 1px solid var(--border); }
.table-editor td input { min-width: 100px; padding: 7px 8px; }
.block-list { display: grid; gap: 12px; }
.content-block { padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.block-label { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 8px; color: var(--on-surface-variant); font-size: 11px; }
.icon-action { display: inline-grid; place-items: center; width: 34px; height: 34px; padding: 0; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--primary); cursor: pointer; }
.icon-action:hover { border-color: var(--primary); }
@media (max-width: 780px) { .editor-page { padding: 14px 14px 42px; } .editor-header { align-items: flex-start; flex-direction: column; } .editor-actions { width: 100%; justify-content: space-between; } .title-input { font-size: 26px; } .editor-layout { display: block; } .editor-aside { position: static; margin-bottom: 26px; } .field-grid { grid-template-columns: 1fr; } .table-editor-toolbar { display: grid; grid-template-columns: 1fr 1fr; } .table-editor-toolbar label { min-width: 0; } .table-editor-toolbar input { width: 100%; } }
</style>
