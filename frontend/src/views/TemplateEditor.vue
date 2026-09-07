<template>
  <div class="card">
    <div class="head-row">
      <h2>{{ isNew ? '新建模板' : '编辑模板' }}</h2>
      <div class="form-row" style="margin-top: 0">
        <button class="primary" :disabled="saving || loading || !!loadError" @click="save">{{ saving ? '保存中…' : '💾 保存模板' }}</button>
        <button @click="$router.push('/templates')">返回</button>
      </div>
    </div>
    <div v-if="loadError" class="error-banner" role="alert">⚠ {{ loadError }}</div>
    <div v-else-if="loading" style="color: var(--muted); margin-bottom: 12px">正在加载模板…</div>
    <div v-if="error" class="error-banner" role="alert">⚠ {{ error }}</div>
    <div v-if="saved" class="saved-banner" role="status" aria-live="polite">✅ 模板已保存{{ savedAt ? ` · ${savedAt}` : '' }}</div>

    <!-- 基本信息 -->
    <div class="form-row">
      <label>模板 ID</label>
      <input v-model="tpl.id" :disabled="!isNew" placeholder="如 school-2026"
             style="padding: 8px; border: 1px solid var(--border); border-radius: 8px" />
      <label>模板名称</label>
      <input v-model="tpl.name" placeholder="如 XX 大学学位论文模板"
             style="padding: 8px; border: 1px solid var(--border); border-radius: 8px; width: 240px" />
    </div>
    <div class="form-row">
      <label>描述</label>
      <input v-model="tpl.description" placeholder="模板说明"
             style="padding: 8px; border: 1px solid var(--border); border-radius: 8px; width: 100%" />
    </div>
    <div style="margin-top: 12px">
      <label style="font-size: 14px; display: flex; align-items: center; gap: 8px; cursor: pointer;">
        <input type="checkbox" v-model="tpl.allow_structure_edit" />
        <span style="font-weight: 500;">允许学生自由增删和调整论文章节框架（如新增子节、调整章名）</span>
      </label>
      <p style="color: var(--muted); font-size: 12px; margin: 4px 0 0 24px;">
        关闭时，学生仅能在固定的章节大纲内撰写正文与插入图表，不可擅自增删章节。
      </p>
    </div>

    <!-- 封面字段 -->
    <h3 style="margin: 24px 0 12px">📋 封面字段</h3>
    <div v-for="(f, i) in tpl.meta_fields" :key="i" class="form-row">
      <input v-model="f.key" placeholder="字段key(如 author)" style="padding: 6px; border: 1px solid var(--border); border-radius: 6px; width: 180px" />
      <input v-model="f.label" placeholder="显示名(如 作者)" style="padding: 6px; border: 1px solid var(--border); border-radius: 6px" />
      <label style="font-size: 13px"><input type="checkbox" v-model="f.required" /> 必填</label>
      <button style="color: var(--error)" @click="tpl.meta_fields.splice(i, 1)">删除</button>
    </div>
    <button style="margin-top: 8px" @click="tpl.meta_fields.push({ key: '', label: '', required: true })">➕ 添加封面字段</button>

    <!-- 章节结构 -->
    <h3 style="margin: 24px 0 12px">📑 章节结构</h3>
    <div class="tree">
      <section-node v-for="node in tpl.sections" :key="node.key" :node="node"
                    @remove="removeTopLevel(node.key)" />
    </div>
    <button style="margin-top: 8px" @click="addSection()">➕ 添加顶层章节</button>

    <!-- 样式规范 -->
    <h3 style="margin: 24px 0 12px">🎨 样式规范</h3>
    <p style="color: var(--muted); font-size: 13px; margin-bottom: 12px">
      每类元素的排版样式。学生写作时自动应用, 不可修改。
    </p>
    <div v-for="key in stylesKeys" :key="key" class="style-block">
      <div class="head-row" @click="toggleStyle(key)" style="cursor: pointer">
        <span style="font-weight: 600">{{ styleLabel(key) }}</span>
        <span class="rule-id">{{ collapsed[key] ? '展开 ▾' : '收起 ▴' }}</span>
      </div>
      <div v-show="!collapsed[key]" class="style-grid">
        <label>中文字体 <input v-model="tpl.styles[key].font_zh" /></label>
        <label>西文字体 <input v-model="tpl.styles[key].font_en" /></label>
        <label>字号(pt) <input type="number" step="0.5" v-model.number="tpl.styles[key].size_pt" /></label>
        <label>行距 <input type="number" step="0.1" v-model.number="tpl.styles[key].line_spacing" /></label>
        <label>段前(pt) <input type="number" step="1" v-model.number="tpl.styles[key].space_before_pt" /></label>
        <label>段后(pt) <input type="number" step="1" v-model.number="tpl.styles[key].space_after_pt" /></label>
        <label>首行缩进(字符) <input type="number" step="0.5" v-model.number="tpl.styles[key].first_line_indent_chars" /></label>
        <label>对齐
          <select v-model="tpl.styles[key].align">
            <option value="left">左对齐</option>
            <option value="center">居中</option>
            <option value="right">右对齐</option>
            <option value="justify">两端对齐</option>
          </select>
        </label>
        <label class="checkbox"><input type="checkbox" v-model="tpl.styles[key].bold" /> 加粗</label>
        <label class="checkbox"><input type="checkbox" v-model="tpl.styles[key].italic" /> 斜体</label>
        <label class="checkbox"><input type="checkbox" v-model="tpl.styles[key].underline" /> 下划线</label>
        <label class="checkbox"><input type="checkbox" v-model="tpl.styles[key].page_break_before" /> 段前分页</label>
        <div class="style-preview">
          <span>预览:</span>
          <span class="preview-text" :style="previewStyle(tpl.styles[key])">
            {{ styleLabel(key) }}示例 — 汉字 Abc 123
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchTemplate, saveTemplate } from '../api'
import SectionNode from '../components/SectionNode.vue'

const route = useRoute()
const router = useRouter()
const tplId = computed(() => String(route.params.id || ''))
const isNew = computed(() => tplId.value === 'new')

const STYLE_LABELS = {
  cover_title: '封面标题', cover_meta: '封面信息',
  abstract: '中文摘要', english_abstract: '英文摘要', keywords: '中文关键词', english_keywords: '英文关键词', toc: '目录', heading1: '一级标题(章)',
  heading2: '二级标题(节)', heading3: '三级标题(小节)',
  body: '正文', references: '参考文献',
  acknowledgement: '致谢', appendix: '附录',
  figure_caption: '图注', table_caption: '表注', table_cell: '表格单元格',
}
const DEFAULT_STYLE = () => ({
  font_zh: '宋体', font_en: 'Times New Roman', size_pt: 12,
  bold: false, italic: false, underline: false, align: 'justify',
  line_spacing: 1.5, space_before_pt: 0, space_after_pt: 0,
  first_line_indent_chars: 0, page_break_before: false,
})

const tpl = ref({
  id: '', name: '', description: '',
  allow_structure_edit: true,
  sections: [], styles: {}, meta_fields: [],
})
const stylesKeys = ref([])
const collapsed = ref({})
const error = ref('')
const saved = ref(false)
const savedAt = ref('')
const saving = ref(false)
const loadError = ref('')
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  loadError.value = ''
  if (isNew.value) {
    // 新模板: 以默认样式为起点, 一个空结构
    tpl.value.id = ''
    tpl.value.name = ''
    tpl.value.styles = {
      cover_title: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 22, bold: true, align: 'center' },
      cover_meta: { ...DEFAULT_STYLE(), size_pt: 16, align: 'center', line_spacing: 2 },
      abstract: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 18, bold: true, align: 'center', page_break_before: true },
      toc: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 18, bold: true, align: 'center', page_break_before: true },
      heading1: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 18, bold: true, align: 'center', page_break_before: true },
      heading2: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 16, bold: true, align: 'left' },
      heading3: { ...DEFAULT_STYLE(), font_zh: '黑体', size_pt: 14, bold: true, align: 'left' },
      body: { ...DEFAULT_STYLE(), first_line_indent_chars: 2 },
      references: { ...DEFAULT_STYLE(), page_break_before: true },
      acknowledgement: { ...DEFAULT_STYLE(), page_break_before: true },
      appendix: { ...DEFAULT_STYLE(), page_break_before: true },
    }
  } else {
    try {
      tpl.value = await fetchTemplate(tplId.value)
    } catch (e) {
      loadError.value = e.message || '模板加载失败'
    }
  }
  stylesKeys.value = Object.keys(tpl.value.styles)
  for (const k of stylesKeys.value) collapsed.value[k] = true
  loading.value = false
})

function addSection() {
  const used = new Set()
  function collect(nodes) {
    for (const node of nodes || []) {
      if (node.key) used.add(node.key)
      collect(node.children)
    }
  }
  collect(tpl.value.sections)
  let n = tpl.value.sections.length + 1
  let key = 'ch' + n
  while (used.has(key)) key = 'ch' + (++n)
  tpl.value.sections.push({ key, type: 'heading1', title: '新章节', required: true, children: [] })
}

function removeTopLevel(key) {
  const index = tpl.value.sections.findIndex((node) => node.key === key)
  if (index >= 0) tpl.value.sections.splice(index, 1)
}

function toggleStyle(k) { collapsed.value[k] = !collapsed.value[k] }

function styleLabel(k) { return STYLE_LABELS[k] || k }

function previewStyle(s) {
  const alignMap = { left: 'left', center: 'center', right: 'right', justify: 'justify' }
  return {
    fontFamily: `'${s.font_zh}', '${s.font_en}', sans-serif`,
    fontSize: s.size_pt + 'pt',
    fontWeight: s.bold ? 'bold' : 'normal',
    fontStyle: s.italic ? 'italic' : 'normal',
    textDecoration: s.underline ? 'underline' : 'none',
    textAlign: alignMap[s.align] || 'left',
    lineHeight: s.line_spacing,
    textIndent: s.first_line_indent_chars ? (s.first_line_indent_chars * s.size_pt) + 'pt' : '0',
    margin: (s.space_before_pt || 0) + 'pt 0 ' + (s.space_after_pt || 0) + 'pt',
  }
}

function validateTemplate() {
  if (!isNew.value && !tpl.value.id.trim()) return '模板 ID 不能为空'
  if (!tpl.value.name.trim()) return '模板名称不能为空'
  const keys = new Set()
  let invalid = ''
  function walk(nodes) {
    for (const node of nodes || []) {
      if (!node.key?.trim()) { invalid = '章节 key 不能为空'; return }
      if (keys.has(node.key)) { invalid = `章节 key 重复: ${node.key}`; return }
      if (!node.title?.trim()) { invalid = `章节标题不能为空: ${node.key}`; return }
      keys.add(node.key)
      walk(node.children)
      if (invalid) return
    }
  }
  walk(tpl.value.sections)
  if (invalid) return invalid
  const metaKeys = new Set()
  for (const field of tpl.value.meta_fields) {
    if (!field.key?.trim() || !field.label?.trim()) return '封面字段的 key 和显示名不能为空'
    if (metaKeys.has(field.key)) return `封面字段 key 重复: ${field.key}`
    metaKeys.add(field.key)
  }
  return ''
}

async function save() {
  saving.value = true; error.value = ''; saved.value = false
  const validationError = validateTemplate()
  if (validationError) {
    error.value = validationError
    saving.value = false
    return
  }
  try {
    const result = await saveTemplate(tpl.value, isNew.value)
    tpl.value = result
    saved.value = true
    savedAt.value = new Date().toLocaleTimeString()
    if (isNew.value) router.replace('/templates/' + result.id + '/edit')
  } catch (e) { error.value = e.message }
  finally { saving.value = false }
}
</script>

<style scoped>
.saved-banner { color: #15803d; background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 10px 14px; margin-bottom: 12px; }
.style-block { border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; margin-bottom: 10px; }
.style-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; padding: 12px 0; }
.style-grid label { font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
.style-grid input, .style-grid select {
  padding: 6px; border: 1px solid var(--border); border-radius: 6px; font-size: 14px; color: var(--text);
}
.style-grid label.checkbox { flex-direction: row; align-items: center; gap: 6px; }
.style-preview { grid-column: 1 / -1; display: flex; align-items: center; gap: 10px; font-size: 12px; color: var(--muted); }
.preview-text {
  flex: 1; background: #fff; border: 1px dashed var(--border); border-radius: 6px;
  padding: 8px 12px; color: #1f2937; min-height: 32px;
}
.head-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
</style>
