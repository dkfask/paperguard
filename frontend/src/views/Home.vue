<template>
  <div class="stitch-dashboard">
    <!-- 1:1 Stitch Hero Section (Modern Academic Rigor) -->
    <section class="stitch-hero">
      <div class="stitch-hero-inner">
        <div class="hero-text-col">
          <p class="hero-overline">MODERN ACADEMIC RIGOR</p>
          <h1 class="hero-headline">GB/T 7713 Compliance</h1>
          <p class="hero-subtext">
            Automated structural and typographic analysis for your academic manuscript. Ensure perfect alignment with institutional formatting standards before submission.
          </p>
        </div>
        <div class="hero-badge-col">
          <div class="engine-active-card">
            <div class="engine-active-header">
              <span class="material-symbols-outlined engine-icon">verified</span>
              <span class="engine-text">Engine Active</span>
            </div>
            <p class="engine-subtext">Ruleset: GB/T 7713-1987 / 高校规范</p>
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
            Upload Inspection
          </button>
          <button class="pill-btn" :class="{ active: tab === 'projects' }" @click="tab = 'projects'">
            My Theses ({{ projects.length }})
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
              标准规则包检查
            </button>
            <button :class="{ active: checkMode === 'template' }" @click="switchCheckMode('template')">
              高校模板对照
            </button>
          </div>

          <div v-if="checkMode === 'pack'" class="rule-pack-picker">
            <label for="pack">规范选择：</label>
            <select id="pack" v-model="pack" :disabled="checking || fixing">
              <option v-for="p in packs" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>
        </div>

        <!-- 模板文件选择区 (如果是模板对照模式) -->
        <div v-if="checkMode === 'template'" class="template-upload-card">
          <div class="template-title-row">
            <span>基准模板：</span>
            <strong>{{ templateFile?.name || '尚未上传学校 .docx 模板' }}</strong>
          </div>
          <label for="template-file" class="template-pick-btn" :class="{ disabled: checking || fixing }">
            <span class="material-symbols-outlined">description</span>
            <span>{{ templateFile ? '更换基准模板' : '选择高校官方规范模板 .docx' }}</span>
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
            {{ file ? file.name : 'Drag & Drop your thesis' }}
          </h3>
          <p class="dropzone-subtitle">Supports .docx formats. Max size 50MB.</p>
          <div class="dropzone-action">
            <button class="primary" type="button" :disabled="checking || fixing" style="pointer-events: none;">
              {{ file ? '已选就绪，可重新点击选择' : 'Browse Files' }}
            </button>
          </div>
        </label>
        <input id="paper-file" ref="fileInput" class="file-input-hidden" type="file" accept=".docx" :disabled="checking || fixing" @change="onPick" />

        <!-- 触发执行体检按钮 -->
        <div class="run-inspection-bar">
          <button class="primary run-btn" :disabled="!file || (checkMode === 'template' && !templateFile) || checking || fixing || initializing" @click="runCheck">
            <span class="material-symbols-outlined">{{ checking ? 'sync' : 'search_check' }}</span>
            <span>{{ checking ? '正在深度解析排版中…' : 'Start Inspection' }}</span>
          </button>
        </div>

        <div v-if="initializing" class="hint-center">正在加载规范规则库与论文环境…</div>

        <!-- 1:1 Stitch Metrics Row & Detailed Findings -->
        <section v-if="report" class="report-container">
          <!-- 4张指标卡片 -->
          <div class="stitch-metrics-grid">
            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label">Total Issues</span>
                <span class="material-symbols-outlined text-secondary">analytics</span>
              </div>
              <span class="metric-num text-on-surface">{{ report.summary.total }}</span>
            </div>

            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label text-error">Critical Errors</span>
                <span class="material-symbols-outlined text-error">error</span>
              </div>
              <span class="metric-num text-error">{{ report.summary.error }}</span>
            </div>

            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label text-tertiary">Format Warnings</span>
                <span class="material-symbols-outlined text-tertiary">warning</span>
              </div>
              <span class="metric-num text-tertiary">{{ report.summary.warning }}</span>
            </div>

            <div class="stitch-metric-card">
              <div class="metric-top">
                <span class="metric-label text-primary">Formatting Tips</span>
                <span class="material-symbols-outlined text-primary">lightbulb</span>
              </div>
              <span class="metric-num text-primary">{{ report.summary.info }}</span>
            </div>
          </div>

          <!-- 报告详细列表与一键修复按钮 -->
          <div class="findings-wrapper">
            <div class="findings-header">
              <h2 class="findings-title">Detailed Findings</h2>
              <div v-if="hasFixableFindings && checkMode === 'pack'">
                <button class="stitch-repair-btn" :disabled="checking || fixing || initializing" @click="runFix">
                  <span class="material-symbols-outlined text-[16px]">build</span>
                  <span>{{ fixing ? '自动修复中…' : 'One-Click Repair & Download' }}</span>
                </button>
              </div>
            </div>

            <!-- 空状态：完美合规 -->
            <div v-if="report.findings.length === 0" class="stitch-empty-findings">
              <span class="material-symbols-outlined check-circle-icon">check_circle</span>
              <h3 style="font-weight: 700; margin-top: 8px">Perfect! No Formatting Issues Found</h3>
              <p style="color: var(--on-surface-variant); font-size: 14px">当前论文完全符合目标规范要求，可以直接提交评审或打印归档。</p>
            </div>

            <!-- 1:1 Stitch Finding Row (with Actual vs Expected Diff) -->
            <div v-else class="findings-list">
              <div v-for="(f, i) in report.findings" :key="i" class="stitch-finding-row">
                <div class="finding-content">
                  <div class="finding-badge-row">
                    <span class="stitch-severity-badge" :class="f.severity">
                      {{ f.severity === 'error' ? 'Critical' : (f.severity === 'warning' ? 'Warning' : 'Info') }}
                    </span>
                    <h4 class="finding-rule-name">{{ f.rule_id }}</h4>
                    <span v-if="f.location.paragraph_index !== null" class="finding-loc">
                      Paragraph #{{ f.location.paragraph_index }}
                    </span>
                  </div>

                  <!-- 1:1 Actual vs Expected Diff Grid -->
                  <div class="stitch-diff-box" v-if="f.actual !== null || f.expected !== null">
                    <div class="diff-pane">
                      <p class="diff-pane-label">ACTUAL</p>
                      <div class="diff-pane-box actual-box">{{ f.actual ?? '未设置' }}</div>
                    </div>
                    <div class="diff-pane">
                      <p class="diff-pane-label">EXPECTED (GB/T 7713 / 模板)</p>
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
            <h2 style="font-size: 20px; font-weight: 700;">My Theses Workstation</h2>
            <p style="font-size: 14px; color: var(--on-surface-variant); margin-top: 2px">所有在线撰写的毕业论文均自动与选定高校模板格式实时绑定与合规体检。</p>
          </div>
        </div>

        <!-- 新建论文条 -->
        <div class="create-thesis-bar">
          <select id="tpl" v-model="newProjectTemplate">
            <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
          <input v-model="newProjectTitle" placeholder="输入论文题目，例如：基于深度学习的高校排版引擎设计" style="flex: 1" />
          <button class="primary" :disabled="creating || initializing || !newProjectTemplate" @click="createProject">
            <span class="material-symbols-outlined text-[16px]">add</span>
            <span>{{ creating ? '创建中…' : '新建论文' }}</span>
          </button>
        </div>

        <div v-if="initializing" class="hint-center">正在读取论文列表…</div>

        <!-- 空状态 -->
        <div v-else-if="projects.length === 0" class="stitch-empty-findings">
          <span class="material-symbols-outlined" style="font-size: 40px; color: var(--outline)">note_add</span>
          <h3 style="font-weight: 700; margin-top: 10px">No Thesis Projects Yet</h3>
          <p style="color: var(--on-surface-variant); font-size: 14px">选择对应学校的格式模板并输入题目，立即开启规范约束写作体验。</p>
        </div>

        <!-- 论文卡片网格 -->
        <div v-else class="theses-grid">
          <div v-for="p in projects" :key="p.id" class="thesis-card">
            <div class="thesis-card-top">
              <span class="thesis-template-tag">
                <span class="material-symbols-outlined text-[14px]">school</span>
                {{ p.template_name || '通用学术模板' }}
              </span>
              <span class="thesis-date">{{ shortDate(p.updated_at) }}</span>
            </div>

            <h3 class="thesis-card-title" :title="p.title || p.template_name">
              {{ p.title || '未命名毕业论文' }}
            </h3>

            <div class="thesis-card-actions">
              <button class="primary small" @click="$router.push('/write/' + p.id)">
                <span class="material-symbols-outlined text-[16px]">edit_note</span>
                <span>继续写作</span>
              </button>
              <div style="display: flex; gap: 8px">
                <button class="small" @click="exportProject(p)" :disabled="exporting.has(p.id)">
                  <span class="material-symbols-outlined text-[16px]">download</span>
                  <span>{{ exporting.has(p.id) ? '导出中…' : '导出' }}</span>
                </button>
                <button class="small danger" @click="deleteProject(p)" :disabled="deleting.has(p.id)">
                  <span class="material-symbols-outlined text-[16px]">delete</span>
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
import { fetchPacks, checkDocx, checkDocxWithTemplate, fixDocx, fetchTemplates, fetchProjects, createProject as apiCreateProject, exportProject as apiExportProject, deleteProject as apiDeleteProject } from "../api"

const route = useRoute()
const router = useRouter()

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
    const [p, t, prj] = await Promise.all([fetchPacks(), fetchTemplates(), fetchProjects()])
    packs.value = p
    if (p.length) pack.value = p[0]
    templates.value = t
    if (t.length) newProjectTemplate.value = t[0].id
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
    const p = await apiCreateProject(newProjectTitle.value || "未命名毕业论文", newProjectTemplate.value)
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
  if (deleting.has(p.id) || !confirm(`确定删除「${p.title || p.template_name}」?`)) return
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

/* 1:1 Stitch Hero Section */
.stitch-hero {
  width: 100vw;
  position: relative;
  left: 50%;
  right: 50%;
  margin-left: -50vw;
  margin-right: -50vw;
  background: var(--inverse-surface);
  color: var(--inverse-on-surface);
  padding: 40px 24px;
  border-bottom: 1px solid rgba(195, 198, 215, 0.2);
  display: flex;
  justify-content: center;
}

.stitch-hero-inner {
  max-width: 1080px;
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 32px;
}

.hero-text-col {
  max-width: 680px;
}

.hero-overline {
  font-family: "Geist", sans-serif;
  font-size: 12px;
  font-weight: 600;
  color: var(--primary-fixed-dim);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.hero-headline {
  font-family: "Source Serif 4", serif;
  font-size: 38px;
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -0.02em;
  margin-bottom: 12px;
  color: #ffffff;
}

.hero-subtext {
  font-family: "Geist", sans-serif;
  font-size: 15px;
  line-height: 1.6;
  color: var(--surface-variant);
}

.hero-badge-col {
  display: flex;
  align-items: center;
}

.engine-active-card {
  background: rgba(37, 99, 235, 0.16);
  border: 1px solid rgba(37, 99, 235, 0.35);
  border-radius: var(--radius-xl);
  padding: 16px 20px;
  backdrop-filter: blur(8px);
}

.engine-active-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.engine-icon {
  color: var(--primary-fixed-dim);
  font-size: 22px;
}

.engine-text {
  font-size: 16px;
  font-weight: 600;
  color: var(--primary-fixed);
}

.engine-subtext {
  font-family: "JetBrains Mono", monospace;
  font-size: 12px;
  color: rgba(211, 228, 254, 0.8);
}

/* 1:1 Content Stage */
.stitch-content-stage {
  max-width: 1040px;
  width: 100%;
  margin-top: 32px;
}

/* 1:1 Stitch Segmented Navigation Pills */
.stitch-segmented-wrapper {
  display: flex;
  justify-content: center;
  margin-bottom: 24px;
}

.stitch-segmented-pills {
  display: inline-flex;
  background: var(--surface-container-low);
  padding: 4px;
  border-radius: var(--radius-pill);
  border: 1px solid rgba(195, 198, 215, 0.4);
}

.pill-btn {
  padding: 8px 24px;
  border-radius: var(--radius-pill);
  font-family: "Geist", sans-serif;
  font-size: 13px;
  font-weight: 500;
  color: var(--on-surface-variant);
  background: transparent;
  border: none;
  box-shadow: none;
  cursor: pointer;
  transition: all 0.2s ease;
}

.pill-btn:hover {
  color: var(--on-surface);
}

.pill-btn.active {
  background: #ffffff;
  color: var(--on-surface);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}

/* 检查选项 */
.check-options-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.mode-switch-capsule {
  display: inline-flex;
  background: var(--surface-container-low);
  padding: 3px;
  border-radius: var(--radius-md);
  gap: 4px;
}

.mode-switch-capsule button {
  border: none;
  background: transparent;
  padding: 4px 12px;
  font-size: 13px;
  border-radius: var(--radius-sm);
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
  gap: 8px;
  font-size: 13px;
  color: var(--on-surface-variant);
}

/* 1:1 Stitch Upload Dropzone */
.stitch-dropzone {
  width: 100%;
  background: var(--surface-container-lowest);
  border-radius: var(--radius-xl);
  border: 2px dashed rgba(37, 99, 235, 0.4);
  padding: 48px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  cursor: pointer;
  box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.04);
  transition: all 0.2s ease;
}

.stitch-dropzone:hover, .stitch-dropzone.dragging {
  border-color: var(--primary);
  background: var(--surface-container-low);
}

.dropzone-icon-circle {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: rgba(37, 99, 235, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 16px;
  transition: transform 0.2s ease;
}

.stitch-dropzone:hover .dropzone-icon-circle {
  transform: scale(1.1);
}

.dropzone-icon {
  font-size: 32px;
  color: var(--primary);
}

.dropzone-title {
  font-family: "Geist", sans-serif;
  font-size: 20px;
  font-weight: 600;
  color: var(--on-surface);
  margin-bottom: 6px;
}

.dropzone-subtitle {
  font-size: 14px;
  color: var(--on-surface-variant);
  margin-bottom: 20px;
}

.run-inspection-bar {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}

.run-btn {
  padding: 10px 24px;
  font-size: 15px;
  box-shadow: 0 4px 14px rgba(0, 74, 198, 0.3);
}

/* 1:1 Stitch Metrics Row */
.stitch-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-top: 32px;
}

.stitch-metric-card {
  background: var(--surface-container-lowest);
  padding: 18px 20px;
  border-radius: var(--radius-xl);
  border: 1px solid rgba(195, 198, 215, 0.4);
  box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.02);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metric-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.metric-label {
  font-family: "Geist", sans-serif;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--on-surface-variant);
}

.metric-num {
  font-family: "Source Serif 4", serif;
  font-size: 32px;
  font-weight: 700;
  line-height: 1.1;
}

/* 1:1 Stitch Detailed Findings List */
.findings-wrapper {
  margin-top: 32px;
}

.findings-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.findings-title {
  font-family: "Geist", sans-serif;
  font-size: 20px;
  font-weight: 600;
  color: var(--on-surface);
}

.stitch-repair-btn {
  background: #ffffff;
  border: 1px solid var(--primary);
  color: var(--primary);
  font-size: 13px;
  font-weight: 600;
  padding: 6px 14px;
}

.stitch-repair-btn:hover:not(:disabled) {
  background: var(--primary-container);
  color: #ffffff;
}

.stitch-finding-row {
  background: var(--surface-container-lowest);
  border-radius: var(--radius-xl);
  border: 1px solid rgba(195, 198, 215, 0.4);
  padding: 20px 24px;
  margin-bottom: 14px;
  box-shadow: 0 4px 20px -2px rgba(15, 23, 42, 0.02);
}

.finding-badge-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.stitch-severity-badge {
  font-family: "JetBrains Mono", monospace;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  text-transform: uppercase;
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
  font-size: 14px;
  font-weight: 600;
  color: var(--on-surface);
}

.finding-loc {
  font-size: 12px;
  color: var(--on-surface-variant);
  margin-left: auto;
}

/* 1:1 Stitch Actual vs Expected Diff Box */
.stitch-diff-box {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  background: var(--surface-container-low);
  padding: 16px;
  border-radius: var(--radius-lg);
  border: 1px solid rgba(195, 198, 215, 0.3);
  margin-bottom: 12px;
}

.diff-pane-label {
  font-family: "JetBrains Mono", monospace;
  font-size: 11px;
  font-weight: 600;
  color: var(--on-surface-variant);
  margin-bottom: 4px;
  text-transform: uppercase;
}

.diff-pane-box {
  padding: 10px 14px;
  border-radius: var(--radius-xs);
  font-size: 13px;
  font-family: "JetBrains Mono", monospace;
}

.diff-pane-box.actual-box {
  background: #ffffff;
  border: 1px solid rgba(195, 198, 215, 0.2);
  color: var(--on-surface-variant);
}

.diff-pane-box.expected-box {
  background: rgba(219, 225, 255, 0.25);
  border: 1px solid rgba(0, 74, 198, 0.2);
  color: var(--primary);
  font-weight: 700;
}

.finding-desc {
  font-size: 14px;
  color: var(--on-surface-variant);
}

.stitch-empty-findings {
  text-align: center;
  padding: 48px;
  background: var(--surface-container-lowest);
  border-radius: var(--radius-xl);
  border: 1px dashed var(--border);
}

.check-circle-icon {
  font-size: 48px;
  color: #10b981;
}

/* 我的论文 */
.theses-header-bar {
  margin-bottom: 20px;
}

.create-thesis-bar {
  background: var(--surface-container-lowest);
  padding: 16px 20px;
  border-radius: var(--radius-xl);
  border: 1px solid var(--border);
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 24px;
}

.theses-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 18px;
}

.thesis-card {
  background: var(--surface-container-lowest);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  padding: 22px;
  box-shadow: var(--shadow-card);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.thesis-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.thesis-template-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--surface-container-low);
  color: var(--on-surface-variant);
  font-size: 12px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
}

.thesis-date {
  font-size: 12px;
  color: var(--outline);
}

.thesis-card-title {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.4;
  margin-bottom: 20px;
}

.thesis-card-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 14px;
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
  padding: 24px;
  color: var(--on-surface-variant);
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
}
</style>
