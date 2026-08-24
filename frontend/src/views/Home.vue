<template>
  <div>
    <div class="tabs">
      <button :class="{ active: tab === 'check' }" @click="tab = 'check'">📄 上传检查</button>
      <button :class="{ active: tab === 'projects' }" @click="tab = 'projects'">✍️ 我的论文</button>
    </div>

    <div v-if="error" class="error-banner" role="alert" aria-live="polite">⚠ {{ error }}</div>
    <!-- ============ 上传检查 ============ -->
    <section v-if="tab === 'check'">
      <div class="card">
        <h2>上传论文并检查</h2>
        <div class="upload-zone" :class="{ dragging, disabled: checking || fixing }" @click="pickFile"
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
          <select id="pack" v-model="pack" :disabled="checking || fixing">
            <option v-for="p in packs" :key="p" :value="p">{{ p }}</option>
          </select>
          <button class="primary" :disabled="!file || checking || fixing || initializing" @click="runCheck">
            {{ checking ? '检查中…' : '开始检查' }}
          </button>
        </div>
      </div>

        <div v-if="error" class="error-banner" role="alert" aria-live="polite">⚠ {{ error }}</div>

      <div v-if="initializing" class="hint" style="margin-bottom: 16px">正在加载规则包、模板和论文项目…</div>
      <section v-if="report" class="card">
        <h2>检查结果</h2>
        <div class="meta">文件: {{ file.name }} · 规则包: {{ pack }}</div>
        <div class="summary-cards">
          <div class="summary-card total"><div class="num">{{ report.summary.total }}</div><div class="label">问题总数</div></div>
          <div class="summary-card error"><div class="num">{{ report.summary.error }}</div><div class="label">错误</div></div>
          <div class="summary-card warning"><div class="num">{{ report.summary.warning }}</div><div class="label">警告</div></div>
          <div class="summary-card info"><div class="num">{{ report.summary.info }}</div><div class="label">提示</div></div>
        </div>
        <div v-if="hasFixableFindings" class="form-row fix-actions">
          <button class="fix-btn" :disabled="checking || fixing || initializing" @click="runFix" aria-live="polite">
            {{ fixing ? '修复中…' : '自动修复并下载' }}
          </button>
          <span class="hint">将应用所有可修复项并下载新的 docx 文件</span>
          <span v-if="lastFixRemaining !== null" class="hint">仍有 {{ lastFixRemaining }} 项需手动处理</span>
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
      <div v-if="initializing" class="hint" style="margin-bottom: 16px">正在加载论文项目…</div>
      <div class="form-row" style="margin-bottom: 16px">
        <label for="tpl">基于模板</label>
        <select id="tpl" v-model="newProjectTemplate">
          <option v-for="t in templates" :key="t.id" :value="t.id">{{ t.name }}</option>
        </select>
        <input v-model="newProjectTitle" placeholder="论文标题" style="padding: 8px; border: 1px solid var(--border); border-radius: 8px" />
          <button class="primary" :disabled="creating || initializing || !newProjectTemplate" @click="createProject">{{ creating ? '创建中…' : '新建论文' }}</button>
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
          <button @click="exportProject(p)" :disabled="exporting.has(p.id)">{{ exporting.has(p.id) ? '导出中…' : '⬇️ 导出 docx' }}</button>
          <button style="color: var(--error)" @click="deleteProject(p)" :disabled="deleting.has(p.id)">{{ deleting.has(p.id) ? '删除中…' : '删除' }}</button>
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
import { fetchPacks, checkDocx, fixDocx, fetchTemplates, fetchProjects, createProject as apiCreateProject, exportProject as apiExportProject, deleteProject as apiDeleteProject } from "../api"

const route = useRoute()
const router = useRouter()
const tab = ref(route.query.tab === 'projects' ? 'projects' : 'check')

const file = ref(null)
const packs = ref([])
const pack = ref('GB-T-7713-1-2006')
const checking = ref(false)
const fixing = ref(false)
const report = ref(null)
const error = ref('')
const dragging = ref(false)
const fileInput = ref(null)

const templates = ref([])
const projects = ref([])
const newProjectTemplate = ref('')
const newProjectTitle = ref('')
const initializing = ref(true)
const lastFixRemaining = ref(null)
const creating = ref(false)
const exporting = reactive(new Set())
const deleting = reactive(new Set())

onMounted(async () => {
  const results = await Promise.allSettled([
    fetchPacks(), fetchTemplates(), fetchProjects(),
  ])
  const [packsResult, templatesResult, projectsResult] = results
  if (packsResult.status === 'fulfilled') {
    packs.value = packsResult.value
    if (!packs.value.includes(pack.value) && packs.value.length) pack.value = packs.value[0]
  } else { error.value = packsResult.reason?.message || '获取规则包失败' }
  if (templatesResult.status === 'fulfilled') {
    templates.value = templatesResult.value
    if (templates.value.length) newProjectTemplate.value = templates.value[0].id
  } else if (!error.value) { error.value = templatesResult.reason?.message || '获取模板失败' }
  if (projectsResult.status === 'fulfilled') {
    projects.value = projectsResult.value
  } else if (!error.value) { error.value = projectsResult.reason?.message || '获取项目列表失败' }
  initializing.value = false
})

watch(() => route.query.tab, (v) => { tab.value = v === 'projects' ? 'projects' : 'check' })

function pickFile() { if (!checking.value && !fixing.value) fileInput.value?.click() }
function onPick(e) {
  const f = e.target.files?.[0]
  e.target.value = ''
  if (f && !checking.value && !fixing.value) setFile(f)
}
function onDrop(e) {
  dragging.value = false
  if (checking.value || fixing.value) return
  const f = e.dataTransfer?.files?.[0]
  if (f) setFile(f)
}
function setFile(f) {
  if (!f.name.toLowerCase().endsWith('.docx')) { error.value = '只支持 .docx 文件'; return }
  file.value = f; error.value = ''; report.value = null; lastFixRemaining.value = null
}

const hasFixableFindings = computed(() => report.value?.findings?.some((finding) => finding.fix) ?? false)

function fixedFilename() {
  const original = file.value?.name || 'paper.docx'
  return original.replace(/\.docx$/i, '') + '-fixed.docx'
}

async function runCheck() {
  if (!file.value || checking.value || fixing.value) return
  checking.value = true; error.value = ''; report.value = null
  try { report.value = await checkDocx(file.value, pack.value) }
  catch (e) { error.value = e.message }
  finally { checking.value = false }
}

async function runFix() {
  if (!file.value || !hasFixableFindings.value || checking.value || fixing.value) return
  fixing.value = true; error.value = ''
  try {
    lastFixRemaining.value = await fixDocx(file.value, pack.value, fixedFilename())
  } catch (e) { error.value = e.message }
  finally { fixing.value = false }
}

async function createProject() {
  if (creating.value || initializing.value || !newProjectTemplate.value) return
  creating.value = true
  error.value = ''
  try {
    const p = await apiCreateProject(newProjectTemplate.value, newProjectTitle.value)
    projects.value = await fetchProjects()
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
