<template>
  <div class="policy-page">
    <div class="policy-page-head">
      <div>
        <span class="eyebrow">WRITING REQUIREMENTS</span>
        <h1>写作规范</h1>
        <p>把论文内容要求与排版模板分开管理，项目创建时会冻结当前版本。</p>
      </div>
      <div v-if="canManage" class="policy-head-actions">
        <button class="primary" @click="newPolicy">新建规范</button>
        <button class="ai-import-button" :disabled="importing" @click="openAiImport">
          <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
          {{ importing ? 'AI 解析中…' : 'AI 导入规范' }}
        </button>
        <input ref="aiImportFile" class="file-input-hidden" type="file"
               accept=".docx,.pdf,.md,.markdown,.txt" :disabled="importing" @change="importWithAi" />
      </div>
    </div>

    <div v-if="error" class="error-banner" role="alert">⚠ {{ error }}</div>
    <div class="policy-layout">
      <aside class="policy-list card">
        <div class="list-head">
          <strong>可用规范</strong>
          <button class="tiny" :disabled="loading" @click="loadPolicies">刷新</button>
        </div>
        <div v-if="loading" class="empty-state">正在加载规范…</div>
        <div v-else-if="policies.length === 0" class="empty-state">暂无可用规范</div>
        <button v-for="policy in policies" v-else :key="policy.id" class="policy-list-item"
                :class="{ active: draft.id === policy.id }" @click="selectPolicy(policy.id)">
          <span class="policy-list-title">{{ policy.name }}</span>
          <span class="policy-list-meta">
            {{ levelLabel(policy.level) }} · v{{ policy.version }} · {{ statusLabel(policy.status) }}
          </span>
        </button>
      </aside>

      <section class="policy-editor card">
        <div v-if="!editing" class="empty-editor">
          <span class="material-symbols-outlined">fact_check</span>
          <h2>选择一套规范</h2>
          <p>规范会在论文项目创建时保存为快照，历史检查不受后续修订影响。</p>
        </div>
        <template v-else>
          <div class="editor-head">
            <div>
                <span class="type-tag">{{ levelLabel(draft.level) }}</span>
              <h2>{{ draft.id ? '编辑写作规范' : '新建写作规范' }}</h2>
            </div>
            <span class="status-pill" :class="draft.status">{{ statusLabel(draft.status) }}</span>
          </div>

          <div class="form-grid">
            <label>规范名称
              <input v-model="draft.name" :disabled="readOnly" placeholder="如：本科论文写作规范" />
            </label>
            <label>版本号
              <input v-model="draft.version" :disabled="readOnly" placeholder="如：2026.1" />
            </label>
            <label class="span-2">说明
              <input v-model="draft.description" :disabled="readOnly" placeholder="面向学生展示的简短说明" />
            </label>
            <label>生效时间
              <input v-model="draft.effective_from" :disabled="readOnly" type="datetime-local" />
            </label>
            <label>失效时间
              <input v-model="draft.effective_to" :disabled="readOnly" type="datetime-local" />
            </label>
            <label>规范层级
              <select v-model="draft.level" :disabled="readOnly || auth.user?.role !== 'admin'">
                <option value="school">学校</option>
                <option value="college">学院</option>
                <option value="major">专业</option>
                <option value="course">课程</option>
                <option value="batch">批次</option>
                <option value="teacher">教师</option>
              </select>
            </label>
            <label>作用域 ID
              <input v-model="draft.scope_id" :disabled="readOnly" placeholder="组织/批次 ID，可留空" />
            </label>
            <label>适用阶段
              <input v-model="draft.stage" :disabled="readOnly" placeholder="如 proposal / midterm / final" />
            </label>
            <label>截止时间
              <input v-model="draft.deadline_at" :disabled="readOnly" type="datetime-local" />
            </label>
          </div>

          <div class="section-heading">
            <div>
              <h3>作用域绑定</h3>
              <p>组织、课程或批次绑定会在项目创建时自动继承。</p>
            </div>
            <button v-if="!readOnly" class="small" @click="addBinding">添加绑定</button>
          </div>
          <div v-if="draft.bindings?.length" class="binding-list">
            <div v-for="(binding, index) in draft.bindings" :key="binding.id + index" class="binding-row">
              <input v-model="binding.id" :disabled="readOnly" placeholder="绑定 ID" />
              <select v-model="binding.scope_type" :disabled="readOnly">
                <option value="global">全局</option><option value="school">学校</option><option value="college">学院</option>
                <option value="major">专业</option><option value="course">课程</option><option value="batch">批次</option>
                <option value="teacher">教师</option><option value="student">学生</option>
              </select>
              <input v-model="binding.scope_id" :disabled="readOnly || ['global', 'all'].includes(binding.scope_type)" placeholder="作用域 ID" />
              <input v-model.number="binding.priority" :disabled="readOnly" type="number" placeholder="优先级" />
              <label class="binding-enabled"><input v-model="binding.enabled" :disabled="readOnly" type="checkbox" />启用</label>
              <button v-if="!readOnly" class="tiny danger" @click="draft.bindings.splice(index, 1)">删除</button>
            </div>
          </div>
          <div v-else class="inline-empty">未配置组织绑定</div>

          <div class="section-heading">
            <div>
              <h3>结构化检查规则</h3>
              <p>只有自动规则会判定合规；人工要求会在报告中列为“未检查”。</p>
            </div>
            <button v-if="!readOnly" class="small" @click="addItem">添加规则</button>
          </div>
          <div v-if="draft.items.length === 0" class="inline-empty">还没有结构化规则</div>
          <div v-for="(item, index) in draft.items" :key="item.id + index" class="rule-row">
            <div class="rule-row-top">
              <input v-model="item.id" :disabled="readOnly" placeholder="规则 ID" />
              <select v-model="item.type" :disabled="readOnly">
                <option value="auto">自动检查</option>
                <option value="manual">人工检查</option>
                <option value="doc">参考文档</option>
              </select>
              <button v-if="!readOnly" class="tiny danger" title="删除规则" @click="draft.items.splice(index, 1)">删除</button>
            </div>
            <input v-model="item.description" :disabled="readOnly" class="rule-description" placeholder="面向学生的要求说明" />
            <div class="rule-fields">
              <select v-model="item.checker" :disabled="readOnly || item.type !== 'auto'">
                <option value="">选择检查器</option>
                <option value="section.exists">必填章节</option>
                <option value="section.word_count">章节字数</option>
                <option value="abstract.word_count">摘要字数</option>
                <option value="keywords.count">关键词数量</option>
                <option value="references.count">参考文献数量</option>
                <option value="references.recent_count">近年参考文献</option>
                <option value="references.recent_ratio">近年参考文献比例</option>
                <option value="references.format">参考文献著录格式</option>
                <option value="chapter.page_break">章前分页</option>
                <option value="ai.usage_declared">AI 使用声明</option>
              </select>
              <input v-model="item.target" :disabled="readOnly" placeholder="目标，如 abstract / ch1" />
              <input v-model="item.min" :disabled="readOnly || item.type !== 'auto'" type="number" min="0" placeholder="最小值" />
              <input v-model="item.max" :disabled="readOnly || item.type !== 'auto'" type="number" min="0" placeholder="最大值" />
              <select v-model="item.severity" :disabled="readOnly">
                <option value="error">错误</option>
                <option value="warning">警告</option>
                <option value="info">提示</option>
              </select>
              <select v-model="item.enforce" :disabled="readOnly">
                <option value="hard">硬性要求</option>
                <option value="soft">建议要求</option>
              </select>
            </div>
            <div class="rule-pack-fields">
              <label>附加规则包 ID
                <input v-model="item.rule_pack_id" :disabled="readOnly" placeholder="可选，如 GB-T-7714-1-2015" />
              </label>
              <label>规则包参数 JSON
                <textarea v-model="item.rulePackParamsText" :disabled="readOnly" rows="3"
                          placeholder='{"PG-REF-FONT-SIZE":{"when":{"!=":[{"var":"font_size_pt"},12.0]}}}'></textarea>
              </label>
            </div>
          </div>

          <div class="section-heading markdown-heading">
            <div>
              <h3>规范正文</h3>
              <p>可粘贴 Markdown；frontmatter 中的 rules 可直接生成自动检查规则。</p>
            </div>
          </div>
          <textarea v-model="draft.markdown_content" :disabled="readOnly" class="markdown-input"
                    placeholder="---\ntitle: 本科论文写作规范\nrules:\n  - id: body-min\n    type: word_count\n    target: body\n    min: 8000\n---"></textarea>

          <div class="section-heading">
            <div>
              <h3>参考文档</h3>
              <p>附件会随规范版本保存，学生只能下载当前可见版本。</p>
            </div>
          </div>
          <div v-if="draft.documents?.length" class="document-list">
            <div v-for="document in draft.documents" :key="document.id" class="document-row">
              <span>{{ document.filename }} · {{ formatBytes(document.size_bytes) }}</span>
              <span class="document-actions">
                <button class="tiny" @click="downloadDocument(document)">下载</button>
                <button v-if="!readOnly" class="tiny danger" @click="removeDocument(document)">删除</button>
              </span>
            </div>
          </div>
          <div v-if="!readOnly" class="upload-row">
            <input ref="docFile" type="file" accept=".md,.markdown,.txt,.pdf,.doc,.docx" @change="uploadDocument" />
          </div>

          <div class="editor-actions">
            <span v-if="savedMessage" class="saved-message">{{ savedMessage }}</span>
            <button v-if="canManage && draft.id && draft.status !== 'published'" class="primary" :disabled="saving" @click="save">
              {{ saving ? '保存中…' : '保存草稿' }}
            </button>
            <button v-if="canManage && draft.id && draft.status !== 'published'" :disabled="saving" @click="publish">
              发布版本
            </button>
            <button v-if="canManage && draft.id && draft.status === 'draft'" :disabled="saving" @click="submitReview">
              提交审核
            </button>
            <button v-if="canManage && draft.id && draft.status === 'published'" :disabled="saving" @click="cloneVersion">
              创建新版本
            </button>
            <button v-if="canManage && draft.id && draft.status === 'published'" :disabled="saving || bulkBusy" @click="bulkUpgrade">
              {{ bulkBusy ? '升级中…' : '批量升级项目' }}
            </button>
            <button v-if="canManage && draft.id && draft.status === 'published'" class="danger" :disabled="saving" @click="archive">
              停用规范
            </button>
            <button v-if="canManage && !draft.id" class="primary" :disabled="saving" @click="save">
              {{ saving ? '保存中…' : '创建规范' }}
            </button>
          </div>
        </template>
      </section>
    </div>

    <div v-if="cloneModal" class="modal-mask" @click.self="cloneModal = false">
      <div class="modal card">
        <h3>创建规范新版本</h3>
        <p class="hint">新版本会复制当前规范内容，发布前仍可继续编辑。</p>
        <label class="modal-field">新版本号
          <input v-model="cloneVersionInput" autofocus placeholder="如 2026.2" />
        </label>
        <div class="modal-actions">
          <button class="primary" :disabled="saving || !cloneVersionInput.trim()" @click="confirmCloneVersion">创建版本</button>
          <button @click="cloneModal = false">取消</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { auth, isManager } from '../auth'
import {
  archivePolicy, createPolicyVersion, deletePolicyDocument, downloadPolicyDocument, fetchPolicies, fetchPolicy,
  importPolicyWithAi, publishPolicy, savePolicy, submitPolicyReview, upgradePolicyProjects, uploadPolicyDocument,
} from '../api'

const policies = ref([])
const loading = ref(false)
const saving = ref(false)
const bulkBusy = ref(false)
const importing = ref(false)
const error = ref('')
const savedMessage = ref('')
const editing = ref(false)
const draft = ref(emptyPolicy())
const docFile = ref(null)
const aiImportFile = ref(null)
const cloneModal = ref(false)
const cloneVersionInput = ref('')
const canManage = computed(() => isManager() && (auth.user?.role === 'admin' || auth.user?.status === 'active'))
const readOnly = computed(() => !canManage.value || draft.value.status === 'published')

function emptyPolicy() {
  const isCollegeAdmin = auth.user?.role === 'college_admin'
  const context = auth.user?.organization_context || {}
  const defaultBindings = isCollegeAdmin && context.college_id
    ? [{ id: `binding-${Date.now()}`, scope_type: 'college', scope_id: context.college_id, priority: 0, enabled: true, note: '' }]
    : []
  return {
    id: '', name: '', level: auth.user?.role === 'admin' ? 'school' : (isCollegeAdmin ? 'college' : 'teacher'),
    scope: auth.user?.role === 'admin' ? 'global' : (isCollegeAdmin ? 'college' : 'teacher'), owner_id: auth.user?.role === 'teacher' ? auth.user.id : null,
    status: 'draft', version: '1', description: '', markdown_content: '',
    effective_from: '', effective_to: '', scope_id: '', stage: '', deadline_at: '', items: [], documents: [], bindings: defaultBindings,
  }
}

function formItem(item) {
  return {
    ...item,
    params: { ...(item.params || {}) },
    min: item.params?.min ?? item.params?.min_count ?? '',
    max: item.params?.max ?? '',
    rulePackParamsText: Object.keys(item.rule_pack_params || {}).length
      ? JSON.stringify(item.rule_pack_params, null, 2) : '',
  }
}

function statusLabel(status) {
  return { draft: '草稿', review: '待发布', published: '已发布', archived: '已停用' }[status] || status
}

function levelLabel(level) {
  return { school: '学校规范', college: '学院规范', major: '专业规范', course: '课程规范', batch: '批次规范', teacher: '教师要求' }[level] || level
}

async function loadPolicies() {
  loading.value = true
  error.value = ''
  try {
    policies.value = await fetchPolicies()
    if (draft.value.id && policies.value.some((item) => item.id === draft.value.id)) await selectPolicy(draft.value.id)
  } catch (e) { error.value = e.message || '获取写作规范失败' }
  finally { loading.value = false }
}

async function selectPolicy(id) {
  error.value = ''
  try {
    const value = await fetchPolicy(id)
    draft.value = { ...value, items: (value.items || []).map(formItem) }
    editing.value = true
    savedMessage.value = ''
  } catch (e) { error.value = e.message || '获取写作规范失败' }
}

function newPolicy() {
  draft.value = emptyPolicy()
  editing.value = true
  savedMessage.value = ''
  error.value = ''
}

function addItem() {
  draft.value.items.push({
    id: `rule-${Date.now()}`, type: 'auto', target: 'document', checker: '', params: {},
    min: '', max: '', severity: 'warning', enforce: 'soft', description: '',
    rule_pack_id: '', rule_pack_params: {}, rulePackParamsText: '',
  })
}

function addBinding() {
  draft.value.bindings = draft.value.bindings || []
  draft.value.bindings.push({ id: `binding-${Date.now()}`, scope_type: 'global', scope_id: '', priority: 0, enabled: true, note: '' })
}

function openAiImport() {
  aiImportFile.value?.click()
}

function payload() {
  return {
    ...draft.value,
    items: draft.value.items.map((item) => {
      const params = { ...(item.params || {}) }
      if (item.min !== '' && item.min !== null) params.min = Number(item.min)
      else delete params.min
      if (item.max !== '' && item.max !== null) params.max = Number(item.max)
      else delete params.max
      let rulePackParams = {}
      if (item.rulePackParamsText?.trim()) {
        try {
          rulePackParams = JSON.parse(item.rulePackParamsText)
        } catch {
          throw new Error(`规则 ${item.id || '未命名'} 的规则包参数不是有效 JSON`)
        }
        if (!rulePackParams || Array.isArray(rulePackParams) || typeof rulePackParams !== 'object') {
          throw new Error(`规则 ${item.id || '未命名'} 的规则包参数必须是 JSON 对象`)
        }
      }
      const { min, max, rulePackParamsText, ...rest } = item
      return { ...rest, params, rule_pack_params: rulePackParams }
    }),
  }
}

async function save() {
  saving.value = true; error.value = ''; savedMessage.value = ''
  try {
    const result = await savePolicy(payload(), !draft.value.id)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    editing.value = true
    savedMessage.value = '草稿已保存'
    await loadPolicies()
  } catch (e) { error.value = e.message || '保存写作规范失败' }
  finally { saving.value = false }
}

async function publish() {
  saving.value = true; error.value = ''
  try {
    if (!draft.value.id) await save()
    const result = await publishPolicy(draft.value.id)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    savedMessage.value = '规范已发布，新项目会使用该版本'
    await loadPolicies()
  } catch (e) { error.value = e.message || '发布写作规范失败' }
  finally { saving.value = false }
}

async function submitReview() {
  saving.value = true; error.value = ''
  try {
    const result = await submitPolicyReview(draft.value.id)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    savedMessage.value = '已提交审核'
    await loadPolicies()
  } catch (e) { error.value = e.message || '提交规范审核失败' }
  finally { saving.value = false }
}

function cloneVersion() {
  cloneVersionInput.value = `${draft.value.version}.1`
  cloneModal.value = true
}

async function confirmCloneVersion() {
  const next = cloneVersionInput.value.trim()
  if (!next) return
  saving.value = true; error.value = ''
  try {
    const result = await createPolicyVersion(draft.value.id, { version: next, name: draft.value.name, reason: '从当前版本修订' })
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    savedMessage.value = '已创建新版本草稿'
    cloneModal.value = false
    await loadPolicies()
  } catch (e) { error.value = e.message || '创建规范新版本失败' }
  finally { saving.value = false }
}

async function bulkUpgrade() {
  if (!window.confirm('将所有使用本规范旧版本的项目升级到当前已发布版本？项目内容不会改变。')) return
  bulkBusy.value = true; error.value = ''
  try {
    const result = await upgradePolicyProjects(draft.value.id, { reason: `规范 ${draft.value.version} 批量升级` })
    savedMessage.value = `已升级 ${result.upgraded.length} 个项目，跳过 ${result.skipped.length} 个项目`
  } catch (e) { error.value = e.message || '批量升级项目规范失败' }
  finally { bulkBusy.value = false }
}

async function uploadDocument(event) {
  const file = event.target.files?.[0]
  if (!file || !draft.value.id) return
  saving.value = true; error.value = ''
  try {
    const result = await uploadPolicyDocument(draft.value.id, file)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    savedMessage.value = '参考文档已上传'
  } catch (e) { error.value = e.message || '上传规范文档失败' }
  finally { saving.value = false; if (docFile.value) docFile.value.value = '' }
}

async function importWithAi(event) {
  const file = event.target.files?.[0]
  if (!file) return
  importing.value = true; error.value = ''; savedMessage.value = ''
  try {
    const result = await importPolicyWithAi(file)
    const imported = result.policy || result
    draft.value = { ...imported, items: (imported.items || []).map(formItem) }
    editing.value = true
    await loadPolicies()
    savedMessage.value = `AI 已生成草稿：识别 ${result.analysis?.detected_rule_count ?? draft.value.items.length} 条要求，请核对后发布`
  } catch (e) { error.value = e.message || 'AI 导入规范失败' }
  finally { importing.value = false; if (aiImportFile.value) aiImportFile.value.value = '' }
}

async function removeDocument(document) {
  if (!window.confirm(`删除参考文档「${document.filename}」？`)) return
  try {
    const result = await deletePolicyDocument(draft.value.id, document.id)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
  } catch (e) { error.value = e.message || '删除规范文档失败' }
}

async function downloadDocument(document) {
  try { await downloadPolicyDocument(draft.value.id, document.id, document.filename) }
  catch (e) { error.value = e.message || '下载规范文档失败' }
}

function formatBytes(size) {
  const value = Number(size || 0)
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

async function archive() {
  saving.value = true; error.value = ''
  try {
    const result = await archivePolicy(draft.value.id)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    savedMessage.value = '规范已停用，历史项目快照不受影响'
    await loadPolicies()
  } catch (e) { error.value = e.message || '停用写作规范失败' }
  finally { saving.value = false }
}

onMounted(loadPolicies)
</script>

<style scoped>
.policy-page { padding: 28px 0; }
.policy-page-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; margin-bottom: 22px; }
.policy-head-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; position: relative; }
.ai-import-button { display: inline-flex; align-items: center; gap: 5px; }
.ai-import-button .material-symbols-outlined { font-size: 18px; }
.file-input-hidden { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.eyebrow { color: var(--primary); font-size: 11px; font-weight: 700; letter-spacing: .08em; }
.policy-page-head h1 { margin: 5px 0 4px; font-size: 28px; }
.policy-page-head p, .section-heading p, .empty-editor p { color: var(--muted); font-size: 13px; }
.policy-layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 18px; align-items: start; }
.policy-list { padding: 14px; }
.list-head, .editor-head, .rule-row-top, .editor-actions, .section-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.list-head { padding: 0 2px 10px; border-bottom: 1px solid var(--border); }
.policy-list-item { width: 100%; display: flex; flex-direction: column; align-items: flex-start; gap: 3px; padding: 10px; margin-top: 8px; text-align: left; background: transparent; border-color: transparent; }
.policy-list-item:hover, .policy-list-item.active { background: var(--surface-container-low); border-color: var(--primary-fixed); }
.policy-list-title { font-weight: 600; color: var(--on-surface); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; width: 100%; }
.policy-list-meta { color: var(--muted); font-size: 11px; }
.empty-state, .inline-empty { color: var(--muted); font-size: 13px; text-align: center; padding: 28px 8px; }
.policy-editor { min-width: 0; }
.empty-editor { min-height: 300px; display: grid; place-content: center; text-align: center; gap: 8px; }
.empty-editor .material-symbols-outlined { font-size: 42px; color: var(--primary); }
.editor-head { align-items: flex-start; margin-bottom: 20px; }
.editor-head h2 { margin-top: 7px; font-size: 20px; }
.type-tag, .status-pill { display: inline-block; font-size: 11px; border-radius: 999px; padding: 3px 9px; }
.type-tag { background: #eff6ff; color: var(--primary); }
.status-pill { background: #f1f5f9; color: var(--muted); }
.status-pill.published { background: #dcfce7; color: #166534; }
.status-pill.archived { background: #fee2e2; color: #991b1b; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.form-grid label { display: flex; flex-direction: column; gap: 5px; color: var(--muted); font-size: 12px; }
.form-grid .span-2 { grid-column: 1 / -1; }
.form-grid input, .rule-row input, .rule-row select, .markdown-input { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; color: var(--on-surface); background: var(--card); font: inherit; font-size: 13px; }
.section-heading { margin: 26px 0 10px; align-items: flex-end; }
.section-heading h3 { font-size: 15px; margin-bottom: 2px; }
.rule-row { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin-bottom: 9px; background: var(--surface-container-lowest); }
.rule-row-top { justify-content: flex-start; }
.rule-row-top input { flex: 1; }
.rule-row-top select { width: 130px; }
.rule-description { margin-top: 8px; }
.rule-fields { display: grid; grid-template-columns: 1.2fr 1fr .7fr .7fr .8fr .9fr; gap: 7px; margin-top: 8px; }
.rule-pack-fields { display: grid; grid-template-columns: minmax(200px, .8fr) minmax(260px, 1.2fr); gap: 8px; margin-top: 8px; }
.rule-pack-fields label { display: flex; flex-direction: column; gap: 5px; color: var(--muted); font-size: 12px; }
.rule-pack-fields input, .rule-pack-fields textarea { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; color: var(--on-surface); background: var(--card); font: inherit; font-size: 12px; }
.rule-pack-fields textarea { resize: vertical; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; line-height: 1.45; }
.markdown-input { min-height: 150px; resize: vertical; line-height: 1.6; }
.editor-actions { justify-content: flex-end; margin-top: 16px; }
.saved-message { margin-right: auto; color: #15803d; font-size: 13px; }
.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, .45); display: flex; align-items: center; justify-content: center; z-index: 60; }
.modal { width: min(420px, 92vw); padding: 20px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow-card); }
.modal h3 { margin: 0 0 8px; font-size: 18px; }
.hint { color: var(--muted); font-size: 13px; line-height: 1.6; }
.modal-field { display: flex; flex-direction: column; gap: 5px; margin-top: 16px; color: var(--muted); font-size: 12px; }
.modal-field input { width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; color: var(--on-surface); background: var(--card); font: inherit; font-size: 13px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
@media (max-width: 820px) {
  .policy-layout { grid-template-columns: 1fr; }
  .policy-list { display: block; }
  .rule-fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rule-pack-fields { grid-template-columns: 1fr; }
}
@media (max-width: 520px) {
  .policy-page-head { align-items: flex-start; flex-direction: column; }
  .form-grid { grid-template-columns: 1fr; }
  .form-grid .span-2 { grid-column: auto; }
  .rule-row-top { flex-wrap: wrap; }
  .rule-row-top input, .rule-row-top select { width: 100%; }
}
</style>
