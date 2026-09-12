<template>
  <div v-if="open" class="modal-mask" role="presentation" @click.self="cancel">
    <section
      class="modal card confirm-dialog"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-describedby="description ? descriptionId : undefined"
      @keydown.esc="cancel"
    >
      <div class="modal-head">
        <div>
          <p class="workflow-kicker">{{ t('dialog.confirmation') }}</p>
          <h3 :id="titleId">{{ title }}</h3>
        </div>
        <button type="button" class="icon-button" :title="t('dialog.close')" :aria-label="t('dialog.close')" @click="cancel">
          <span class="material-symbols-outlined">close</span>
        </button>
      </div>
      <p v-if="description" :id="descriptionId" class="hint">{{ description }}</p>
      <div class="form-actions">
        <button type="button" class="small" :disabled="busy" @click="cancel">{{ t('dialog.cancel') }}</button>
        <button ref="confirmButton" type="button" class="small" :class="danger ? 'danger' : 'primary'" :disabled="busy" @click="confirm">
          <span class="material-symbols-outlined">{{ danger ? 'delete' : 'check' }}</span>
          {{ busy ? t('dialog.processing') : displayConfirmLabel }}
        </button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from '../i18n'

const { t } = useI18n()

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, required: true },
  description: { type: String, default: '' },
  confirmLabel: { type: String, default: '' },
  busy: { type: Boolean, default: false },
  danger: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel'])
const confirmButton = ref(null)
const titleId = `confirm-dialog-title-${Math.random().toString(36).slice(2)}`
const descriptionId = `confirm-dialog-description-${Math.random().toString(36).slice(2)}`
const displayConfirmLabel = computed(() => props.confirmLabel || t('dialog.confirm'))

watch(() => props.open, async (open) => {
  if (!open) return
  await nextTick()
  confirmButton.value?.focus()
})

function cancel() { emit('cancel') }
function confirm() { emit('confirm') }
</script>

<style scoped>
.confirm-dialog:focus { outline: 2px solid var(--primary); outline-offset: 2px; }
</style>
