<template>
  <div>
    <div class="tabs">
      <button :class="{ active: tab === 'check' }" @click="tab = 'check'">📄 上传检查</button>
      <button :class="{ active: tab === 'projects' }" @click="tab = 'projects'">✍️ 我的论文</button>
    </div>

    <!-- ============ 上传检查 ============ -->
    <section v-if="tab === 'check'">
      <div class="card">
        <h2>上传论文并检查</h2>
        <div class="upload-zone" :class="{ dragging }" @click="pickFile"
             @dragover.prevent="dragging = true" @dragleave="dragging = false"
             @drop.prevent="onDrop">
          <div class="icon">📁</div>
          <div v-if="file">{{ file.name }}</div>
          <div v-else>点击选择或拖入 .docx 文件</div>
          <div class="hint">仅支持 .docx;文件仅在服务器临时处理,不保存论文内容</div>
        </div>
        <input ref="fileInput" type="file" accept=".docx" hidden @change="onPick" />
        <div class="form-row">
          <label for="pack">规则包</label>
          <select id="pack" v-model="pack">
            <option v-for="p in packs" :key="p" :value="p">{{ p }}</option>
          </select>
          <button class="primary" :disabled="!file || checking" @click="runCheck">
            {{ checking ? '检查中…' : '开始检查' }}
          </button>
        </div>
      </div>

      <div v-if="error" class="error-banner">⚠ {{ error }}</div>

      <section v-if="report" class="card">
        <h2>检查结果</h2>
        <div class="summary-cards">
          <div class="summary-card total"><div class="num">{{ report.summary.total }}</div><div class="label">问题总数</div></div>
          <div class="summary-card error"><div class="num">{{ report.summary.error }}</div><div class="label">错误</div></div>
          <div class="summary-card warning"><div class="num">{{ report.summary.warning }}</div><div class="label">警告</div></div>
          <div class="summary-card info"><div class="num">{{ report.summary.info }}</div><div class="label">提示</div></div>
        </div>
        <div v-if="report.findings.length === 0" style="color: var(--muted)">
          ✅ 未发现格式问题。
        </div>
        <div v-for="(f, i) in report.findings" :key="i" class="finding" :class="'severity-' + f.severity">
          <div class="head">
            <span class="rule-id">{{ f.rule_id }}</span>
            <span class="badge" :class="f.severity">
              {{ { error: '错误', warning: '警告', info: '提示' }[f.severity] }}
            </span>
          </div>
          <div class="message">{{ f.message }}</div>
          <div class="meta" v-if="f.location.paragraph_index !== null">段落 #{{ f.location.paragraph_index }}</div>
          <span class="snippet" v-if="f.location.text_snippet">"{{ f.location.text_snippet }}"</span>
        </div>
      </section>
    </section>

    <!-- ============ 我的论文 ============ -->
    <section v-else class="card">
      <h2>我的论文</h2>
      <div class="form-row" style="margin-bottom: 16px">
        <label for="tpl">基于模板</label>
        <select id="tpl" v-model="newProjectTemplate">
          <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
        </select>
        <input v-model="newProjectTitle" placeholder="论文标题" style="padding: 8px; border: 1px solid var(--border); border-radius: 8px" />
        <button class="primary" @click="createProject">新建论文</button>
      </div>

      <div v-if="projects.length === 0" style="color: var(--muted)">
        还没有论文项目。选择模板新建一篇, 写作时格式将自动按模板约束。
      </div>
      <div v-for="p in projects" :key="p.id" class="finding" style="border-left-color: var(--primary)">
        <div class="head">
          <span style="font-weight: 600">{{ p.title || p.template_name }}</span>
          <span class="rule-id">{{ p.template_name }} · 更新于 {{ shortDate(p.updated_at) }}</span>
        </div>
        <div class="form-row" style="margin-top: 10px">
          <button class="primary" @click="$router.push('/write/' + p.id)">✍️ 继续写作</button>
          <button @click="exportProject(p)">⬇️ 导出 docx</button>
          <button style="color: var(--error)" @click="deleteProject(p)">删除</button>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VueOfficeDocx from '@vue-office/docx'
import '@vue-office/docx/lib/index.css'
import { fetchPacks, checkDocx, fetchTemplates, fetchProjects, createProject as apiCreateProject, exportProject as apiExportProject, deleteProject as apiDeleteProject } from "../api"

const route = useRoute()
const router = useRouter()
const tab = ref(route.query.tab === 'projects' ? 'projects' : 'check')

const file = ref(null)
const packs = ref([])
const pack = ref('GB-T-7713-1-2006')
const checking = ref(false)
const report = ref(null)
const error = ref('')
const dragging = ref(false)
const fileInput = ref(null)

const templates = ref([])
const projects = ref([])
const newProjectTemplate = ref('')
const newProjectTitle = ref('')

onMounted(async () => {
  try {
    packs.value = await fetchPacks()
    if (!packs.value.includes(pack.value) && packs.value.length) pack.value = packs.value[0]
    templates.value = await fetchTemplates()
    if (templates.value.length) newProjectTemplate.value = templates.value[0].id
    projects.value = await fetchProjects()
  } catch (e) {
    error.value = e.message
  }
})

watch(() => route.query.tab, (v) => { tab.value = v === 'projects' ? 'projects' : 'check' })

function pickFile() { fileInput.value?.click() }
function onPick(e) { const f = e.target.files?.[0]; if (f) setFile(f) }
function onDrop(e) { dragging.value = false; const f = e.dataTransfer?.files?.[0]; if (f) setFile(f) }
function setFile(f) {
  if (!f.name.toLowerCase().endsWith('.docx')) { error.value = '只支持 .docx 文件'; return }
  file.value = f; error.value = ''; report.value = null
}

async function runCheck() {
  if (!file.value) return
  checking.value = true; error.value = ''
  try { report.value = await checkDocx(file.value, pack.value) }
  catch (e) { error.value = e.message }
  finally { checking.value = false }
}

async function createProject() {
  try {
    const p = await apiCreateProject(newProjectTemplate.value, newProjectTitle.value)
    projects.value = await fetchProjects()
    router.push('/write/' + p.id)
  } catch (e) { error.value = e.message }
}

async function exportProject(p) {
  try { await apiExportProject(p.id) }
  catch (e) { error.value = e.message }
}

async function deleteProject(p) {
  if (!confirm(`确定删除「${p.title || p.template_name}」?`)) return
  try { await apiDeleteProject(p.id); projects.value = await fetchProjects() }
  catch (e) { error.value = e.message }
}

function shortDate(s) { return s ? s.slice(0, 10) : '' }
</script>
