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
          <h2>{{ importResult.filename }} {{ t('templates.importResult') }}</h2>
        </div>
        <span class="material-symbols-outlined panel-icon" aria-hidden="true">document_scanner</span>
      </div>
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
      <div class="form-actions import-actions">
        <button class="small primary" :disabled="saving" @click="createFromImport">
          <span class="material-symbols-outlined" aria-hidden="true">check_circle</span>
          {{ saving ? t('common.creating') : t('templates.confirmCreate') }}
        </button>
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
          <input ref="importInput" type="file" accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" hidden @change="onImportPick" />
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
            </div>
            <div class="template-row-actions">
              <button v-if="canManage" class="small primary" type="button" @click="$router.push('/templates/' + template.id + '/edit')">
                <span class="material-symbols-outlined" aria-hidden="true">edit</span>{{ t('templates.edit') }}
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
import ScreenDeck from '../components/ScreenDeck.vue'
import ScreenCollection from '../components/ScreenCollection.vue'
import { computed, ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import AsyncState from '../components/AsyncState.vue'
import { fetchTemplates, deleteTemplateApi, importDocxTemplate, stripTemplateComments, saveTemplate, fetchTemplate } from '../api'
import { auth } from '../auth'
import { useI18n } from '../i18n'

const router = useRouter()
const { lang, t, formatDate } = useI18n()
const isTeacher = computed(() => auth.user?.role === 'teacher')
// 「相关入口」对所有角色渲染，审计记录后端只允许 admin/college_admin，
// 不加条件教师点进去只会看到一个 403 空壳页面（同 F-20 在 Policies.vue 的漏网）。
const canViewAudit = computed(() => ['admin', 'college_admin'].includes(auth.user?.role))
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
  const styles = importResult.value?.styles || {}
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
    importOpen.value = true
    importResult.value = await importDocxTemplate(f)
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

function mergeImportedSections(imported, baseSections) {
  if (!imported.length) return baseSections

  // 只把"封面"提到最前(封面不在 Word 正文里), 其余保持模板文档里的真实顺序
  const structural = baseSections.filter((section) => section.type === 'cover')
  const fallbackAbstract = baseSections.find((section) => section.type === 'abstract')
  const fallbackToc = baseSections.find((section) => section.type === 'toc')
  const fallbackTail = baseSections.filter((section) => ['references', 'acknowledgement', 'appendix'].includes(section.type))
  const normalize = (section) => {
    const title = String(section.title || '').replace(/\s+/g, '').toLowerCase()
    let normalized = section
    if (section.type === 'english_abstract' || title.includes('英文摘要') || title.includes('englishabstract') || title === 'abstract') {
      normalized = { ...section, key: 'english_abstract', type: 'english_abstract', required: true }
    } else if (section.type === 'abstract' || title.includes('摘要')) {
      normalized = { ...section, key: 'abstract', type: 'abstract', required: true }
    } else if (section.type === 'toc' || title.includes('目录') || title.includes('contents') || title.includes('tableofcontents')) {
      normalized = { ...section, key: 'toc', type: 'toc', required: true }
    } else if (section.type === 'references' || title.includes('参考文献')) {
      normalized = { ...section, key: 'references', type: 'references', required: true }
    } else if (section.type === 'acknowledgement' || title.includes('致谢') || title.includes('谢辞')) {
      normalized = { ...section, key: 'acknowledgement', type: 'acknowledgement', required: false }
    } else if (section.type === 'appendix' || title.includes('附录')) {
      normalized = { ...section, key: 'appendix', type: 'appendix', required: false }
    }
    return { ...normalized, children: (normalized.children || []).map(normalize) }
  }

  const sections = [...structural, ...imported.map(normalize)]
  const seen = new Set()
  if (!sections.some((section) => section.type === 'abstract') && fallbackAbstract) sections.splice(1, 0, fallbackAbstract)
  // 模板文档里没有目录节点时, 在摘要之后补一个内置目录
  if (!sections.some((section) => section.type === 'toc') && fallbackToc) {
    const abstractIndex = sections.findIndex((section) => section.type === 'abstract')
    sections.splice(abstractIndex >= 0 ? abstractIndex + 1 : 1, 0, fallbackToc)
  }
  return sections.filter((section) => {
    if (seen.has(section.key)) return false
    seen.add(section.key)
    return true
  }).concat(fallbackTail.filter((section) => !seen.has(section.key)))
}


async function createFromImport() {
  saving.value = true
  error.value = ''
  try {
    // 章节结构优先使用识别结果, 未识别到时沿用内置模板
    const base = await fetchTemplate('default-gb7713')
    const importedMeta = importResult.value.meta_fields || []
    const tpl = {
      id: '',
      name: importResult.value.filename.replace(/\.docx?$/i, '') + ` (${t('templates.importWord')})`,
      description: t('templates.importDescription', { styles: importResult.value.style_names.join(lang.value === 'zh' ? '、' : ', ') }),
      sections: mergeImportedSections(
        importResult.value.sections && importResult.value.sections.length
          ? importResult.value.sections : [],
        base.sections,
      ),
      // 封面字段优先用模板识别结果(学院/班级等), 识别不到时沿用内置模板
      meta_fields: importedMeta.length ? importedMeta : base.meta_fields,
      // 封面版式(校名/报告类型/信息表): 有则按模板版式渲染封面
      cover_blocks: importResult.value.cover_blocks || [],
      // 模板源文件: 导出时以它为排版基底, 完整继承模板的样式/页眉页脚/页边距
      source_docx: importResult.value.source_docx || '',
      styles: importResult.value.styles,
    }
    const saved = await saveTemplate(tpl, true)
    importResult.value = null
    importFile.value = null
    stripResult.value = null
    await loadTemplates()
    router.push('/templates/' + saved.id + '/edit')
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
