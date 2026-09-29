<template>
  <aside v-if="open" class="check-panel context-panel" :class="{ clean: isClean }">
    <div class="check-panel-head">
      <div class="check-panel-title">
        <span class="check-panel-icon material-symbols-outlined">fact_check</span>
        <div>
          <span class="check-panel-kicker">FORMATTING REVIEW</span>
          <strong>{{ t('writer.formatCheck') }}</strong>
        </div>
      </div>
      <button class="check-refresh-btn" :disabled="checking" @click="$emit('refresh')">
        <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
        <span>{{ t('common.refresh') }}</span>
      </button>
    </div>

    <div v-if="checking" class="check-loading">
      <span>{{ t('writer.checking') }}</span>
    </div>

    <div v-else-if="error" class="check-error error-banner">
      ⚠️ {{ error }}
    </div>

    <div v-else-if="result" class="check-results">
      <div class="check-summary">
        <div class="summary-stat">
          <span class="stat-number error">{{ result.summary.errors || 0 }}</span>
          <span class="stat-label">{{ t('policies.error') }}</span>
        </div>
        <div class="summary-stat">
          <span class="stat-number warning">{{ result.summary.warnings || 0 }}</span>
          <span class="stat-label">{{ t('policies.warning') }}</span>
        </div>
        <div class="summary-stat">
          <span class="stat-number info">{{ result.summary.info || 0 }}</span>
          <span class="stat-label">{{ t('policies.info') }}</span>
        </div>
      </div>

      <div v-if="result.findings?.length" class="findings-list">
        <div
          v-for="finding in result.findings"
          :key="finding.id"
          class="finding-item"
          :class="finding.severity"
        >
          <div class="finding-header">
            <span class="finding-severity-badge">
              <span class="material-symbols-outlined" aria-hidden="true">
                {{ finding.severity === 'error' ? 'error' : finding.severity === 'info' ? 'info' : 'warning' }}
              </span>
              <span>{{ severityLabel(finding.severity) }}</span>
            </span>
            <span class="finding-message">{{ finding.message }}</span>
          </div>

          <div class="finding-actions">
            <span v-if="finding.section_key" class="finding-location">
              {{ getSectionTitle(finding.section_key) }} →
            </span>
            <button
              v-if="finding.auto_fixable"
              class="tiny primary"
              @click="$emit('fix-finding', finding)"
            >
              {{ t('writer.autoFix') }}
            </button>
            <button
              v-if="finding.source_type === 'requirement'"
              class="tiny"
              @click="$emit('request-waiver', finding)"
            >
              {{ t('writer.requestWaiver') }}
            </button>
          </div>
        </div>
      </div>

      <div v-else class="check-clean">
        {{ t('writer.noIssues') }}
      </div>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '../../i18n'

const { t } = useI18n()

const props = defineProps({
  open: Boolean,
  checking: Boolean,
  result: Object,
  error: String,
  sectionTitles: { type: Object, default: () => ({}) },
})

defineEmits(['refresh', 'fix-finding', 'request-waiver'])

const isClean = computed(() => {
  return props.result &&
         props.result.summary.total === 0 &&
         !props.error
})

function getSectionTitle(key) {
  return props.sectionTitles[key] || t('writer.unknownSection')
}

function severityLabel(severity) {
  return { error: t('writer.errorShort'), warning: t('writer.warningShort'), info: t('writer.infoShort') }[severity] || severity
}
</script>
