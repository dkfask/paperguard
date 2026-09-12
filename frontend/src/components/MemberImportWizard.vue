<template>
  <section class="panel member-import-panel member-import-wizard">
    <div class="panel-heading">
      <div>
        <p class="workflow-kicker">{{ t('memberImport.kicker') }}</p>
        <h3>{{ t('memberImport.title') }}</h3>
        <p>{{ t('memberImport.description') }}</p>
      </div>
      <span class="material-symbols-outlined panel-icon">group_add</span>
    </div>
    <textarea
      :value="modelValue"
      rows="5"
      :placeholder="t('memberImport.placeholder')"
      @input="$emit('update:modelValue', $event.target.value)"
    />
    <div class="form-actions">
      <button class="small" :disabled="busy || !rows.length" @click="$emit('preview')">
        <span class="material-symbols-outlined">fact_check</span>{{ t('memberImport.preview') }}
      </button>
      <button class="small primary" :disabled="busy || !rows.length" @click="$emit('commit')">
        <span class="material-symbols-outlined">publish</span>{{ t('memberImport.commit') }}
      </button>
    </div>
    <div v-if="result" class="member-import-result">
      <div class="import-summary">
        <span>{{ t('memberImport.total', { count: result.summary.total }) }}</span>
        <span>{{ t('memberImport.success', { count: result.summary.imported }) }}</span>
        <span>{{ t('memberImport.failed', { count: result.summary.failed }) }}</span>
      </div>
      <div v-for="row in result.results" :key="row.row_number" class="import-row">
        <span>{{ t('memberImport.row', { row: row.row_number }) }}</span>
        <strong :class="row.status === 'failed' ? 'preview-error' : 'preview-ok'">{{ row.status === 'failed' ? t('memberImport.failedStatus') : row.status === 'ready' ? t('memberImport.ready') : t('memberImport.imported') }}</strong>
        <small>{{ row.errors?.join('；') || row.warnings?.join('；') || t('memberImport.validated') }}</small>
      </div>
    </div>
  </section>
</template>

<script setup>
import { useI18n } from '../i18n'

const { t } = useI18n()

defineProps({
  modelValue: { type: String, default: '' },
  rows: { type: Array, default: () => [] },
  result: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})

defineEmits(['update:modelValue', 'preview', 'commit'])
</script>

<style scoped>
.member-import-wizard { display: block; }
.panel { background: var(--surface-container-lowest); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 18px; margin-bottom: 14px; box-shadow: var(--shadow-card); }
.panel-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.panel-heading h3 { margin: 0; font-size: 16px; }
.panel-heading p { margin: 4px 0 0; color: var(--on-surface-variant); font-size: 12px; }
.workflow-kicker { margin: 0 0 4px; color: var(--primary); font-size: 10px; font-weight: 700; letter-spacing: .12em; }
.panel-icon { color: var(--primary); font-size: 26px; }
.member-import-panel { border-left: 3px solid var(--primary); }
.member-import-wizard > textarea { width: 100%; margin-top: 14px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.form-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 15px; }
.member-import-result { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 10px; }
.import-summary { display: flex; gap: 14px; color: var(--on-surface-variant); font-size: 11px; margin-bottom: 7px; }
.import-row { display: grid; grid-template-columns: 80px 60px minmax(0, 1fr); gap: 8px; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 11px; }
.import-row small { color: var(--on-surface-variant); overflow-wrap: anywhere; }
.preview-ok { color: #087f5b; }
.preview-error { color: var(--error); }
@media (max-width: 560px) { .import-row { grid-template-columns: 64px 50px minmax(0, 1fr); } }
</style>
