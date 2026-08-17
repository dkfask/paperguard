<template>
  <div class="writer">
    <!-- 左侧章节导航 -->
    <aside class="sidebar">
      <div class="sidebar-head">
        <span class="proj-title">{{ project.title || '未命名论文' }}</span>
        <button class="small" @click="exportDocx" :disabled="exporting">⬇️ 导出</button>
      </div>
      <nav class="tree-nav">
        <div v-for="node in templateSections" :key="node.key" class="nav-node">
          <div class="nav-row" :class="{ active: current === node.key }" @click="select(node)">
            <span v-if="isContentType(node.type) && node.key !== 'cover'">✍️</span>
            <span v-else-if="node.type === 'cover'">📋</span>
            <span class="nav-title">{{ label(node) }}</span>
            <span v-if="isRequired(node) && !isDone(node)" class="dot" title="未完成">●</span>
          </div>
          <div v-if="node.children && node.children.length" class="nav-children">
            <template v-for="child in node.children" :key="child.key">
              <div class="nav-row child" :class="{ active: current === child.key }" @click="select(child)">
                <span v-if="isContentType(child.type)">✍️</span>
                <span class="nav-title">{{ label(child) }}</span>
                <span v-if="isRequired(child) && !isDone(child)" class="dot">●</span>
              </div>
              <div v-if="child.children && child.children.length" class="nav-children">
                <div v-for="grand in child.children" :key="grand.key"
                     class="nav-row grand" :class="{ active: current === grand.key }"
                     @click="select(grand)">
                  <span class="nav-title">{{ label(grand) }}</span>
                  <span v-if="isRequired(grand) && !isDone(grand)" class="dot">●</span>
                </div>
              </div>
            </template>
          </div>
        </div>
      </nav>
      <div class="sidebar-foot">
        <span v-if="saving">保存中…</span>
        <span v-else-if="savedAt" style="color: var(--muted); font-size: 12px">已保存 {{ savedAt }}</span>
        <button class="small" @click="$router.push('/')">← 返回</button>
      </div>
    </aside>

    <!-- 右侧编辑区 -->
    <section class="editor-pane">
      <div v-if="error" class="error-banner">⚠ {{ error }}</div>

      <!-- 封面 -->
      <div v-if="currentSection && currentSection.type === 'cover'">
        <h2>📋 封面信息</h2>
        <p style="color: var(--muted); font-size: 14px; margin-bottom: 16px">填写封面字段, 导出时自动排版。</p>
        <div v-for="f in metaFields" :key="f.key" class="meta-row">
          <label>{{ f.label }}</label>
          <input :value="project.meta[f.key]" @input="project.meta[f.key] = $event.target.value"
                 :placeholder="f.required ? '必填' : '选填'" />
        </div>
        <button class="primary" @click="saveMeta">💾 保存封面信息</button>
      </div>

      <!-- 标题节: 标题 + 内容块 -->
      <div v-else-if="currentSection && isHeading(currentSection.type)">
        <div class="editor-head">
          <span class="type-tag">{{ typeLabel(currentSection.type) }}</span>
          <span class="hint">标题排版由模板锁定, 下方为本章正文(可插图片/表格)</span>
        </div>
        <input v-model="currentSection.title" class="heading-input"
               :class="'type-' + currentSection.type" @change="saveCurrent" />
        <block-list :paragraphs="currentSection.paragraphs" :project-id="projectId"
                    :style-class="'style-' + currentSection.type" :section-title="currentSection.title"
                    @change="scheduleSave" />
      </div>

      <!-- 正文节: 内容块编辑器 -->
      <div v-else-if="currentSection">
        <div class="editor-head">
          <span class="type-tag">{{ typeLabel(currentSection.type) }}</span>
          <span class="hint">字体/字号/行距/缩进由模板自动控制, 可插入图片与表格</span>
        </div>
        <input v-model="currentSection.title" class="heading-input" :class="'type-' + currentSection.type"
               @change="saveCurrent" />
        <block-list :paragraphs="currentSection.paragraphs" :project-id="projectId"
                    :style-class="'style-' + currentSection.type" :section-title="currentSection.title"
                    @change="scheduleSave" />
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import {
  fetchProject, fetchTemplate, updateProjectSection, updateProjectMeta, exportProject,
} from '../api'
import BlockList from '../components/BlockList.vue'

const route = useRoute()
const projectId = route.params.id

const project = ref(null)
const template = ref(null)
const current = ref('cover')
const error = ref('')
const saving = ref(false)
const savedAt = ref('')
const exporting = ref(false)

let saveTimer = null
const CONTENT_TYPES = ['body', 'references', 'acknowledgement', 'appendix', 'abstract']
const HEADING_TYPES = ['heading1', 'heading2', 'heading3']

const currentSection = computed(() => {
  if (!project.value) return null
  if (current.value === 'cover') {
    return { type: 'cover', key: 'cover', title: '封面' }
  }
  return project.value.sections.find((s) => s.key === current.value) || null
})

const metaFields = computed(() => template.value?.meta_fields || [])
const templateSections = computed(() => template.value?.sections || [])

onMounted(async () => {
  try {
    const p = await fetchProject(projectId)
    project.value = p
    template.value = await fetchTemplate(p.template_id)
  } catch (e) { error.value = e.message }
})

function label(node) {
  return node.title || node.key
}

function isContentType(type) { return CONTENT_TYPES.includes(type) }
function isHeading(type) { return HEADING_TYPES.includes(type) }

function isRequired(node) {
  const sec = project.value?.sections.find((s) => s.key === node.key)
  return node.required !== false && (sec ? sec.required !== false : true)
}

function isDone(node) {
  const sec = project.value?.sections.find((s) => s.key === node.key)
  if (!sec || sec.type === 'cover') return true
  if (sec.paragraphs && sec.paragraphs.length) {
    return sec.paragraphs.some((p) => {
      if (p.type === 'image') return !!(p.image && p.image.url)
      if (p.type === 'table') return !!p.table && p.table.cells.flat().some((c) => String(c).trim())
      return p.text.trim()
    })
  }
  return !!sec.title && sec.title.trim() !== ''
}

function typeLabel(type) {
  return {
    cover: '封面', abstract: '摘要', toc: '目录', heading1: '一级标题',
    heading2: '二级标题', heading3: '三级标题', body: '正文', references: '参考文献',
    acknowledgement: '致谢', appendix: '附录',
  }[type] || type
}

function select(node) {
  current.value = node.key
}

// ---------- 保存 ----------
function scheduleSave() {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(saveCurrent, 800)
}

async function saveCurrent() {
  const sec = currentSection.value
  if (!sec || sec.type === 'cover') return
  saving.value = true
  try {
    await updateProjectSection(projectId, sec.key, {
      title: sec.title,
      paragraphs: sec.paragraphs,
    })
    savedAt.value = new Date().toLocaleTimeString()
  } catch (e) { error.value = e.message }
  finally { saving.value = false }
}

async function saveMeta() {
  saving.value = true
  try {
    await updateProjectMeta(projectId, project.value.meta)
    savedAt.value = new Date().toLocaleTimeString()
  } catch (e) { error.value = e.message }
  finally { saving.value = false }
}

async function exportDocx() {
  exporting.value = true
  try { await exportProject(projectId) }
  catch (e) { error.value = e.message }
  finally { exporting.value = false }
}

onBeforeUnmount(() => clearTimeout(saveTimer))
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
</style>
