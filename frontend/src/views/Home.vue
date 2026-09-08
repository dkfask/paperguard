<template>
  <div class="stitch-dashboard">
    <!-- 1:1 Stitch Hero Section (Modern Academic Rigor) -->
    <section class="stitch-hero">
      <div class="stitch-hero-inner">
        <div class="hero-text-col">
          <p class="hero-overline">{{ t('heroOverline') }}</p>
          <h1 class="hero-headline">{{ t('heroHeadline') }}</h1>
          <p class="hero-subtext">
            {{ t('heroSubtext') }}
          </p>
        </div>
        <div class="hero-badge-col">
          <div class="engine-active-card">
            <div class="engine-active-header">
              <span class="material-symbols-outlined engine-icon">verified</span>
              <span class="engine-text">{{ t('engineActive') }}</span>
            </div>
            <p class="engine-subtext">{{ t('rulesetText') }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- 1:1 Stitch Content Stage -->
    <div class="stitch-content-stage">
      <!-- Segmented Navigation Pills -->
      <div class="stitch-segmented-wrapper">
        <div class="stitch-segmented-pills">
          <button class="pill-btn" :class="{ active: tab === 'check' }" @click="tab = 'check'">
            {{ t('tabUpload') }}
          </button>
          <button class="pill-btn" :class="{ active: tab === 'projects' }" @click="tab = 'projects'">
            {{ t('tabProjects') }} ({{ projects.length }})
          </button>
        </div>
      </div>

      <div v-if="error" class="error-banner" role="alert" aria-live="polite">
        <span class="material-symbols-outlined" style="font-size: 18px">warning</span>
        <span>{{ error }}</span>
      </div>

      <!-- ============ TAB 1: UPLOAD INSPECTION ============ -->
      <section v-if="tab === 'check'" class="inspection-section">
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
            <span class="material-symbols-outlined">description</span>
            <span>{{ templateFile ? t('changeTemplate') : t('chooseTemplate') }}</span>
          </label>
          <input id="template-file" ref="templateInput" class="file-input-hidden" type="file" accept=".docx" :disabled="checking || fixing" @change="onTemplatePick" />
        </div>

        <!-- 1:1 Stitch Upload Dropzone -->
        <label for="paper-file" class="stitch-dropzone" :class="{ dragging, disabled: checking || fixing }"
               @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="onDrop">
          <div class="dropzone-icon-circle">
            <span class="material-symbols-outlined dropzone-icon">upload_file</span>
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
            <span class="material-symbols-outlined">{{ checking ? 'sync' : 'search_check' }}</span>
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
                <span class="material-symbols-outlined text-secondary">analytics</span>
              </div>
              <span class="metric-num text-on-surface">{{ report.summary.total }}</span>
            </div>

            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label text-error">{{ t('criticalErrors') }}</span>
                <span class="material-symbols-outlined text-error">error</span>
              </div>
              <span class="metric-num text-error">{{ report.summary.error }}</span>
            </div>

            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label text-tertiary">{{ t('formatWarnings') }}</span>
                <span class="material-symbols-outlined text-tertiary">warning</span>
              </div>
              <span class="metric-num text-tertiary">{{ report.summary.warning }}</span>
            </div>

            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label text-primary">{{ t('formattingTips') }}</span>
                <span class="material-symbols-outlined text-primary">lightbulb</span>
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
                  <span class="material-symbols-outlined text-[15px]">build</span>
                  <span>{{ fixing ? t('repairing') : t('oneClickRepair') }}</span>
                </button>
              </div>
            </div>

            <!-- 空状态：完美合规 -->
            <div v-if="report.findings.length === 0" class="stitch-empty-findings">
              <span class="material-symbols-outlined check-circle-icon">check_circle</span>
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
                      <div class="diff-pane-box actual-box">{{ f.actual ?? '未设置' }}</div>
                    </div>
                    <div class="diff-pane">
                      <p class="diff-pane-label">{{ t('expectedLabel') }}</p>
                      <div class="diff-pane-box expected-box">{{ f.expected ?? '未设置' }}</div>
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

      <!-- ============ TAB 2: MY THESES (我的论文工作台) ============ -->
      <section v-else class="theses-section">
        <div class="theses-header-bar">
          <div>
            <h2 style="font-size: 18px; font-weight: 700;">{{ t('myThesesTitle') }}</h2>
            <p style="font-size: 13px; color: var(--on-surface-variant); margin-top: 2px">{{ t('myThesesSub') }}</p>
          </div>
        </div>

        <!-- 新建论文条 -->
        <div class="create-thesis-bar">
          <select id="tpl" v-model="newProjectTemplate">
            <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
          <input v-model="newProjectTitle" :placeholder="t('thesisTitlePlaceholder')" style="flex: 1" />
          <button class="primary" :disabled="creating || initializing || !newProjectTemplate" @click="createProject">
            <span class="material-symbols-outlined text-[15px]">add</span>
            <span>{{ creating ? t('creating') : t('newThesisBtn') }}</span>
          </button>
        </div>

        <div v-if="initializing" class="hint-center">{{ t('loadingEnv') }}</div>

        <!-- 空状态 -->
        <div v-else-if="projects.length === 0" class="stitch-empty-findings">
          <span class="material-symbols-outlined" style="font-size: 36px; color: var(--outline)">note_add</span>
          <h3 style="font-weight: 700; margin-top: 8px">{{ t('noThesesTitle') }}</h3>
          <p style="color: var(--on-surface-variant); font-size: 13px">{{ t('noThesesSub') }}</p>
        </div>

        <!-- 论文卡片网格 -->
        <div v-else class="theses-grid">
          <div v-for="p in projects" :key="p.id" class="thesis-card">
            <div class="thesis-card-top">
              <span class="thesis-template-tag">
                <span class="material-symbols-outlined text-[13px]">school</span>
                {{ p.template_name || '通用学术模板' }}
              </span>
              <span class="thesis-date">{{ shortDate(p.updated_at) }}</span>
            </div>

            <h3 class="thesis-card-title" :title="p.title || p.template_name">
              {{ p.title || '未命名毕业论文' }}
            </h3>

            <div class="thesis-card-actions">
              <button class="primary small" @click="$router.push('/write/' + p.id)">
                <span class="material-symbols-outlined text-[14px]">edit_note</span>
                <span>{{ t('continueWriting') }}</span>
              </button>
              <div style="display: flex; gap: 6px">
                <button class="small" @click="exportProject(p)" :disabled="exporting.has(p.id)">
                  <span class="material-symbols-outlined text-[14px]">download</span>
                  <span>{{ exporting.has(p.id) ? t('exporting') : t('exportDocx') }}</span>
                </button>
                <button class="small danger" @click="deleteProject(p)" :disabled="deleting.has(p.id)">
                  <span class="material-symbols-outlined text-[14px]">delete</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from '../i18n'
import { fetchPacks, checkDocx, checkDocxWithTemplate, fixDocx, fetchTemplates, fetchProjects, createProject as apiCreateProject, exportProject as apiExportProject, deleteProject as apiDeleteProject } from "../api"

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const tab = ref(route.query.tab === 'projects' ? 'projects' : 'check')
watch(() => route.query.tab, (newTab) => {
  if (newTab === 'projects' || newTab === 'check') {
    tab.value = newTab
  }
})

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
const initializing = ref(false)

const fileInput = ref(null)
const templateInput = ref(null)

const templates = ref([])
const projects = ref([])
const newProjectTitle = ref("")
const newProjectTemplate = ref("")
const creating = ref(false)
const exporting = reactive(new Set())
const deleting = reactive(new Set())

const hasFixableFindings = computed(() => {
  if (!report.value || !report.value.findings) return false
  return report.value.findings.some(f => f.fixable)
})

onMounted(async () => {
  initializing.value = true
  try {
    const [p, tData, prj] = await Promise.all([fetchPacks(), fetchTemplates(), fetchProjects()])
    packs.value = p
    if (p.length) pack.value = p[0]
    templates.value = tData
    if (tData.length) newProjectTemplate.value = tData[0].id
    projects.value = prj
  } catch (e) {
    error.value = "初始化失败: " + e.message
  } finally {
    initializing.value = false
  }
})

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
    error.value = "请先选择规范模板 .docx 文件"
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
    const p = await apiCreateProject(newProjectTemplate.value, newProjectTitle.value || "未命名毕业论文")
    projects.value = await fetchProjects()
    newProjectTitle.value = ""
    router.push('/write/' + p.id)
  } catch (e) { error.value = e.message }
  finally { creating.value = false }
}

async function exportProject(p) {
  if (exporting.has(p.id)) return
  exporting.add(p.id)
  try { await apiExportProject(p.id) }
  catch (e) { error.value = e.message }
  finally { exporting.delete(p.id) }
}

async function deleteProject(p) {
  if (deleting.has(p.id) || !confirm(t('deleteConfirm'))) return
  deleting.add(p.id)
  try { await apiDeleteProject(p.id); projects.value = await fetchProjects() }
  catch (e) { error.value = e.message }
  finally { deleting.delete(p.id) }
}

function shortDate(s) { return s ? s.slice(0, 10) : '' }
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
}
</style>
