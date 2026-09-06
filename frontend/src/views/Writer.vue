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
        <span class="head-actions">
          <button class="small" @click="openAiSettings" title="AI 设置">✨AI</button>
          <button class="small" @click="exportDocx(false)" :disabled="exporting || saving || dirtyCount > 0">⬇️ 导出</button>
        </span>
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
                    :chapter="chapterOf(currentSection.key)"
                    :ai-enabled="aiEnabled" :ai-busy="aiBusy" @ai="onBlockAi" @change="scheduleSave" />
      </div>

      <!-- 正文节: 内容块编辑器 -->
      <div v-else-if="currentSection">
        <div class="editor-head">
          <span class="type-tag">{{ typeLabel(currentSection.type) }}</span>
          <span class="hint">字体/字号/行距/缩进由模板自动控制, 可插图片/表格</span>
          <button v-if="aiEnabled && isAiSection(currentSection.type)" class="small ai-gen"
                  :disabled="!!aiBusy || aiGenerating" @click="aiGenerate">
            {{ aiGenerating ? '✨ AI 生成中…' : '✨ AI 生成' }}
          </button>
        </div>
        <input v-model="currentSection.title" class="heading-input" :class="'type-' + currentSection.type"
               @input="scheduleSave" />
        <block-list :paragraphs="currentSection.paragraphs" :project-id="projectId"
                    :style-class="'style-' + currentSection.type" :section-title="currentSection.title"
                    :chapter="chapterOf(currentSection.key)"
                    :ai-enabled="aiEnabled" :ai-busy="aiBusy" @ai="onBlockAi" @change="scheduleSave" />
      </div>

      <!-- AI 对照选择弹窗 -->
      <div v-if="aiDiff" class="modal-mask" @click.self="cancelAiDiff">
        <div class="modal card diff-modal">
          <h3 style="margin-top:0; display:flex; align-items:center; gap:8px">
            <span>{{ aiDiff.action === 'continue' ? '▶ AI 续写对照' : '✨ AI 润色对照' }}</span>
            <span class="type-tag" style="font-size:12px; font-weight:normal">
              第 {{ aiDiff.index + 1 }} 块
            </span>
          </h3>
          <p class="hint" style="margin-top:0">
            请审阅 AI 生成的结果。确认满意后可点击替换或追加，不满意可直接放弃保持原状。
          </p>

          <div class="diff-grid">
            <div class="diff-pane">
              <div class="diff-label">【原段落内容】</div>
              <div class="diff-box original">{{ aiDiff.original }}</div>
            </div>
            <div class="diff-pane">
              <div class="diff-label">
                {{ aiDiff.action === 'continue' ? '【AI 续写新内容】' : '【AI 润色后内容】' }}
              </div>
              <div class="diff-box generated">{{ aiDiff.generated }}</div>
            </div>
          </div>

          <div v-if="aiDiff.action === 'continue'" class="diff-preview-pane">
            <div class="diff-label">【合并预览效果】</div>
            <div class="diff-box preview">{{ aiDiff.preview }}</div>
          </div>

          <div class="form-row" style="margin-top:16px; justify-content:flex-end">
            <button class="primary" @click="applyAiDiff">
              {{ aiDiff.action === 'continue' ? '✔ 采纳并追加' : '✔ 采纳并替换' }}
            </button>
            <button @click="cancelAiDiff">✖ 放弃 (保留原样)</button>
          </div>
        </div>
      </div>

      <!-- AI 设置弹窗 -->
      <div v-if="showAiSettings" class="modal-mask" @click.self="showAiSettings = false">
        <div class="modal card">
          <h3 style="margin-top:0">✨ AI 设置</h3>
          <div v-if="isAdmin" class="ai-tab-row">
            <button class="ai-tab-btn" :class="{ active: aiTab === 'user' }" @click="switchAiTab('user')">
              个人模型 (仅本人生效)
            </button>
            <button class="ai-tab-btn" :class="{ active: aiTab === 'admin' }" @click="switchAiTab('admin')">
              ⚙️ 全站默认模型 (管理员)
            </button>
          </div>
          <p class="hint" style="font-size: 13px; margin-top: 4px">
            <template v-if="aiTab === 'user'">
              配置自己的 OpenAI 兼容模型, 仅对本账号生效, 优先于系统默认。
              <br />
              <template v-if="aiEnabled">
                当前: {{ aiSource === 'user' ? '自有模型' : '系统默认模型' }} ({{ aiModel }})
              </template>
              <template v-else>当前 AI 功能未启用</template>
            </template>
            <template v-else>
              配置全站默认 OpenAI 兼容模型, 全体未自带 Key 的用户将默认使用该配置。
              <br />
              <template v-if="adminAiInfo.configured">
                当前默认来源: {{ adminAiInfo.source === 'database' ? '数据库 (管理员自定义)' : '环境变量 (PAPERGUARD_AI_*)' }}
              </template>
              <template v-else>当前全站未配置默认模型</template>
            </template>
          </p>

          <!-- 个人模型表单 -->
          <template v-if="aiTab === 'user'">
            <label class="field">Base URL
              <input v-model="aiCfgForm.base_url" placeholder="如 https://api.openai.com/v1" />
            </label>
            <label class="field">API Key
              <input v-model="aiCfgForm.api_key" type="password" autocomplete="new-password"
                     :placeholder="aiCfgInfo.configured ? '已保存 ' + aiCfgInfo.api_key_masked + ' (重填可覆盖)' : '必填, 如 sk-xxx'" />
            </label>
            <label class="field">模型
              <input v-model="aiCfgForm.model" placeholder="如 gpt-4o-mini / glm-4-flash" />
            </label>
            <p v-if="aiCfgInfo.message" :class="aiCfgInfo.ok ? 'ok-banner' : 'error-banner'" style="font-size: 13px">
              {{ aiCfgInfo.ok ? '✅' : '⚠' }} {{ aiCfgInfo.message }}
            </p>
            <div class="form-row">
              <button class="primary" :disabled="aiCfgInfo.saving" @click="saveAiCfg">
                {{ aiCfgInfo.saving ? '保存中…' : '保存自有配置' }}
              </button>
              <button :disabled="aiCfgInfo.testing" @click="testAiConnection">
                {{ aiCfgInfo.testing ? '测试中…' : '测试连接' }}
              </button>
              <button v-if="aiCfgInfo.configured" @click="clearAiCfg">清除自有配置</button>
              <button @click="showAiSettings = false">关闭</button>
            </div>
          </template>

          <!-- 管理员全站默认模型表单 -->
          <template v-else>
            <label class="field">Base URL
              <input v-model="adminAiForm.base_url" placeholder="如 https://api.openai.com/v1" />
            </label>
            <label class="field">API Key
              <input v-model="adminAiForm.api_key" type="password" autocomplete="new-password"
                     :placeholder="adminAiInfo.configured ? '已配置 ' + adminAiInfo.api_key_masked + ' (输入新Key覆盖)' : '必填, 如 sk-xxx'" />
            </label>
            <label class="field">模型
              <input v-model="adminAiForm.model" placeholder="如 gpt-4o-mini / glm-4-flash" />
            </label>
            <p v-if="adminAiInfo.message" :class="adminAiInfo.ok ? 'ok-banner' : 'error-banner'" style="font-size: 13px">
              {{ adminAiInfo.ok ? '✅' : '⚠' }} {{ adminAiInfo.message }}
            </p>
            <div class="form-row">
              <button class="primary" :disabled="adminAiInfo.saving" @click="saveAdminAiCfg">
                {{ adminAiInfo.saving ? '保存中…' : '保存全站默认' }}
              </button>
              <button :disabled="adminAiInfo.testing" @click="testAdminAiConnection">
                {{ adminAiInfo.testing ? '测试中…' : '测试连接' }}
              </button>
              <button v-if="adminAiInfo.source === 'database'" @click="clearAdminAiCfg">恢复环境变量默认值</button>
              <button @click="showAiSettings = false">关闭</button>
            </div>
          </template>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { auth } from '../auth'
import {
  fetchProject, fetchTemplate, updateProjectSection, updateProjectMeta, exportProject, checkProjectTemplate,
  aiStatus, getAiConfig, saveAiConfig, clearAiConfig, testAiConfig, aiAssist,
  getAdminAiConfig, saveAdminAiConfig, clearAdminAiConfig, testAdminAiConfig,
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

// ---------- AI 写作助手 ----------
const AI_GEN_TYPES = ['abstract', 'english_abstract', 'keywords', 'english_keywords']
const aiEnabled = ref(false)
const aiSource = ref(null)
const aiModel = ref('')
const aiBusy = ref(null)          // { index, action } 块级 AI 处理中
const aiDiff = ref(null)          // { index, action, original, generated, preview } 差异对照选择
const aiGenerating = ref(false)   // 章节级 AI 生成中
const showAiSettings = ref(false)
const isAdmin = computed(() => auth.user?.role === 'admin')
const aiTab = ref('user') // 'user' | 'admin'
const aiCfgForm = reactive({ base_url: '', api_key: '', model: '' })
const aiCfgInfo = reactive({
  configured: false, api_key_masked: '', testing: false, saving: false, message: '', ok: null,
})
const adminAiForm = reactive({ base_url: '', api_key: '', model: '' })
const adminAiInfo = reactive({
  configured: false, source: null, api_key_masked: '', has_env_fallback: false,
  testing: false, saving: false, message: '', ok: null,
})

function switchAiTab(tab) {
  aiTab.value = tab
  if (tab === 'admin') {
    void loadAdminAiCfg()
  } else {
    void loadUserAiCfg()
  }
}

onMounted(loadAiStatus)

async function loadAiStatus() {
  try {
    const s = await aiStatus()
    aiEnabled.value = s.enabled
    aiSource.value = s.source
    aiModel.value = s.model || ''
  } catch { aiEnabled.value = false }
}

async function loadUserAiCfg() {
  aiCfgInfo.message = ''
  aiCfgInfo.ok = null
  try {
    const cfg = await getAiConfig()
    aiCfgInfo.configured = cfg.configured
    aiCfgInfo.api_key_masked = cfg.api_key_masked || ''
    aiCfgForm.base_url = cfg.base_url || ''
    aiCfgForm.model = cfg.model || ''
    aiCfgForm.api_key = ''
  } catch (e) {
    aiCfgInfo.message = e.message || '读取个人 AI 配置失败'
  }
}

async function loadAdminAiCfg() {
  adminAiInfo.message = ''
  adminAiInfo.ok = null
  try {
    const cfg = await getAdminAiConfig()
    adminAiInfo.configured = cfg.configured
    adminAiInfo.source = cfg.source
    adminAiInfo.api_key_masked = cfg.api_key_masked || ''
    adminAiInfo.has_env_fallback = cfg.has_env_fallback
    adminAiForm.base_url = cfg.base_url || ''
    adminAiForm.model = cfg.model || ''
    adminAiForm.api_key = ''
  } catch (e) {
    adminAiInfo.message = e.message || '读取全站 AI 配置失败'
  }
}

async function openAiSettings() {
  showAiSettings.value = true
  aiTab.value = 'user'
  await loadUserAiCfg()
  if (isAdmin.value) {
    await loadAdminAiCfg()
  }
}

function adminAiFilled() {
  return !!(adminAiForm.base_url && adminAiForm.api_key && adminAiForm.model)
}

async function testAdminAiConnection() {
  if (!adminAiFilled()) {
    adminAiInfo.ok = false
    adminAiInfo.message = '请先完整填写 Base URL / API Key / 模型'
    return
  }
  adminAiInfo.testing = true
  adminAiInfo.message = ''
  try {
    const res = await testAdminAiConfig({ ...adminAiForm })
    adminAiInfo.ok = res.ok
    adminAiInfo.message = res.ok ? `测试成功 (模型: ${res.model})` : (res.error || '测试失败')
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || '测试失败'
  } finally {
    adminAiInfo.testing = false
  }
}

async function saveAdminAiCfg() {
  if (!adminAiFilled()) {
    adminAiInfo.ok = false
    adminAiInfo.message = '请先完整填写 Base URL / API Key / 模型'
    return
  }
  adminAiInfo.saving = true
  adminAiInfo.message = ''
  try {
    await saveAdminAiConfig({ ...adminAiForm })
    adminAiInfo.configured = true
    adminAiInfo.source = 'database'
    adminAiInfo.ok = true
    adminAiInfo.message = '已保存为全站默认模型！'
    await loadAiStatus()
    await loadAdminAiCfg()
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || '保存全站配置失败'
  } finally {
    adminAiInfo.saving = false
  }
}

async function clearAdminAiCfg() {
  try {
    await clearAdminAiConfig()
    adminAiInfo.ok = true
    adminAiInfo.message = '已恢复系统默认设置'
    await loadAiStatus()
    await loadAdminAiCfg()
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || '清除失败'
  }
}

function isAiSection(type) { return AI_GEN_TYPES.includes(type) }

/** 块级 AI: 润色/续写当前块, 获得结果后展示对比确认面板 */
async function onBlockAi({ action, index }) {
  const sec = currentSection.value
  if (!sec || aiBusy.value) return
  const par = sec.paragraphs[index]
  if (!par || par.type !== 'paragraph') return
  const text = (par.text || '').trim()
  if (!text) {
    error.value = '当前块为空, 请先输入一些内容再使用 AI'
    return
  }
  aiBusy.value = { index, action }
  error.value = ''
  try {
    const result = await aiAssist({
      action, text, section_title: sec.title, section_type: sec.type,
    })
    const generated = result.text || ''
    const original = par.text || ''
    aiDiff.value = {
      index,
      action,
      original,
      generated,
      preview: action === 'continue' ? (original + (original ? '\n' : '') + generated) : generated,
    }
  } catch (e) {
    error.value = e.message || 'AI 调用失败'
  } finally {
    aiBusy.value = null
  }
}

/** 用户在对比面板选择采纳结果 */
function applyAiDiff() {
  if (!aiDiff.value) return
  const { index, action, generated, original } = aiDiff.value
  const sec = currentSection.value
  if (sec && sec.paragraphs[index]) {
    const par = sec.paragraphs[index]
    if (action === 'continue') {
      par.text = original + (original ? '\n' : '') + generated
    } else {
      par.text = generated
    }
    scheduleSave()
  }
  aiDiff.value = null
}

/** 用户放弃本次 AI 生成结果 */
function cancelAiDiff() {
  aiDiff.value = null
}

/** 拼接正文上下文: 除封面/目录/四个 AI 目标章节外的所有章节段落 */
function bodyContext() {
  const exclude = new Set([...AI_GEN_TYPES, 'cover', 'toc'])
  const parts = []
  for (const s of project.value?.sections || []) {
    if (exclude.has(s.type)) continue
    for (const p of s.paragraphs || []) {
      if (p.type === 'paragraph' && (p.text || '').trim()) parts.push(p.text.trim())
    }
  }
  return parts.join('\n')
}

/** 章节级 AI: 按正文生成 摘要/Abstract/关键词 */
async function aiGenerate() {
  const sec = currentSection.value
  if (!sec || aiGenerating.value || aiBusy.value) return
  const body = bodyContext()
  if (body.length < 50) {
    error.value = '正文内容太少, 请先撰写正文章节, 再回到本页生成'
    return
  }
  aiGenerating.value = true
  error.value = ''
  try {
    const result = await aiAssist({
      action: sec.type === 'english_keywords' ? 'keywords' : sec.type,
      text: body.slice(0, 11000),
      section_title: sec.title, section_type: sec.type,
    })
    let first = sec.paragraphs.find((p) => p.type === 'paragraph')
    if (!first) {
      first = { id: `p${Date.now()}`, type: 'paragraph', text: '', bold: false, italic: false, underline: false }
      sec.paragraphs.unshift(first)
    }
    first.text = result.text
    scheduleSave()
  } catch (e) {
    error.value = e.message || 'AI 生成失败'
  } finally {
    aiGenerating.value = false
  }
}

function aiCfgFilled() { return !!(aiCfgForm.base_url && aiCfgForm.api_key && aiCfgForm.model) }

async function testAiConnection() {
  if (!aiCfgFilled()) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = '请先填写完整 Base URL / API Key / 模型'
    return
  }
  aiCfgInfo.testing = true
  aiCfgInfo.message = ''
  try {
    const r = await testAiConfig({ ...aiCfgForm })
    aiCfgInfo.ok = r.ok
    aiCfgInfo.message = r.ok ? `连接成功 (模型: ${r.model})` : (r.error || '连接失败')
  } catch (e) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = e.message || '测试失败'
  } finally {
    aiCfgInfo.testing = false
  }
}

async function saveAiCfg() {
  if (!aiCfgFilled()) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = '请填写完整 Base URL / API Key / 模型'
    return
  }
  aiCfgInfo.saving = true
  aiCfgInfo.message = ''
  try {
    await saveAiConfig({ ...aiCfgForm })
    aiCfgInfo.configured = true
    aiCfgInfo.ok = true
    aiCfgInfo.message = '已保存, 现在使用你自己的模型'
    await loadAiStatus()
  } catch (e) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = e.message || '保存失败'
  } finally {
    aiCfgInfo.saving = false
  }
}

async function clearAiCfg() {
  try {
    await clearAiConfig()
    await loadAiStatus()
    aiCfgInfo.configured = false
    aiCfgForm.base_url = ''
    aiCfgForm.model = ''
    aiCfgForm.api_key = ''
    aiCfgInfo.ok = true
    aiCfgInfo.message = '已清除自有配置, 恢复使用系统默认模型(若已配置)'
  } catch (e) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = e.message || '清除失败'
  }
}

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
.head-actions { display: flex; gap: 6px; }
.ai-gen { margin-left: auto; }

/* AI 设置与对比弹窗 */
.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, .45); display: flex; align-items: center; justify-content: center; z-index: 60; }
.modal { width: min(480px, 92vw); text-align: left; max-height: 88vh; overflow-y: auto; }
.modal.diff-modal { width: min(720px, 94vw); }
.diff-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.diff-pane { display: flex; flex-direction: column; gap: 6px; }
.diff-label { font-size: 12px; font-weight: 600; color: var(--muted); }
.diff-box {
  border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
  font-size: 13px; line-height: 1.6; max-height: 240px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-word; font-family: inherit;
}
.diff-box.original { background: #f8fafc; color: #475569; }
.diff-box.generated { background: #f0fdf4; border-color: #bbf7d0; color: #166534; }
.diff-preview-pane { margin-top: 12px; }
.diff-box.preview { background: #eff6ff; border-color: #bfdbfe; color: #1e40af; max-height: 180px; }
.modal .field { display: block; font-size: 13px; color: var(--muted); margin-bottom: 12px; }
.modal .field input { width: 100%; margin-top: 4px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
.ok-banner { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.error-banner { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.ai-tab-row { display: flex; gap: 8px; margin-bottom: 10px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.ai-tab-btn { background: transparent; border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; color: var(--muted); }
.ai-tab-btn.active { background: #eff6ff; border-color: var(--primary); color: var(--primary); font-weight: 600; }
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
