<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="open"
        class="modal-mask base-modal-mask"
        @click.self="handleClose"
      >
        <div
          ref="dialog"
          class="modal base-modal"
          :class="sizeClasses"
          role="dialog"
          aria-modal="true"
          :aria-label="title || t('dialog.confirmation')"
          tabindex="-1"
          @click.stop
        >
          <button
            class="base-modal-close icon-button"
            type="button"
            @click="handleClose"
            :aria-label="t('common.close')"
          >
            <span class="text-xl leading-none">&times;</span>
          </button>
          <div class="base-modal-body">
            <slot />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, ref, toRef } from 'vue'
import { useI18n } from '../../i18n'
import { useDialogFocus } from '../../shared/useDialogFocus'

const props = defineProps({
  open: Boolean,
  title: String,
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['small', 'default', 'large', 'full'].includes(v)
  }
})

const emit = defineEmits(['close'])
const { t } = useI18n()
const dialog = ref(null)
useDialogFocus(toRef(props, 'open'), dialog, handleClose)

const sizeClasses = computed(() => {
  const sizes = {
    small: 'modal-small',
    default: 'modal-default',
    large: 'modal-large',
    full: 'modal-full',
  }
  return sizes[props.size]
})

function handleClose() {
  emit('close')
}
</script>

<style scoped>
.base-modal-mask { z-index: 100; padding: 20px; }
.base-modal { position: relative; width: 100%; max-height: 90dvh; overflow-y: auto; margin: 0; padding: 0; background: var(--surface-container-lowest); }
.base-modal.modal-small { max-width: 448px; }
.base-modal.modal-default { max-width: 580px; }
.base-modal.modal-large { max-width: 896px; }
.base-modal.modal-full { max-width: 1280px; }
.base-modal-close { position: absolute; right: 16px; top: 16px; }
.base-modal-body { padding: 24px; }
.base-modal-body :deep(h2:first-child) { padding-right: 32px; font-size: 18px; margin-bottom: 20px; }
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-active .base-modal,
.modal-leave-active .base-modal {
  transition: transform 0.2s ease, opacity 0.2s ease;
}

.modal-enter-from .base-modal,
.modal-leave-to .base-modal {
  transform: scale(0.96) translateY(4px);
  opacity: 0;
}
@media (prefers-reduced-motion: reduce) { .modal-enter-active, .modal-leave-active, .modal-enter-active .base-modal, .modal-leave-active .base-modal { transition: none; } }
</style>
