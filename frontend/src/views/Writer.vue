<template>
  <div v-if="loading" class="card loading-state">正在加载论文…</div>
  <div v-else-if="loadError" class="card">
    <div class="error-banner" role="alert">⚠ {{ loadError }}</div>
    <button @click="loadProject">重新加载</button>
    <button @click="$router.push('/')">返回论文列表</button>
  </div>
  <div v-else-if="project && template" class="writer">
    <!-- 左侧章节导航 -->
    <aside class="sidebar">
      <div class="sidebar-head">
        <span class="proj-title">{{ project.title || '未命名论文' }}</span>
        <button class="small" @click="exportDocx(false)" :disabled="exporting || saving || dirtyCount > 0">⬇️ 导出</button>
      </div>
      <nav class="tree-nav">
        <div v-for="node in flatTemplateSections" :key="node.key"
             class="nav-row" :class="{ active: current === node.key }"
             :style="{ paddingLeft: (8 + node.depth * 16) + 'px' }" @click="select(node)">
          <span v-if="isContentType(node.type) && node.key !== 'cover'">✍️</span>
          <span v-else-if="node.type === 'cover'">📋</span>
          <span class="nav-title">{{ label(node) }}</span>
          <span v-if="isRequired(node) && !isDone(node)" class="dot" title="未完成">●</span>
        </div>
      </nav>
      <div class="sidebar-foot">
        <span v-if="saving">保存中…</span>
        <span v-else-if="dirtyCount" style="color: var(--warning); font-size: 12px">有 {{ dirtyCount }} 项待保存</span>
        <span v-else-if="savedAt" style="color: var(--muted); font-size: 12px">已保存 {{ savedAt }}</span>
        <button class="small" @click="$router.push('/')">← 返回</button>
      </div>

      <!-- 字数与进度 -->
      <div class="stats-panel">
        <div class="progress-row">
          <span class="stats-label">完成度</span>
          <span class="stats-value">{{ doneCount }}/{{ requiredCount }}</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
        </div>
        <div class="stats-row">
          <span>总字数 <b>{{ totalChars }}</b></span>
          <span>本章 <b>{{ sectionChars }}</b></span>
        </div>
      </div>
    </aside>

    <!-- 右侧编辑区 -->
    <section class="editor-pane">
      <div v-if="error" class="error-banner" role="alert">⚠ {{ error }}</div>

      <!-- 实时格式检查面板 -->
      <div v-if="checking || checkResult || checkError" class="check-panel" :class="{ clean: checkResult && checkResult.summary.total === 0 }">
        <div class="check-head" @click="checkOpen = !checkOpen">
          <span v-if="checking">⏳ 检查中…</span>
          <span v-else-if="checkError">⚠ 检查失败: {{ checkError }}</span>
          <span v-else-if="checkStale">⚠ 内容已变化, 检查结果可能过期</span>
          <span v-else-if="checkResult.context && checkResult.summary.total === 0">✅ 按模板「{{ checkResult.context.template_name }}」检查通过</span>
          <span v-else>⚠ {{ checkResult.summary.total }} 个模板格式问题({{ checkResult.summary.error }} 错 / {{ checkResult.summary.warning }} 警)</span>
          <button class="tiny" :disabled="checking" @click.stop="runCheckNow">🔄 重新检查</button>
        </div>
        <div v-if="checkOpen && checkResult && checkResult.findings.length" class="check-list">
          <div v-for="(f, i) in checkResult.findings" :key="f.rule_id + ':' + (f.section_key || '') + ':' + i" class="check-item"
               :class="'severity-' + f.severity" @click="jumpTo(f)">
            <span class="badge" :class="f.severity">{{ { error: '错', warning: '警', info: '提' }[f.severity] }}</span>
            <span class="check-msg">{{ f.message }}</span>
            <span class="check-sec" v-if="f.section_key || f.location?.section_key">{{ sectionTitle(f.section_key || f.location.section_key) }} →</span>
          </div>
        </div>
      </div>

      <!-- 导出缺失清单 -->
      <div v-if="missing.length" class="missing-panel">
        <b>⚠ 以下必填项未完成, 无法导出:</b>
        <ul>
          <li v-for="(m, i) in missing" :key="i">{{ m }}</li>
        </ul>
        <div class="form-row" style="margin-top: 8px">
          <button @click="exportDocx(true)" :disabled="exporting">仍要导出(跳过检查)</button>
          <button @click="missing = []">知道了</button>
        </div>
      </div>

      <!-- 封面 -->
      <div v-if="currentSection && currentSection.type === 'cover'">
        <h2>📋 封面信息</h2>
        <p style="color: var(--muted); font-size: 14px; margin-bottom: 16px">填写封面字段, 导出时自动排版。</p>
        <div v-for="f in metaFields" :key="f.key" class="meta-row">
          <label>{{ f.label }}</label>
          <input :value="project.meta[f.key]" @input="onMetaInput(f.key, $event.target.value)"
                 :placeholder="f.required ? '必填' : '选填'" />
        </div>
        <button class="primary" @click="saveMeta" :disabled="saving">💾 {{ saving ? '保存中…' : '保存封面信息' }}</button>
      </div>

      <!-- 标题节: 标题 + 内容块 -->
      <div v-else-if="currentSection && isHeading(currentSection.type)">
        <div class="editor-head">
          <span class="type-tag">{{ typeLabel(currentSection.type) }}</span>
          <span class="hint">标题排版由模板锁定, 下方为本章正文(可插图片/表格)</span>
        </div>
        <input v-model="currentSection.title" class="heading-input"
               :class="'type-' + currentSection.type" @input="scheduleSave" />
        <block-list :paragraphs="currentSection.paragraphs" :project-id="projectId"
                    :style-class="'style-' + currentSection.type" :section-title="currentSection.title"
                    :chapter="chapterOf(currentSection.key)" @change="scheduleSave" />
      </div>

      <!-- 正文节: 内容块编辑器 -->
      <div v-else-if="currentSection">
        <div class="editor-head">
          <span class="type-tag">{{ typeLabel(currentSection.type) }}</span>
          <span class="hint">字体/字号/行距/缩进由模板自动控制, 可插图片/表格</span>
        </div>
        <input v-model="currentSection.title" class="heading-input" :class="'type-' + currentSection.type"
               @input="scheduleSave" />
        <block-list :paragraphs="currentSection.paragraphs" :project-id="projectId"
                    :style-class="'style-' + currentSection.type" :section-title="currentSection.title"
                    :chapter="chapterOf(currentSection.key)" @change="scheduleSave" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import {
  fetchProject, fetchTemplate, updateProjectSection, updateProjectMeta, exportProject, checkProjectTemplate,
} from '../api'
import BlockList from '../components/BlockList.vue'

const route = useRoute()
const router = useRouter()
const projectId = route.params.id

const project = ref(null)
const template = ref(null)
const current = ref('cover')
const error = ref('')
const loadError = ref('')
const loading = ref(true)
const saving = ref(false)
const savedAt = ref('')
const exporting = ref(false)
const checkResult = ref(null)
const checking = ref(false)
const checkError = ref('')
const checkStale = ref(false)
const checkOpen = ref(true)
const missing = ref([])
const dirtyKeys = reactive(new Set())

const saveTimers = new Map()
const saveVersions = new Map()
const pendingSaves = new Map()
let pendingMeta = null
let metaTimer = null
let saveQueue = Promise.resolve()
const inFlightSaves = new Set()
let checkGeneration = 0
let beforeUnloadHandler = null

const chapterMap = ref({})
const CONTENT_TYPES = ['body', 'references', 'acknowledgement', 'appendix', 'abstract', 'english_abstract', 'keywords', 'english_keywords']
const HEADING_TYPES = ['heading1', 'heading2', 'heading3']

const currentSection = computed(() => {
  if (!project.value) return null
  if (current.value === 'cover') return { type: 'cover', key: 'cover', title: '封面' }
  return project.value.sections.find((s) => s.key === current.value) || null
})

const metaFields = computed(() => template.value?.meta_fields || [])
const flatTemplateSections = computed(() => {
  const result = []
  function walk(nodes, depth = 0) {
    for (const node of nodes || []) {
      result.push({ ...node, depth })
      walk(node.children, depth + 1)
    }
  }
  walk(template.value?.sections)
  return result
})
const dirtyCount = computed(() => dirtyKeys.size)

onMounted(loadProject)

async function loadProject() {
  loading.value = true
  loadError.value = ''
  error.value = ''
  try {
    const p = await fetchProject(projectId)
    project.value = p
    template.value = p.template_snapshot
      ? p.template_snapshot
      : await fetchTemplate(p.template_id)
    computeChapterNumbers()
    loading.value = false
    beforeUnloadHandler = (event) => {
      if (dirtyCount.value || saving.value) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', beforeUnloadHandler)
    void runCheckNow()
  } catch (e) {
    loading.value = false
    loadError.value = e.message || '论文加载失败'
  }
}

/** 计算每个章节所属章号(一级标题/参考文献各占一章) */
function computeChapterNumbers() {
  const map = {}
  let chapter = 0
  function walk(nodes) {
    for (const node of nodes || []) {
      if (node.type === 'heading1' || node.type === 'references') chapter += 1
      map[node.key] = chapter
      walk(node.children)
    }
  }
  walk(template.value?.sections)
  chapterMap.value = map
}

function chapterOf(key) { return chapterMap.value[key] || 0 }

// ---------- 字数统计与完成度 ----------
const requiredSections = computed(() =>
  (project.value?.sections || []).filter((s) => s.required && s.type !== 'cover' && s.type !== 'toc'),
)
const doneCount = computed(() => requiredSections.value.filter((s) => isDoneForSec(s)).length)
const requiredCount = computed(() => requiredSections.value.length)
const progressPct = computed(() => requiredCount.value ? Math.round((doneCount.value / requiredCount.value) * 100) : 0)
const totalChars = computed(() => {
  if (!project.value) return 0
  return project.value.sections.reduce((n, sec) => n + sectionCharsOf(sec), 0)
})
const sectionChars = computed(() => currentSection.value ? sectionCharsOf(currentSection.value) : 0)

function sectionCharsOf(sec) {
  let n = (sec.title || '').trim().length
  for (const p of sec.paragraphs || []) {
    if (p.type === 'paragraph') n += (p.text || '').trim().length
    else if (p.type === 'table' && p.table) {
      n += p.table.cells.flat().reduce((s, c) => s + String(c || '').trim().length, 0)
    }
  }
  return n
}

function isDoneForSec(sec) {
  if (sec.type === 'cover' || sec.type === 'toc') return true
  if (sec.paragraphs && sec.paragraphs.length) {
    return sec.paragraphs.some((p) => {
      if (p.type === 'image') return !!(p.image && p.image.url)
      if (p.type === 'table') return !!p.table && p.table.cells.flat().some((c) => String(c).trim())
      return (p.text || '').trim()
    })
  }
  return !!sec.title && sec.title.trim() !== ''
}

function label(node) { return node.title || node.key }
function isContentType(type) { return CONTENT_TYPES.includes(type) }
function isHeading(type) { return HEADING_TYPES.includes(type) }
function isRequired(node) {
  const sec = project.value?.sections.find((s) => s.key === node.key)
  return node.required !== false && (sec ? sec.required !== false : true)
}
function isDone(node) {
  const sec = project.value?.sections.find((s) => s.key === node.key)
  return sec ? isDoneForSec(sec) : true
}
function typeLabel(type) {
  return {
    cover: '封面', abstract: '中文摘要', english_abstract: '英文摘要', keywords: '中文关键词', english_keywords: '英文关键词', toc: '目录', heading1: '一级标题', heading2: '二级标题',
    heading3: '三级标题', body: '正文', references: '参考文献', acknowledgement: '致谢', appendix: '附录',
  }[type] || type
}

async function select(node) {
  if (node.key === current.value) return
  try {
    await flushPendingSaves()
    current.value = node.key
  } catch { /* 保存错误已显示, 保留当前章节 */ }
}

// ---------- 保存队列 ----------
function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function markDirty(key) { dirtyKeys.add(key) }
function markClean(key) { dirtyKeys.delete(key) }

function scheduleSave() {
  const sec = currentSection.value
  if (!sec || sec.type === 'cover' || !project.value) return
  const key = sec.key
  const version = (saveVersions.get(key) || 0) + 1
  saveVersions.set(key, version)
  pendingSaves.set(key, { version, payload: clone({ title: sec.title, paragraphs: sec.paragraphs }) })
  markDirty(key)
  clearTimeout(saveTimers.get(key))
  saveTimers.set(key, setTimeout(() => {
    saveTimers.delete(key)
    const pending = pendingSaves.get(key)
    if (!pending) return
    pendingSaves.delete(key)
    enqueueSectionSave(key, pending.payload, pending.version)
  }, 800))
  scheduleCheck()
}

function onMetaInput(key, value) {
  if (!project.value) return
  project.value.meta[key] = value
  pendingMeta = { version: (saveVersions.get('__meta') || 0) + 1, payload: clone(project.value.meta) }
  saveVersions.set('__meta', pendingMeta.version)
  markDirty('__meta')
  clearTimeout(metaTimer)
  metaTimer = setTimeout(() => {
    metaTimer = null
    const pending = pendingMeta
    pendingMeta = null
    enqueueMetaSave(pending.payload, pending.version)
  }, 800)
  scheduleCheck()
}

function scheduleCheck() {
  checkStale.value = true
  clearTimeout(scheduleCheck.timer)
  scheduleCheck.timer = setTimeout(() => void runCheckNow(), 2500)
}

function enqueueSectionSave(key, payload, version) {
  const task = async () => {
    if (!project.value) return
    saving.value = true
    try {
      const result = await updateProjectSection(projectId, key, payload, project.value.revision)
      project.value.revision = result.revision
      if (saveVersions.get(key) === version && !pendingSaves.has(key)) markClean(key)
      savedAt.value = new Date().toLocaleTimeString()
    } catch (e) {
      error.value = e.message || '保存章节失败'
      throw e
    } finally {
      saving.value = false
    }
  }
  return enqueueSaveTask(task)
}

function enqueueMetaSave(payload, version) {
  const task = async () => {
    if (!project.value) return
    saving.value = true
    try {
      const result = await updateProjectMeta(projectId, payload, project.value.revision)
      project.value.revision = result.revision
      if (saveVersions.get('__meta') === version && !pendingMeta) markClean('__meta')
      savedAt.value = new Date().toLocaleTimeString()
    } catch (e) {
      error.value = e.message || '保存封面信息失败'
      throw e
    } finally {
      saving.value = false
    }
  }
  return enqueueSaveTask(task)
}

function enqueueSaveTask(task) {
  const promise = saveQueue.catch(() => {}).then(task)
  saveQueue = promise.catch(() => {})
  inFlightSaves.add(promise)
  promise.finally(() => inFlightSaves.delete(promise))
  return promise
}

async function flushPendingSaves() {
  for (const [key, pending] of pendingSaves) {
    clearTimeout(saveTimers.get(key))
    saveTimers.delete(key)
    pendingSaves.delete(key)
    enqueueSectionSave(key, pending.payload, pending.version)
  }
  if (metaTimer) {
    clearTimeout(metaTimer)
    metaTimer = null
  }
  if (pendingMeta) {
    const pending = pendingMeta
    pendingMeta = null
    enqueueMetaSave(pending.payload, pending.version)
  }
  const pendingPromises = [...inFlightSaves]
  if (pendingPromises.length) await Promise.all(pendingPromises)
}

async function saveCurrent() { scheduleSave(); await flushPendingSaves() }
async function saveMeta() {
  if (!project.value) return
  clearTimeout(metaTimer)
  metaTimer = null
  pendingMeta = null
  const version = (saveVersions.get('__meta') || 0) + 1
  saveVersions.set('__meta', version)
  markDirty('__meta')
  await enqueueMetaSave(clone(project.value.meta), version)
}

// ---------- 实时检查 ----------
async function runCheckNow() {
  if (!project.value || loading.value || checking.value) return
  const generation = ++checkGeneration
  try {
    await flushPendingSaves()
  } catch {
    checkError.value = '内容尚未保存, 请先解决保存冲突'
    return
  }
  checking.value = true
  checkError.value = ''
  try {
    const result = await checkProjectTemplate(projectId)
    if (generation !== checkGeneration) return
    checkResult.value = result
    checkStale.value = false
  } catch (e) {
    if (generation === checkGeneration) checkError.value = e.message || '检查失败'
  } finally {
    if (generation === checkGeneration) checking.value = false
  }
}

function jumpTo(f) { if (f.section_key) current.value = f.section_key }
function sectionTitle(key) {
  const sec = project.value?.sections.find((s) => s.key === key)
  return sec ? sec.title || key : key
}

async function exportDocx(force = false) {
  if (exporting.value) return
  exporting.value = true
  error.value = ''
  missing.value = []
  try {
    await flushPendingSaves()
    await exportProject(projectId, force)
  } catch (e) {
    if (e.missing && e.missing.length) missing.value = e.missing
    else error.value = e.message || '导出失败'
  } finally {
    exporting.value = false
  }
}

onBeforeRouteLeave(async () => {
  try {
    await flushPendingSaves()
    return true
  } catch {
    return false
  }
})

onBeforeUnmount(() => {
  for (const timer of saveTimers.values()) clearTimeout(timer)
  clearTimeout(metaTimer)
  clearTimeout(scheduleCheck.timer)
  if (beforeUnloadHandler) window.removeEventListener('beforeunload', beforeUnloadHandler)
})
</script>

<style scoped>
.writer { display: flex; gap: 24px; align-items: flex-start; }
.sidebar {
  width: 280px; flex-shrink: 0; background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px; position: sticky; top: 16px; max-height: calc(100vh - 120px);
  display: flex; flex-direction: column;
}
.sidebar-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.proj-title { font-weight: 600; font-size: 14px; }
.tree-nav { flex: 1; overflow-y: auto; }
.nav-row { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.nav-row:hover { background: #f1f5f9; }
.nav-row.active { background: #eff6ff; color: var(--primary); font-weight: 600; }
.nav-row.child { padding-left: 24px; }
.nav-row.grand { padding-left: 40px; }
.nav-title { flex: 1; }
.dot { color: #f59e0b; font-size: 10px; }
.sidebar-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
button.small { font-size: 13px; padding: 6px 12px; }

.editor-pane { flex: 1; min-width: 0; }
.editor-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.type-tag { background: #eff6ff; color: var(--primary); font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.hint { color: var(--muted); font-size: 13px; }
.heading-input { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 16px; margin-bottom: 20px; }

.para-block { border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px; background: var(--card); }
.para-toolbar { display: flex; align-items: center; gap: 6px; padding: 6px 10px; border-bottom: 1px solid var(--border); }
.para-no { font-size: 12px; color: var(--muted); margin-right: auto; }
button.tiny { font-size: 12px; padding: 2px 8px; }
button.tiny.on { background: var(--primary); color: #fff; border-color: var(--primary); }
button.tiny.danger { color: var(--error); }
.para-text { width: 100%; border: none; padding: 10px 12px; font-size: 14px; resize: vertical; line-height: 1.6; font-family: inherit; }
.para-text:focus { outline: none; }

/* 模板锁定样式的编辑预览 */
.para-text.style-abstract, .para-text.style-body,
.para-text.style-references, .para-text.style-acknowledgement {
  text-indent: 2em;
}

.meta-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.meta-row label { width: 120px; font-size: 14px; color: var(--muted); }
.meta-row input { flex: 1; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }

/* 实时检查面板 */
.check-panel {
  border: 1px solid #fecaca; background: #fff7f7; border-radius: 10px;
  padding: 10px 14px; margin-bottom: 16px;
}
.check-panel.clean { border-color: #bbf7d0; background: #f0fdf4; }
.check-head { display: flex; align-items: center; gap: 10px; font-size: 14px; cursor: pointer; }
.check-head .tiny { margin-left: auto; }
.check-list { margin-top: 8px; border-top: 1px solid #fee2e2; padding-top: 8px; max-height: 220px; overflow-y: auto; }
.check-item { display: flex; align-items: center; gap: 8px; padding: 5px 4px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.check-item:hover { background: #fff; }
.check-item .badge { font-size: 11px; padding: 1px 8px; }
.check-msg { flex: 1; }
.check-sec { color: var(--primary); white-space: nowrap; }

/* 导出缺失清单 */
.missing-panel {
  border: 1px solid #fecaca; background: #fef2f2; color: var(--error);
  border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; font-size: 14px;
}
.missing-panel ul { margin: 8px 0 0 20px; }
.missing-panel li { margin: 2px 0; }

/* 字数统计与进度 */
.stats-panel {
  margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border);
  font-size: 13px;
}
.progress-row { display: flex; justify-content: space-between; margin-bottom: 6px; }
.stats-label { color: var(--muted); }
.stats-value { font-weight: 600; color: var(--primary); }
.progress-bar { height: 8px; background: #e2e8f0; border-radius: 999px; overflow: hidden; margin-bottom: 10px; }
.progress-fill { height: 100%; background: var(--primary); border-radius: 999px; transition: width .3s; }
.stats-row { display: flex; justify-content: space-between; color: var(--muted); }
@media (max-width: 768px) {
  .writer { flex-direction: column; gap: 14px; }
  .sidebar { width: 100%; position: static; max-height: none; }
  .tree-nav { max-height: 220px; }
  .editor-head { align-items: flex-start; flex-wrap: wrap; }
  .meta-row { align-items: stretch; flex-direction: column; gap: 4px; }
  .meta-row label { width: auto; }
}

@media (max-width: 480px) {
  .sidebar-head, .sidebar-foot { gap: 8px; flex-wrap: wrap; }
  .check-head { align-items: flex-start; flex-wrap: wrap; }
  .check-head .tiny { margin-left: 0; }
  .check-sec { display: none; }
  .caption-row { align-items: stretch; flex-direction: column; gap: 4px; }
  .caption-num { width: max-content; }
}
</style>
