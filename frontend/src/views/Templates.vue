<template>
  <div class="card">
    <div class="head-row">
      <h2>论文模板</h2>
      <div class="form-row" style="margin-top: 0">
        <template v-if="canManage">
          <button class="primary" @click="createNew">➕ 新建模板</button>
          <button @click="pickImport">📥 从 Word 导入</button>
          <input ref="importInput" type="file" accept=".docx" hidden @change="onImportPick" />
        </template>
        <span v-else class="readonly-tag">👀 只读模式(教师/管理员可管理模板)</span>
      </div>
    </div>
    <p style="color: var(--muted); font-size: 14px; margin-bottom: 16px">
      模板定义论文的章节结构、每类元素的排版样式(字体/字号/行距/缩进/分页)和封面字段。
      学生写作时格式自动按模板约束。<b>从 Word 导入</b>可自动识别 .docx 模板中的字体、字号、行距、缩进等样式。
    </p>

    <div v-if="error" class="error-banner">⚠ {{ error }}</div>
    <div v-if="importing" style="color: var(--muted); margin-bottom: 12px">⏳ 正在识别模板中的样式…</div>

    <!-- 导入识别结果预览 -->
    <div v-if="importResult" class="import-result">
      <h3>📄 {{ importResult.filename }} 识别结果</h3>
      <p style="color: var(--muted); font-size: 13px">
        识别到 {{ importResult.style_names.length }} 个样式:{{ importResult.style_names.join('、') }}
      </p>
      <table class="style-table">
        <thead>
          <tr><th>元素</th><th>中文字体</th><th>西文字体</th><th>字号</th><th>加粗</th><th>行距</th><th>段前</th><th>段后</th><th>缩进</th><th>对齐</th></tr>
        </thead>
        <tbody>
          <tr v-for="(s, key) in importResult.styles" :key="key">
            <td><b>{{ styleLabel(key) }}</b></td>
            <td>{{ s.font_zh }}</td>
            <td>{{ s.font_en }}</td>
            <td>{{ s.size_pt }}pt</td>
            <td>{{ s.bold ? '是' : '否' }}</td>
            <td>{{ s.line_spacing }}</td>
            <td>{{ s.space_before_pt }}pt</td>
            <td>{{ s.space_after_pt }}pt</td>
            <td>{{ s.first_line_indent_chars }}字</td>
            <td>{{ alignLabel(s.align) }}</td>
          </tr>
        </tbody>
      </table>
      <div class="form-row" style="margin-top: 12px">
        <button class="primary" :disabled="saving" @click="createFromImport">
          {{ saving ? '创建中…' : '✅ 识别正确, 创建为模板' }}
        </button>
        <button @click="importResult = null">取消</button>
      </div>
    </div>

    <div v-if="templates.length === 0" style="color: var(--muted)">暂无模板</div>
    <div v-for="t in templates" :key="t.id" class="finding" style="border-left-color: var(--primary)">
      <div class="head">
        <span style="font-weight: 600">{{ t.name }}</span>
        <span class="rule-id">{{ t.section_count }} 个章节 · {{ shortDate(t.updated_at) }}</span>
      </div>
      <div class="meta">{{ t.description || '无描述' }}</div>
      <div class="form-row" style="margin-top: 10px">
        <template v-if="canManage">
          <button class="primary" @click="$router.push('/templates/' + t.id + '/edit')">✏️ 编辑</button>
          <button v-if="t.id !== 'default-gb7713'" style="color: var(--error)" @click="remove(t)">删除</button>
        </template>
        <button v-else @click="$router.push('/templates/' + t.id + '/edit')" style="pointer-events: none; opacity: .6">🔒 只读</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { fetchTemplates, deleteTemplateApi, importDocxTemplate, saveTemplate, fetchTemplate } from '../api'
import { isManager } from '../auth'

const router = useRouter()
const canManage = isManager()
const templates = ref([])
const error = ref('')
const importing = ref(false)
const saving = ref(false)
const importResult = ref(null)
const importInput = ref(null)

const STYLE_LABELS = {
  cover_title: '封面标题', cover_meta: '封面信息', abstract: '摘要', toc: '目录',
  heading1: '一级标题', heading2: '二级标题', heading3: '三级标题',
  body: '正文', references: '参考文献', acknowledgement: '致谢', appendix: '附录',
  figure_caption: '图注', table_caption: '表注', table_cell: '表格单元格',
}
const ALIGN_LABELS = { left: '左对齐', center: '居中', right: '右对齐', justify: '两端对齐' }

onMounted(async () => {
  try { templates.value = await fetchTemplates() }
  catch (e) { error.value = e.message }
})

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
  try {
    importResult.value = await importDocxTemplate(f)
  } catch (err) {
    error.value = err.message
  } finally {
    importing.value = false
  }
}

async function createFromImport() {
  saving.value = true
  error.value = ''
  try {
    // 章节结构优先用识别的 Heading 树, 识别不到才沿用内置模板
    const base = await fetchTemplate('default-gb7713')
    const tpl = {
      id: '',
      name: importResult.value.filename.replace(/\.docx$/i, '') + ' (导入)',
      description: '由 Word 模板自动识别生成: ' + importResult.value.style_names.join('、'),
      sections: (importResult.value.sections && importResult.value.sections.length)
        ? importResult.value.sections : base.sections,
      meta_fields: base.meta_fields,
      styles: importResult.value.styles,
    }
    const saved = await saveTemplate(tpl, true)
    importResult.value = null
    templates.value = await fetchTemplates()
    router.push('/templates/' + saved.id + '/edit')
  } catch (err) {
    error.value = err.message
  } finally {
    saving.value = false
  }
}

async function remove(t) {
  if (!confirm(`确定删除模板「${t.name}」?`)) return
  try { await deleteTemplateApi(t.id); templates.value = await fetchTemplates() }
  catch (e) { error.value = e.message }
}

function shortDate(s) { return s ? s.slice(0, 10) : '' }
function styleLabel(k) { return STYLE_LABELS[k] || k }
function alignLabel(a) { return ALIGN_LABELS[a] || a }
</script>

<style scoped>
.import-result {
  border: 1px solid #bfdbfe;
  background: #eff6ff;
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 20px;
}
.style-table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px; background: #fff; }
.style-table th, .style-table td { border: 1px solid var(--border); padding: 6px 8px; text-align: left; }
.style-table th { background: #f1f5f9; color: var(--muted); font-weight: 600; }
.readonly-tag { font-size: 13px; color: var(--muted); }
</style>
