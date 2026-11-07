<template>
  <div class="workspace-page resource-page templates-page">
    <section class="workspace-hero">
      <div>
        <p class="workspace-kicker">{{ t(isTeacher ? 'sidebar.studentGuidance' : 'templates.kicker') }}</p>
        <h1>{{ t(isTeacher ? 'sidebar.publishThesisTemplate' : 'templates.title') }}</h1>
        <p>{{ t(isTeacher ? 'personalDocuments.studentTemplateDetail' : 'templates.description') }}</p>
      </div>
      <div class="workspace-identity">
        <span class="material-symbols-outlined" aria-hidden="true">description</span>
        <div>
          <strong>{{ t('common.templatesCount', { count: templates.length }) }}</strong>
          <span>{{ canManage ? t('templates.manageable') : t('templates.readonlyMode') }}</span>
        </div>
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

    <BaseModal :open="importOpen" :title="t('templates.importPreview')" @close="importOpen = false"><section v-if="importResult" class="import-result">
      <div class="panel-heading">
        <div>
          <h2>{{ importResult.filename || importResult.name }} {{ t('templates.importResult') }}</h2>
        </div>
        <span class="material-symbols-outlined panel-icon" aria-hidden="true">document_scanner</span>
      </div>
      <p class="candidate-summary">{{ t('documentPackagePublisher.sections') }}: {{ importResult.definition.sections.length }} · {{ t('documentPackagePublisher.fields') }}: {{ importResult.definition.fields.length }}</p>
      <WritingSpecificationPreview :specification="importResult.definition.extensions?.writing_specification" :markdown="importResult.definition.extensions?.writing_specification_markdown" />
      <div class="style-preview-list">
        <section v-for="group in importStyleGroups" :key="group.key" class="style-preview-group" :aria-label="t(group.label)">
          <h3>{{ t(group.label) }}</h3>
          <details v-for="(style, key) in group.styles" :key="key" class="style-preview-item">
            <summary>{{ styleLabel(key) }}</summary>
            <dl>
              <div><dt>{{ t('templates.chineseFont') }}</dt><dd>{{ style.font_zh }}</dd></div>
              <div><dt>{{ t('templates.westernFont') }}</dt><dd>{{ style.font_en }}</dd></div>
              <div><dt>{{ t('templates.fontSize') }}</dt><dd>{{ style.size_pt }}pt</dd></div>
              <div><dt>{{ t('templates.bold') }}</dt><dd>{{ style.bold ? t('common.yes') : t('common.no') }}</dd></div>
              <div><dt>{{ t('templates.lineSpacing') }}</dt><dd>{{ style.line_spacing }}</dd></div>
              <div><dt>{{ t('templates.beforeSpacing') }}</dt><dd>{{ style.space_before_pt }}pt</dd></div>
              <div><dt>{{ t('templates.afterSpacing') }}</dt><dd>{{ style.space_after_pt }}pt</dd></div>
              <div><dt>{{ t('templates.indent') }}</dt><dd>{{ style.first_line_indent_chars }}{{ t('templates.charactersUnit') }}</dd></div>
              <div><dt>{{ t('templates.alignment') }}</dt><dd>{{ alignLabel(style.align) }}</dd></div>
            </dl>
          </details>
        </section>
      </div>
      <section v-if="importResult.admission_assessment" class="template-admission" :class="importResult.admission_assessment.status" data-test="import-template-admission">
        <div class="admission-heading"><strong>{{ t('templateAdmission.title') }}</strong><span>{{ importResult.admission_assessment.status === 'blocked' ? t('templateAdmission.blocked') : t('templateAdmission.officeReview') }}</span></div>
        <p>{{ t('templateAdmission.boundVersion', { version: importResult.version, digest: importResult.admission_assessment.assessment_hash?.slice(0, 12) }) }}</p>
        <p>{{ t('templateAdmission.autoCoverage', { count: importResult.admission_assessment.auto_verifiable_constraints?.length || 0 }) }}</p>
        <ul v-if="importResult.admission_assessment.blockers?.length" class="admission-findings">
          <li v-for="item in importResult.admission_assessment.blockers" :key="`${item.code}:${item.path}`">{{ item.message }} <small>{{ item.path }}</small></li>
        </ul>
        <ul v-if="importResult.admission_assessment.manual_checks?.length" class="admission-findings manual">
          <li v-for="item in importResult.admission_assessment.manual_checks" :key="item.code">{{ item.message }}</li>
        </ul>
        <p class="admission-limit">{{ t('templateAdmission.noComplianceClaim') }}</p>
      </section>
      <ul v-if="importResult.issues?.length" class="issue-list" data-test="import-template-issues">
        <li v-for="(issue, index) in importResult.issues" :key="`${issue.code}:${issue.path}:${index}`">{{ issue.message }} <small v-if="issue.path">{{ issue.path }}</small></li>
      </ul>
      <p v-if="importResult.admission_assessment?.blockers?.length" class="admission-limit" data-test="candidate-admission-diagnostic">{{ t('templates.candidateAdmissionDiagnostic') }}</p>
      <WritingProfileConfirmation
        :template="candidateWritingTemplate"
        :candidate="importResult"
        :dirty="saving"
        @published="publishFromCandidate"
      />
      <div class="form-actions import-actions">
        <button class="small" :disabled="strippingComments" @click="downloadWithoutComments">
          <span class="material-symbols-outlined" aria-hidden="true">cleaning_services</span>
          {{ strippingComments ? t('templates.cleaningComments') : t('templates.cleanComments') }}
        </button>
        <span v-if="stripResult" class="strip-result">{{ t('templates.commentsRemoved', stripResult) }}</span>
      </div>
    </section></BaseModal>

    <div class="template-layout">
      <section class="panel template-list-panel">
        <div class="panel-heading">
          <div>
            <p class="workspace-kicker">{{ t('templates.available') }}</p>
            <h2>{{ t('templates.available') }}</h2>
            <p>{{ t('templates.choose') }}</p>
          </div>
          <div class="template-list-actions">
            <span class="template-count">{{ templates.length }}</span>
            <button class="icon-button" type="button" :title="t('common.refresh')" :aria-label="t('common.refresh')" :disabled="loading" @click="loadTemplates">
              <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
            </button>
          </div>
        </div>

        <div v-if="canManage" class="template-toolbar">
          <button class="small primary" type="button" @click="createNew">
            <span class="material-symbols-outlined" aria-hidden="true">add</span>
            {{ t('templates.new') }}
          </button>
          <button class="small" type="button" :disabled="importing" @click="pickImport">
            <span class="material-symbols-outlined" aria-hidden="true">upload_file</span>
            {{ importing ? t('templates.aiRecognizing') : t('templates.importWord') }}
          </button>
          <input ref="importInput" data-test="template-import-file" type="file" accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" hidden @change="onImportPick" />
        <button v-if="importResult" type="button" class="small" @click="importOpen = true">{{ t('templates.importPreview') }}</button></div>
        <div v-else class="readonly-note">
          <span class="material-symbols-outlined" aria-hidden="true">visibility</span>
          {{ t('templates.readonlyNote') }}
        </div>

        <AsyncState v-if="loading" state="loading" :title="t('templates.loading')" :detail="t('templates.loadingDetail')" />
        <AsyncState v-else-if="templates.length === 0" state="empty" :title="t('templates.empty')" :detail="t('templates.emptyDetail')" :action-label="t('common.reload')" @action="loadTemplates" />
        <div v-else class="template-list">
          <ScreenCollection :items="templates" :row-height="64" v-slot="{ items: visibleItems }"><article v-for="template in visibleItems" :key="template.id" class="template-row">
            <div class="template-row-icon"><span class="material-symbols-outlined" aria-hidden="true">article</span></div>
            <div class="template-row-main">
              <div class="template-row-title">
                <h3>{{ template.name }}</h3>
                <span v-if="template.id === 'default-gb7713'" class="template-state">{{ t('templates.system') }}</span>
              </div>
              <p>{{ template.description || t('templates.noDescription') }}</p>
              <div class="template-row-meta">
                <span><span class="material-symbols-outlined" aria-hidden="true">account_tree</span>{{ t('common.sectionsCount', { count: template.section_count }) }}</span>
                <span><span class="material-symbols-outlined" aria-hidden="true">schedule</span>{{ t('templates.updated', { date: shortDate(template.updated_at) }) }}</span>
                <code>{{ template.id }}</code>
              </div>
              <p v-if="template.admission" class="template-admission-summary" :class="template.admission.status" data-test="template-admission-summary">
                {{ template.admission.status === 'blocked' ? t('templateAdmission.blocked') : t('templateAdmission.draftAllowedOfficeReview') }}
                <span v-if="template.admission.blockers?.length">· {{ template.admission.blockers.length }}</span>
              </p>
            </div>
            <div class="template-row-actions">
              <button v-if="canManage" class="small primary" type="button" @click="$router.push('/templates/' + template.id + '/edit')">
                <span class="material-symbols-outlined" aria-hidden="true">{{ template.immutable ? 'visibility' : 'edit' }}</span>{{ template.immutable ? t('templates.readonly') : t('templates.edit') }}
              </button>
              <span v-else class="readonly-label"><span class="material-symbols-outlined" aria-hidden="true">lock</span>{{ t('templates.readonly') }}</span>
              <button v-if="canManage && template.id !== 'default-gb7713'" class="icon-action danger" type="button" :title="t('templates.delete')" :aria-label="t('templates.delete')" @click="remove(template)">
                <span class="material-symbols-outlined" aria-hidden="true">delete</span>
              </button>
            </div>
          </article></ScreenCollection>
        </div>
      </section>

      <ScreenDeck class="workspace-side">
        <section class="panel workspace-shortcuts">
          <div class="panel-heading">
            <div><p class="workspace-kicker">{{ t('templates.governance') }}</p><h3>{{ t('templates.related') }}</h3></div>
          </div>
          <RouterLink to="/policies"><span class="material-symbols-outlined" aria-hidden="true">menu_book</span>{{ t('templates.writingPolicies') }}<span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink>
          <RouterLink to="/organizations"><span class="material-symbols-outlined" aria-hidden="true">account_tree</span>{{ t('templates.organizations') }}<span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink>
          <RouterLink v-if="canViewAudit" to="/audit"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('templates.audit') }}<span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink>
        </section>
        <section class="panel template-side-note" :aria-label="t('templates.responsibilityTitle')">
          <span class="material-symbols-outlined" aria-hidden="true">info</span>
          <div><strong>{{ t('templates.responsibilityTitle') }}</strong><p>{{ t('templates.responsibility') }}</p></div>
        </section>
      </ScreenDeck>
    </div>
  </div>
  <ConfirmDialog
    :open="confirmDialog.open"
    :title="confirmDialog.title"
    :description="confirmDialog.description"
    :confirm-label="confirmDialog.confirmLabel"
    :danger="confirmDialog.danger"
    :busy="confirmBusy"
    @confirm="confirmAction"
    @cancel="closeConfirmDialog"
  />
</template>

<script setup>
import BaseModal from '../components/base/BaseModal.vue'
import WritingSpecificationPreview from '../components/WritingSpecificationPreview.vue'
import WritingProfileConfirmation from '../components/WritingProfileConfirmation.vue'
import ScreenDeck from '../components/ScreenDeck.vue'
import ScreenCollection from '../components/ScreenCollection.vue'
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import AsyncState from '../components/AsyncState.vue'
import {
  fetchTemplates,
  deleteTemplateApi,
  parseDocumentTemplate,
  confirmDocumentTemplateCandidate,
  publishPaperTemplateFromCandidate,
  stripTemplateComments,
} from '../api'
import { auth } from '../auth'
import { useI18n } from '../i18n'

const router = useRouter()
const { t, formatDate } = useI18n()
const isTeacher = computed(() => auth.user?.role === 'teacher')
// 试点只开放系统管理员查看审计；college_admin 是已关闭的旧角色。
const canViewAudit = computed(() => auth.user?.role === 'admin')
const canManage = computed(() => auth.user?.role === 'admin'
  || (auth.user?.role === 'teacher' && auth.user?.status === 'active'))
const templates = ref([])
const loading = ref(true)
const error = ref('')
const importing = ref(false)
const saving = ref(false)
const confirmBusy = ref(false)
const confirmDialog = ref({ open: false, title: '', description: '', confirmLabel: t('templates.confirmDelete'), danger: true, action: null })
const importOpen = ref(false)
const importResult = ref(null)
const candidateWritingTemplate = computed(() => ({
  id: importResult.value?.template_id,
  source_docx: importResult.value?.definition?.source_docx,
  meta_fields: (importResult.value?.definition?.fields || []).filter(field => !field.location || field.location === 'metadata'),
  writing_specification: importResult.value?.definition?.extensions?.writing_specification,
  source_execution: importResult.value?.definition?.extensions?.source_execution,
  version: importResult.value?.version,
}))
const importFile = ref(null)
const strippingComments = ref(false)
const stripResult = ref(null)
const importInput = ref(null)

const STYLE_KEYS = {
  cover_title: 'templates.style.coverTitle', cover_meta: 'templates.style.coverMeta', abstract: 'section.abstract', english_abstract: 'section.englishAbstract',
  keywords: 'section.keywords', english_keywords: 'section.englishKeywords', toc: 'section.toc',
  heading1: 'section.heading1', heading2: 'section.heading2', heading3: 'section.heading3', body: 'section.body', references: 'section.references',
  acknowledgement: 'section.acknowledgement', appendix: 'section.appendix', figure_caption: 'templates.style.figureCaption', table_caption: 'templates.style.tableCaption', table_cell: 'templates.style.tableCell',
}

const HEADING_STYLE_KEYS = ['heading1', 'heading2', 'heading3']
const importStyleGroups = computed(() => {
  const styles = importResult.value?.definition?.styles || {}
  return [
    { key: 'headings', label: 'templates.headingLevels', styles: Object.fromEntries(HEADING_STYLE_KEYS.filter(key => key in styles).map(key => [key, styles[key]])) },
    { key: 'other', label: 'templates.otherStyles', styles: Object.fromEntries(Object.entries(styles).filter(([key]) => !HEADING_STYLE_KEYS.includes(key))) },
  ].filter(group => Object.keys(group.styles).length)
})

async function loadTemplates() {
  loading.value = true
  error.value = ''
  try { templates.value = await fetchTemplates() }
  catch (e) { error.value = e.message || t('common.operationFailed') }
  finally { loading.value = false }
}

onMounted(loadTemplates)

function createNew() {
  router.push({ path: '/templates/new/edit' })
}

function pickImport() { importInput.value?.click() }

async function onImportPick(e) {
  const f = e.target.files?.[0]
  e.target.value = ''
  if (!f) return
  importing.value = true
  error.value = ''
  stripResult.value = null
  importFile.value = f
  try {
    importResult.value = await parseDocumentTemplate(f)
    importOpen.value = true
  } catch (err) {
    error.value = err.message || t('common.operationFailed')
  } finally {
    importing.value = false
  }
}

async function downloadWithoutComments() {
  if (!importFile.value || strippingComments.value) return
  strippingComments.value = true
  error.value = ''
  try {
    stripResult.value = await stripTemplateComments(importFile.value)
  } catch (err) {
    error.value = err.message || t('common.operationFailed')
  } finally {
    strippingComments.value = false
  }
}

async function publishFromCandidate(result) {
  if (saving.value) return
  saving.value = true
  error.value = ''
  try {
    const preparedCandidate = result?.candidate
    if (!preparedCandidate?.candidate_id) throw new Error(t('common.operationFailed'))
    importResult.value = preparedCandidate
    const blockers = preparedCandidate.admission_assessment?.blockers || []
    if (blockers.length) return
    const confirmed = await confirmDocumentTemplateCandidate(preparedCandidate)
    const saved = await publishPaperTemplateFromCandidate(confirmed.candidate_id)
    const templateId = saved.template?.id || saved.id
    if (!templateId) throw new Error(t('common.operationFailed'))
    importResult.value = null
    importFile.value = null
    stripResult.value = null
    importOpen.value = false
    await loadTemplates()
    router.push('/templates/' + templateId + '/edit')
  } catch (err) {
    error.value = err.message || t('common.operationFailed')
  } finally {
    saving.value = false
  }
}

async function remove(template) {
  confirmDialog.value = { open: true, title: t('templates.deleteTitle', { name: template.name }), description: t('templates.deleteDescription'), confirmLabel: t('templates.confirmDelete'), danger: true, action: async () => {
    error.value = ''
    await deleteTemplateApi(template.id)
    await loadTemplates()
  } }
}

function closeConfirmDialog() { if (confirmBusy.value) return; confirmDialog.value.open = false; confirmDialog.value.action = null }
async function confirmAction() {
  const action = confirmDialog.value.action
  if (!action || confirmBusy.value) return
  confirmBusy.value = true
  try { await action() } catch (e) { error.value = e.message || t('common.operationFailed') } finally { confirmBusy.value = false; closeConfirmDialog() }
}

function shortDate(s) { return s ? formatDate(s, { year: 'numeric', month: '2-digit', day: '2-digit' }) : '-' }
function styleLabel(k) { return t(STYLE_KEYS[k] || k) }
function alignLabel(a) { return t(`editor.${a}`) }
</script>

<style scoped>
.template-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 288px;
  gap: 18px;
  align-items: start;
}

.template-list-panel,
.template-side-note {
  min-width: 0;
}

.template-list-actions,
.template-toolbar,
.template-row-actions,
.template-row-meta,
.import-actions,
.readonly-note,
.readonly-label {
  display: flex;
  align-items: center;
}

.template-list-actions {
  gap: 10px;
}

.template-count {
  color: var(--on-surface-variant);
  font-size: 12px;
  white-space: nowrap;
}

.template-toolbar {
  flex-wrap: wrap;
  gap: 8px;
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
}

.readonly-note {
  gap: 7px;
  padding: 14px 0;
  border-bottom: 1px solid var(--border);
  color: var(--on-surface-variant);
  font-size: 12px;
}

.readonly-note .material-symbols-outlined {
  color: var(--primary);
  font-size: 18px;
}

.template-list {
  border-top: 1px solid var(--border);
}

.template-list-panel .template-toolbar + .template-list {
  border-top: 0;
}

.template-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  min-width: 0;
  padding: 16px 0;
  border-bottom: 1px solid var(--border);
}

.template-row:last-child {
  border-bottom: 0;
}

.template-row:hover {
  background: var(--surface-container-low);
}

.template-row-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--primary-fixed-dim);
  border-radius: var(--radius-sm);
  background: var(--surface-container-low);
  color: var(--primary);
}

.template-row-main {
  min-width: 0;
}

.template-row-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.template-row-title h3 {
  overflow: hidden;
  margin: 0;
  font-size: 15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-row-main > p {
  overflow: hidden;
  margin: 4px 0 8px;
  color: var(--on-surface-variant);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-state {
  flex: 0 0 auto;
  padding: 2px 7px;
  border-radius: var(--radius-pill);
  background: var(--primary-fixed);
  color: var(--on-primary-fixed);
  font-size: 10px;
  font-weight: 650;
}

.template-row-meta {
  flex-wrap: wrap;
  gap: 10px;
  color: var(--on-surface-variant);
  font-size: 11px;
}

.template-row-meta > span {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.template-row-meta .material-symbols-outlined {
  color: var(--primary);
  font-size: 14px;
}

.template-row-meta code {
  overflow-wrap: anywhere;
  color: var(--outline);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10px;
}

.template-admission {
  display: grid;
  gap: 8px;
  margin: 14px 0;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface-container-low);
}
.template-admission.blocked { border-color: var(--error, #b42318); background: var(--error-container, #fff1f0); }
.template-admission p, .template-admission-summary { margin: 0; color: var(--on-surface-variant); font-size: 12px; line-height: 1.5; }
.template-admission-summary { margin-top: 6px; }
.admission-heading { display: flex; justify-content: space-between; gap: 12px; }
.admission-findings { margin: 0; padding-left: 20px; display: grid; gap: 6px; }
.admission-findings small { display: block; color: var(--on-surface-variant); }
.admission-findings.manual { color: var(--on-surface-variant); }
.admission-limit { color: var(--on-surface-variant); font-size: 12px; }

.template-row-actions {
  justify-content: flex-end;
  gap: 6px;
}

.readonly-label {
  gap: 4px;
  color: var(--outline);
  font-size: 11px;
  white-space: nowrap;
}

.readonly-label .material-symbols-outlined {
  font-size: 15px;
}

.import-result {
  margin-bottom: 18px;
}

.import-actions {
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.style-preview-list { margin-top: 12px; }
.style-preview-group + .style-preview-group { margin-top: 20px; }
.style-preview-group h3 { margin: 0; padding-bottom: 10px; border-bottom: 1px solid var(--border); color: var(--on-surface-variant); font-size: 12px; font-weight: 500; }
.style-preview-item { border-bottom: 1px solid var(--border); }
.style-preview-item summary { padding: 12px 0; cursor: pointer; font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.style-preview-item dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 20px; padding: 0 0 16px; margin: 0; font-size: 12px; }
.style-preview-item dt { color: var(--on-surface-variant); }
.style-preview-item dd { margin: 4px 0 0; overflow-wrap: anywhere; }
.import-result .panel-heading h2 { overflow-wrap: anywhere; }


.strip-result {
  color: #15803d;
  font-size: 13px;
  font-weight: 500;
}

.template-side-note {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  color: var(--on-surface-variant);
}

.template-side-note > .material-symbols-outlined {
  flex: 0 0 auto;
  color: var(--primary);
  font-size: 20px;
}

.template-side-note strong {
  display: block;
  color: var(--on-surface);
  font-size: 13px;
}

.template-side-note p {
  margin-top: 5px;
  font-size: 12px;
  line-height: 1.6;
}

@media (max-width: 900px) {
  .template-layout {
    grid-template-columns: 1fr;
  }

  .template-side-note {
    display: none;
  }
}

@media (max-width: 600px) {
  .template-row {
    grid-template-columns: 30px minmax(0, 1fr);
    align-items: start;
  }

  .template-row-icon {
    width: 30px;
    height: 30px;
  }

  .template-row-actions {
    grid-column: 2;
    justify-content: flex-start;
    margin-top: -3px;
  }

  .template-row-main > p {
    white-space: normal;
  }
}
</style>
