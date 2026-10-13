<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="open"
        class="modal-mask base-modal-mask"
        :style="{ zIndex: layer }"
        :class="{ 'screen-drawer-mask': presentation === 'drawer' }"
        @click.self="handleClose"
      >
        <div
          ref="dialog"
          class="modal base-modal"
          :class="[sizeClasses, { 'screen-drawer': presentation === 'drawer' }]"
          role="dialog"
          aria-modal="true"
          :aria-label="title || t('dialog.confirmation')"
          tabindex="-1"
          @click.stop
        >
          <div v-if="title" class="base-modal-header" :class="{ 'screen-drawer-header': presentation === 'drawer' }"><h2>{{ title }}</h2></div>
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
  presentation: { type: String, default: 'dialog' },
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
const { layer } = useDialogFocus(toRef(props, 'open'), dialog, handleClose)

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
.base-modal { position: relative; display: flex; flex-direction: column; width: 100%; max-height: 90dvh; overflow: hidden; margin: 0; padding: 0; background: var(--surface-container-lowest); }
.base-modal.modal-small { max-width: 448px; }
.base-modal.modal-default { max-width: 580px; }
.base-modal.modal-large { max-width: 896px; }
.base-modal.modal-full { max-width: 1280px; }
.base-modal-close { position: absolute; right: 16px; top: 16px; }
.base-modal-header { flex: 0 0 auto; padding: 20px 56px 20px 24px; border-bottom: 1px solid var(--border); }
.base-modal-header h2 { margin: 0; font-size: 18px; line-height: 24px; overflow-wrap: anywhere; }
.base-modal-body { min-height: 0; overflow-y: auto; padding: 24px; }
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
.screen-drawer-mask { justify-content: flex-end; padding: 0; }
.screen-drawer { display: flex; flex-direction: column; overflow: hidden; height: 100dvh; max-height: 100dvh; width: min(680px, 100%); max-width: min(680px, 100%) !important; border-radius: 16px 0 0 16px; box-shadow: -12px 0 40px #0f172a26; }
.screen-drawer-header { flex: 0 0 auto; padding: 20px 56px 20px 24px; border-bottom: 1px solid var(--border); }
.screen-drawer-header h2 { font-size: 18px; line-height: 24px; margin: 0; }
.screen-drawer .base-modal-body { flex: 1; min-height: 0; overflow: auto; padding: 24px; }
.modal-enter-active .screen-drawer, .modal-leave-active .screen-drawer { transition: transform .22s ease, opacity .22s ease; }
.modal-enter-from .screen-drawer, .modal-leave-to .screen-drawer { transform: translateX(100%); opacity: 1; }
@media (prefers-reduced-motion: reduce) { .modal-enter-active, .modal-leave-active, .modal-enter-active .base-modal, .modal-leave-active .base-modal { transition: none; } }
</style>
