<template>
  <Teleport to="body">
  <div v-if="open" class="modal-mask" :style="{ zIndex: layer }" role="presentation" @click.self="cancel">
    <section
      class="modal card confirm-dialog"
      ref="dialog"
      tabindex="-1"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-describedby="description ? descriptionId : undefined"
    >
      <div class="modal-head">
        <div>
          <p class="workflow-kicker">{{ t('dialog.confirmation') }}</p>
          <h3 :id="titleId">{{ title }}</h3>
        </div>
        <button type="button" class="icon-button" :title="t('dialog.close')" :aria-label="t('dialog.close')" @click="cancel">
          <span class="material-symbols-outlined" aria-hidden="true">close</span>
        </button>
      </div>
      <p v-if="description" :id="descriptionId" class="hint">{{ description }}</p>
      <div class="form-actions">
        <button type="button" class="small" :disabled="busy" @click="cancel">{{ t('dialog.cancel') }}</button>
        <button ref="confirmButton" type="button" class="small" :class="danger ? 'danger' : 'primary'" :disabled="busy" @click="confirm">
          <span class="material-symbols-outlined" aria-hidden="true">{{ danger ? 'delete' : 'check' }}</span>
          {{ busy ? t('dialog.processing') : displayConfirmLabel }}
        </button>
      </div>
    </section>
  </div>
  </Teleport>
</template>

<script setup>
import { computed, ref, toRef } from 'vue'
import { useI18n } from '../i18n'
import { useDialogFocus } from '../shared/useDialogFocus'

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
const dialog = ref(null)
const titleId = `confirm-dialog-title-${Math.random().toString(36).slice(2)}`
const descriptionId = `confirm-dialog-description-${Math.random().toString(36).slice(2)}`
const displayConfirmLabel = computed(() => props.confirmLabel || t('dialog.confirm'))

const { layer } = useDialogFocus(toRef(props, 'open'), dialog, cancel, confirmButton)

function cancel() { if (!props.busy) emit('cancel') }
function confirm() { if (!props.busy) emit('confirm') }
</script>

<style scoped>
.confirm-dialog:focus { outline: 2px solid var(--primary); outline-offset: 2px; }
</style>
