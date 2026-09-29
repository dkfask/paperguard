<template>
  <div class="writing-home">
    <section class="writing-home-head">
      <div>
        <p class="writing-eyebrow">{{ t('home.kicker') }}</p>
        <h1>{{ t('myThesesTitle') }}</h1>
        <p class="writing-home-subtitle">{{ t('myThesesSub') }}</p>
      </div>
      <div class="writing-home-summary">
        <span class="material-symbols-outlined" aria-hidden="true">edit_note</span>
        <div>
          <strong>{{ projects.length }}</strong>
          <span>{{ t('home.activeTheses') }}</span>
        </div>
      </div>
    </section>

    <div class="workspace-switcher" role="tablist" :aria-label="t('home.viewSwitcher')">
      <button class="workspace-tab" :class="{ active: tab === 'projects' }" role="tab" :aria-selected="tab === 'projects'" @click="selectTab('projects')">
        <span class="material-symbols-outlined" aria-hidden="true">edit_note</span>
        <span>{{ t('tabProjects') }}</span>
        <span class="workspace-tab-count">{{ projects.length }}</span>
      </button>
      <button class="workspace-tab" :class="{ active: tab === 'check' }" role="tab" :aria-selected="tab === 'check'" @click="selectTab('check')">
        <span class="material-symbols-outlined" aria-hidden="true">rule</span>
        <span>{{ t('tabUpload') }}</span>
      </button>
    </div>

    <div v-if="error" class="error-banner" role="alert" aria-live="polite">
      <span class="material-symbols-outlined" style="font-size: 18px" aria-hidden="true">warning</span>
      <span>{{ error }}</span>
    </div>

    <!-- ============ TAB 1: WRITING WORKSPACE ============ -->
    <section v-if="tab === 'projects'" class="writing-projects">
      <div v-if="primaryProject" class="continue-writing-panel">
        <div class="continue-writing-copy">
          <p class="writing-eyebrow">{{ t('home.continueKicker') }}</p>
          <h2>{{ primaryProject.title || t('home.untitledThesis') }}</h2>
          <p class="continue-writing-meta">
            <span>{{ primaryProject.template_name || t('home.defaultTemplate') }}</span>
            <span aria-hidden="true">·</span>
            <span>{{ primaryProject.last_written_section || t('home.startFromOutline') }}</span>
            <span aria-hidden="true">·</span>
            <span>{{ t('home.lastEdited', { date: shortDate(primaryProject.updated_at) || t('common.justNow') }) }}</span>
          </p>
          <div class="continue-writing-progress">
            <div class="progress-track"><span :style="{ width: projectProgress(primaryProject) + '%' }"></span></div>
            <span>{{ t('home.completed', { percent: projectProgress(primaryProject) }) }}</span>
          </div>
        </div>
        <button class="primary continue-writing-action" @click="$router.push('/write/' + primaryProject.id)">
          <span class="material-symbols-outlined" aria-hidden="true">play_arrow</span>
          <span>{{ t('continueWriting') }}</span>
        </button>
      </div>

      <div class="section-heading-row">
        <div>
          <p class="writing-eyebrow">{{ t('home.manuscripts') }}</p>
          <h2>{{ t('home.recentTheses') }}</h2>
        </div>
        <span class="section-heading-note">{{ t('home.sortedByRecent') }}</span>
      </div>

      <div class="create-thesis-bar">
        <span class="material-symbols-outlined create-thesis-icon" aria-hidden="true">add_circle</span>
        <div class="create-thesis-label">
          <strong>{{ t('home.startNewThesis') }}</strong>
          <span>{{ t('home.startNewThesisDetail') }}</span>
        </div>
        <select id="tpl" v-model="newProjectTemplate" :aria-label="t('home.chooseTemplate')">
          <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
        </select>
        <input v-model="newProjectTitle" :placeholder="t('thesisTitlePlaceholder')" :aria-label="t('home.thesisTitle')" />
        <button class="primary" :disabled="creating || initializing || !newProjectTemplate" @click="createProject">
          <span class="material-symbols-outlined" aria-hidden="true">add</span>
          <span>{{ creating ? t('creating') : t('newThesisBtn') }}</span>
        </button>
      </div>

      <div v-if="initializing" class="hint-center">{{ t('loadingEnv') }}</div>

      <div v-else-if="projects.length === 0" class="writing-empty-state">
        <span class="material-symbols-outlined" aria-hidden="true">note_add</span>
        <h3>{{ t('noThesesTitle') }}</h3>
        <p>{{ t('noThesesSub') }}</p>
      </div>

      <div v-else class="theses-list" :aria-label="t('home.thesisList')">
        <div v-for="p in projects" :key="p.id" class="thesis-row">
          <div class="thesis-row-icon"><span class="material-symbols-outlined" aria-hidden="true">description</span></div>
          <div class="thesis-row-main">
            <h3 :title="p.title || p.template_name">{{ p.title || t('home.untitledThesis') }}</h3>
            <p>{{ p.template_name || t('home.defaultTemplate') }} <span aria-hidden="true">·</span> {{ p.last_written_section || t('home.notStarted') }}</p>
          </div>
          <div class="thesis-row-progress">
            <div class="progress-track"><span :style="{ width: projectProgress(p) + '%' }"></span></div>
            <span>{{ projectProgress(p) }}%</span>
          </div>
          <div class="thesis-row-date">{{ shortDate(p.updated_at) }}</div>
          <div class="thesis-row-actions">
            <button class="primary small" @click="$router.push('/write/' + p.id)">
              <span class="material-symbols-outlined" aria-hidden="true">edit_note</span>
              <span>{{ t('continueWriting') }}</span>
            </button>
            <button class="icon-action" :title="exporting.has(p.id) ? t('exporting') : t('exportDocx')" :aria-label="t('exportDocx')" @click="exportProject(p)" :disabled="exporting.has(p.id)">
              <span class="material-symbols-outlined" aria-hidden="true">download</span>
            </button>
            <button class="icon-action danger" :title="t('delete')" :aria-label="t('delete')" @click="deleteProject(p)" :disabled="deleting.has(p.id)">
              <span class="material-symbols-outlined" aria-hidden="true">delete</span>
            </button>
          </div>
        </div>
      </div>

      <section v-if="trashProjects.length" class="writing-trash">
        <div class="section-heading-row"><div><p class="writing-eyebrow">{{ t('home.recycleBin') }}</p><h2>{{ t('home.recycleBin') }}</h2></div><span class="section-heading-note">{{ t('home.recycleBinDetail') }}</span></div>
        <div v-for="p in trashProjects" :key="p.id" class="trash-row"><div><strong>{{ p.title || t('home.untitledThesis') }}</strong><span>{{ p.template_name || t('home.defaultTemplate') }} · {{ t('home.deletedAt', { date: shortDate(p.deleted_at) }) }}</span></div><button class="small" @click="restoreProject(p)"><span class="material-symbols-outlined" aria-hidden="true">restore_from_trash</span>{{ t('home.restoreThesis') }}</button></div>
      </section>

      <div class="writing-utilities">
        <RouterLink to="/policies" class="writing-utility-link">
          <span class="material-symbols-outlined" aria-hidden="true">menu_book</span>
          <span><strong>{{ t('templates.writingPolicies') }}</strong><small>{{ t('home.policyDetail') }}</small></span>
          <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
        </RouterLink>
        <RouterLink to="/templates" class="writing-utility-link">
          <span class="material-symbols-outlined" aria-hidden="true">description</span>
          <span><strong>{{ t('templates.title') }}</strong><small>{{ t('home.templateDetail') }}</small></span>
          <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
        </RouterLink>
      </div>
    </section>

    <!-- ============ TAB 2: FORMAT INSPECTION ============ -->
    <section v-else class="inspection-section">
        <!-- 模式切换小胶囊 (规则包 vs 模板) -->
        <div class="check-options-bar">
          <div class="mode-switch-capsule">
            <button :class="{ active: checkMode === 'pack' }" @click="switchCheckMode('pack')">
              {{ t('modePack') }}
            </button>
            <button :class="{ active: checkMode === 'template' }" @click="switchCheckMode('template')">
              {{ t('modeTemplate') }}
            </button>
          </div>

          <div v-if="checkMode === 'pack'" class="rule-pack-picker">
            <label for="pack">{{ t('ruleSelectLabel') }}</label>
            <select id="pack" v-model="pack" :disabled="checking || fixing">
              <option v-for="p in packs" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>
        </div>

        <!-- 模板文件选择区 (如果是模板对照模式) -->
        <div v-if="checkMode === 'template'" class="template-upload-card">
          <div class="template-title-row">
            <span>{{ t('benchmarkTemplate') }}</span>
            <strong>{{ templateFile?.name || t('noTemplateSelected') }}</strong>
          </div>
          <label for="template-file" class="template-pick-btn" :class="{ disabled: checking || fixing }">
            <span class="material-symbols-outlined" aria-hidden="true">description</span>
            <span>{{ templateFile ? t('changeTemplate') : t('chooseTemplate') }}</span>
          </label>
          <input id="template-file" ref="templateInput" class="file-input-hidden" type="file" accept=".docx" :disabled="checking || fixing" @change="onTemplatePick" />
        </div>

        <!-- 1:1 Stitch Upload Dropzone -->
        <label for="paper-file" class="stitch-dropzone" :class="{ dragging, disabled: checking || fixing }"
               @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="onDrop">
          <div class="dropzone-icon-circle">
            <span class="material-symbols-outlined dropzone-icon" aria-hidden="true">upload_file</span>
          </div>
          <h3 class="dropzone-title">
            {{ file ? (t('dropzoneTitleSelected') + file.name) : t('dropzoneTitle') }}
          </h3>
          <p class="dropzone-subtitle">{{ t('dropzoneSubtitle') }}</p>
          <div class="dropzone-action">
            <button class="primary" type="button" :disabled="checking || fixing" style="pointer-events: none;">
              {{ file ? t('fileReady') : t('browseFiles') }}
            </button>
          </div>
        </label>
        <input id="paper-file" ref="fileInput" class="file-input-hidden" type="file" accept=".docx" :disabled="checking || fixing" @change="onPick" />

        <!-- 触发执行体检按钮 -->
        <div class="run-inspection-bar">
          <button class="primary run-btn" :disabled="!file || (checkMode === 'template' && !templateFile) || checking || fixing || initializing" @click="runCheck">
            <span class="material-symbols-outlined" aria-hidden="true">{{ checking ? 'sync' : 'search_check' }}</span>
            <span>{{ checking ? t('inspecting') : t('startInspection') }}</span>
          </button>
        </div>

        <div v-if="initializing" class="hint-center">{{ t('loadingEnv') }}</div>

        <!-- 1:1 Stitch Metrics Row & Detailed Findings -->
        <section v-if="report" class="report-container">
          <!-- 4张指标卡片 -->
          <div class="stitch-metrics-grid">
            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label">{{ t('totalIssues') }}</span>
                <span class="material-symbols-outlined text-secondary" aria-hidden="true">analytics</span>
              </div>
              <span class="metric-num text-on-surface">{{ report.summary.total }}</span>
            </div>

            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label text-error">{{ t('criticalErrors') }}</span>
                <span class="material-symbols-outlined text-error" aria-hidden="true">error</span>
              </div>
              <span class="metric-num text-error">{{ report.summary.error }}</span>
            </div>

            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label text-tertiary">{{ t('formatWarnings') }}</span>
                <span class="material-symbols-outlined text-tertiary" aria-hidden="true">warning</span>
              </div>
              <span class="metric-num text-tertiary">{{ report.summary.warning }}</span>
            </div>

            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label text-primary">{{ t('formattingTips') }}</span>
                <span class="material-symbols-outlined text-primary" aria-hidden="true">lightbulb</span>
              </div>
              <span class="metric-num text-primary">{{ report.summary.info }}</span>
            </div>
          </div>

          <!-- 报告详细列表与一键修复按钮 -->
          <div class="findings-wrapper">
            <div class="findings-header">
              <h2 class="findings-title">{{ t('detailedFindings') }}</h2>
              <div v-if="hasFixableFindings && checkMode === 'pack'">
                <button class="stitch-repair-btn" :disabled="checking || fixing || initializing" @click="runFix">
                  <span class="material-symbols-outlined text-[15px]" aria-hidden="true">build</span>
                  <span>{{ fixing ? t('repairing') : t('oneClickRepair') }}</span>
                </button>
              </div>
            </div>

            <!-- 空状态：完美合规 -->
            <div v-if="report.findings.length === 0" class="stitch-empty-findings">
              <span class="material-symbols-outlined check-circle-icon" aria-hidden="true">check_circle</span>
              <h3 style="font-weight: 700; margin-top: 8px">{{ t('perfectTitle') }}</h3>
              <p style="color: var(--on-surface-variant); font-size: 13px">{{ t('perfectSub') }}</p>
            </div>

            <!-- 1:1 Stitch Finding Row (with Actual vs Expected Diff) -->
            <div v-else class="findings-list">
              <div v-for="(f, i) in report.findings" :key="i" class="stitch-finding-row">
                <div class="finding-content">
                  <div class="finding-badge-row">
                    <span class="stitch-severity-badge" :class="f.severity">
                      {{ f.severity === 'error' ? t('badgeCritical') : (f.severity === 'warning' ? t('badgeWarning') : t('badgeInfo')) }}
                    </span>
                    <h4 class="finding-rule-name">{{ f.rule_id }}</h4>
                    <span v-if="f.location.paragraph_index !== null" class="finding-loc">
                      {{ t('paragraphLoc') }}{{ f.location.paragraph_index }}
                    </span>
                  </div>

                  <!-- 1:1 Actual vs Expected Diff Grid -->
                  <div class="stitch-diff-box" v-if="f.actual !== null || f.expected !== null">
                    <div class="diff-pane">
                      <p class="diff-pane-label">{{ t('actualLabel') }}</p>
                      <div class="diff-pane-box actual-box">{{ f.actual ?? t('home.notSet') }}</div>
                    </div>
                    <div class="diff-pane">
                      <p class="diff-pane-label">{{ t('expectedLabel') }}</p>
                      <div class="diff-pane-box expected-box">{{ f.expected ?? t('home.notSet') }}</div>
                    </div>
                  </div>

                  <p class="finding-desc">{{ f.message }}</p>
                  <span class="snippet" v-if="f.location.text_snippet">“{{ f.location.text_snippet }}”</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </section>

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
import { ref, reactive, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from '../i18n'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { checkDocx, checkDocxWithTemplate, fixDocx } from '../api'
import { projectsApi } from '../features/projects/api'
import { useWorkspaceOverview } from '../features/workspace/queries'

const route = useRoute()
const router = useRouter()
const { t, formatDate } = useI18n()

const tab = ref(route.query.tab === 'check' ? 'check' : 'projects')
watch(() => route.query.tab, (newTab) => {
  tab.value = newTab === 'check' ? 'check' : 'projects'
})

function selectTab(nextTab) {
  router.push({ path: '/', query: { tab: nextTab } })
}

const workspaceQuery = useWorkspaceOverview()
const packs = ref([])
const pack = ref("")
const checkMode = ref("pack")
const file = ref(null)
const templateFile = ref(null)
const dragging = ref(false)
const checking = ref(false)
const fixing = ref(false)
const report = ref(null)
const lastFixRemaining = ref(null)
const error = ref("")
const initializing = computed(() => workspaceQuery.isPending.value || workspaceQuery.isFetching.value)

const fileInput = ref(null)
const templateInput = ref(null)

const templates = ref([])
const projects = ref([])
const trashProjects = ref([])
const primaryProject = computed(() => projects.value[0] || null)
const newProjectTitle = ref("")
const newProjectTemplate = ref("")
const creating = ref(false)
const exporting = reactive(new Set())
const deleting = reactive(new Set())
const confirmBusy = ref(false)
const confirmDialog = reactive({ open: false, title: '', description: '', confirmLabel: t('templates.confirmDelete'), danger: true, action: null })

const hasFixableFindings = computed(() => {
  if (!report.value || !report.value.findings) return false
  return report.value.findings.some(f => f.fixable)
})

watch(() => workspaceQuery.data.value, (data) => {
  if (!data) return
  packs.value = data.packs || []
  if (!pack.value && packs.value.length) pack.value = packs.value[0]
  templates.value = data.templates || []
  if (!newProjectTemplate.value && templates.value.length) newProjectTemplate.value = templates.value[0].id
  projects.value = data.projects || []
  trashProjects.value = data.deletedProjects || []
}, { immediate: true })

watch(() => workspaceQuery.error.value, (exception) => {
  if (exception) error.value = t('home.initFailed', { message: exception.message || t('home.readWorkspaceFailed') })
})

async function refreshWorkspace() {
  await workspaceQuery.refetch()
}

function switchCheckMode(mode) {
  checkMode.value = mode
  report.value = null
  lastFixRemaining.value = null
  error.value = ""
}

function onPick(e) {
  if (e.target.files.length) file.value = e.target.files[0]
}

function onTemplatePick(e) {
  if (e.target.files.length) templateFile.value = e.target.files[0]
}

function onDrop(e) {
  dragging.value = false
  if (e.dataTransfer.files.length) file.value = e.dataTransfer.files[0]
}

async function runCheck() {
  if (!file.value) return
  if (checkMode.value === 'template' && !templateFile.value) {
    error.value = t('home.selectTemplateFirst')
    return
  }
  checking.value = true
  error.value = ""
  report.value = null
  lastFixRemaining.value = null
  try {
    if (checkMode.value === 'template') {
      report.value = await checkDocxWithTemplate(file.value, templateFile.value)
    } else {
      report.value = await checkDocx(file.value, pack.value)
    }
  } catch (e) {
    error.value = e.message
  } finally {
    checking.value = false
  }
}

async function runFix() {
  if (!file.value) return
  fixing.value = true
  error.value = ""
  try {
    const { blob, filename, remainingFindings } = await fixDocx(file.value, pack.value)
    lastFixRemaining.value = remainingFindings.length
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = filename || "fixed.docx"
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    error.value = e.message
  } finally {
    fixing.value = false
  }
}

async function createProject() {
  if (!newProjectTemplate.value) return
  creating.value = true
  error.value = ""
  try {
    const p = await projectsApi.create(newProjectTemplate.value, newProjectTitle.value || t('home.untitledThesis'))
    await refreshWorkspace()
    newProjectTitle.value = ""
    router.push('/write/' + p.id)
  } catch (e) { error.value = e.message }
  finally { creating.value = false }
}

async function exportProject(p) {
  if (exporting.has(p.id)) return
  exporting.add(p.id)
  try { await projectsApi.export(p.id) }
  catch (e) { error.value = e.message }
  finally { exporting.delete(p.id) }
}

async function deleteProject(p) {
  if (deleting.has(p.id)) return
  Object.assign(confirmDialog, {
    open: true,
    title: t('home.deleteTitle', { name: p.title || t('home.untitledThesis') }),
    description: t('home.deleteDescription'),
    confirmLabel: t('templates.confirmDelete'),
    danger: true,
    action: async () => {
      deleting.add(p.id)
      try { await projectsApi.remove(p.id); await refreshWorkspace() }
      finally { deleting.delete(p.id) }
    },
  })
}

function closeConfirmDialog() { if (confirmBusy.value) return; confirmDialog.open = false; confirmDialog.action = null }
async function confirmAction() {
  const action = confirmDialog.action
  if (!action || confirmBusy.value) return
  confirmBusy.value = true
  try { await action() } catch (e) { error.value = e.message } finally { confirmBusy.value = false; closeConfirmDialog() }
}

async function restoreProject(p) {
  try { await projectsApi.restore(p.id); await refreshWorkspace() }
  catch (e) { error.value = e.message }
}

function shortDate(s) { return s ? formatDate(s, { year: 'numeric', month: '2-digit', day: '2-digit' }) : '' }

function projectProgress(project) {
  const value = Number(project?.progress_pct ?? project?.progress ?? 0)
  return Math.min(100, Math.max(0, Number.isFinite(value) ? Math.round(value) : 0))
}
</script>

<style scoped>
.stitch-dashboard {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
}

/* 规范化可控比例 Hero Section */
.stitch-hero {
  width: 100%;
  background: var(--inverse-surface);
  color: var(--inverse-on-surface);
  padding: 28px 24px;
  border-radius: var(--radius-lg);
  border: 1px solid rgba(195, 198, 215, 0.2);
  margin-top: 16px;
  box-shadow: 0 4px 16px -2px rgba(15, 23, 42, 0.12);
}

.stitch-hero-inner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
}

.hero-text-col {
  max-width: 640px;
}

.hero-overline {
  font-family: "Geist", sans-serif;
  font-size: 11px;
  font-weight: 600;
  color: var(--primary-fixed-dim);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 6px;
}

.hero-headline {
  font-family: "Source Serif 4", serif;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: -0.01em;
  margin-bottom: 8px;
  color: #ffffff;
}

.hero-subtext {
  font-family: "Geist", sans-serif;
  font-size: 13px;
  line-height: 1.55;
  color: var(--surface-variant);
}

.hero-badge-col {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.engine-active-card {
  background: rgba(37, 99, 235, 0.18);
  border: 1px solid rgba(37, 99, 235, 0.35);
  border-radius: var(--radius-lg);
  padding: 12px 16px;
  backdrop-filter: blur(8px);
}

.engine-active-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
}

.engine-icon {
  color: var(--primary-fixed-dim);
  font-size: 18px;
}

.engine-text {
  font-size: 14px;
  font-weight: 600;
  color: var(--primary-fixed);
}

.engine-subtext {
  font-family: "JetBrains Mono", monospace;
  font-size: 11px;
  color: rgba(211, 228, 254, 0.85);
}

/* Content Stage */
.stitch-content-stage {
  width: 100%;
  margin-top: 20px;
}

/* 比例适中的 Segmented Navigation Pills */
.stitch-segmented-wrapper {
  display: flex;
  justify-content: center;
  margin-bottom: 18px;
}

.stitch-segmented-pills {
  display: inline-flex;
  background: var(--surface-container-low);
  padding: 3px;
  border-radius: var(--radius-pill);
  border: 1px solid var(--border);
}

.pill-btn {
  padding: 6px 18px;
  border-radius: var(--radius-pill);
  font-family: "Geist", sans-serif;
  font-size: 13px;
  font-weight: 500;
  color: var(--on-surface-variant);
  background: transparent;
  border: none;
  box-shadow: none;
  cursor: pointer;
  transition: all 0.15s ease;
}

.pill-btn:hover {
  color: var(--on-surface);
}

.pill-btn.active {
  background: #ffffff;
  color: var(--primary);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(0,0,0,0.06);
}

/* 检查选项 */
.check-options-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
  flex-wrap: wrap;
  gap: 10px;
}

.mode-switch-capsule {
  display: inline-flex;
  background: var(--surface-container-low);
  padding: 2px;
  border-radius: var(--radius-sm);
  gap: 2px;
  border: 1px solid var(--border);
}

.mode-switch-capsule button {
  border: none;
  background: transparent;
  padding: 4px 10px;
  font-size: 12px;
  border-radius: var(--radius-xs);
  color: var(--on-surface-variant);
  box-shadow: none;
}

.mode-switch-capsule button.active {
  background: #ffffff;
  color: var(--primary);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}

.rule-pack-picker {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--on-surface-variant);
}

/* 模板选择辅助卡 */
.template-upload-card {
  background: var(--surface-container-lowest);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  padding: 12px 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}

.template-title-row {
  font-size: 13px;
  color: var(--on-surface-variant);
}

.template-pick-btn {
  font-size: 12px;
  padding: 4px 10px;
  background: var(--surface-container-low);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--primary);
  font-weight: 500;
}

/* 优雅紧凑的 Dropzone */
.stitch-dropzone {
  width: 100%;
  background: var(--surface-container-lowest);
  border-radius: var(--radius-lg);
  border: 2px dashed rgba(37, 99, 235, 0.4);
  padding: 32px 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  cursor: pointer;
  box-shadow: var(--shadow-card);
  transition: all 0.15s ease;
}

.stitch-dropzone:hover, .stitch-dropzone.dragging {
  border-color: var(--primary);
  background: var(--surface-container-low);
}

.dropzone-icon-circle {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: rgba(37, 99, 235, 0.08);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
  transition: transform 0.15s ease;
}

.stitch-dropzone:hover .dropzone-icon-circle {
  transform: scale(1.06);
}

.dropzone-icon {
  font-size: 26px;
  color: var(--primary);
}

.dropzone-title {
  font-family: "Geist", sans-serif;
  font-size: 16px;
  font-weight: 600;
  color: var(--on-surface);
  margin-bottom: 4px;
}

.dropzone-subtitle {
  font-size: 13px;
  color: var(--on-surface-variant);
  margin-bottom: 14px;
}

.run-inspection-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.run-btn {
  padding: 8px 20px;
  font-size: 14px;
}

/* 4张指标卡片（紧凑舒适比例） */
.stitch-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-top: 24px;
}

.stitch-metric-card {
  background: var(--surface-container-lowest);
  padding: 14px 16px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metric-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.metric-label {
  font-family: "Geist", sans-serif;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--on-surface-variant);
}

.metric-num {
  font-family: "Source Serif 4", serif;
  font-size: 26px;
  font-weight: 700;
  line-height: 1.1;
}

.text-error { color: var(--error) !important; }
.text-tertiary { color: var(--tertiary) !important; }
.text-primary { color: var(--primary) !important; }

/* 报告列表与修复按钮 */
.findings-wrapper {
  margin-top: 24px;
}

.findings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}

.findings-title {
  font-family: "Geist", sans-serif;
  font-size: 17px;
  font-weight: 600;
  color: var(--on-surface);
}

.stitch-repair-btn {
  background: #ffffff;
  border: 1px solid var(--primary);
  color: var(--primary);
  font-size: 12px;
  font-weight: 600;
  padding: 5px 12px;
}

.stitch-repair-btn:hover:not(:disabled) {
  background: var(--primary-container);
  color: #ffffff;
}

.stitch-finding-row {
  background: var(--surface-container-lowest);
  border-radius: var(--radius-md);
  border: 1px solid var(--border);
  padding: 16px 18px;
  margin-bottom: 12px;
  box-shadow: var(--shadow-card);
}

.finding-badge-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.stitch-severity-badge {
  font-family: "JetBrains Mono", monospace;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: var(--radius-pill);
  font-weight: 700;
}

.stitch-severity-badge.error {
  background: var(--error-container);
  color: var(--on-error-container);
}

.stitch-severity-badge.warning {
  background: var(--tertiary-container);
  color: var(--on-tertiary-container);
}

.stitch-severity-badge.info {
  background: var(--surface-container-high);
  color: var(--primary);
}

.finding-rule-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--on-surface);
}

.finding-loc {
  font-size: 11px;
  color: var(--on-surface-variant);
  margin-left: auto;
}

/* 双栏 Diff 对比 */
.stitch-diff-box {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  background: var(--surface-container-low);
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(195, 198, 215, 0.4);
  margin-bottom: 8px;
}

.diff-pane-label {
  font-family: "JetBrains Mono", monospace;
  font-size: 10px;
  font-weight: 600;
  color: var(--on-surface-variant);
  margin-bottom: 4px;
}

.diff-pane-box {
  padding: 6px 10px;
  border-radius: var(--radius-xs);
  font-size: 12px;
  font-family: "JetBrains Mono", monospace;
}

.diff-pane-box.actual-box {
  background: #ffffff;
  border: 1px solid var(--border);
  color: var(--on-surface-variant);
}

.diff-pane-box.expected-box {
  background: rgba(219, 225, 255, 0.4);
  border: 1px solid rgba(0, 74, 198, 0.2);
  color: var(--primary);
  font-weight: 600;
}

.finding-desc {
  font-size: 13px;
  color: var(--on-surface-variant);
}

.stitch-empty-findings {
  text-align: center;
  padding: 36px 20px;
  background: var(--surface-container-lowest);
  border-radius: var(--radius-lg);
  border: 1px dashed var(--border);
}

.check-circle-icon {
  font-size: 38px;
  color: #10b981;
}

/* 我的论文 */
.theses-header-bar {
  margin-bottom: 16px;
}

.create-thesis-bar {
  background: var(--surface-container-lowest);
  padding: 12px 16px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border);
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 20px;
}

.theses-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.thesis-card {
  background: var(--surface-container-lowest);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 18px;
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.thesis-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.thesis-template-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--surface-container-low);
  color: var(--on-surface-variant);
  font-size: 11px;
  padding: 2px 6px;
  border-radius: var(--radius-pill);
}

.thesis-date {
  font-size: 11px;
  color: var(--outline);
}

.thesis-card-title {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  margin-bottom: 16px;
}

.thesis-card-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 10px;
  border-top: 1px solid var(--surface-container-low);
}

.snippet {
  display: block;
  background: var(--surface-container-low);
  padding: 6px 10px;
  border-radius: var(--radius-xs);
  font-size: 12px;
  color: var(--on-surface-variant);
  margin-top: 6px;
  font-family: "JetBrains Mono", monospace;
}

.hint-center {
  text-align: center;
  padding: 20px;
  color: var(--on-surface-variant);
  font-size: 13px;
}

/* Stitch writing-first dashboard */
.writing-home {
  width: 100%;
  max-width: 1280px;
  margin: 0 auto;
}

.writing-home-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 34px 0 26px;
  border-bottom: 1px solid var(--border);
}

.writing-eyebrow {
  color: var(--primary);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: .08em;
  line-height: 1.4;
  margin-bottom: 6px;
}

.writing-home-head h1 {
  font-size: 28px;
  line-height: 1.25;
  font-weight: 650;
  margin-bottom: 6px;
}

.writing-home-subtitle {
  color: var(--on-surface-variant);
  font-size: 13px;
  max-width: 680px;
}

.writing-home-summary {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--primary);
  padding: 8px 12px;
  background: var(--surface-container-low);
  border: 1px solid var(--primary-fixed);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.writing-home-summary > .material-symbols-outlined { font-size: 22px; }
.writing-home-summary div { display: flex; flex-direction: column; gap: 1px; }
.writing-home-summary strong { font-family: "JetBrains Mono", monospace; font-size: 17px; line-height: 1; }
.writing-home-summary span:last-child { color: var(--on-surface-variant); font-size: 11px; }

.workspace-switcher {
  display: flex;
  align-items: center;
  gap: 24px;
  min-height: 52px;
  border-bottom: 1px solid var(--border);
}

.workspace-tab {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 52px;
  padding: 0 2px;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--on-surface-variant);
  position: relative;
  box-shadow: none;
}

.workspace-tab:hover { color: var(--primary); background: transparent; }
.workspace-tab.active { color: var(--primary); font-weight: 650; }
.workspace-tab.active::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; background: var(--primary); }
.workspace-tab .material-symbols-outlined { font-size: 18px; }
.workspace-tab-count { min-width: 20px; padding: 1px 5px; border-radius: 10px; background: var(--surface-container-low); font-family: "JetBrains Mono", monospace; font-size: 10px; text-align: center; }

.writing-projects { padding-top: 24px; }

.continue-writing-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 22px 24px;
  border: 1px solid var(--outline-variant);
  border-left: 3px solid var(--primary);
  border-radius: var(--radius-sm);
  background: var(--surface-container-lowest);
  box-shadow: var(--shadow-card);
  margin-bottom: 30px;
}

.continue-writing-copy { min-width: 0; }
.continue-writing-copy h2 { font-size: 19px; line-height: 1.4; margin-bottom: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.continue-writing-meta { display: flex; flex-wrap: wrap; gap: 7px; color: var(--on-surface-variant); font-size: 12px; }
.continue-writing-progress { display: flex; align-items: center; gap: 10px; margin-top: 14px; color: var(--on-surface-variant); font-size: 11px; }
.progress-track { height: 6px; width: 180px; border-radius: 999px; overflow: hidden; background: var(--surface-container-high); }
.progress-track span { display: block; height: 100%; border-radius: inherit; background: var(--primary); transition: width .2s ease; }
.continue-writing-action { flex-shrink: 0; display: inline-flex; align-items: center; gap: 6px; }

.section-heading-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.section-heading-row h2 { font-size: 17px; font-weight: 650; }
.section-heading-note { color: var(--on-surface-variant); font-size: 12px; }

.create-thesis-bar {
  display: grid;
  grid-template-columns: auto minmax(185px, 1fr) 200px minmax(220px, 1.4fr) auto;
  gap: 12px;
  align-items: center;
  margin-bottom: 18px;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-container-low);
  box-shadow: none;
}

.create-thesis-icon { color: var(--primary); font-size: 22px; }
.create-thesis-label { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.create-thesis-label strong { font-size: 13px; }
.create-thesis-label span { color: var(--on-surface-variant); font-size: 11px; white-space: nowrap; }
.create-thesis-bar select, .create-thesis-bar input { min-width: 0; height: 34px; }

.writing-empty-state {
  padding: 48px 24px;
  border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border);
  text-align: center;
  color: var(--on-surface-variant);
}
.writing-empty-state .material-symbols-outlined { color: var(--outline); font-size: 36px; margin-bottom: 8px; }
.writing-empty-state h3 { color: var(--on-surface); font-size: 16px; margin-bottom: 4px; }
.writing-empty-state p { font-size: 13px; }

.theses-list { border-top: 1px solid var(--border); }
.thesis-row {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr) 200px 90px auto;
  align-items: center;
  gap: 14px;
  min-height: 76px;
  padding: 12px 4px;
  border-bottom: 1px solid var(--border);
}
.thesis-row:hover { background: rgba(239, 244, 255, .45); }
.thesis-row-icon { width: 30px; height: 30px; display: grid; place-items: center; border: 1px solid var(--primary-fixed); border-radius: var(--radius-xs); background: var(--surface-container-low); color: var(--primary); }
.thesis-row-icon .material-symbols-outlined { font-size: 17px; }
.thesis-row-main { min-width: 0; }
.thesis-row-main h3 { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-bottom: 3px; }
.thesis-row-main p { color: var(--on-surface-variant); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.thesis-row-progress { display: flex; align-items: center; gap: 8px; color: var(--on-surface-variant); font-family: "JetBrains Mono", monospace; font-size: 11px; }
.thesis-row-progress .progress-track { width: 120px; }
.thesis-row-date { color: var(--on-surface-variant); font-family: "JetBrains Mono", monospace; font-size: 11px; }
.thesis-row-actions { display: flex; align-items: center; justify-content: flex-end; gap: 5px; }
.thesis-row-actions .small { display: inline-flex; align-items: center; gap: 5px; }
.icon-action { display: inline-grid; place-items: center; width: 30px; height: 30px; padding: 0; border: 1px solid transparent; background: transparent; color: var(--on-surface-variant); border-radius: var(--radius-xs); }
.icon-action:hover { background: var(--surface-container-low); color: var(--primary); }
.icon-action.danger:hover { color: var(--error); background: var(--error-container); }
.icon-action .material-symbols-outlined { font-size: 17px; }
.writing-trash { margin-top: 28px; padding-top: 18px; border-top: 1px solid var(--border); }.trash-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 11px 4px; border-bottom: 1px solid var(--border); }.trash-row strong, .trash-row span { display: block; }.trash-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }

.writing-utilities { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-top: 30px; padding-top: 18px; border-top: 1px solid var(--border); }
.writing-utility-link { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 11px 4px; color: var(--on-surface); text-decoration: none; border-bottom: 1px solid transparent; }
.writing-utility-link:hover { color: var(--primary); border-bottom-color: var(--primary-fixed); }
.writing-utility-link > .material-symbols-outlined:first-child { color: var(--primary); font-size: 19px; }
.writing-utility-link > span:nth-child(2) { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.writing-utility-link strong { font-size: 13px; }
.writing-utility-link small { color: var(--on-surface-variant); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.writing-utility-link > .material-symbols-outlined:last-child { color: var(--outline); font-size: 17px; }

@media (max-width: 960px) {
  .create-thesis-bar { grid-template-columns: auto minmax(160px, 1fr) minmax(180px, 1fr); }
  .create-thesis-label { grid-column: span 2; }
  .create-thesis-bar select { grid-column: 2; }
  .create-thesis-bar input { grid-column: 3; }
  .create-thesis-bar button { grid-column: 1 / -1; justify-self: end; }
  .thesis-row { grid-template-columns: 32px minmax(0, 1fr) auto; }
  .thesis-row-progress, .thesis-row-date { display: none; }
}

@media (max-width: 768px) {
  .stitch-metrics-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .stitch-diff-box {
    grid-template-columns: 1fr;
  }
  .stitch-hero-inner {
    flex-direction: column;
    align-items: flex-start;
  }
  .create-thesis-bar {
    flex-direction: column;
    align-items: stretch;
  }
  .writing-home-head { align-items: flex-start; flex-direction: column; padding-top: 24px; }
  .writing-home-head h1 { font-size: 23px; }
  .writing-home-summary { align-self: stretch; }
  .workspace-switcher { gap: 18px; overflow-x: auto; }
  .continue-writing-panel { align-items: flex-start; flex-direction: column; padding: 18px; }
  .continue-writing-copy h2 { white-space: normal; }
  .continue-writing-action { width: 100%; justify-content: center; }
  .create-thesis-bar { display: flex; flex-direction: column; align-items: stretch; }
  .create-thesis-icon { display: none; }
  .create-thesis-label span { white-space: normal; }
  .writing-utilities { grid-template-columns: 1fr; }
  .thesis-row { grid-template-columns: 30px minmax(0, 1fr); gap: 10px; }
  .thesis-row-actions { grid-column: 2; justify-content: flex-start; }
  .thesis-row-actions .primary.small { flex: 1; justify-content: center; }
}
</style>
