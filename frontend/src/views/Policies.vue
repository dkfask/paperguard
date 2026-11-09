<template>
  <div class="workspace-page resource-page policies-page">
    <section class="workspace-hero">
      <div>
        <p class="workspace-kicker">{{ t('policies.kicker') }}</p>
        <h1>{{ t('policies.title') }}</h1>
        <p>{{ t('policies.description') }}</p>
      </div>
      <div class="resource-hero-actions">
        <template v-if="canManage && !emptyList">
          <button class="small primary" type="button" @click="newPolicy">
            <span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('policies.new') }}
          </button>
          <button class="small" type="button" :disabled="importing" @click="openAiImport">
            <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
            {{ importing ? t('policies.aiParsing') : t('policies.aiImport') }}
          </button>
        </template>
        <span v-else-if="!canManage" class="hero-readonly-label"><span class="material-symbols-outlined" aria-hidden="true">visibility</span>{{ t('policies.readonly') }}</span>
        <input ref="aiImportFile" type="file" hidden :aria-label="t('policies.aiImport')"
               accept=".docx,.pdf,.md,.markdown,.txt" :disabled="importing" @change="importWithAi" />
      </div>
    </section>

    <div v-if="(!editing && error) || (listError && policies.length)" class="error-banner" role="alert">{{ (!editing && error) || listError }}</div>
    <div class="policy-layout">
      <section class="panel policy-list-panel" :class="{ 'policy-list-panel--state': loading || policies.length === 0 }">
        <div v-if="!loading && policies.length" class="panel-heading">
          <div>
            <h2>{{ t('policies.available') }}</h2>
            <p>{{ t('policies.choose') }}</p>
          </div>
          <div class="policy-list-actions">
            <span class="policy-count">{{ policies.length }}</span>
            <button class="icon-button" type="button" :title="t('common.refresh')" :aria-label="t('common.refresh')" :disabled="loading" @click="loadPolicies">
              <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
            </button>
          </div>
        </div>

        <AsyncState v-if="loading" state="loading" :title="t('policies.loading')" :detail="t('policies.loadingDetail')" />
        <AsyncState v-else-if="listError && policies.length === 0" state="error" :title="t('policies.loadFailed')" :detail="listError" :action-label="t('common.reload')" @action="loadPolicies" />
        <div v-else-if="policies.length === 0" class="policy-empty-state">
          <AsyncState state="empty" :title="t('policies.empty')" :detail="canManage ? t('policies.emptyManageDetail') : t('policies.emptyDetail')" />
          <div class="policy-empty-actions">
            <template v-if="canManage">
              <button class="small primary" type="button" @click="newPolicy">
                <span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('policies.new') }}
              </button>
              <button class="small" type="button" :disabled="importing" @click="openAiImport">
                <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
                {{ importing ? t('policies.aiParsing') : t('policies.aiImport') }}
              </button>
            </template>
            <button class="small" type="button" @click="loadPolicies">
              <span class="material-symbols-outlined" aria-hidden="true">refresh</span>{{ t('common.refresh') }}
            </button>
          </div>
        </div>
        <div v-else class="policy-list">
          <ScreenCollection :items="policies" :row-height="72" v-slot="{ items: visibleItems }"><button v-for="policy in visibleItems" :key="policy.id" class="policy-list-item"
                  :class="{ active: draft.id === policy.id }" type="button" @click="selectPolicy(policy.id)">
            <span class="policy-list-item-icon"><span class="material-symbols-outlined" aria-hidden="true">menu_book</span></span>
            <span class="policy-list-item-copy">
              <span class="policy-list-title">{{ policy.name }}</span>
              <span class="policy-list-meta">{{ levelLabel(policy.level) }} · v{{ policy.version }}</span>
            </span>
            <span class="status-pill" :class="policy.status">{{ statusLabel(policy.status) }}</span>
          </button></ScreenCollection>
        </div>
      </section>

      <BaseModal presentation="drawer" :open="editing" :title="t('policies.editTitle')" @close="editing = false"><section class="panel policy-editor-panel">
        <div v-if="!editing" class="empty-editor">
          <span class="material-symbols-outlined" aria-hidden="true">fact_check</span>
          <h2>{{ t('policies.selectTitle') }}</h2>
          <p>{{ t('policies.selectDetail') }}</p>
        </div>
        <template v-else>
          <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
          <div class="editor-head panel-heading">
            <div>
              <p class="workspace-kicker">{{ t('policies.available') }}</p>
              <h2>{{ draft.id ? t('policies.editTitle') : t('policies.createTitle') }}</h2>
              <p>{{ draft.name || t('policies.unnamed') }} · v{{ draft.version || '1' }}</p>
            </div>
            <div class="editor-state">
              <span class="type-tag">{{ levelLabel(draft.level) }}</span>
              <span class="status-pill" :class="draft.status">{{ statusLabel(draft.status) }}</span>
            </div>
          </div>

          <ScreenDeck><section class="screen-form-section"><h3>{{ t('screen.basic') }}</h3><div class="form-grid">
            <label>{{ t('policies.name') }}
              <input v-model="draft.name" :disabled="readOnly" :placeholder="t('policies.namePlaceholder')" />
            </label>
            <label>{{ t('policies.version') }}
              <input v-model="draft.version" :disabled="readOnly" :placeholder="t('policies.versionPlaceholder')" />
            </label>
            <label class="span-2">{{ t('policies.descriptionLabel') }}
              <input v-model="draft.description" :disabled="readOnly" :placeholder="t('policies.descriptionPlaceholder')" />
            </label>
            <label>{{ t('policies.startAt') }}
              <input v-model="draft.effective_from" :disabled="readOnly" type="datetime-local" />
            </label>
            <label>{{ t('policies.endAt') }}
              <input v-model="draft.effective_to" :disabled="readOnly" type="datetime-local" />
            </label>
            <label>{{ t('policies.level') }}
              <select v-model="draft.level" :disabled="readOnly || auth.user?.role !== 'admin'">
                <option value="school">{{ levelLabel('school') }}</option><option value="college">{{ levelLabel('college') }}</option>
                <option value="major">{{ levelLabel('major') }}</option><option value="course">{{ levelLabel('course') }}</option>
                <option value="batch">{{ levelLabel('batch') }}</option><option value="teacher">{{ levelLabel('teacher') }}</option>
              </select>
            </label>
            <label>{{ t('policies.stage') }}
              <input v-model="draft.stage" :disabled="readOnly" :placeholder="t('policies.stagePlaceholder')" />
            </label>
            <label>{{ t('policies.deadline') }}
              <input v-model="draft.deadline_at" :disabled="readOnly" type="datetime-local" />
            </label>
          </div>

          </section><section class="screen-form-section"><div class="section-heading">
            <div>
              <h3>{{ t('policies.bindings') }}</h3>
              <p>{{ t('policies.bindingsDetail') }}</p>
            </div>
            <button v-if="!readOnly" class="small" type="button" @click="addBinding"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('policies.addBinding') }}</button>
          </div>
          <div v-if="draft.bindings?.length" class="binding-list">
            <div v-for="(binding, index) in draft.bindings" :key="binding.id + index" class="binding-row">
              <label>{{ t('policies.bindingScope') }}<select v-model="binding.scope_type" :aria-label="t('policies.bindingScope')" :disabled="readOnly" @change="binding.scope_id = ''">
                <option v-for="type in bindingTypes(binding)" :key="type" :value="type">{{ levelLabel(type) }}</option>
              </select></label>
              <label v-if="!['global', 'all'].includes(binding.scope_type)">{{ t('policies.bindingTarget') }}<select v-model="binding.scope_id" :aria-label="t('policies.bindingTarget')" :disabled="readOnly">
                <option value="">{{ t('policies.chooseTarget') }}</option>
                <option v-if="binding.scope_id && !bindingTargets(binding.scope_type).some(item => item.id === binding.scope_id)" :value="binding.scope_id" disabled>{{ t('policies.unavailableTarget') }}</option>
                <option v-for="item in bindingTargets(binding.scope_type)" :key="item.id" :value="item.id">{{ item.name }}</option>
              </select></label>
              <label class="binding-enabled"><input v-model="binding.enabled" :disabled="readOnly" type="checkbox" />{{ t('common.enabled') }}</label>
              <button v-if="!readOnly" class="tiny danger" type="button" @click="draft.bindings.splice(index, 1)"><span class="material-symbols-outlined" aria-hidden="true">delete</span>{{ t('common.remove') }}</button>
            </div>
          </div>
          <div v-else class="inline-empty">{{ t('policies.notConfigured') }}</div>

          </section><section class="screen-form-section"><div class="section-heading">
            <div>
              <h3>{{ t('policies.rules') }}</h3>
              <p>{{ t('policies.rulesDetail') }}</p>
            </div>
            <button v-if="!readOnly" class="small" type="button" @click="addItem"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('policies.addRule') }}</button>
          </div>
          <div v-if="draft.items.length === 0" class="inline-empty">{{ t('policies.noRules') }}</div>
          <div v-for="(item, index) in draft.items" :key="item.id + index" class="rule-row">
            <div class="rule-row-top">
              <input v-model="item.id" :disabled="readOnly" :placeholder="t('policies.ruleId')" />
              <select v-model="item.type" :disabled="readOnly">
                <option value="auto">{{ t('policies.autoCheck') }}</option>
                <option value="manual">{{ t('policies.manualCheck') }}</option>
                <option value="doc">{{ t('policies.referenceDocument') }}</option>
              </select>
              <button v-if="!readOnly" class="tiny danger" type="button" :title="t('policies.deleteRule')" @click="draft.items.splice(index, 1)"><span class="material-symbols-outlined" aria-hidden="true">delete</span>{{ t('common.remove') }}</button>
            </div>
            <input v-model="item.description" :disabled="readOnly" class="rule-description" :placeholder="t('policies.studentRequirement')" />
            <div class="rule-fields">
              <select v-model="item.checker" :disabled="readOnly || item.type !== 'auto'" @change="checkerChanged(item, $event.target.value)">
                <option value="">{{ t('policies.selectChecker') }}</option>
                <option value="section.exists">{{ t('checker.requiredSection') }}</option>
                <option value="section.word_count">{{ t('checker.sectionWordCount') }}</option>
                <option value="abstract.word_count">{{ t('checker.abstractWordCount') }}</option>
                <option value="keywords.count">{{ t('checker.keywordCount') }}</option>
                <option value="references.count">{{ t('checker.referenceCount') }}</option>
                <option value="references.recent_count">{{ t('checker.recentReferenceCount') }}</option>
                <option value="references.recent_ratio">{{ t('checker.recentReferenceRatio') }}</option>
                <option value="references.format">{{ t('checker.referenceFormat') }}</option>
                <option value="references.list_facts">{{ t('checker.referenceFacts') }}</option>
                <option value="cover.fields_present">{{ t('checker.coverFields') }}</option>
                <option value="chapter.page_break">{{ t('checker.chapterPageBreak') }}</option>
                <option value="ai.usage_declared">{{ t('checker.aiDeclaration') }}</option>
              </select>
              <input v-model="item.target" :disabled="readOnly" :placeholder="t('policies.targetPlaceholder')" />
              <input v-model="item.min" :disabled="readOnly || item.type !== 'auto'" type="number" min="0" :placeholder="t('policies.min')" />
              <input v-model="item.max" :disabled="readOnly || item.type !== 'auto'" type="number" min="0" :placeholder="t('policies.max')" />
              <select v-model="item.severity" :disabled="readOnly">
                <option value="error">{{ t('policies.error') }}</option>
                <option value="warning">{{ t('policies.warning') }}</option>
                <option value="info">{{ t('policies.info') }}</option>
              </select>
              <select v-model="item.enforce" :disabled="readOnly">
                <option value="hard">{{ t('policies.hard') }}</option>
                <option value="soft">{{ t('policies.soft') }}</option>
              </select>
            </div>
            <div v-if="item.checker === 'cover.fields_present'" class="rule-pack-fields">
              <label>{{ t('policies.coverFieldKeys') }}
                <input v-model="item.fieldKeysText" :disabled="readOnly" :placeholder="t('policies.coverFieldKeysExample')" />
                <small>{{ t('policies.coverFieldKeysHint') }}</small>
              </label>
            </div>
            <label v-if="item.checker === 'references.list_facts'" class="reference-required">
              <input v-model="item.referenceRequired" :disabled="readOnly" type="checkbox" />{{ t('policies.referenceListRequired') }}
            </label>
            <label v-if="item.checker === 'references.list_facts'" class="reference-sequential">
              <input v-model="item.referenceSequential" :disabled="readOnly || item.type !== 'auto'" type="checkbox" />{{ t('policies.referenceSequential') }}
              <small>{{ t('policies.referenceSequentialHint') }}</small>
            </label>
            <div class="rule-pack-fields">
              <label>{{ t('policies.rulePackId') }}
                <input v-model="item.rule_pack_id" :disabled="readOnly" :placeholder="t('policies.rulePackPlaceholder')" />
              </label>
              <label>{{ t('policies.rulePackParams') }}
                <textarea v-model="item.rulePackParamsText" :disabled="readOnly" rows="3"
                          placeholder='{"PG-REF-FONT-SIZE":{"when":{"!=":[{"var":"font_size_pt"},12.0]}}}'></textarea>
              </label>
            </div>
          </div>

          </section><section class="screen-form-section"><div class="section-heading markdown-heading">
            <div>
              <h3>{{ t('policies.body') }}</h3>
              <p>{{ t('policies.bodyDetail') }}</p>
            </div>
          </div>
          <textarea v-model="draft.markdown_content" :disabled="readOnly" class="markdown-input"
                    :placeholder="t('policies.markdownPlaceholder')"></textarea>

          </section><section class="screen-form-section"><div class="section-heading">
            <div>
              <h3>{{ t('policies.referenceDocs') }}</h3>
              <p>{{ t('policies.referenceDocsDetail') }}</p>
            </div>
          </div>
          <div v-if="draft.documents?.length" class="document-list">
            <div v-for="document in draft.documents" :key="document.id" class="document-row">
              <span>{{ document.filename }} · {{ formatBytes(document.size_bytes) }}</span>
              <span class="document-actions">
                <button class="tiny" type="button" @click="downloadDocument(document)"><span class="material-symbols-outlined" aria-hidden="true">download</span>{{ t('common.download') }}</button>
                <button v-if="!readOnly" class="tiny danger" type="button" @click="removeDocument(document)"><span class="material-symbols-outlined" aria-hidden="true">delete</span>{{ t('common.remove') }}</button>
              </span>
            </div>
          </div>
          <div v-if="!readOnly" class="upload-row">
            <input ref="docFile" type="file" accept=".md,.markdown,.txt,.pdf,.doc,.docx" @change="uploadDocument" />
          </div>

          </section></ScreenDeck><div class="editor-actions">
            <span v-if="savedMessage" class="saved-message" role="status"><span class="material-symbols-outlined" aria-hidden="true">check_circle</span>{{ savedMessage }}</span>
            <button v-if="canManage && draft.id && draft.status !== 'published'" class="small primary" type="button" :disabled="saving" @click="save">
              <span class="material-symbols-outlined" aria-hidden="true">save</span>{{ saving ? t('common.saving') : t('policies.saveDraft') }}
            </button>
            <button v-if="canManage && draft.id && draft.status !== 'published'" class="small" type="button" :disabled="saving" @click="publish"><span class="material-symbols-outlined" aria-hidden="true">publish</span>{{ t('policies.publish') }}</button>
            <button v-if="canManage && draft.id && draft.status === 'draft'" class="small" type="button" :disabled="saving" @click="submitReview"><span class="material-symbols-outlined" aria-hidden="true">rate_review</span>{{ t('policies.submitReview') }}</button>
            <button v-if="canManage && draft.id && draft.status === 'published'" class="small" type="button" :disabled="saving" @click="cloneVersion"><span class="material-symbols-outlined" aria-hidden="true">content_copy</span>{{ t('policies.newVersion') }}</button>
            <button v-if="canManage && draft.id && draft.status === 'published'" class="small" type="button" :disabled="saving || bulkBusy" @click="bulkUpgrade"><span class="material-symbols-outlined" aria-hidden="true">upgrade</span>{{ bulkBusy ? t('policies.upgrading') : t('policies.upgrade') }}</button>
            <button v-if="canManage && draft.id && draft.status === 'published'" class="small danger" type="button" :disabled="saving" @click="archive"><span class="material-symbols-outlined" aria-hidden="true">archive</span>{{ t('policies.archive') }}</button>
            <button v-if="canManage && !draft.id" class="small primary" type="button" :disabled="saving" @click="save"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ saving ? t('common.saving') : t('policies.new') }}</button>
          </div>
        </template>
      </section></BaseModal>
    </div>

    <div class="policy-related-links">
      <RouterLink v-if="canUseTemplates" to="/templates" class="policy-related-link"><span class="material-symbols-outlined" aria-hidden="true">description</span><span><strong>{{ t('templates.title') }}</strong><small>{{ t('policies.templatesRelated') }}</small></span><span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink>
      <RouterLink v-if="canViewAudit" to="/audit" class="policy-related-link"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span><span><strong>{{ t('templates.audit') }}</strong><small>{{ t('policies.auditRelated') }}</small></span><span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink>
    </div>

    <BaseModal :open="cloneModal" size="small" :title="t('policies.cloneTitle')" @close="cloneModal = false"><form @submit.prevent="confirmCloneVersion">
        <p class="hint">{{ t('policies.cloneDetail') }}</p>
        <label class="modal-field">{{ t('policies.version') }}
          <input v-model="cloneVersionInput" autofocus :placeholder="t('policies.versionPlaceholder')" />
        </label>
        <div class="modal-actions">
          <button class="primary" type="submit" :disabled="saving || !cloneVersionInput.trim()">{{ t('policies.newVersion') }}</button>
          <button type="button" @click="cloneModal = false">{{ t('common.cancel') }}</button>
        </div>
      </form></BaseModal>
    </div>
  <ConfirmDialog
    :open="confirmDialog.open"
    :title="confirmDialog.title"
    :description="confirmDialog.description"
    :confirm-label="confirmDialog.confirmLabel"
    :danger="confirmDialog.danger"
    :busy="confirmBusy"
    @confirm="confirmAction"
    @cancel="closeConfirmDialog"
  />
</template>

<script setup>
import BaseModal from '../components/base/BaseModal.vue'
import ScreenDeck from '../components/ScreenDeck.vue'
import ScreenCollection from '../components/ScreenCollection.vue'
import { computed, onMounted, ref } from 'vue'
import { auth, isManager } from '../auth'
import AsyncState from '../components/AsyncState.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import {
  fetchOrganizations, fetchCohorts, getAdminUsers, getTeacherStudents,
  archivePolicy, createPolicyVersion, deletePolicyDocument, downloadPolicyDocument, fetchPolicies, fetchPolicy,
  importPolicyWithAi, publishPolicy, savePolicy, submitPolicyReview, upgradePolicyProjects, uploadPolicyDocument,
} from '../api'
import { useI18n } from '../i18n'

const { t } = useI18n()
const policies = ref([])
const organizations = ref([])
const cohorts = ref([])
const scopeUsers = ref([])
const loading = ref(true)
const saving = ref(false)
const bulkBusy = ref(false)
const importing = ref(false)
const error = ref('')
const listError = ref('')
const savedMessage = ref('')
const editing = ref(false)
const draft = ref(emptyPolicy())
const docFile = ref(null)
const aiImportFile = ref(null)
const cloneModal = ref(false)
const cloneVersionInput = ref('')
const confirmBusy = ref(false)
const confirmDialog = ref({ open: false, title: '', description: '', confirmLabel: t('common.confirm'), danger: false, action: null })
const canManage = computed(() => isManager() && (auth.user?.role === 'admin' || auth.user?.status === 'active'))
const emptyList = computed(() => !loading.value && !listError.value && policies.value.length === 0)
// 底部「论文模板 / 审计记录」入口必须与后端门槛一致，否则点了只会看到 403 死路。
const canUseTemplates = computed(() => auth.user?.role === 'admin'
  || (auth.user?.role === 'teacher' && auth.user?.status === 'active'))
const canViewAudit = computed(() => auth.user?.role === 'admin')
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
    fieldKeysText: Array.isArray(item.params?.field_keys) ? item.params.field_keys.join(', ') : '',
    referenceRequired: item.params?.required === true,
    referenceSequential: item.params?.numbering === 'sequential',
    min: item.params?.min ?? item.params?.min_count ?? '',
    max: item.params?.max ?? '',
    rulePackParamsText: Object.keys(item.rule_pack_params || {}).length
      ? JSON.stringify(item.rule_pack_params, null, 2) : '',
  }
}

function statusLabel(status) {
  const keys = { draft: 'policies.draft', review: 'policies.review', published: 'policies.published', archived: 'policies.archived' }
  return keys[status] ? t(keys[status]) : status
}

function levelLabel(level) {
  const keys = { global: 'scope.global', school: 'scope.school', college: 'scope.college', major: 'scope.major', course: 'scope.course', batch: 'scope.batch', teacher: 'scope.teacher', student: 'scope.student' }
  return keys[level] ? t(keys[level]) : level
}

async function loadPolicies() {
  loading.value = true
  error.value = ''
  listError.value = ''
  try {
    policies.value = await fetchPolicies()
    if (draft.value.id && policies.value.some((item) => item.id === draft.value.id)) await selectPolicy(draft.value.id, false)
  } catch (e) { listError.value = e.message || t('common.operationFailed') }
  finally { loading.value = false }
}

async function selectPolicy(id, openEditor = true) {
  error.value = ''
  try {
    const value = await fetchPolicy(id)
    if (!openEditor && draft.value.id !== id) return
    draft.value = { ...value, items: (value.items || []).map(formItem) }
    if (openEditor) {
      editing.value = true
      savedMessage.value = ''
    }
  } catch (e) { error.value = e.message || t('common.operationFailed') }
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
    fieldKeysText: '', referenceRequired: false, referenceSequential: false, rule_pack_id: '', rule_pack_params: {}, rulePackParamsText: '',
  })
}

function checkerChanged(item, checker) {
  if (checker === 'cover.fields_present') item.target = 'cover'
  if (checker === 'references.list_facts') item.target = 'references'
}

function bindingTargets(type) {
  if (type === 'teacher' || type === 'student') return scopeUsers.value.filter(item => item.role === type).map(item => ({ id: item.id, name: item.display_name || item.username }))
  const units = organizations.value.filter(item => item.type === type).map(item => ({ id: item.id, name: item.name }))
  if (type === 'batch') units.push(...cohorts.value.map(item => ({ id: item.id, name: item.name })))
  return [...new Map(units.map(item => [item.id, item])).values()]
}
function bindingTypes(binding) {
  return [...new Set(['global', ...['school', 'college', 'major', 'course', 'batch', 'teacher', 'student'].filter(type => bindingTargets(type).length), binding.scope_type])]
}
async function loadBindingTargets() {
  try {
    const userPromise = !canManage.value ? Promise.resolve([]) : auth.user?.role === 'admin' ? getAdminUsers()
      : auth.user?.role === 'teacher' ? getTeacherStudents().then(result => [auth.user, ...(result.students || []).map(item => ({ ...item, role: 'student' }))]) : Promise.resolve([auth.user])
    const [units, batches, users] = await Promise.all([fetchOrganizations(), canManage.value ? fetchCohorts() : Promise.resolve([]), canManage.value ? userPromise : Promise.resolve([])])
    organizations.value = units; cohorts.value = batches; scopeUsers.value = users.filter(Boolean)
  } catch (e) { error.value = e.message || t('common.operationFailed') }
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
      if (item.type === 'auto' && item.checker === 'cover.fields_present') {
        const fieldKeys = item.fieldKeysText.split(/[,，]/).map(key => key.trim()).filter(Boolean)
        if (!fieldKeys.length || new Set(fieldKeys).size !== fieldKeys.length) {
          throw new Error(t('policies.invalidCoverFieldKeys', { id: item.id || t('policies.unnamed') }))
        }
        params.field_keys = fieldKeys
      }
      if (item.type === 'auto' && item.checker === 'references.list_facts') {
        params.required = item.referenceRequired
        if (item.referenceSequential) params.numbering = 'sequential'
        else if (params.numbering === 'sequential') delete params.numbering
      }
      if (item.min !== '' && item.min !== null) params.min = Number(item.min)
      else delete params.min
      if (item.max !== '' && item.max !== null) params.max = Number(item.max)
      else delete params.max
      let rulePackParams = {}
      if (item.rulePackParamsText?.trim()) {
        try {
          rulePackParams = JSON.parse(item.rulePackParamsText)
        } catch {
          throw new Error(t('policies.invalidRulePackJson', { id: item.id || t('policies.unnamed') }))
        }
        if (!rulePackParams || Array.isArray(rulePackParams) || typeof rulePackParams !== 'object') {
          throw new Error(t('policies.invalidRulePackObject', { id: item.id || t('policies.unnamed') }))
        }
      }
      const { min, max, fieldKeysText, referenceRequired, referenceSequential, rulePackParamsText, ...rest } = item
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
    savedMessage.value = t('policies.savedDraft')
    await loadPolicies()
  } catch (e) { error.value = e.message || t('common.operationFailed') }
  finally { saving.value = false }
}

async function publish() {
  saving.value = true; error.value = ''
  try {
    if (!draft.value.id) await save()
    const result = await publishPolicy(draft.value.id)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    savedMessage.value = t('policies.publishedMessage')
    await loadPolicies()
  } catch (e) { error.value = e.message || t('common.operationFailed') }
  finally { saving.value = false }
}

async function submitReview() {
  saving.value = true; error.value = ''
  try {
    const result = await submitPolicyReview(draft.value.id)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    savedMessage.value = t('policies.reviewSubmitted')
    await loadPolicies()
  } catch (e) { error.value = e.message || t('common.operationFailed') }
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
    const result = await createPolicyVersion(draft.value.id, { version: next, name: draft.value.name, reason: t('policies.revisionReason') })
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    savedMessage.value = t('policies.versionCreated')
    cloneModal.value = false
    await loadPolicies()
  } catch (e) { error.value = e.message || t('common.operationFailed') }
  finally { saving.value = false }
}

function bulkUpgrade() {
  confirmDialog.value = { open: true, title: t('policies.bulkUpgradeTitle'), description: t('policies.bulkUpgradeDescription'), confirmLabel: t('policies.confirmUpgrade'), danger: false, action: performBulkUpgrade }
}

async function performBulkUpgrade() {
  bulkBusy.value = true; error.value = ''
  try {
    const result = await upgradePolicyProjects(draft.value.id, { reason: t('policies.bulkUpgradeReason', { version: draft.value.version }) })
    savedMessage.value = t('policies.upgradedMessage', { upgraded: result.upgraded.length, skipped: result.skipped.length })
  } catch (e) { error.value = e.message || t('common.operationFailed') }
  finally { bulkBusy.value = false }
}

async function uploadDocument(event) {
  const file = event.target.files?.[0]
  if (!file || !draft.value.id) return
  saving.value = true; error.value = ''
  try {
    const result = await uploadPolicyDocument(draft.value.id, file)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
    savedMessage.value = t('policies.documentUploaded')
  } catch (e) { error.value = e.message || t('common.operationFailed') }
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
    savedMessage.value = t('policies.aiDraftCreated', { count: result.analysis?.detected_rule_count ?? draft.value.items.length })
  } catch (e) { error.value = e.message || t('common.operationFailed') }
  finally { importing.value = false; if (aiImportFile.value) aiImportFile.value.value = '' }
}

function removeDocument(document) {
  confirmDialog.value = { open: true, title: t('policies.deleteDocumentTitle', { name: document.filename }), description: t('policies.deleteDocumentDescription'), confirmLabel: t('templates.confirmDelete'), danger: true, action: async () => {
    const result = await deletePolicyDocument(draft.value.id, document.id)
    draft.value = { ...result, items: (result.items || []).map(formItem) }
  } }
}

function closeConfirmDialog() { if (confirmBusy.value) return; confirmDialog.value.open = false; confirmDialog.value.action = null }
async function confirmAction() {
  const action = confirmDialog.value.action
  if (!action || confirmBusy.value) return
  confirmBusy.value = true
  try { await action() } catch (e) { error.value = e.message || t('common.operationFailed') } finally { confirmBusy.value = false; closeConfirmDialog() }
}

async function downloadDocument(document) {
  try { await downloadPolicyDocument(draft.value.id, document.id, document.filename) }
  catch (e) { error.value = e.message || t('common.operationFailed') }
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
    savedMessage.value = t('policies.archivedMessage')
    await loadPolicies()
  } catch (e) { error.value = e.message || t('common.operationFailed') }
  finally { saving.value = false }
}

onMounted(() => { loadPolicies(); loadBindingTargets() })
</script>

<style scoped>
.hero-readonly-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--on-surface-variant);
  font-size: 12px;
}

.hero-readonly-label .material-symbols-outlined {
  color: var(--primary-fixed-dim);
  font-size: 17px;
}

.policy-layout {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.policy-list-panel,
.policy-editor-panel {
  min-width: 0;
}

.policy-list-panel {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.policy-list-panel--state {
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.policy-empty-state {
  display: flex;
  flex: 1;
  flex-direction: column;
  justify-content: center;
  min-height: 240px;
}

.policy-list-panel--state > .async-state {
  flex: 1;
  align-content: center;
}

.policy-list-panel--state > .async-state,
.policy-empty-state :deep(.async-state) {
  min-height: 0;
  padding: 24px 12px;
  border: 0;
}

.policy-empty-state :deep(.async-state h3) {
  font-size: 20px;
}

.policy-empty-state :deep(.async-state p) {
  font-size: 14px;
  line-height: 1.7;
}

.policy-empty-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 12px;
  padding: 0 12px 24px;
}

.policy-list-actions,
.editor-state,
.policy-list-item,
.document-actions,
.saved-message,
.policy-related-link {
  display: flex;
  align-items: center;
}

.policy-list-actions {
  gap: 10px;
}

.policy-count {
  color: var(--on-surface-variant);
  font-size: 12px;
  white-space: nowrap;
}

.policy-list {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  margin-top: 14px;
  border-top: 1px solid var(--border);
}

.policy-list-item {
  width: 100%;
  min-width: 0;
  gap: 9px;
  margin: 0;
  padding: 12px 0;
  border: 0;
  border-bottom: 1px solid var(--border);
  border-radius: 0;
  background: transparent;
  text-align: left;
}

.policy-list-item:hover,
.policy-list-item.active {
  background: var(--surface-container-low);
}

.policy-list-item.active {
  padding-left: 6px;
  border-left: 3px solid var(--primary);
}

.policy-list-item-icon {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  border-radius: var(--radius-xs);
  background: var(--surface-container-low);
  color: var(--primary);
}

.policy-list-item-copy {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 3px;
}

.policy-list-title {
  overflow: hidden;
  color: var(--on-surface);
  font-size: 13px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.policy-list-meta {
  overflow: hidden;
  color: var(--on-surface-variant);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-editor {
  display: grid;
  justify-items: center;
  min-height: 340px;
  padding: 34px 20px;
  align-content: center;
  gap: 8px;
  text-align: center;
}

.empty-editor .material-symbols-outlined {
  color: var(--primary);
  font-size: 42px;
}

.empty-editor h2 {
  font-size: 20px;
}

.empty-editor p,
.section-heading p {
  color: var(--on-surface-variant);
  font-size: 13px;
}

.editor-head {
  margin-bottom: 20px;
}

.editor-head .workspace-kicker {
  margin-bottom: 4px;
}

.editor-head h2 {
  font-size: 20px;
}

.editor-state {
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}

.type-tag,
.status-pill {
  display: inline-block;
  padding: 3px 9px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  white-space: nowrap;
}

.type-tag {
  background: var(--primary-fixed);
  color: var(--on-primary-fixed);
}

.status-pill {
  background: var(--surface-container-low);
  color: var(--on-surface-variant);
}

.status-pill.review {
  background: var(--tertiary-fixed);
  color: var(--tertiary);
}

.status-pill.published {
  background: #dcfce7;
  color: #166534;
}

.status-pill.archived {
  background: var(--error-container);
  color: var(--on-error-container);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.form-grid label,
.rule-pack-fields label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--on-surface-variant);
  font-size: 12px;
}

.form-grid .span-2 {
  grid-column: 1 / -1;
}

.form-grid input,
.form-grid select,
.rule-row input,
.rule-row select,
.markdown-input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  color: var(--on-surface);
  background: var(--surface-container-lowest);
  font: inherit;
  font-size: 13px;
}

.section-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
  margin: 26px 0 10px;
}

.section-heading h3 {
  margin-bottom: 2px;
  font-size: 15px;
}

.rule-row {
  margin-bottom: 9px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-container-lowest);
}

.rule-row-top,
.editor-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.rule-row-top {
  justify-content: flex-start;
}

.rule-row-top input {
  flex: 1;
}

.rule-row-top select {
  width: 130px;
}

.rule-description {
  margin-top: 8px;
}

.rule-fields {
  display: grid;
  grid-template-columns: 1.2fr 1fr .7fr .7fr .8fr .9fr;
  gap: 7px;
  margin-top: 8px;
}

.rule-pack-fields {
  display: grid;
  grid-template-columns: minmax(200px, .8fr) minmax(260px, 1.2fr);
  gap: 8px;
  margin-top: 8px;
}

.rule-pack-fields input,
.rule-pack-fields textarea {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  color: var(--on-surface);
  background: var(--surface-container-lowest);
  font: inherit;
  font-size: 12px;
}

.rule-pack-fields textarea {
  resize: vertical;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  line-height: 1.45;
}

.inline-empty {
  padding: 20px 8px;
  border: 1px dashed var(--border);
  color: var(--on-surface-variant);
  font-size: 13px;
  text-align: center;
}

.markdown-input {
  min-height: 150px;
  resize: vertical;
  line-height: 1.6;
}

.document-list {
  border-top: 1px solid var(--border);
}

.document-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
  color: var(--on-surface-variant);
  font-size: 12px;
}

.document-row > span:first-child {
  min-width: 0;
  overflow-wrap: anywhere;
}

.document-actions {
  flex: 0 0 auto;
  gap: 6px;
}

.upload-row {
  margin-top: 12px;
}

.upload-row input {
  max-width: 100%;
  font-size: 12px;
}

.editor-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.saved-message {
  gap: 5px;
  margin-right: auto;
  color: #15803d;
  font-size: 13px;
}

.saved-message .material-symbols-outlined {
  font-size: 17px;
}

.policy-related-links {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 8px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}

.policy-related-link {
  min-width: 0;
  gap: 10px;
  padding: 10px 4px;
  color: var(--on-surface);
  text-decoration: none;
}

.policy-related-link:hover {
  color: var(--primary);
}

.policy-related-link > .material-symbols-outlined:first-child {
  color: var(--primary);
  font-size: 19px;
}

.policy-related-link > span:nth-child(2) {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}

.policy-related-link strong {
  font-size: 13px;
}

.policy-related-link small {
  overflow: hidden;
  color: var(--on-surface-variant);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.policy-related-link > .material-symbols-outlined:last-child {
  color: var(--outline);
  font-size: 17px;
}

.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, .45);
}

.modal {
  width: min(420px, 92vw);
  padding: 20px;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--card);
  box-shadow: var(--shadow-card);
}

.modal h3 {
  margin: 0 0 8px;
  font-size: 18px;
}

.hint {
  color: var(--on-surface-variant);
  font-size: 13px;
  line-height: 1.6;
}

.modal-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 16px;
  color: var(--on-surface-variant);
  font-size: 12px;
}

.modal-field input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  color: var(--on-surface);
  background: var(--card);
  font: inherit;
  font-size: 13px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}

@media (max-width: 900px) {
  .rule-fields {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .rule-pack-fields {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .form-grid {
    grid-template-columns: 1fr;
  }

  .form-grid .span-2 {
    grid-column: auto;
  }

  .editor-head,
  .section-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .editor-state {
    justify-content: flex-start;
  }

  .rule-row-top {
    flex-wrap: wrap;
  }

  .rule-row-top input,
  .rule-row-top select {
    width: 100%;
  }

  .document-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .policy-related-links {
    grid-template-columns: 1fr;
  }
}
.binding-row { display: flex; align-items: end; gap: 12px; flex-wrap: wrap; padding: 12px 0; border-bottom: 1px solid var(--border); }
.binding-row > label:not(.binding-enabled) { display: grid; gap: 6px; flex: 1 1 180px; font-size: 12px; color: var(--on-surface-variant); }
.binding-row select { width: 100%; min-width: 0; }
</style>
