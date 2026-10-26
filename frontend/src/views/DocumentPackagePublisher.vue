<template>
  <div class="publisher-page">
    <header class="publisher-header">
      <div>
        <RouterLink to="/document-projects" class="back-link">
          <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>
          {{ publisherText('back') }}
        </RouterLink>
        <p class="workspace-kicker">{{ publisherText('kicker') }}</p>
        <h1>{{ publisherText('title') }}</h1>
        <p>{{ publisherText('description') }}</p>
      </div>
      <span class="material-symbols-outlined publisher-icon" aria-hidden="true">{{ personalWriting ? 'settings' : 'publish' }}</span>
    </header>

    <section v-if="!canManage" class="access-state" role="alert">
      <span class="material-symbols-outlined" aria-hidden="true">lock</span>
      <h2>{{ t('documentPackagePublisher.accessTitle') }}</h2>
      <p>{{ t('documentPackagePublisher.accessDetail') }}</p>
      <RouterLink to="/document-projects" class="small primary">{{ publisherText('backToDocuments') }}</RouterLink>
    </section>

    <template v-else>
      <div v-if="error && !templateReviewOpen && !specReviewOpen" class="error-banner" role="alert">{{ error }}</div>

      <ScreenDeck class="publisher-deck"><article class="step-panel">
          <div class="step-heading">
            <span class="step-number">1</span>
            <div>
              <p class="workspace-kicker">{{ t('documentPackagePublisher.templateEyebrow') }}</p>
              <h2>{{ t('documentPackagePublisher.templateTitle') }}</h2>
              <p>{{ t('documentPackagePublisher.templateDetail') }}</p>
            </div>
          </div>

          <div class="parser-upload">
          <label class="file-picker">
            <span>{{ t('documentPackagePublisher.templateFile') }}</span>
            <input data-test="template-file" type="file" accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" :disabled="templateBusy || publishBusy" @change="selectTemplate" />
            <small>{{ templateFile?.name || t('documentPackagePublisher.noFile') }}</small>
          </label>
          <button data-test="template-parse" class="small primary" type="button" :disabled="!templateFile || templateBusy || publishBusy" @click="parseTemplate">
            <span class="material-symbols-outlined" aria-hidden="true">document_scanner</span>
            {{ templateBusy ? t('documentPackagePublisher.parsing') : t('documentPackagePublisher.parse') }}
          </button>
          </div>



          <label v-if="templateCandidates.length" class="saved-picker">
            <span>{{ t('documentPackagePublisher.savedCandidates') }}</span>
            <select data-test="template-saved" v-model="templateCandidateId" @change="restoreTemplate">
              <option value="">{{ t('documentPackagePublisher.selectSaved') }}</option>
              <option v-for="candidate in templateCandidates" :key="candidate.candidate_id" :value="candidate.candidate_id">
                {{ candidate.name }} · {{ candidate.version }} · {{ statusLabel(candidate.status) }}
              </option>
            </select>
          </label>

          <div v-if="templateCandidate" class="candidate-review candidate-summary">
            <div class="review-heading"><strong>{{ templateCandidate.name }}</strong><span class="status-pill" :class="templateCandidate.status">{{ statusLabel(templateCandidate.status) }}</span></div>
            <div class="review-summary"><span>{{ t('documentPackagePublisher.sections') }}: {{ templateSections.length }}</span><span>{{ t('documentPackagePublisher.fields') }}: {{ templateCandidate.definition.fields.length }}</span></div>
            <button data-test="template-review" type="button" class="small" @click="templateReviewOpen = true">{{ t('screen.reviewResults') }}</button>
          </div>
        </article><article class="step-panel">
          <div class="step-heading">
            <span class="step-number">2</span>
            <div>
              <p class="workspace-kicker">{{ t('documentPackagePublisher.specEyebrow') }}</p>
              <h2>{{ t('documentPackagePublisher.specTitle') }}</h2>
              <p>{{ t('documentPackagePublisher.specDetail') }}</p>
            </div>
          </div>

          <div class="parser-upload">
          <label class="file-picker">
            <span>{{ t('documentPackagePublisher.specFile') }}</span>
            <input ref="specFileInput" data-test="spec-file" type="file" accept=".doc,.docx,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.pdf,.md,.markdown,.txt" :disabled="specBusy || publishBusy" @change="selectSpec" />
            <small>{{ specFile?.name || t('documentPackagePublisher.noFile') }}</small>
          </label>
          <button data-test="spec-parse" class="small primary" type="button" :disabled="!specIncluded || !specFile || specBusy || publishBusy" @click="parseSpec">
            <span class="material-symbols-outlined" aria-hidden="true">rule_folder</span>
            {{ specBusy ? t('documentPackagePublisher.parsing') : t('documentPackagePublisher.parse') }}
          </button>
          </div>

          <label class="check-editor optional-toggle">
            <input data-test="spec-enabled" v-model="specIncluded" type="checkbox" :disabled="specBusy || publishBusy" @change="toggleSpec" />
            {{ t('documentPackagePublisher.specUseSeparate') }}
          </label>
          <p v-if="!specIncluded" class="optional-note">
            <span class="material-symbols-outlined" aria-hidden="true">info</span>
            {{ publisherText('specOptional') }}
          </p>



          <label v-if="specCandidates.length" class="saved-picker">
            <span>{{ t('documentPackagePublisher.savedCandidates') }}</span>
            <select data-test="spec-saved" v-model="specCandidateId" @change="restoreSpec">
              <option value="">{{ t('documentPackagePublisher.selectSaved') }}</option>
              <option v-for="candidate in specCandidates" :key="candidate.candidate_id" :value="candidate.candidate_id">
                {{ candidate.name }} · {{ candidate.version }} · {{ statusLabel(candidate.status) }}
              </option>
            </select>
          </label>

          <div v-if="specCandidate" class="candidate-review candidate-summary">
            <div class="review-heading"><strong>{{ specCandidate.name }}</strong><span class="status-pill" :class="specCandidate.status">{{ statusLabel(specCandidate.status) }}</span></div>
            <div class="review-summary"><span>{{ t('documentPackagePublisher.rules') }}: {{ specCandidate.rules.length }}</span></div>
            <button data-test="spec-review" type="button" class="small" @click="specReviewOpen = true">{{ t('screen.reviewResults') }}</button>
          </div>
        </article><section class="publish-panel">
        <div class="section-heading">
          <div>
            <p class="workspace-kicker">{{ publisherText('publishEyebrow') }}</p>
            <h2>{{ publisherText('publishTitle') }}</h2>
            <p>{{ publisherText('publishDetail') }}</p>
          </div>
          <span class="material-symbols-outlined panel-icon" aria-hidden="true">inventory_2</span>
        </div>

        <div class="publish-form">
          <label>{{ publisherText('documentType') }}
            <select v-if="personalWriting" v-model="documentTypeId" :aria-label="publisherText('documentType')"><option value="generic">{{ t('personalTemplates.otherDocument') }}</option><option value="research-paper">{{ t('personalTemplates.researchPaper') }}</option><option value="grant-application">{{ t('personalTemplates.grantApplication') }}</option></select>
            <input v-else v-model="documentTypeId" maxlength="80" :placeholder="publisherText('documentTypePlaceholder')" />
          </label>
          <div class="readiness" role="status" aria-live="polite">
            <span :class="{ ready: templateReady }"><span class="material-symbols-outlined" aria-hidden="true">{{ templateReady ? 'check_circle' : 'radio_button_unchecked' }}</span>{{ t('documentPackagePublisher.templateStatus') }}</span>
            <span :class="{ ready: specReady }"><span class="material-symbols-outlined" aria-hidden="true">{{ specReady ? 'check_circle' : 'radio_button_unchecked' }}</span>{{ t('documentPackagePublisher.specStatus') }}</span>
          </div>
          <button data-test="publish" class="primary" type="button" :disabled="!readyToPublish || publishBusy" @click="publish">
            <span class="material-symbols-outlined" aria-hidden="true">{{ personalWriting ? 'save' : 'publish' }}</span>
            {{ publishBusy ? publisherText('publishing') : publisherText('publish') }}
          </button>
        </div>

        <div v-if="packageResult" class="success-panel" role="status">
          <span class="material-symbols-outlined" aria-hidden="true">task_alt</span>
          <div>
            <strong>{{ publisherText('published') }}</strong>
            <p>{{ packageResult.template.name }} · {{ packageResult.spec_set.name }}</p>
            <small v-if="!personalWriting">{{ packageResult.package.id }}</small>
          </div>
          <RouterLink to="/document-projects" class="small primary">{{ publisherText('openDocuments') }}</RouterLink>
        </div>
      </section></ScreenDeck>
      <BaseModal presentation="drawer" :open="templateReviewOpen" :title="t('documentPackagePublisher.templateTitle')" @close="templateReviewOpen = false">
        <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
<div v-if="templateCandidate" class="candidate-review candidate-review-drawer">
            <div class="review-heading">
              <strong>{{ templateCandidate.name }}</strong>
              <span class="status-pill" :class="templateCandidate.status">{{ statusLabel(templateCandidate.status) }}</span>
            </div>
            <div class="review-editor">
              <label>{{ t('documentPackagePublisher.candidateName') }}
                <input data-test="template-candidate-name" v-model.trim="templateCandidate.name" maxlength="160" :disabled="templateCandidate.status === 'confirmed'" />
              </label>
              <label>{{ t('documentPackagePublisher.candidateVersion') }}
                <input data-test="template-candidate-version" v-model.trim="templateCandidate.version" maxlength="40" :disabled="templateCandidate.status === 'confirmed'" />
              </label>
            </div>
            <div class="review-summary">
              <span>{{ t('documentPackagePublisher.sections') }}: {{ templateSections.length }}</span>
              <span>{{ t('documentPackagePublisher.fields') }}: {{ templateCandidate.definition.fields.length }}</span>
            </div>
            <WritingSpecificationPreview :specification="templateCandidate.definition.extensions?.writing_specification" :markdown="templateCandidate.definition.extensions?.writing_specification_markdown" />
            <WritingProfileConfirmation v-if="templateCandidate.status !== 'confirmed'" :template="candidateWritingTemplate" :candidate="templateCandidate" @published="candidateProfilePrepared" />
            <section v-if="templateCandidate.admission_assessment" class="template-admission" :class="templateCandidate.admission_assessment.status" data-test="template-admission">
              <div class="admission-heading">
                <strong>{{ t('templateAdmission.title') }}</strong>
                <span>{{ templateCandidate.admission_assessment.status === 'blocked' ? t('templateAdmission.blocked') : t('templateAdmission.officeReview') }}</span>
              </div>
              <p>{{ t('templateAdmission.boundVersion', { version: templateCandidate.version, digest: templateCandidate.admission_assessment.assessment_hash?.slice(0, 12) }) }}</p>
              <p>{{ t('templateAdmission.autoCoverage', { count: templateCandidate.admission_assessment.auto_verifiable_constraints?.length || 0 }) }}</p>
              <ul v-if="templateCandidate.admission_assessment.blockers?.length" class="admission-findings">
                <li v-for="item in templateCandidate.admission_assessment.blockers" :key="`${item.code}:${item.path}`">{{ item.message }} <small>{{ item.path }}</small></li>
              </ul>
              <ul v-if="templateCandidate.admission_assessment.manual_checks?.length" class="admission-findings manual">
                <li v-for="item in templateCandidate.admission_assessment.manual_checks" :key="item.code">{{ item.message }}</li>
              </ul>
              <p class="admission-limit">{{ t('templateAdmission.noComplianceClaim') }}</p>
              <button v-if="templateAssessmentStale" data-test="template-reassess" class="small" type="button" :disabled="templateBusy" @click="reassessTemplateCandidate">{{ t('templateAdmission.reassess') }}</button>
              <p v-if="templateAssessmentStale" class="admission-stale" role="status">{{ t('templateAdmission.stale') }}</p>
            </section>
            <ul v-if="templateCandidate.issues.length" class="issue-list">
              <li v-for="issue in templateCandidate.issues" :key="`${issue.code}:${issue.path}`" :class="issue.severity">
                <span class="issue-severity">{{ issue.severity }}</span>
                <span>{{ issue.message }}</span>
                <small v-if="issue.path">{{ issue.path }}</small>
              </li>
            </ul>
            <p v-else class="clean-note"><span class="material-symbols-outlined" aria-hidden="true">check_circle</span>{{ t('documentPackagePublisher.noIssues') }}</p>
            <div v-if="templateSections.length" class="detected-list">
              <h3>{{ t('documentPackagePublisher.detectedSections') }}</h3>
              <div v-for="section in templateSections" :key="section.node.key" class="section-preview-row" :data-test="`template-section-${section.node.key}`" :style="{ paddingLeft: `${10 + Math.min(section.depth, 3) * 16}px` }">
                <textarea data-test="template-section-title" v-model.trim="section.node.title" rows="1" :aria-label="t('documentPackagePublisher.sectionTitle')" :disabled="templateCandidate.status === 'confirmed'" />
                <span class="section-level">{{ sectionLevelLabel(section) }}</span>
              </div>
            </div>
            <div v-if="templateTables.length" class="detected-list" data-test="template-table-summary">
              <h3>{{ t('documentPackagePublisher.detectedTables') }}: {{ templateTables.length }}</h3>
              <div v-for="table in templateTables" :key="table.index" class="detected-row table-row" :data-test="`template-table-${table.index}`">
                <span class="material-symbols-outlined" aria-hidden="true">table_chart</span>
                <span>{{ t('documentPackagePublisher.tableNumber') }} {{ table.index }}</span>
                <small>
                  {{ table.rows }} × {{ table.cols }} · {{ t('documentPackagePublisher.nonEmptyCells') }} {{ table.non_empty_cells }}
                  <span v-if="table.has_merged_cells"> · {{ t('documentPackagePublisher.mergedCells') }}</span>
                  <span v-if="table.section_title"> · {{ table.section_title }}</span>
                </small>
              </div>
            </div>
            <details v-if="templateCandidate.definition.fields.length" class="detected-list template-fields">
              <summary>{{ t('documentPackagePublisher.reviewFields') }}</summary>
              <div v-for="field in templateCandidate.definition.fields" :key="field.key" class="field-editor" :data-test="`template-field-${field.key}`">
                <input :value="field.key" :aria-label="`${t('documentPackagePublisher.fieldKey')}: ${field.key}`" readonly />
                <input data-test="template-field-label" v-model.trim="field.label" :aria-label="t('documentPackagePublisher.fieldLabel')" :disabled="templateCandidate.status === 'confirmed'" />
                <select v-model="field.type" :aria-label="t('documentPackagePublisher.fieldType')" :disabled="templateCandidate.status === 'confirmed'">
                  <option value="text">text</option>
                  <option value="textarea">textarea</option>
                  <option value="multiline">multiline</option>
                  <option value="richtext">richtext</option>
                  <option value="date">date</option>
                  <option value="number">number</option>
                  <option value="email">email</option>
                  <option value="url">url</option>
                  <option value="checkbox">checkbox</option>
                  <option value="select">select</option>
                  <option value="enum">enum</option>
                </select>
                <input
                  v-if="['select', 'enum'].includes(field.type)"
                  data-test="template-field-options"
                  :value="choiceOptions(field).join(', ')"
                  :aria-label="t('documentPackagePublisher.fieldOptions')"
                  :disabled="templateCandidate.status === 'confirmed'"
                  @input="setChoiceOptions(field, $event)"
                />
                <label class="check-editor">
                  <input v-model="field.required" type="checkbox" :disabled="templateCandidate.status === 'confirmed'" />
                  {{ t('documentPackagePublisher.required') }}
                </label>
              </div>
            </details>
            <button data-test="template-confirm" class="small" type="button" :disabled="templateBusy || templateCandidate.status === 'confirmed' || templateAssessmentStale || templateCandidate.admission_assessment?.blockers?.length" @click="confirmTemplate">
              <span class="material-symbols-outlined" aria-hidden="true">verified</span>
              {{ templateCandidate.status === 'confirmed' ? t('documentPackagePublisher.confirmed') : t('documentPackagePublisher.confirmTemplate') }}
            </button>
          </div>
      </BaseModal>

      <BaseModal presentation="drawer" :open="specReviewOpen" :title="t('documentPackagePublisher.specTitle')" @close="specReviewOpen = false">
        <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
<div v-if="specCandidate" class="candidate-review candidate-review-drawer">
            <div class="review-heading">
              <strong>{{ specCandidate.name }}</strong>
              <span class="status-pill" :class="specCandidate.status">{{ statusLabel(specCandidate.status) }}</span>
            </div>
            <div class="review-editor">
              <label>{{ t('documentPackagePublisher.candidateName') }}
                <input v-model.trim="specCandidate.name" maxlength="160" :disabled="specCandidate.status === 'confirmed'" />
              </label>
              <label>{{ t('documentPackagePublisher.candidateVersion') }}
                <input v-model.trim="specCandidate.version" maxlength="40" :disabled="specCandidate.status === 'confirmed'" />
              </label>
            </div>
            <label class="full-editor">{{ t('documentPackagePublisher.descriptionLabel') }}
              <textarea v-model.trim="specCandidate.description" rows="2" maxlength="2000" :disabled="specCandidate.status === 'confirmed'"></textarea>
            </label>
            <div class="review-summary">
              <span>{{ t('documentPackagePublisher.rules') }}: {{ specCandidate.rules.length }}</span>
              <span>{{ t('documentPackagePublisher.extraction') }}: {{ specCandidate.extraction_mode }}</span>
              <span>{{ t('documentPackagePublisher.sourceCharacters') }}: {{ specCandidate.source_characters }}</span>
            </div>
            <ul v-if="specCandidate.issues.length" class="issue-list">
              <li v-for="issue in specCandidate.issues" :key="`${issue.code}:${issue.path}`" :class="issue.severity">
                <span class="issue-severity">{{ issue.severity }}</span>
                <span>{{ issue.message }}</span>
                <small v-if="issue.path">{{ issue.path }}</small>
              </li>
            </ul>
            <p v-else class="clean-note"><span class="material-symbols-outlined" aria-hidden="true">check_circle</span>{{ t('documentPackagePublisher.noIssues') }}</p>
            <div v-if="specCandidate.rules.length" class="detected-list">
              <h3>{{ t('documentPackagePublisher.reviewRules') }}</h3>
              <div v-for="rule in specCandidate.rules" :key="rule.id" class="rule-editor">
                <div class="rule-editor-grid">
                  <input :value="rule.id" :aria-label="`${t('documentPackagePublisher.ruleId')}: ${rule.id}`" readonly />
                  <select v-model="rule.kind" :aria-label="t('documentPackagePublisher.ruleKind')" :disabled="specCandidate.status === 'confirmed'">
                    <option value="auto">auto</option>
                    <option value="manual">manual</option>
                    <option value="doc">doc</option>
                  </select>
                  <input v-model.trim="rule.target" :aria-label="t('documentPackagePublisher.ruleTarget')" :disabled="specCandidate.status === 'confirmed'" />
                  <input v-model.trim="rule.checker" :aria-label="t('documentPackagePublisher.ruleChecker')" :disabled="specCandidate.status === 'confirmed'" />
                  <select v-model="rule.severity" :aria-label="t('documentPackagePublisher.ruleSeverity')" :disabled="specCandidate.status === 'confirmed'">
                    <option value="error">error</option>
                    <option value="warning">warning</option>
                    <option value="info">info</option>
                  </select>
                  <select v-model="rule.enforce" :aria-label="t('documentPackagePublisher.ruleEnforce')" :disabled="specCandidate.status === 'confirmed'">
                    <option value="hard">hard</option>
                    <option value="soft">soft</option>
                  </select>
                </div>
                <textarea v-model.trim="rule.description" rows="2" :aria-label="t('documentPackagePublisher.ruleDescription')" :disabled="specCandidate.status === 'confirmed'"></textarea>
                <textarea v-model="specParams[rule.id]" rows="2" :aria-label="t('documentPackagePublisher.ruleParams')" :disabled="specCandidate.status === 'confirmed'"></textarea>
              </div>
            </div>
            <button data-test="spec-confirm" class="small" type="button" :disabled="specBusy || specCandidate.status === 'confirmed'" @click="confirmSpec">
              <span class="material-symbols-outlined" aria-hidden="true">verified</span>
              {{ specCandidate.status === 'confirmed' ? t('documentPackagePublisher.confirmed') : t('documentPackagePublisher.confirmSpec') }}
            </button>
          </div>
      </BaseModal>
    </template>
  </div>
</template>

<script setup>
import ScreenDeck from '../components/ScreenDeck.vue'
import BaseModal from '../components/base/BaseModal.vue'
import WritingSpecificationPreview from '../components/WritingSpecificationPreview.vue'
import WritingProfileConfirmation from '../components/WritingProfileConfirmation.vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from '../i18n'
import { auth } from '../auth'
import {
  confirmDocumentSpecCandidate,
  confirmDocumentTemplateCandidate,
  assessDocumentTemplateCandidate,
  getDocumentSpecCandidates,
  getDocumentTemplateCandidates,
  parseDocumentSpec,
  parseDocumentTemplate,
  publishDocumentPackage,
} from '../api.js'

const { t } = useI18n()
const canManage = computed(() => auth.canManage())
const personalWriting = computed(() => auth.user?.role === 'teacher')
function publisherText(key) { return t(`${personalWriting.value ? 'personalTemplates' : 'documentPackagePublisher'}.${key}`) }
const templateFile = ref(null)
const specFile = ref(null)
const specFileInput = ref(null)
const templateCandidate = ref(null)
const candidateWritingTemplate = computed(() => ({id:templateCandidate.value?.template_id, source_docx:templateCandidate.value?.definition.source_docx, writing_specification:templateCandidate.value?.definition.extensions?.writing_specification}))
function candidateProfilePrepared(result) {
  templateCandidate.value = result.candidate
  templateAssessmentSnapshot.value = templateAssessmentInput(result.candidate)
}
const specCandidate = ref(null)
const templateCandidates = ref([])
const specCandidates = ref([])
const templateCandidateId = ref('')
const specCandidateId = ref('')
const specIncluded = ref(false)
const specParams = ref({})
const documentTypeId = ref('generic')
const templateBusy = ref(false)
const specBusy = ref(false)
const publishBusy = ref(false)
const packageResult = ref(null)
const error = ref('')
const templateReviewOpen = ref(false)
const specReviewOpen = ref(false)
const templateAssessmentSnapshot = ref('')

function templateAssessmentInput(candidate) {
  return candidate ? JSON.stringify({ template_id: candidate.template_id, version: candidate.version, definition: candidate.definition }) : ''
}
const templateAssessmentStale = computed(() => Boolean(templateCandidate.value) && (!templateCandidate.value?.admission_assessment?.assessment_hash || templateAssessmentInput(templateCandidate.value) !== templateAssessmentSnapshot.value))
const templateReady = computed(() => templateCandidate.value?.status === 'confirmed' && !templateCandidate.value?.admission_assessment?.blockers?.length && !templateAssessmentStale.value)
const specReady = computed(() => !specIncluded.value || specCandidate.value?.status === 'confirmed')
const readyToPublish = computed(() => Boolean(canManage.value && templateReady.value && specReady.value && documentTypeId.value.trim()))

const templateSections = computed(() => {
  const flattened = []
  function walk(nodes, depth = 0) {
    for (const section of nodes || []) {
      flattened.push({ node: section, depth })
      walk(section.children, depth + 1)
    }
  }
  walk(templateCandidate.value?.definition?.sections)
  return flattened
})

const templateTables = computed(() => templateCandidate.value?.definition?.extensions?.source_document?.tables || [])

function upsertCandidate(collection, candidate) {
  if (!candidate?.candidate_id) return
  const index = collection.value.findIndex(item => item.candidate_id === candidate.candidate_id)
  if (index === -1) collection.value.unshift(candidate)
  else collection.value[index] = candidate
}

function syncSpecParams(candidate) {
  const values = {}
  for (const rule of candidate?.rules || []) {
    values[rule.id] = JSON.stringify(rule.params || {}, null, 2)
  }
  specParams.value = values
}

async function loadSavedCandidates() {
  if (!canManage.value) return
  try {
    const [templates, specs] = await Promise.all([
      getDocumentTemplateCandidates(),
      getDocumentSpecCandidates(),
    ])
    templateCandidates.value = Array.isArray(templates) ? templates : []
    specCandidates.value = Array.isArray(specs) ? specs : []
  } catch (exception) {
    error.value = exception.message
  }
}

function statusLabel(status) {
  if (status === 'confirmed') return t('documentPackagePublisher.confirmed')
  return t('documentPackagePublisher.needsConfirmation')
}

function choiceOptions(field) {
  return Array.isArray(field.constraints?.options) ? field.constraints.options : []
}

function sectionLevelLabel({ node, depth }) {
  const labels = {
    content: 'newBody', body: 'newBody', cover: 'cover', toc: 'toc',
    abstract: 'abstract', english_abstract: 'englishAbstract',
    keywords: 'keywords', english_keywords: 'englishKeywords',
    references: 'references', appendix: 'appendix', acknowledgement: 'acknowledgement',
    heading1: 'heading1', heading2: 'heading2', heading3: 'heading3',
  }
  return t(`section.${labels[node.kind] || `heading${Math.min(depth + 1, 3)}`}`)
}

function setChoiceOptions(field, event) {
  field.constraints = {
    ...(field.constraints || {}),
    options: event.target.value.split(',').map(value => value.trim()).filter(Boolean),
  }
}

function selectTemplate(event) {
  templateFile.value = event.target.files?.[0] || null
  templateCandidate.value = null
  templateCandidateId.value = ''
  packageResult.value = null
  error.value = ''
}

function selectSpec(event) {
  specFile.value = event.target.files?.[0] || null
  specIncluded.value = Boolean(specFile.value)
  specCandidate.value = null
  specCandidateId.value = ''
  specParams.value = {}
  packageResult.value = null
  error.value = ''
}

function toggleSpec(event) {
  if (!event.target.checked) {
    specFile.value = null
    specCandidate.value = null
    specCandidateId.value = ''
    specParams.value = {}
    if (specFileInput.value) specFileInput.value.value = ''
  }
  packageResult.value = null
  error.value = ''
}

async function parseTemplate() {
  if (!templateFile.value) return
  templateBusy.value = true
  error.value = ''
  try {
    templateCandidate.value = await parseDocumentTemplate(templateFile.value)
    templateAssessmentSnapshot.value = templateAssessmentInput(templateCandidate.value)
    templateCandidateId.value = templateCandidate.value.candidate_id
    upsertCandidate(templateCandidates, templateCandidate.value)
    templateReviewOpen.value = true
  } catch (exception) {
    error.value = exception.message
  } finally {
    templateBusy.value = false
  }
}

async function confirmTemplate() {
  if (!templateCandidate.value) return
  templateBusy.value = true
  error.value = ''
  try {
    if (templateAssessmentStale.value || !templateCandidate.value.admission_assessment?.assessment_hash) {
      templateCandidate.value = await assessDocumentTemplateCandidate(templateCandidate.value)
      templateAssessmentSnapshot.value = templateAssessmentInput(templateCandidate.value)
      upsertCandidate(templateCandidates, templateCandidate.value)
    }
    if (templateCandidate.value.admission_assessment?.blockers?.length) {
      error.value = t('templateAdmission.blockedConfirm')
      return
    }
    templateCandidate.value = await confirmDocumentTemplateCandidate(templateCandidate.value)
    templateAssessmentSnapshot.value = templateAssessmentInput(templateCandidate.value)
    templateCandidateId.value = templateCandidate.value.candidate_id
    upsertCandidate(templateCandidates, templateCandidate.value)
    templateReviewOpen.value = false
  } catch (exception) {
    error.value = exception.message
  } finally {
    templateBusy.value = false
  }
}

async function reassessTemplateCandidate() {
  if (!templateCandidate.value || templateBusy.value) return
  templateBusy.value = true
  error.value = ''
  try {
    templateCandidate.value = await assessDocumentTemplateCandidate(templateCandidate.value)
    templateAssessmentSnapshot.value = templateAssessmentInput(templateCandidate.value)
    upsertCandidate(templateCandidates, templateCandidate.value)
  } catch (exception) {
    error.value = exception.message
  } finally {
    templateBusy.value = false
  }
}

async function parseSpec() {
  if (!specFile.value) return
  specBusy.value = true
  error.value = ''
  try {
    specCandidate.value = await parseDocumentSpec(specFile.value)
    specCandidateId.value = specCandidate.value.candidate_id
    syncSpecParams(specCandidate.value)
    upsertCandidate(specCandidates, specCandidate.value)
    specReviewOpen.value = true
  } catch (exception) {
    error.value = exception.message
  } finally {
    specBusy.value = false
  }
}

async function confirmSpec() {
  if (!specCandidate.value) return
  specBusy.value = true
  error.value = ''
  try {
    const candidate = JSON.parse(JSON.stringify(specCandidate.value))
    candidate.rules = candidate.rules.map(rule => ({
      ...rule,
      params: JSON.parse(specParams.value[rule.id] || '{}'),
    }))
    specCandidate.value = await confirmDocumentSpecCandidate(candidate)
    specCandidateId.value = specCandidate.value.candidate_id
    syncSpecParams(specCandidate.value)
    upsertCandidate(specCandidates, specCandidate.value)
    specReviewOpen.value = false
  } catch (exception) {
    error.value = exception instanceof SyntaxError
      ? t('documentPackagePublisher.invalidRuleParams')
      : exception.message
  } finally {
    specBusy.value = false
  }
}

function restoreTemplate() {
  const candidate = templateCandidates.value.find(item => item.candidate_id === templateCandidateId.value)
  if (!candidate) return
  templateCandidate.value = candidate
  templateAssessmentSnapshot.value = templateAssessmentInput(candidate)
  packageResult.value = null
  error.value = ''
}

function restoreSpec() {
  const candidate = specCandidates.value.find(item => item.candidate_id === specCandidateId.value)
  if (!candidate) return
  specIncluded.value = true
  specCandidate.value = candidate
  syncSpecParams(candidate)
  packageResult.value = null
  error.value = ''
}

async function publish() {
  if (!readyToPublish.value) return
  publishBusy.value = true
  error.value = ''
  packageResult.value = null
  try {
    const payload = {
      template_candidate: templateCandidate.value,
      document_type_id: documentTypeId.value.trim(),
    }
    if (personalWriting.value) payload.usage = 'personal'
    if (specIncluded.value) payload.spec_candidate = specCandidate.value
    packageResult.value = await publishDocumentPackage(payload)
  } catch (exception) {
    error.value = exception.message
  } finally {
    publishBusy.value = false
  }
}

onMounted(loadSavedCandidates)
</script>

<style scoped>
.candidate-summary .review-heading strong { overflow-wrap: anywhere; }
.template-admission { display: grid; gap: 8px; margin: 12px 0; padding: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface-container-low); }
.template-admission.blocked { border-color: var(--error, #b42318); background: var(--error-container, #fff1f0); }
.admission-heading { display: flex; justify-content: space-between; gap: 12px; }
.admission-findings { margin: 0; padding-left: 20px; display: grid; gap: 6px; }
.admission-findings small { display: block; color: var(--on-surface-variant); }
.admission-findings.manual { color: var(--on-surface-variant); }
.admission-limit, .admission-stale { margin: 0; color: var(--on-surface-variant); font-size: 12px; }
.publisher-page { max-width: 1240px; margin: 0 auto; padding: 22px 20px 56px; }
/* This is an in-page heading, not the sticky 60px application header from style.css. */
.publisher-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; min-height: 0; height: auto; position: static; z-index: auto; padding: 10px 0 28px; border-bottom: 1px solid var(--border); background: transparent; box-shadow: none; backdrop-filter: none; }
.back-link { display: inline-flex; align-items: center; gap: 5px; color: var(--primary); font-size: 13px; text-decoration: none; }
.back-link .material-symbols-outlined { font-size: 18px; }
.publisher-header .workspace-kicker { margin-top: 22px; }
.publisher-header h1 { margin: 4px 0 8px; font-size: 25px; line-height: 1.25; }
.publisher-header p:not(.workspace-kicker) { max-width: 720px; margin: 0; color: var(--on-surface-variant); line-height: 1.7; }
.publisher-icon { color: var(--primary); font-size: 48px; }
.publisher-steps { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; padding: 26px 0; }
.step-panel, .publish-panel, .access-state { padding: 20px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.step-heading { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 18px; }
.step-number { display: grid; place-items: center; width: 28px; height: 28px; flex: none; border-radius: 50%; background: var(--primary); color: var(--on-primary, #fff); font-size: 13px; font-weight: 700; }
.step-heading h2, .publish-panel h2 { margin: 3px 0 6px; font-size: 20px; }
.step-heading p:not(.workspace-kicker), .publish-panel p:not(.workspace-kicker) { margin: 0; color: var(--on-surface-variant); font-size: 13px; line-height: 1.55; }
.file-picker { display: grid; gap: 7px; margin-bottom: 12px; color: var(--on-surface); font-size: 13px; font-weight: 600; }
.file-picker input { width: 100%; box-sizing: border-box; padding: 10px; border: 1px dashed var(--border); border-radius: 6px; background: var(--surface-container, var(--surface)); color: var(--on-surface); font: inherit; font-weight: 400; }
.file-picker small { color: var(--on-surface-variant); font-size: 12px; overflow-wrap: anywhere; }
.saved-picker { display: grid; gap: 6px; margin-top: 14px; color: var(--on-surface); font-size: 12px; font-weight: 600; }
.saved-picker select, .review-editor input, .full-editor textarea, .field-editor input, .field-editor select, .rule-editor input, .rule-editor select, .rule-editor textarea { box-sizing: border-box; width: 100%; min-width: 0; padding: 8px 9px; border: 1px solid var(--border); border-radius: 5px; background: var(--surface); color: var(--on-surface); font: inherit; font-size: 12px; }
.saved-picker select:focus, .review-editor input:focus, .full-editor textarea:focus, .field-editor input:focus, .field-editor select:focus, .rule-editor input:focus, .rule-editor select:focus, .rule-editor textarea:focus { outline: 2px solid color-mix(in srgb, var(--primary) 45%, transparent); outline-offset: 1px; }
.candidate-review { display: grid; gap: 13px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); }
.candidate-review-drawer { border-top: 0; margin-top: 0; padding-top: 0; }
.parser-upload { display: grid; gap: 8px; }
.review-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.review-heading strong { min-width: 0; overflow-wrap: anywhere; }
.review-editor { display: grid; grid-template-columns: minmax(0, 2fr) minmax(120px, 1fr); gap: 10px; }
.review-editor-wide { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.review-editor label, .full-editor { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 11px; font-weight: 600; }
.full-editor textarea { resize: vertical; min-height: 54px; }
.review-summary { display: flex; flex-wrap: wrap; gap: 8px 16px; color: var(--on-surface-variant); font-size: 12px; }
.issue-list { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.issue-list li { display: flex; align-items: baseline; gap: 7px; padding: 8px 9px; border-left: 3px solid var(--tertiary, #a56a00); background: color-mix(in srgb, var(--tertiary) 7%, var(--surface)); font-size: 12px; }
.issue-list li.error { border-left-color: var(--error, #b3261e); background: color-mix(in srgb, var(--error) 6%, var(--surface)); }
.issue-list li.info { border-left-color: var(--primary); background: color-mix(in srgb, var(--primary) 6%, var(--surface)); }
.issue-severity { flex: none; font-size: 10px; font-weight: 700; text-transform: uppercase; }
.issue-list small { margin-left: auto; color: var(--on-surface-variant); font-size: 10px; overflow-wrap: anywhere; }
.clean-note { display: flex; align-items: center; gap: 6px; margin: 0; color: var(--success, #2f7d4a); font-size: 12px; }
.clean-note .material-symbols-outlined { font-size: 17px; }
.optional-toggle { margin: 2px 0 8px; font-size: 12px; }
.optional-note { display: flex; align-items: flex-start; gap: 6px; margin: 0 0 12px; color: var(--on-surface-variant); font-size: 12px; line-height: 1.5; }
.optional-note .material-symbols-outlined { flex: none; font-size: 17px; }
.detected-list { display: grid; gap: 6px; }
.detected-list h3 { margin: 0 0 2px; font-size: 13px; }
.detected-row { display: flex; align-items: center; gap: 7px; min-width: 0; padding: 7px 8px; border-bottom: 1px solid var(--border); font-size: 12px; }
.detected-row > span:nth-child(2) { min-width: 0; flex: 1; overflow-wrap: anywhere; }
.detected-row .material-symbols-outlined { color: var(--primary); font-size: 17px; }
.detected-row small { color: var(--on-surface-variant); font-size: 10px; overflow-wrap: anywhere; }
.section-preview-row { display: flex; align-items: flex-start; gap: 14px; min-width: 0; padding: 12px 10px; border-bottom: 1px solid var(--border); }
.section-preview-row textarea { box-sizing: border-box; field-sizing: content; flex: 1; min-width: 0; min-height: 32px; width: 100%; padding: 5px 0; border: 0; border-bottom: 1px solid transparent; border-radius: 0; background: transparent; color: var(--on-surface); font: inherit; font-size: 14px; line-height: 22px; resize: none; overflow: hidden; }
.section-preview-row textarea:focus { outline: none; border-bottom-color: var(--primary); }
.section-preview-row textarea:disabled { opacity: 1; -webkit-text-fill-color: var(--on-surface); }
.section-level { flex-shrink: 0; padding-top: 7px; color: var(--on-surface-variant); font-size: 12px; line-height: 20px; }
.template-fields summary { padding: 10px 0; cursor: pointer; color: var(--on-surface-variant); font-size: 13px; }
.inline-editor { display: flex; align-items: center; flex: 1; min-width: 0; gap: 7px; }
.inline-editor input { flex: 1; min-width: 0; border: 0; border-bottom: 1px solid var(--border); border-radius: 0; background: transparent; color: var(--on-surface); font: inherit; padding: 4px 2px; }
.inline-editor input:focus { outline: none; border-bottom-color: var(--primary); }
.check-editor { display: inline-flex; align-items: center; gap: 5px; flex: none; color: var(--on-surface-variant); font-size: 10px; font-weight: 600; white-space: nowrap; }
.check-editor input { width: 14px; height: 14px; margin: 0; accent-color: var(--primary); }
.field-editor { display: grid; grid-template-columns: minmax(80px, .85fr) minmax(100px, 1.4fr) minmax(80px, .8fr) auto; align-items: center; gap: 7px; padding: 7px 0; border-bottom: 1px solid var(--border); }
.field-editor input[readonly], .rule-editor input[readonly] { color: var(--on-surface-variant); background: var(--surface-container, var(--surface)); }
.anchor-row { align-items: flex-start; }
.anchor-row > span:nth-child(2) { flex: none; }
.anchor-row small { margin-left: auto; text-align: right; }
.table-row { align-items: flex-start; flex-wrap: wrap; }
.table-row > span:nth-child(2) { flex: none; }
.table-row small { flex: 1 1 180px; }
.rule-editor { display: grid; gap: 7px; padding: 9px 0; border-bottom: 1px solid var(--border); }
.rule-editor-grid { display: grid; grid-template-columns: minmax(70px, .9fr) minmax(70px, .7fr) minmax(90px, 1fr) minmax(110px, 1.2fr) minmax(80px, .7fr) minmax(70px, .7fr); gap: 7px; }
.rule-editor textarea { resize: vertical; min-height: 44px; }
.rule-editor textarea:last-child { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }
.rule-row { align-items: flex-start; }
.rule-kind { flex: none; padding: 2px 5px; border-radius: 4px; background: color-mix(in srgb, var(--primary) 12%, var(--surface)); color: var(--primary); font-size: 10px; }
.rule-kind.manual { background: color-mix(in srgb, var(--tertiary) 13%, var(--surface)); color: var(--tertiary); }
.publish-panel { margin-bottom: 24px; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.panel-icon { color: var(--primary); font-size: 30px; }
.publish-form { display: grid; grid-template-columns: minmax(200px, 300px) 1fr auto; align-items: end; gap: 18px; }
.publish-form label { display: grid; gap: 6px; color: var(--on-surface); font-size: 13px; font-weight: 600; }
.publish-form input { width: 100%; box-sizing: border-box; padding: 10px 11px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--on-surface); font: inherit; font-weight: 400; }
.readiness { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; color: var(--on-surface-variant); font-size: 12px; }
.readiness span { display: inline-flex; align-items: center; gap: 5px; }
.readiness span.ready { color: var(--success, #2f7d4a); }
.readiness .material-symbols-outlined { font-size: 17px; }
.success-panel { display: flex; align-items: center; gap: 12px; margin-top: 20px; padding: 14px; border: 1px solid color-mix(in srgb, var(--success, #2f7d4a) 40%, var(--border)); background: color-mix(in srgb, var(--success, #2f7d4a) 7%, var(--surface)); }
.success-panel > .material-symbols-outlined { color: var(--success, #2f7d4a); font-size: 28px; }
.success-panel div { min-width: 0; flex: 1; }
.success-panel strong { display: block; }
.success-panel p { margin: 3px 0; font-size: 12px; }
.success-panel small { color: var(--on-surface-variant); overflow-wrap: anywhere; }
.access-state { display: grid; justify-items: center; gap: 8px; margin-top: 28px; text-align: center; }
.access-state > .material-symbols-outlined { color: var(--on-surface-variant); font-size: 36px; }
.access-state h2 { margin: 0; font-size: 20px; }
.access-state p { margin: 0 0 8px; color: var(--on-surface-variant); }
@media (min-width: 769px) {
  .publisher-page .publisher-header { padding: 0 0 12px; border-bottom: 0; }
  .publisher-page .publisher-header .workspace-kicker { margin-top: 6px; }
  .publisher-deck .step-panel { padding: 14px; }
  .publisher-deck .step-heading { margin-bottom: 10px; }
  .publisher-deck .parser-upload { grid-template-columns: minmax(0, 1fr) auto; align-items: center; }
  .publisher-deck .file-picker { gap: 4px; margin: 0; }
  .publisher-deck .file-picker input { padding: 6px; }
  .publisher-deck .saved-picker { margin-top: 10px; gap: 4px; }
  .publisher-deck .candidate-summary { grid-template-columns: minmax(0, 1fr) auto; margin-top: 10px; padding-top: 10px; gap: 6px 12px; }
  .publisher-deck .candidate-summary > :is(.review-heading, .review-summary) { grid-column: 1; }
  .publisher-deck .candidate-summary > button { grid-column: 2; grid-row: 1 / span 2; align-self: center; }
}
@media (max-width: 800px) { .publisher-page { padding: 14px 14px 42px; } .publisher-header { padding-top: 6px; } .publisher-icon { display: none; } .publisher-steps { grid-template-columns: 1fr; } .publish-form { grid-template-columns: 1fr; align-items: stretch; } .publish-form button { width: 100%; } .success-panel { align-items: flex-start; flex-wrap: wrap; } .success-panel a { width: 100%; text-align: center; } .review-editor, .review-editor-wide { grid-template-columns: 1fr; } .field-editor { grid-template-columns: 1fr 1fr; } .field-editor input[readonly] { grid-column: 1 / -1; } .field-editor .check-editor { justify-self: start; } .rule-editor-grid { grid-template-columns: 1fr 1fr; } .rule-editor-grid input:first-child { grid-column: 1 / -1; } .anchor-row { flex-wrap: wrap; } .anchor-row small { width: 100%; margin-left: 24px; text-align: left; } }
@media (max-width: 480px) { .section-preview-row { gap: 8px; } .section-level { font-size: 11px; } }
</style>
