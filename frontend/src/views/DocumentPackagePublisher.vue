<template>
  <div class="publisher-page">
    <header class="publisher-header">
      <div>
        <RouterLink to="/document-projects" class="back-link">
          <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>
          {{ t('documentPackagePublisher.back') }}
        </RouterLink>
        <p class="workspace-kicker">{{ t('documentPackagePublisher.kicker') }}</p>
        <h1>{{ t('documentPackagePublisher.title') }}</h1>
        <p>{{ t('documentPackagePublisher.description') }}</p>
      </div>
      <span class="material-symbols-outlined publisher-icon" aria-hidden="true">publish</span>
    </header>

    <section v-if="!canManage" class="access-state" role="alert">
      <span class="material-symbols-outlined" aria-hidden="true">lock</span>
      <h2>{{ t('documentPackagePublisher.accessTitle') }}</h2>
      <p>{{ t('documentPackagePublisher.accessDetail') }}</p>
      <RouterLink to="/document-projects" class="small primary">{{ t('documentPackagePublisher.backToDocuments') }}</RouterLink>
    </section>

    <template v-else>
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

      <section class="publisher-steps" aria-label="Document package publishing steps">
        <article class="step-panel">
          <div class="step-heading">
            <span class="step-number">1</span>
            <div>
              <p class="workspace-kicker">{{ t('documentPackagePublisher.templateEyebrow') }}</p>
              <h2>{{ t('documentPackagePublisher.templateTitle') }}</h2>
              <p>{{ t('documentPackagePublisher.templateDetail') }}</p>
            </div>
          </div>

          <label class="file-picker">
            <span>{{ t('documentPackagePublisher.templateFile') }}</span>
            <input data-test="template-file" type="file" accept=".docx" :disabled="templateBusy || publishBusy" @change="selectTemplate" />
            <small>{{ templateFile?.name || t('documentPackagePublisher.noFile') }}</small>
          </label>

          <button data-test="template-parse" class="small primary" type="button" :disabled="!templateFile || templateBusy || publishBusy" @click="parseTemplate">
            <span class="material-symbols-outlined" aria-hidden="true">document_scanner</span>
            {{ templateBusy ? t('documentPackagePublisher.parsing') : t('documentPackagePublisher.parse') }}
          </button>

          <div v-if="templateCandidate" class="candidate-review">
            <div class="review-heading">
              <strong>{{ templateCandidate.name }}</strong>
              <span class="status-pill" :class="templateCandidate.status">{{ statusLabel(templateCandidate.status) }}</span>
            </div>
            <div class="review-summary">
              <span>{{ t('documentPackagePublisher.sections') }}: {{ templateSections.length }}</span>
              <span>{{ t('documentPackagePublisher.fields') }}: {{ templateCandidate.definition.fields.length }}</span>
              <span>{{ t('documentPackagePublisher.styles') }}: {{ Object.keys(templateCandidate.definition.styles || {}).length }}</span>
            </div>
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
              <div v-for="section in templateSections" :key="section.key" class="detected-row" :style="{ paddingLeft: `${10 + section.depth * 16}px` }">
                <span class="material-symbols-outlined" aria-hidden="true">account_tree</span>
                <span>{{ section.title || section.key }}</span>
                <small>{{ section.key }}</small>
              </div>
            </div>
            <button data-test="template-confirm" class="small" type="button" :disabled="templateBusy || templateCandidate.status === 'confirmed'" @click="confirmTemplate">
              <span class="material-symbols-outlined" aria-hidden="true">verified</span>
              {{ templateCandidate.status === 'confirmed' ? t('documentPackagePublisher.confirmed') : t('documentPackagePublisher.confirmTemplate') }}
            </button>
          </div>
        </article>

        <article class="step-panel">
          <div class="step-heading">
            <span class="step-number">2</span>
            <div>
              <p class="workspace-kicker">{{ t('documentPackagePublisher.specEyebrow') }}</p>
              <h2>{{ t('documentPackagePublisher.specTitle') }}</h2>
              <p>{{ t('documentPackagePublisher.specDetail') }}</p>
            </div>
          </div>

          <label class="file-picker">
            <span>{{ t('documentPackagePublisher.specFile') }}</span>
            <input data-test="spec-file" type="file" accept=".docx,.pdf,.md,.markdown,.txt" :disabled="specBusy || publishBusy" @change="selectSpec" />
            <small>{{ specFile?.name || t('documentPackagePublisher.noFile') }}</small>
          </label>

          <button data-test="spec-parse" class="small primary" type="button" :disabled="!specFile || specBusy || publishBusy" @click="parseSpec">
            <span class="material-symbols-outlined" aria-hidden="true">rule_folder</span>
            {{ specBusy ? t('documentPackagePublisher.parsing') : t('documentPackagePublisher.parse') }}
          </button>

          <div v-if="specCandidate" class="candidate-review">
            <div class="review-heading">
              <strong>{{ specCandidate.name }}</strong>
              <span class="status-pill" :class="specCandidate.status">{{ statusLabel(specCandidate.status) }}</span>
            </div>
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
              <h3>{{ t('documentPackagePublisher.detectedRules') }}</h3>
              <div v-for="rule in specCandidate.rules" :key="rule.id" class="detected-row rule-row">
                <span class="rule-kind" :class="rule.kind">{{ rule.kind }}</span>
                <span>{{ rule.description || rule.id }}</span>
                <small>{{ rule.checker || t('documentPackagePublisher.manual') }}</small>
              </div>
            </div>
            <button data-test="spec-confirm" class="small" type="button" :disabled="specBusy || specCandidate.status === 'confirmed'" @click="confirmSpec">
              <span class="material-symbols-outlined" aria-hidden="true">verified</span>
              {{ specCandidate.status === 'confirmed' ? t('documentPackagePublisher.confirmed') : t('documentPackagePublisher.confirmSpec') }}
            </button>
          </div>
        </article>
      </section>

      <section class="publish-panel">
        <div class="section-heading">
          <div>
            <p class="workspace-kicker">{{ t('documentPackagePublisher.publishEyebrow') }}</p>
            <h2>{{ t('documentPackagePublisher.publishTitle') }}</h2>
            <p>{{ t('documentPackagePublisher.publishDetail') }}</p>
          </div>
          <span class="material-symbols-outlined panel-icon" aria-hidden="true">inventory_2</span>
        </div>

        <div class="publish-form">
          <label>{{ t('documentPackagePublisher.documentType') }}
            <input v-model="documentTypeId" maxlength="80" :placeholder="t('documentPackagePublisher.documentTypePlaceholder')" />
          </label>
          <div class="readiness" role="status" aria-live="polite">
            <span :class="{ ready: templateReady }"><span class="material-symbols-outlined" aria-hidden="true">{{ templateReady ? 'check_circle' : 'radio_button_unchecked' }}</span>{{ t('documentPackagePublisher.templateStatus') }}</span>
            <span :class="{ ready: specReady }"><span class="material-symbols-outlined" aria-hidden="true">{{ specReady ? 'check_circle' : 'radio_button_unchecked' }}</span>{{ t('documentPackagePublisher.specStatus') }}</span>
          </div>
          <button data-test="publish" class="primary" type="button" :disabled="!readyToPublish || publishBusy" @click="publish">
            <span class="material-symbols-outlined" aria-hidden="true">publish</span>
            {{ publishBusy ? t('documentPackagePublisher.publishing') : t('documentPackagePublisher.publish') }}
          </button>
        </div>

        <div v-if="packageResult" class="success-panel" role="status">
          <span class="material-symbols-outlined" aria-hidden="true">task_alt</span>
          <div>
            <strong>{{ t('documentPackagePublisher.published') }}</strong>
            <p>{{ packageResult.template.name }} · {{ packageResult.spec_set.name }}</p>
            <small>{{ packageResult.package.id }}</small>
          </div>
          <RouterLink to="/document-projects" class="small primary">{{ t('documentPackagePublisher.openDocuments') }}</RouterLink>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from '../i18n'
import { auth } from '../auth'
import {
  confirmDocumentSpecCandidate,
  confirmDocumentTemplateCandidate,
  parseDocumentSpec,
  parseDocumentTemplate,
  publishDocumentPackage,
} from '../api.js'

const { t } = useI18n()
const canManage = computed(() => auth.canManage())
const templateFile = ref(null)
const specFile = ref(null)
const templateCandidate = ref(null)
const specCandidate = ref(null)
const documentTypeId = ref('generic')
const templateBusy = ref(false)
const specBusy = ref(false)
const publishBusy = ref(false)
const packageResult = ref(null)
const error = ref('')

const templateReady = computed(() => templateCandidate.value?.status === 'confirmed')
const specReady = computed(() => specCandidate.value?.status === 'confirmed')
const readyToPublish = computed(() => canManage.value && templateReady.value && specReady.value && documentTypeId.value.trim())

const templateSections = computed(() => {
  const flattened = []
  function walk(nodes, depth = 0) {
    for (const section of nodes || []) {
      flattened.push({ ...section, depth })
      walk(section.children, depth + 1)
    }
  }
  walk(templateCandidate.value?.definition?.sections)
  return flattened
})

function statusLabel(status) {
  if (status === 'confirmed') return t('documentPackagePublisher.confirmed')
  return t('documentPackagePublisher.needsConfirmation')
}

function selectTemplate(event) {
  templateFile.value = event.target.files?.[0] || null
  templateCandidate.value = null
  packageResult.value = null
  error.value = ''
}

function selectSpec(event) {
  specFile.value = event.target.files?.[0] || null
  specCandidate.value = null
  packageResult.value = null
  error.value = ''
}

async function parseTemplate() {
  if (!templateFile.value) return
  templateBusy.value = true
  error.value = ''
  try {
    templateCandidate.value = await parseDocumentTemplate(templateFile.value)
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
    templateCandidate.value = await confirmDocumentTemplateCandidate(templateCandidate.value)
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
    specCandidate.value = await confirmDocumentSpecCandidate(specCandidate.value)
  } catch (exception) {
    error.value = exception.message
  } finally {
    specBusy.value = false
  }
}

async function publish() {
  if (!readyToPublish.value) return
  publishBusy.value = true
  error.value = ''
  packageResult.value = null
  try {
    packageResult.value = await publishDocumentPackage({
      template_candidate: templateCandidate.value,
      spec_candidate: specCandidate.value,
      document_type_id: documentTypeId.value.trim(),
    })
  } catch (exception) {
    error.value = exception.message
  } finally {
    publishBusy.value = false
  }
}
</script>

<style scoped>
.publisher-page { max-width: 1240px; margin: 0 auto; padding: 22px 20px 56px; }
.publisher-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding: 10px 0 28px; border-bottom: 1px solid var(--border); }
.back-link { display: inline-flex; align-items: center; gap: 5px; color: var(--primary); font-size: 13px; text-decoration: none; }
.back-link .material-symbols-outlined { font-size: 18px; }
.publisher-header .workspace-kicker { margin-top: 22px; }
.publisher-header h1 { margin: 4px 0 8px; font-size: 32px; }
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
.candidate-review { display: grid; gap: 13px; margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--border); }
.review-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.review-heading strong { min-width: 0; overflow-wrap: anywhere; }
.review-summary { display: flex; flex-wrap: wrap; gap: 8px 16px; color: var(--on-surface-variant); font-size: 12px; }
.issue-list { display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }
.issue-list li { display: flex; align-items: baseline; gap: 7px; padding: 8px 9px; border-left: 3px solid var(--tertiary, #a56a00); background: color-mix(in srgb, var(--tertiary) 7%, var(--surface)); font-size: 12px; }
.issue-list li.error { border-left-color: var(--error, #b3261e); background: color-mix(in srgb, var(--error) 6%, var(--surface)); }
.issue-list li.info { border-left-color: var(--primary); background: color-mix(in srgb, var(--primary) 6%, var(--surface)); }
.issue-severity { flex: none; font-size: 10px; font-weight: 700; text-transform: uppercase; }
.issue-list small { margin-left: auto; color: var(--on-surface-variant); font-size: 10px; overflow-wrap: anywhere; }
.clean-note { display: flex; align-items: center; gap: 6px; margin: 0; color: var(--success, #2f7d4a); font-size: 12px; }
.clean-note .material-symbols-outlined { font-size: 17px; }
.detected-list { display: grid; gap: 6px; }
.detected-list h3 { margin: 0 0 2px; font-size: 13px; }
.detected-row { display: flex; align-items: center; gap: 7px; min-width: 0; padding: 7px 8px; border-bottom: 1px solid var(--border); font-size: 12px; }
.detected-row > span:nth-child(2) { min-width: 0; flex: 1; overflow-wrap: anywhere; }
.detected-row .material-symbols-outlined { color: var(--primary); font-size: 17px; }
.detected-row small { color: var(--on-surface-variant); font-size: 10px; overflow-wrap: anywhere; }
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
@media (max-width: 800px) { .publisher-page { padding: 14px 14px 42px; } .publisher-header { padding-top: 6px; } .publisher-icon { display: none; } .publisher-steps { grid-template-columns: 1fr; } .publish-form { grid-template-columns: 1fr; align-items: stretch; } .publish-form button { width: 100%; } .success-panel { align-items: flex-start; flex-wrap: wrap; } .success-panel a { width: 100%; text-align: center; } }
</style>
