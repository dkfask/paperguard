<template>
  <Teleport to="body">
    <Transition name="modal">
      <div
        v-if="open"
        class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
        @click.self="handleClose"
      >
        <div
          class="relative max-h-[90vh] w-full overflow-y-auto rounded-lg bg-white shadow-xl"
          :class="sizeClasses"
          @click.stop
        >
          <button
            class="absolute right-4 top-4 rounded-md p-1 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-primary"
            @click="handleClose"
            :aria-label="t('common.close')"
          >
            <span class="text-xl leading-none">&times;</span>
          </button>
          <div class="p-6">
            <slot />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '../../i18n'

const props = defineProps({
  open: Boolean,
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['small', 'default', 'large', 'full'].includes(v)
  }
})

const emit = defineEmits(['close'])
const { t } = useI18n()

const sizeClasses = computed(() => {
  const sizes = {
    small: 'max-w-md',
    default: 'max-w-2xl',
    large: 'max-w-4xl',
    full: 'max-w-7xl',
  }
  return sizes[props.size]
})

function handleClose() {
  emit('close')
}
</script>

<style scoped>
.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-active .rounded-lg,
.modal-leave-active .rounded-lg {
  transition: transform 0.2s ease, opacity 0.2s ease;
}

.modal-enter-from .rounded-lg,
.modal-leave-to .rounded-lg {
  transform: scale(0.96) translateY(4px);
  opacity: 0;
}
</style>
