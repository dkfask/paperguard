<template>
  <div class="workspace-page operations-page">
    <section class="workspace-hero">
      <div>
        <p class="workspace-kicker">{{ t('operations.runtimeKicker') }}</p>
        <h1>{{ t('operations.title') }}</h1>
        <p>{{ t('operations.description') }}</p>
      </div>
      <div class="workspace-identity">
        <span class="material-symbols-outlined" aria-hidden="true">monitor_heart</span>
        <div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ t('role.admin') }}</span></div>
      </div>
    </section>

    <InlineNotice v-if="error && !retentionDrawerOpen" :message="error" />
    <AsyncState v-if="loading" state="loading" :title="t('operations.loading')" :detail="t('operations.loadingDetail')" />
    <AsyncState v-else-if="loadError" state="error" :title="t('operations.loadingFailed')" :detail="loadError" :action-label="t('common.retry')" @action="load" />
    <template v-else>
      <section class="workspace-stats" :aria-label="t('operations.summary')">
        <div><span>{{ t('operations.webService') }}</span><strong>{{ statusText(live) }}</strong><small>{{ t('operations.processProbe') }}</small></div>
        <div><span>{{ t('operations.businessStorage') }}</span><strong>{{ statusText(ready) }}</strong><small>{{ t('operations.databaseProbe') }}</small></div>
        <div><span>{{ t('operations.backgroundJobs') }}</span><strong>{{ jobs.length }}</strong><small>{{ t('operations.visibleJobs') }}</small></div>
        <div><span>{{ t('operations.policyStatus') }}</span><strong>{{ retentionError ? t('operations.unavailable') : !retentionLoaded ? t('common.loading') : retention.configured ? t('operations.configured') : t('operations.notConfigured') }}</strong><small>{{ t('operations.retentionPolicy') }}</small></div>
      </section>

      <div class="workspace-layout">
        <ScreenDeck class="workspace-main">
          <section class="panel">
            <div class="panel-heading"><div><p class="workspace-kicker">{{ t('operations.probesKicker') }}</p><h2>{{ t('operations.probes') }}</h2><p>{{ t('operations.probesDetail') }}</p></div><div class="operations-heading-actions"><button class="small" type="button" :title="retentionError ? retentionError.message : t('operations.backupTitle')" :disabled="backingUp || !retentionLoaded" @click="backup"><span class="material-symbols-outlined" aria-hidden="true">download</span>{{ backingUp ? t('operations.generating') : t('operations.generateBackup') }}</button><button class="icon-button" type="button" :title="t('operations.refresh')" :aria-label="t('operations.refresh')" :disabled="refreshing" @click="load"><span class="material-symbols-outlined" aria-hidden="true">refresh</span></button></div></div>
            <div class="operations-status-list">
              <div class="operations-status-row"><span class="status-dot" :class="live ? 'ok' : 'error'"></span><div><strong>Web API</strong><span>/api/health/live · {{ statusText(live) }}</span></div></div>
              <div class="operations-status-row"><span class="status-dot" :class="ready ? 'ok' : 'error'"></span><div><strong>{{ t('operations.businessStorage') }}</strong><span>/api/health/ready · {{ statusText(ready) }}</span></div></div>
              <div class="operations-status-row"><span class="status-dot" :class="jobs.some((job) => job.state === 'failed') ? 'error' : 'ok'"></span><div><strong>{{ t('operations.jobQueue') }}</strong><span>{{ jobs.some((job) => job.state === 'failed') ? t('operations.failedJobs') : t('operations.noDeadJobs') }}</span></div></div>
            </div>
          </section>

          <section class="panel">
            <div class="panel-heading"><div><p class="workspace-kicker">{{ t('operations.jobsKicker') }}</p><h2>{{ t('operations.backgroundJobs') }}</h2><p>{{ t('operations.jobsDetail') }}</p></div><RouterLink class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'archives' } }">{{ t('operations.openArchive') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div>
            <div v-if="jobs.length" class="operations-job-list">
              <ScreenCollection :items="jobs" :row-height="64" v-slot="{ items: visibleItems }"><div v-for="job in visibleItems" :key="job.id" class="operations-job-row"><div><strong>{{ job.job_type }}</strong><span>{{ job.resource_type }} · {{ job.resource_id }} · {{ t('operations.attempts') }} {{ job.attempt_count }}/{{ job.max_attempts }}</span><small v-if="job.last_error" class="operations-error">{{ job.last_error }}</small></div><div><StatusBadge :status="job.state" :label="jobLabel(job.state)" /><RouterLink v-if="job.state === 'failed'" class="icon-button" :to="{ path: '/workflow', query: { panel: 'archives' } }" :aria-label="t('operations.handleFailed')" :title="t('operations.handleFailed')"><span class="material-symbols-outlined" aria-hidden="true">build</span></RouterLink></div></div></ScreenCollection>
            </div>
            <AsyncState v-else state="empty" :title="t('operations.noJobs')" :detail="t('operations.noJobsDetail')" />
          </section>
        </ScreenDeck>

        <ScreenDeck class="workspace-side">
          <section class="panel operations-retention-panel">
            <div class="panel-heading"><div><p class="workspace-kicker">{{ t('operations.retentionKicker') }}</p><h2>{{ t('operations.retentionTitle') }}</h2><p>{{ t('operations.retentionDetail') }}</p></div></div>
            <AsyncState v-if="retentionError" :state="retentionError.denied ? 'denied' : 'error'" :title="t(retentionError.denied ? 'operations.restricted' : 'operations.loadingFailed')" :detail="retentionError.message" :action-label="t('common.retry')" @action="load" />
            <div v-else-if="retentionLoaded" class="operations-policy-summary">
              <InlineNotice v-if="saveNotice && !retentionDrawerOpen" :message="saveNotice" type="success" />
              <p class="operations-policy-status">{{ t(retention.configured ? 'operations.configured' : 'operations.notConfigured') }}</p>
              <p v-if="retention.policy" class="operations-policy-timezone">{{ t('operations.timezone') }} · {{ retention.policy.timezone }}</p>
              <button class="small primary" type="button" @click="retentionDrawerOpen = true"><span class="material-symbols-outlined" aria-hidden="true">tune</span>{{ t('operations.configurePolicy') }}</button>
            </div>
            <AsyncState v-else state="loading" :title="t('operations.loading')" />
          </section>
          <section class="panel workspace-shortcuts"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.governanceEntries') }}</p><h3>{{ t('operations.related') }}</h3></div></div><RouterLink to="/organizations"><span class="material-symbols-outlined" aria-hidden="true">groups</span>{{ t('templates.organizations') }}</RouterLink><RouterLink to="/audit"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('route.audit') }}</RouterLink></section>
        </ScreenDeck>
      </div>
    </template>
    <BaseModal :open="retentionDrawerOpen" presentation="drawer" @close="retentionDrawerOpen = false" :title="t('operations.retentionTitle')">
      <InlineNotice v-if="error" :message="error" />
      <form class="operations-retention-form" @submit.prevent="saveRetention">
        <label v-for="field in retentionFields" :key="field.key">{{ t(field.labelKey) }}<input v-model.number="retentionForm[field.key]" type="number" min="1" max="36500" required /></label>
        <label class="operations-timezone">{{ t('operations.timezone') }}<input v-model="retentionForm.timezone" maxlength="80" required /></label>
        <p v-if="saveNotice" class="ok-banner" role="status">{{ saveNotice }}</p>
        <button class="small primary" type="submit" :disabled="saving || !retentionLoaded"><span class="material-symbols-outlined" aria-hidden="true">save</span>{{ saving ? t('common.saving') : t('operations.savePolicy') }}</button>
      </form>
    </BaseModal>
  </div>
</template>

<script setup>
import ScreenCollection from '../components/ScreenCollection.vue'
import ScreenDeck from '../components/ScreenDeck.vue'
import { onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { auth } from '../auth'
import { downloadOperationsBackup, fetchHealth, fetchRetentionPolicy, fetchWorkflowJobs, updateRetentionPolicy } from '../api'
import AsyncState from '../components/AsyncState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import BaseModal from '../components/base/BaseModal.vue'
import InlineNotice from '../components/InlineNotice.vue'
import { useI18n } from '../i18n'

const { t } = useI18n()

const loading = ref(true)
const refreshing = ref(false)
const saving = ref(false)
const backingUp = ref(false)
const error = ref('')
const loadError = ref('')
const saveNotice = ref('')
const live = ref(null)
const ready = ref(null)
const jobs = ref([])
const retention = ref({ configured: false, policy: null })
const retentionLoaded = ref(false)
const retentionDrawerOpen = ref(false)
const retentionError = ref(null)
const retentionForm = reactive({ draft_days: 30, attachment_days: 365, submission_days: 730, archive_days: 3650, audit_days: 3650, local_cache_days: 7, timezone: 'Asia/Shanghai' })
const retentionFields = [
  { key: 'draft_days', labelKey: 'operations.draftDays' }, { key: 'attachment_days', labelKey: 'operations.attachmentDays' },
  { key: 'submission_days', labelKey: 'operations.submissionDays' }, { key: 'archive_days', labelKey: 'operations.archiveDays' },
  { key: 'audit_days', labelKey: 'operations.auditDays' }, { key: 'local_cache_days', labelKey: 'operations.localCacheDays' },
]

async function load() {
  if (loadError.value) loading.value = true
  else if (!loading.value) refreshing.value = true
  error.value = ''
  loadError.value = ''
  saveNotice.value = ''
  retentionLoaded.value = false
  retentionError.value = null
  try {
    const [liveResult, readyResult, jobResult, retentionResult] = await Promise.allSettled([
      fetchHealth('live'), fetchHealth('ready'), fetchWorkflowJobs(), fetchRetentionPolicy(),
    ])
    const failedProbe = [liveResult, readyResult, jobResult].find(result => result.status === 'rejected')
    if (failedProbe) throw failedProbe.reason
    live.value = liveResult.value; ready.value = readyResult.value; jobs.value = jobResult.value
    if (retentionResult.status === 'fulfilled') {
      retention.value = retentionResult.value
      retentionLoaded.value = true
      if (retentionResult.value.policy) Object.assign(retentionForm, retentionResult.value.policy)
    } else {
      retentionError.value = { denied: retentionResult.reason.status === 403, message: retentionResult.reason.message }
    }
  } catch (exception) { loadError.value = exception.message } finally { loading.value = false; refreshing.value = false }
}

async function saveRetention() {
  if (!retentionLoaded.value || saving.value) return
  saving.value = true; saveNotice.value = ''; error.value = ''
  try { const result = await updateRetentionPolicy({ ...retentionForm }); retention.value = result; saveNotice.value = t('operations.saved') } catch (exception) { error.value = exception.message } finally { saving.value = false }
}

async function backup() {
  if (!retentionLoaded.value || backingUp.value) return
  backingUp.value = true; saveNotice.value = ''; error.value = ''
  try { await downloadOperationsBackup(); saveNotice.value = t('operations.backupStarted') } catch (exception) { error.value = exception.message } finally { backingUp.value = false }
}

function statusText(value) { return value?.status === 'ok' ? t('operations.normal') : t('operations.abnormal') }
function jobLabel(state) { return { queued: t('archive.queued'), running: t('operations.running'), succeeded: t('archive.succeeded'), failed: t('operations.failed') }[state] || state }

onMounted(load)
</script>

<style scoped>
.operations-status-list, .operations-job-list { border-top: 1px solid var(--border); margin-top: 14px; }
.operations-heading-actions { display: flex; align-items: center; gap: 8px; }
.operations-status-row, .operations-job-row { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--border); }
.operations-status-row > div, .operations-job-row > div:first-child { min-width: 0; flex: 1; }
.operations-status-row strong, .operations-status-row span, .operations-job-row strong, .operations-job-row span, .operations-job-row small { display: block; }
.operations-status-row span, .operations-job-row span { color: var(--on-surface-variant); font-size: 12px; margin-top: 3px; overflow-wrap: anywhere; }
.operations-job-row > div:last-child { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
.operations-job-row .operations-error { color: var(--error); }
.operations-policy-summary { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; min-height: 0; }
.operations-policy-status { margin: 0; color: var(--on-surface-variant); font-size: 12px; }
.operations-policy-timezone { margin: 0; color: var(--on-surface-variant); font-size: 12px; overflow-wrap: anywhere; }
.operations-retention-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-top: 4px; }
.operations-retention-form > :is(.operations-timezone, .ok-banner, button) { grid-column: 1 / -1; }
.operations-retention-form label { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 12px; }
.operations-retention-form input { width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); color: var(--on-surface); font: inherit; }
.operations-retention-form .ok-banner { margin: 0; padding: 8px 10px; font-size: 12px; }
.status-dot { width: 10px; height: 10px; border-radius: 50%; flex: 0 0 auto; background: var(--outline); }
.status-dot.ok { background: var(--success, #14866d); }.status-dot.error { background: var(--error); }
@media (min-width: 769px) {
  .operations-page .panel { padding: 12px 14px; }
  .operations-page .panel-heading { gap: 10px; padding-bottom: 10px; }
  .operations-page .panel-heading p { font-size: 12px; line-height: 1.4; margin-top: 4px; }
  .operations-page .panel-heading .workspace-kicker { font-size: 10px; margin: 0 0 4px; }
  .operations-status-list, .operations-job-list { margin-top: 6px; }
  .operations-status-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
  .operations-status-row { align-items: flex-start; gap: 7px; padding: 8px 0; border-bottom: 0; }
  .operations-status-row .status-dot { margin-top: 4px; }
  .operations-status-row strong { font-size: 13px; line-height: 1.4; }
  .operations-status-row span { font-size: 11px; line-height: 1.4; }
  .operations-page :deep(.async-state) { flex: 1; min-height: 0; padding: 10px; display: flex; flex-direction: column; justify-content: center; align-items: center; }
  .operations-page :deep(.async-state-icon) { font-size: 24px; }
  .operations-page :deep(.async-state h3) { margin-top: 0; font-size: 14px; }
  .operations-page :deep(.async-state p) { line-height: 1.4; }
}
@media (max-width: 560px) { .operations-job-row { align-items: flex-start; flex-wrap: wrap; }.operations-job-row > div:last-child { width: 100%; justify-content: space-between; }.operations-retention-form { grid-template-columns: repeat(2, minmax(0, 1fr)); }.operations-retention-form label:last-of-type, .operations-retention-form .ok-banner, .operations-retention-form button { grid-column: 1 / -1; } }
</style>
