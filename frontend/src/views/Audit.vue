<template>
  <div class="workspace-page audit-page">
    <section class="workspace-hero">
      <div>
        <p class="workspace-kicker">AUDIT CENTER</p>
        <h1>{{ t('audit.title') }}</h1>
        <p>{{ t('audit.description') }}</p>
      </div>
      <div class="workspace-identity">
        <span class="material-symbols-outlined" aria-hidden="true">fact_check</span>
        <div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ auth.user?.role === 'admin' ? t('role.admin') : t('role.college_admin') }}</span></div>
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

    <section class="panel audit-filter-panel">
      <div class="panel-heading">
        <div><p class="workspace-kicker">{{ t('audit.filters') }}</p><h2>{{ t('audit.filterTitle') }}</h2><p>{{ t('audit.filterDescription') }}</p></div>
        <button class="icon-button" type="button" :title="t('audit.refresh')" :aria-label="t('audit.refresh')" :disabled="loading" @click="load">
          <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
        </button>
      </div>
      <form class="audit-filters" @submit.prevent="load">
        <label>{{ t('audit.resourceType') }}<select v-model="filters.resource_type"><option value="">{{ t('common.all') }}</option><option value="cohort">{{ t('audit.cohort') }}</option><option value="stage">{{ t('audit.stage') }}</option><option value="participant">{{ t('audit.participant') }}</option><option value="submission">{{ t('audit.submission') }}</option><option value="process_record">{{ t('audit.processRecord') }}</option><option value="archive">{{ t('audit.archive') }}</option><option value="project">{{ t('audit.project') }}</option><option value="policy">{{ t('audit.policy') }}</option></select></label>
        <label>{{ t('audit.action') }}<input v-model="filters.action" maxlength="120" placeholder="e.g. submission.approved" /></label>
        <label>{{ t('audit.actor') }}<input v-model="filters.actor_id" maxlength="120" :placeholder="t('audit.optional')" /></label>
        <label>{{ t('audit.organization') }}<input v-model="filters.organization_id" maxlength="120" :placeholder="t('audit.optional')" /></label>
        <div class="audit-filter-actions"><button class="small primary" type="submit" :disabled="loading"><span class="material-symbols-outlined" aria-hidden="true">search</span>{{ t('common.search') }}</button><button class="small" type="button" :disabled="loading" @click="resetFilters">{{ t('common.reset') }}</button></div>
      </form>
    </section>

    <section class="panel">
      <div class="panel-heading"><div><p class="workspace-kicker">EVIDENCE LEDGER</p><h2>{{ t('audit.records') }}</h2></div><div class="audit-heading-actions"><span class="audit-count">{{ t('audit.count', { count: rows.length }) }}</span><button class="small" type="button" :title="t('audit.exportCurrent')" :aria-label="t('audit.exportCurrent')" :disabled="exporting || loading" @click="exportRows"><span class="material-symbols-outlined" aria-hidden="true">download</span>{{ exporting ? t('audit.exporting') : t('audit.exportCsv') }}</button></div></div>
      <AsyncState v-if="loading" state="loading" :title="t('audit.loading')" :detail="t('audit.loadingDetail')" />
      <AsyncState v-else-if="!rows.length" state="empty" :title="t('audit.empty')" :detail="t('audit.emptyDetail')" />
      <DataTable v-else :columns="columns" :rows="rows">
        <template #cell-source="{ value }"><span class="audit-source">{{ value === 'policy' ? t('audit.policy') : t('audit.workflow') }}</span></template>
        <template #cell-action="{ value }"><code>{{ value }}</code></template>
        <template #cell-resource_id="{ row }"><span class="audit-resource"><strong>{{ row.resource_type }}</strong><code>{{ row.resource_id }}</code></span></template>
        <template #cell-result="{ value }"><StatusBadge :status="value" :label="value === 'success' ? t('audit.success') : value" /></template>
        <template #cell-reason="{ value }"><span class="audit-reason">{{ value || '-' }}</span></template>
        <template #cell-created_at="{ value }"><time :datetime="value">{{ formatDate(value) }}</time></template>
      </DataTable>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { auth } from '../auth'
import { downloadAuditEvents, fetchAuditEvents } from '../api'
import AsyncState from '../components/AsyncState.vue'
import DataTable from '../components/DataTable.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useI18n } from '../i18n'

const { t, formatDate: formatLocaleDate } = useI18n()

const loading = ref(true)
const exporting = ref(false)
const error = ref('')
const rows = ref([])
const filters = reactive({ resource_type: '', action: '', actor_id: '', organization_id: '' })
const columns = computed(() => [
  { key: 'created_at', label: t('audit.time'), width: '150px' },
  { key: 'source', label: t('audit.source'), width: '72px' },
  { key: 'action', label: t('audit.action'), width: '190px' },
  { key: 'resource_id', label: t('audit.resource'), width: '150px' },
  { key: 'actor_display_name', label: t('audit.actor') },
  { key: 'organization_id', label: t('audit.organization') },
  { key: 'request_id', label: t('audit.requestId') },
  { key: 'result', label: t('audit.result'), width: '80px' },
  { key: 'reason', label: t('audit.reason') },
])

async function load() {
  loading.value = true
  error.value = ''
  try { rows.value = await fetchAuditEvents(filters) } catch (exception) { error.value = exception.message } finally { loading.value = false }
}

function resetFilters() {
  Object.keys(filters).forEach((key) => { filters[key] = '' })
  load()
}

async function exportRows() {
  exporting.value = true
  error.value = ''
  try { await downloadAuditEvents(filters) } catch (exception) { error.value = exception.message } finally { exporting.value = false }
}

function formatDate(value) {
  if (!value) return '-'
  return formatLocaleDate(value, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(load)
</script>

<style scoped>
.audit-filter-panel { margin-bottom: 14px; }
.audit-filters { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)) auto; gap: 10px; align-items: end; margin-top: 14px; }
.audit-filters label { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 12px; min-width: 0; }
.audit-filters input, .audit-filters select { width: 100%; min-width: 0; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); color: var(--on-surface); font: inherit; }
.audit-filter-actions { display: flex; gap: 8px; }
.audit-count { color: var(--on-surface-variant); font-size: 12px; }
.audit-heading-actions { display: flex; align-items: center; gap: 10px; }
.audit-source { color: var(--primary); font-size: 12px; font-weight: 650; }
.audit-resource { display: grid; gap: 2px; }.audit-resource strong { font-size: 12px; }.audit-resource code, .data-table code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; overflow-wrap: anywhere; }
.audit-reason { display: block; max-width: 260px; overflow-wrap: anywhere; color: var(--on-surface-variant); }
.data-table-wrap { overflow-x: auto; margin-top: 14px; border-top: 1px solid var(--border); }
.data-table { width: 100%; min-width: 980px; border-collapse: collapse; font-size: 12px; }
.data-table th, .data-table td { padding: 10px 9px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
.data-table th { color: var(--on-surface-variant); font-weight: 650; white-space: nowrap; background: var(--surface-container-low); }
.data-table tbody tr:hover { background: var(--surface-container-low); }
time { white-space: nowrap; color: var(--on-surface-variant); }
@media (max-width: 900px) { .audit-filters { grid-template-columns: repeat(2, minmax(0, 1fr)); }.audit-filter-actions { grid-column: 1 / -1; } }
@media (max-width: 560px) { .audit-filters { grid-template-columns: 1fr; }.audit-filter-actions { grid-column: auto; } }
</style>
