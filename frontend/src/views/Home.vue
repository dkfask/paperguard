<template>
  <div class="home-container">
    <!-- 学术迎宾 Hero 区域 -->
    <div class="hero-banner">
      <div class="hero-content">
        <div class="hero-tag">🎓 GB/T 7713 国标规范 · 高校毕业设计论文格式全流程合规引擎</div>
        <h1 class="hero-title">论文格式规范智能自检与沉浸写作</h1>
        <p class="hero-desc">
          多维度排版规则深度体检、Word 模板一键逆向解析与适配、AI 智能润色续写与自动化格式修复。
        </p>
      </div>
      <div class="hero-badges">
        <span class="hero-pill">⚡ 秒级自动分析</span>
        <span class="hero-pill">🛡 杜绝排版失分</span>
        <span class="hero-pill">🤖 AI 写作协同</span>
      </div>
    </div>

    <!-- Linear 风格 Segmented 切换胶囊 -->
    <div class="tabs-wrapper">
      <div class="segmented-tabs" role="tablist">
        <button class="segmented-tab" :class="{ active: tab === 'check' }" @click="tab = 'check'">
          📄 上传排版体检
        </button>
        <button class="segmented-tab" :class="{ active: tab === 'projects' }" @click="tab = 'projects'">
          ✍️ 我的论文工作台 ({{ projects.length }})
        </button>
      </div>
    </div>

    <div v-if="error" class="error-banner" role="alert" aria-live="polite">⚠ {{ error }}</div>

    <!-- ============ 上传检查 ============ -->
    <section v-if="tab === 'check'">
      <div class="card check-card">
        <div class="section-head">
          <h2>上传论文进行格式自检</h2>
          <div class="mode-switch-capsule" role="group" aria-label="检查模式">
            <button :class="{ active: checkMode === 'pack' }" @click="switchCheckMode('pack')">
              标准规则包检查
            </button>
            <button :class="{ active: checkMode === 'template' }" @click="switchCheckMode('template')">
              自定义模板对照
            </button>
          </div>
        </div>

        <!-- 模板文件选择区 -->
        <div v-if="checkMode === 'template'" class="template-check-files">
          <div class="file-summary">
            <span>规范基准模板：</span>
            <strong>{{ templateFile?.name || '尚未选择（请在下方载入学校下发的 .docx 模板）' }}</strong>
          </div>
          <label for="template-file" class="upload-zone compact" :class="{ disabled: checking || fixing }">
            <div class="icon-box-small">📋</div>
            <div v-if="templateFile" class="file-name">{{ templateFile.name }}</div>
            <div v-else class="file-prompt">点击选择高校官方规范模板 .docx</div>
          </label>
          <input id="template-file" ref="templateInput" class="file-input-hidden" type="file" accept=".docx" :disabled="checking || fixing" @change="onTemplatePick" />
        </div>

        <!-- 待测论文拖拽上传区 -->
        <label for="paper-file" class="upload-zone" :class="{ dragging, disabled: checking || fixing }"
               @dragover.prevent="dragging = true" @dragleave="dragging = false" @drop.prevent="onDrop">
          <div class="icon-box">
            <span v-if="file">📄</span>
            <span v-else>📁</span>
          </div>
          <div v-if="file" class="file-name">已选论文：{{ file.name }}</div>
          <div v-else class="file-name">点击选择或拖入毕业论文 .docx 文件</div>
          <div class="hint">支持 Word 2007+ (.docx)；文档仅在服务器安全内存中快速体检，不保存任何论文私密内容</div>
        </label>
        <input id="paper-file" ref="fileInput" class="file-input-hidden" type="file" accept=".docx" :disabled="checking || fixing" @change="onPick" />

        <div class="form-row check-action-bar">
          <div v-if="checkMode === 'pack'" class="rule-selector">
            <label for="pack" style="font-weight: 500; font-size: 13px; color: var(--slate-600)">检查规范：</label>
            <select id="pack" v-model="pack" :disabled="checking || fixing">
              <option v-for="p in packs" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>
          <div style="margin-left: auto">
            <button class="primary" :disabled="!file || (checkMode === 'template' && !templateFile) || checking || fixing || initializing" @click="runCheck">
              {{ checking ? '⚡ 正在深度解析排版中…' : '🚀 开始格式体检' }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="initializing" class="hint-center">正在加载规范规则库与论文环境…</div>

      <!-- 检查报告可视化结果 -->
      <section v-if="report" class="card report-card">
        <div class="report-header">
          <div>
            <h2>体检分析报告</h2>
            <div class="meta" style="margin-top: 4px">
              论文：<strong>{{ file.name }}</strong>
              <span v-if="report.context"> · 对照模板：{{ report.context.template_filename }} (包含 {{ report.context.template_section_count }} 个章节规范)</span>
              <span v-else> · 规则体系：{{ pack }}</span>
            </div>
          </div>
          <div v-if="hasFixableFindings && checkMode === 'pack'">
            <button class="fix-btn-glow" :disabled="checking || fixing || initializing" @click="runFix">
              {{ fixing ? '正在自动修复格式…' : '✨ 一键自动修复并下载' }}
            </button>
          </div>
        </div>

        <div class="summary-cards">
          <div class="summary-card total">
            <div class="num">{{ report.summary.total }}</div>
            <div class="label">排版问题总数</div>
          </div>
          <div class="summary-card error">
            <div class="num">{{ report.summary.error }}</div>
            <div class="label">严重不合规</div>
          </div>
          <div class="summary-card warning">
            <div class="num">{{ report.summary.warning }}</div>
            <div class="label">建议调整警告</div>
          </div>
          <div class="summary-card info">
            <div class="num">{{ report.summary.info }}</div>
            <div class="label">格式提示项</div>
          </div>
        </div>

        <div v-if="report.findings.length === 0" class="empty-state-card">
          <div style="font-size: 36px">🎉</div>
          <h3 style="margin-top: 8px; font-weight: 600">完美合规！未发现排版规范问题</h3>
          <p class="hint">当前论文完全符合目标模板与格式要求，可放心提交审核或答辩。</p>
        </div>

        <div v-else class="findings-list">
          <div v-for="(f, i) in report.findings" :key="i" class="finding" :class="'severity-' + f.severity">
            <div class="head">
              <span class="rule-id">{{ f.rule_id }}</span>
              <span class="badge" :class="f.severity">
                {{ { error: '严重错误', warning: '格式警告', info: '排版提示' }[f.severity] }}
              </span>
            </div>
            <div class="message">{{ f.message }}</div>

            <!-- Stitch 风格精致实际值与规范要求双栏对比卡 -->
            <div class="diff-card-grid" v-if="f.actual !== null || f.expected !== null">
              <div class="diff-col actual-col">
                <span class="diff-label">检测到的实际值 (ACTUAL)</span>
                <div class="diff-value actual-value">{{ f.actual ?? '未设置' }}</div>
              </div>
              <div class="diff-col expected-col">
                <span class="diff-label">模板与国标规范 (EXPECTED)</span>
                <div class="diff-value expected-value">{{ f.expected ?? '未设置' }}</div>
              </div>
            </div>

            <div class="meta" v-if="f.location.paragraph_index !== null">
              定位：段落 #{{ f.location.paragraph_index }}
            </div>
            <span class="snippet" v-if="f.location.text_snippet">“{{ f.location.text_snippet }}”</span>
          </div>
        </div>
      </section>
    </section>

    <!-- ============ 我的论文工作台 ============ -->
    <section v-else class="card projects-section">
      <div class="section-head">
        <div>
          <h2>我的论文项目</h2>
          <p class="hint" style="margin-top: 2px">所有在线撰写的毕业论文均自动与选定高校模板格式实时绑定与合规校验。</p>
        </div>
      </div>

      <!-- 新建论文快捷工具条 -->
      <div class="create-project-bar">
        <div class="form-row-compact">
          <select id="tpl" v-model="newProjectTemplate">
            <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
          </select>
          <input v-model="newProjectTitle" placeholder="输入论文题目，例如：基于深度学习的高校排版引擎设计" style="flex: 1" />
          <button class="primary" :disabled="creating || initializing || !newProjectTemplate" @click="createProject">
            {{ creating ? '创建中…' : '➕ 新建论文' }}
          </button>
        </div>
      </div>

      <div v-if="initializing" class="hint-center">正在加载您的论文列表…</div>

      <!-- 空状态 -->
      <div v-else-if="projects.length === 0" class="empty-state-card">
        <div style="font-size: 40px">📝</div>
        <h3 style="margin-top: 10px; font-weight: 600">暂无论文项目</h3>
        <p class="hint">上方选择对应的学校模板并输入论文题目，立即开启规范约束写作体验。</p>
      </div>

      <!-- 现代卡片网格 -->
      <div v-else class="projects-grid">
        <div v-for="p in projects" :key="p.id" class="project-card">
          <div class="project-card-header">
            <span class="project-template-badge">
              🏛 {{ p.template_name || '通用学术模板' }}
            </span>
            <span class="project-date">{{ shortDate(p.updated_at) }}</span>
          </div>

          <h3 class="project-title" :title="p.title || p.template_name">
            {{ p.title || '未命名毕业论文' }}
          </h3>

          <div class="project-footer">
            <button class="primary small" @click="$router.push('/write/' + p.id)">
              ✍️ 继续写作
            </button>
            <div style="display: flex; gap: 6px">
              <button class="small" @click="exportProject(p)" :disabled="exporting.has(p.id)" title="导出为带格式 Word 文档">
                {{ exporting.has(p.id) ? '导出中…' : '⬇️ 导出' }}
              </button>
              <button class="small danger" @click="deleteProject(p)" :disabled="deleting.has(p.id)" title="删除此论文">
                {{ deleting.has(p.id) ? '…' : '删除' }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VueOfficeDocx from '@vue-office/docx'
import '@vue-office/docx/lib/index.css'
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
  if (deleting.has(p.id) || !confirm(`确定删除「${p.title || p.template_name}」?`)) return
  deleting.add(p.id)
  try { await apiDeleteProject(p.id); projects.value = await fetchProjects() }
  catch (e) { error.value = e.message }
  finally { deleting.delete(p.id) }
}

function shortDate(s) { return s ? s.slice(0, 10) : '' }
</script>

<style scoped>
.home-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 学术 Hero Banner */
.hero-banner {
  background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
  border-radius: var(--radius-xl);
  padding: 32px 36px;
  color: #fff;
  box-shadow: var(--shadow-md);
  position: relative;
  overflow: hidden;
}

.hero-banner::before {
  content: '';
  position: absolute;
  top: -50%;
  right: -20%;
  width: 400px;
  height: 400px;
  background: radial-gradient(circle, rgba(37, 99, 235, 0.25) 0%, rgba(37, 99, 235, 0) 70%);
  border-radius: 50%;
  pointer-events: none;
}

.hero-tag {
  font-size: 13px;
  font-weight: 600;
  color: #60a5fa;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  margin-bottom: 8px;
}

.hero-title {
  font-size: 26px;
  font-weight: 800;
  letter-spacing: -0.02em;
  margin-bottom: 10px;
  background: linear-gradient(180deg, #ffffff 0%, #cbd5e1 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.hero-desc {
  color: #94a3b8;
  font-size: 14px;
  max-width: 640px;
  line-height: 1.6;
}

.hero-badges {
  display: flex;
  gap: 10px;
  margin-top: 18px;
  flex-wrap: wrap;
}

.hero-pill {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  padding: 4px 12px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  color: #e2e8f0;
}

.tabs-wrapper {
  display: flex;
  justify-content: center;
  margin: 4px 0 10px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}

.mode-switch-capsule {
  display: inline-flex;
  background: var(--slate-100);
  border: 1px solid var(--slate-200);
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
  color: var(--slate-600);
  box-shadow: none;
}

.mode-switch-capsule button.active {
  background: #fff;
  color: var(--primary);
  font-weight: 600;
  box-shadow: var(--shadow-xs);
}

.check-action-bar {
  margin-top: 18px;
  display: flex;
  align-items: center;
}

.rule-selector {
  display: flex;
  align-items: center;
  gap: 8px;
}

.report-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
  flex-wrap: wrap;
  gap: 12px;
}

.fix-btn-glow {
  background: linear-gradient(180deg, #10b981 0%, #059669 100%);
  color: #fff;
  border: 1px solid rgba(4, 120, 87, 0.5);
  font-weight: 600;
  padding: 8px 18px;
  box-shadow: 0 2px 8px rgba(16, 185, 129, 0.3);
}

.fix-btn-glow:hover:not(:disabled) {
  background: linear-gradient(180deg, #059669 0%, #047857 100%);
  box-shadow: 0 4px 12px rgba(16, 185, 129, 0.4);
}

.empty-state-card {
  text-align: center;
  padding: 48px 24px;
  background: var(--slate-50);
  border: 1px dashed var(--slate-200);
  border-radius: var(--radius-lg);
  color: var(--slate-600);
}

.diff-card-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  background: var(--slate-50);
  border: 1px solid var(--slate-200);
  border-radius: var(--radius-md);
  padding: 10px 14px;
  margin-top: 10px;
}

.diff-col {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.diff-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--slate-500);
}

.diff-value {
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-family: ui-monospace, monospace;
}

.diff-value.actual-value {
  background: #fff;
  border: 1px solid var(--slate-200);
  color: var(--slate-700);
}

.diff-value.expected-value {
  background: var(--primary-50);
  border: 1px solid var(--primary-200);
  color: var(--primary);
  font-weight: 600;
}

@media (max-width: 600px) {
  .diff-card-grid {
    grid-template-columns: 1fr;
  }
}

/* 我的论文网格 (Grid Cards) */
.create-project-bar {
  background: var(--slate-50);
  border: 1px solid var(--slate-200);
  border-radius: var(--radius-lg);
  padding: 14px 18px;
  margin-bottom: 24px;
}

.form-row-compact {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.projects-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 18px;
}

.project-card {
  background: #fff;
  border: 1px solid var(--slate-200);
  border-radius: var(--radius-lg);
  padding: 20px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  box-shadow: var(--shadow-xs);
  transition: all .2s cubic-bezier(.4, 0, .2, 1);
}

.project-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
  border-color: var(--primary-200);
}

.project-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.project-template-badge {
  background: var(--slate-100);
  color: var(--slate-700);
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
}

.project-date {
  font-size: 12px;
  color: var(--slate-400);
}

.project-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--slate-800);
  margin-bottom: 16px;
  line-height: 1.4;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.project-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 14px;
  border-top: 1px solid var(--slate-100);
}

.hint-center {
  text-align: center;
  color: var(--muted);
  padding: 20px;
  font-size: 14px;
}
</style>
