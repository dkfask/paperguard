<template>
  <div class="card">
    <div class="head-row">
      <h2>{{ isNew ? '新建模板' : '编辑模板' }}</h2>
      <div class="form-row" style="margin-top: 0">
        <button class="primary" :disabled="saving" @click="save">{{ saving ? '保存中…' : '💾 保存模板' }}</button>
        <button @click="$router.push('/templates')">返回</button>
      </div>
    </div>
    <div v-if="error" class="error-banner">⚠ {{ error }}</div>
    <div v-if="saved" style="color: #15803d; margin-bottom: 12px">✅ 已保存</div>

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
      <section-node v-for="(node, i) in tpl.sections" :key="i" :node="node"
                    :styles-keys="stylesKeys" @remove="tpl.sections.splice(i, 1)" />
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
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { fetchTemplate, saveTemplate } from '../api'
import SectionNode from '../components/SectionNode.vue'

const route = useRoute()
const tplId = route.params.id
const isNew = tplId === 'new'

const STYLE_LABELS = {
  cover_title: '封面标题', cover_meta: '封面信息',
  abstract: '摘要', toc: '目录', heading1: '一级标题(章)',
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
  sections: [], styles: {}, meta_fields: [],
})
const stylesKeys = ref([])
const collapsed = ref({})
const error = ref('')
const saved = ref(false)
const saving = ref(false)

onMounted(async () => {
  if (isNew) {
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
      tpl.value = await fetchTemplate(tplId)
    } catch (e) { error.value = e.message }
  }
  stylesKeys.value = Object.keys(tpl.value.styles)
  for (const k of stylesKeys.value) collapsed.value[k] = true
})

function addSection() {
  tpl.value.sections.push({ key: 'ch' + (tpl.value.sections.length + 1), type: 'heading1', title: '新章节', required: true, children: [] })
}

function toggleStyle(k) { collapsed.value[k] = !collapsed.value[k] }

function styleLabel(k) { return STYLE_LABELS[k] || k }

async function save() {
  saving.value = true; error.value = ''; saved.value = false
  try {
    const result = await saveTemplate(tpl.value, isNew)
    tpl.value = result
    saved.value = true
  } catch (e) { error.value = e.message }
  finally { saving.value = false }
}
</script>

<style scoped>
.tree { border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: #fafbfc; }
.style-block { border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; margin-bottom: 10px; }
.style-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; padding: 12px 0; }
.style-grid label { font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
.style-grid input, .style-grid select {
  padding: 6px; border: 1px solid var(--border); border-radius: 6px; font-size: 14px; color: var(--text);
}
.style-grid label.checkbox { flex-direction: row; align-items: center; gap: 6px; }
.head-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
</style>
