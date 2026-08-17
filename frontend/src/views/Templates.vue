<template>
  <div class="card">
    <div class="head-row">
      <h2>论文模板</h2>
      <button class="primary" @click="createNew">➕ 新建模板</button>
    </div>
    <p style="color: var(--muted); font-size: 14px; margin-bottom: 16px">
      模板定义论文的章节结构、每类元素的排版样式(字体/字号/行距/缩进/分页)和封面字段。
      学生写作时格式自动按模板约束。
    </p>

    <div v-if="templates.length === 0" style="color: var(--muted)">暂无模板</div>
    <div v-for="t in templates" :key="t.id" class="finding" style="border-left-color: var(--primary)">
      <div class="head">
        <span style="font-weight: 600">{{ t.name }}</span>
        <span class="rule-id">{{ t.section_count }} 个章节 · {{ shortDate(t.updated_at) }}</span>
      </div>
      <div class="meta">{{ t.description || '无描述' }}</div>
      <div class="form-row" style="margin-top: 10px">
        <button class="primary" @click="$router.push('/templates/' + t.id + '/edit')">✏️ 编辑</button>
        <button v-if="t.id !== 'default-gb7713'" style="color: var(--error)" @click="remove(t)">删除</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { fetchTemplates, deleteTemplateApi } from '../api'

const router = useRouter()
const templates = ref([])
const error = ref('')

onMounted(async () => {
  try { templates.value = await fetchTemplates() }
  catch (e) { error.value = e.message }
})

function createNew() {
  router.push({ path: '/templates/new/edit' })
}

async function remove(t) {
  if (!confirm(`确定删除模板「${t.name}」?`)) return
  try { await deleteTemplateApi(t.id); templates.value = await fetchTemplates() }
  catch (e) { error.value = e.message }
}

function shortDate(s) { return s ? s.slice(0, 10) : '' }
</script>
