<template>
  <details
    class="figure-guide"
    :class="[`is-${variant}`, { 'is-strict': isStrictContext }]"
    :open="defaultOpen"
    data-test="figure-typography-guide"
    :data-guide-variant="variant"
  >
    <summary class="figure-guide-summary">
      <span class="material-symbols-outlined figure-guide-icon" aria-hidden="true">{{ summaryIcon }}</span>
      <span class="figure-guide-summary-text">{{ summaryText }}</span>
      <span class="figure-guide-toggle">{{ t('figureGuide.expandLabel') }}</span>
    </summary>

    <div class="figure-guide-body">
      <p class="figure-guide-reason">
        {{ isStrictContext
          ? t('figureGuide.reasonSummary', { size: captionSizeLabel })
          : t('figureGuide.reasonAdvisory', { size: captionSizeLabel }) }}
      </p>

      <div class="figure-guide-examples" data-test="figure-guide-examples">
        <div class="figure-guide-card is-compliant">
          <div class="figure-guide-card-head">
            <span class="material-symbols-outlined" aria-hidden="true">check_circle</span>
            <strong>{{ t('figureGuide.compliantTitle') }}</strong>
          </div>
          <p>{{ t('figureGuide.compliantExample', { size: captionSizeLabel, recommended: recommendedSizeLabel }) }}</p>
        </div>

        <div v-if="isStrictContext" class="figure-guide-card is-blocked">
          <div class="figure-guide-card-head">
            <span class="material-symbols-outlined" aria-hidden="true">block</span>
            <strong>{{ t('figureGuide.blockedTitle') }}</strong>
          </div>
          <ul>
            <li>{{ t('figureGuide.blockedRasterExample') }}</li>
            <li>{{ t('figureGuide.blockedOversizeExample', { size: captionSizeLabel }) }}</li>
          </ul>
        </div>

        <div v-else class="figure-guide-card is-advisory">
          <div class="figure-guide-card-head">
            <span class="material-symbols-outlined" aria-hidden="true">info</span>
            <strong>{{ t('figureGuide.advisoryTitle') }}</strong>
          </div>
          <ul>
            <li>{{ t('figureGuide.advisoryRasterExample') }}</li>
            <li>{{ t('figureGuide.advisoryOversizeExample', { size: captionSizeLabel }) }}</li>
          </ul>
        </div>
      </div>

      <div class="figure-guide-section" data-test="figure-guide-steps">
        <strong>{{ t('figureGuide.stepsTitle') }}</strong>
        <ol>
          <li>{{ t('figureGuide.stepSmartArt') }}</li>
          <li>{{ t('figureGuide.stepFontSize', { size: captionSizeLabel, recommended: recommendedSizeLabel }) }}</li>
          <li>{{ t('figureGuide.stepCaption') }}</li>
        </ol>
      </div>

      <div v-if="isStrictContext" class="figure-guide-section figure-guide-actions" data-test="figure-guide-actions">
        <strong>{{ t('figureGuide.actionsTitle') }}</strong>
        <p v-if="!manuscriptImportEnabled" class="figure-guide-pilot-note">
          {{ t('figureGuide.m1PilotBoundary') }}
        </p>
        <ul>
          <li v-if="!manuscriptImportEnabled">{{ t('figureGuide.studentActionClosed', { size: captionSizeLabel }) }}</li>
          <li v-else>{{ t('figureGuide.studentActionOpen', { size: captionSizeLabel }) }}</li>
          <li>{{ t('figureGuide.teacherAction', { size: captionSizeLabel }) }}</li>
        </ul>
      </div>
    </div>
  </details>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '../../i18n'
import { formatCaptionFontSize } from '../../utils/figureGuide'

const props = defineProps({
  variant: {
    type: String,
    default: 'inline',
    validator: (value) => ['inline', 'check', 'blocked'].includes(value),
  },
  captionSizePt: { type: Number, default: null },
  manuscriptImportEnabled: { type: Boolean, default: false },
  strictRuleActive: { type: Boolean, default: false },
  defaultOpen: { type: Boolean, default: false },
})

const i18n = useI18n()
const t = i18n.t
const currentLocale = computed(() => i18n.lang?.value || 'zh')
const isStrictContext = computed(() => (
  props.strictRuleActive || props.variant === 'check' || props.variant === 'blocked'
))

const captionSizeLabel = computed(() => formatCaptionFontSize(props.captionSizePt, currentLocale.value))
const recommendedSizeLabel = computed(() => {
  const effective = Number.isFinite(Number(props.captionSizePt)) && Number(props.captionSizePt) > 0
    ? Number(props.captionSizePt)
    : 10.5
  const smaller = effective >= 10.5
    ? 9
    : (effective > 8 ? Math.max(8, Number((effective - 1).toFixed(1))) : effective)
  return formatCaptionFontSize(smaller, currentLocale.value)
})

const summaryIcon = computed(() => {
  if (isStrictContext.value) {
    return 'tips_and_updates'
  }
  return 'info'
})

const summaryText = computed(() => {
  if (props.variant === 'blocked') {
    return t('figureGuide.summaryBlocked', { size: captionSizeLabel.value })
  }
  if (props.variant === 'check') {
    return t('figureGuide.summaryCheck', { size: captionSizeLabel.value })
  }
  if (isStrictContext.value) {
    return t('figureGuide.summaryInlineStrict', { size: captionSizeLabel.value })
  }
  return t('figureGuide.summaryInline', { size: captionSizeLabel.value })
})
</script>

<style scoped>
.figure-guide {
  width: 100%;
  margin-top: 10px;
  border: 1px solid var(--border, #dfe3ea);
  border-radius: var(--radius-md, 8px);
  background: var(--surface-container-low, #f7f8fa);
  color: var(--on-surface, #172033);
  font-size: 12px;
  line-height: 1.55;
}

.figure-guide.is-blocked,
.figure-guide.is-check,
.figure-guide.is-inline.is-strict {
  border-color: #f3d299;
  background: #fffaf0;
}

.figure-guide-summary {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 11px;
  cursor: pointer;
  list-style: none;
  color: var(--on-surface, #172033);
}

.figure-guide-summary::-webkit-details-marker {
  display: none;
}

.figure-guide-icon {
  flex: 0 0 auto;
  font-size: 16px;
  margin-top: 1px;
  color: var(--primary, #2757d7);
}

.figure-guide.is-blocked .figure-guide-icon,
.figure-guide.is-check .figure-guide-icon,
.figure-guide.is-inline.is-strict .figure-guide-icon {
  color: var(--tertiary, #a96505);
}

.figure-guide-summary-text {
  flex: 1;
  min-width: 0;
  font-weight: 500;
}

.figure-guide-toggle {
  flex: 0 0 auto;
  color: var(--primary, #2757d7);
  font-weight: 600;
  white-space: nowrap;
}

.figure-guide-body {
  display: grid;
  gap: 10px;
  padding: 10px 12px 12px;
  border-top: 1px solid var(--border, #dfe3ea);
  background: var(--surface, #ffffff);
  border-radius: 0 0 var(--radius-md, 8px) var(--radius-md, 8px);
}

.figure-guide-reason {
  margin: 0;
  color: var(--on-surface-variant, #596579);
}

.figure-guide-examples {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.figure-guide-card {
  padding: 9px 10px;
  border: 1px solid var(--border, #dfe3ea);
  border-radius: var(--radius-sm, 6px);
  background: var(--surface-container-low, #f7f8fa);
}

.figure-guide-card.is-compliant {
  border-color: #b9dfc8;
  background: #f4fbf6;
}

.figure-guide-card.is-blocked {
  border-color: #f3c6cb;
  background: #fff8f8;
}

.figure-guide-card.is-advisory {
  border-color: var(--border, #dfe3ea);
  background: var(--surface-container-low, #f7f8fa);
}

.figure-guide-card-head {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 5px;
  font-size: 12px;
}

.figure-guide-card.is-compliant .figure-guide-card-head {
  color: var(--success, #15835d);
}

.figure-guide-card.is-blocked .figure-guide-card-head {
  color: var(--error, #c63f3f);
}

.figure-guide-card.is-advisory .figure-guide-card-head {
  color: var(--primary, #2757d7);
}

.figure-guide-card-head .material-symbols-outlined {
  font-size: 15px;
}

.figure-guide-card p,
.figure-guide-section p {
  margin: 0;
}

.figure-guide-card ul,
.figure-guide-section ol,
.figure-guide-section ul {
  margin: 4px 0 0;
  padding-left: 18px;
  display: grid;
  gap: 4px;
}

.figure-guide-section {
  display: grid;
  gap: 4px;
}

.figure-guide-section strong {
  color: var(--on-surface, #172033);
  font-size: 12px;
}

.figure-guide-pilot-note {
  padding: 6px 9px;
  border-radius: var(--radius-xs, 4px);
  background: var(--surface-container-low, #f7f8fa);
  color: var(--on-surface-variant, #596579);
}

@media (max-width: 680px) {
  .figure-guide-examples {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
