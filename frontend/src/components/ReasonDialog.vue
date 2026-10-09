<template>
  <div v-if="open" class="modal-mask" role="presentation" @click.self="cancel">
    <section ref="dialog" class="modal card reason-dialog" role="dialog" aria-modal="true" :aria-labelledby="titleId" tabindex="-1">
      <div class="modal-head">
        <div>
          <p class="workflow-kicker">{{ t('dialog.confirmation') }}</p>
          <h3 :id="titleId">{{ title }}</h3>
        </div>
        <button type="button" class="icon-button" :title="t('dialog.close')" :aria-label="t('dialog.close')" @click="cancel">
          <span class="material-symbols-outlined" aria-hidden="true">close</span>
        </button>
      </div>
      <p v-if="description" class="hint">{{ description }}</p>
      <label class="field">
        {{ label }}
        <textarea ref="input" v-model="draft" rows="4" :placeholder="placeholder" @keydown.esc="cancel" @keydown.ctrl.enter="confirm" @keydown.meta.enter="confirm" />
      </label>
      <div class="form-actions">
        <button type="button" class="small" @click="cancel">{{ t('dialog.cancel') }}</button>
        <button type="button" class="small primary" :disabled="busy || !draft.trim()" @click="confirm">
          <span class="material-symbols-outlined" aria-hidden="true">check</span>
          {{ busy ? t('dialog.processing') : confirmText }}
        </button>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, toRef, watch } from 'vue'
import { useI18n } from '../i18n'
import { useDialogFocus } from '../shared/useDialogFocus'

const { t } = useI18n()

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, required: true },
  description: { type: String, default: '' },
  label: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  defaultValue: { type: String, default: '' },
  confirmLabel: { type: String, default: '' },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['confirm', 'cancel'])
const draft = ref('')
const input = ref(null)
const dialog = ref(null)
const titleId = `reason-dialog-${Math.random().toString(36).slice(2)}`

const label = computed(() => props.label || t('reason.label'))
const placeholder = computed(() => props.placeholder || t('reason.placeholder'))
const confirmText = computed(() => props.confirmLabel || t('common.confirm'))
useDialogFocus(toRef(props, 'open'), dialog, cancel, input)

watch(() => props.open, async (open) => {
  if (!open) return
  draft.value = props.defaultValue
  await nextTick()
  input.value?.focus()
})

function cancel() { if (!props.busy) emit('cancel') }
function confirm() {
  if (props.busy) return
  const value = draft.value.trim()
  if (value) emit('confirm', value)
}
</script>
