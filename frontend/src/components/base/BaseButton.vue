<template>
  <button
    :type="type"
    :class="buttonClasses"
    :disabled="disabled || loading"
    @click="$emit('click', $event)"
  >
    <span v-if="loading" class="inline-block animate-spin">⏳</span>
    <slot />
  </button>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  variant: {
    type: String,
    default: 'default',
    validator: (v) => ['default', 'primary', 'danger', 'secondary'].includes(v)
  },
  size: {
    type: String,
    default: 'default',
    validator: (v) => ['small', 'default', 'large'].includes(v)
  },
  type: {
    type: String,
    default: 'button',
  },
  disabled: Boolean,
  loading: Boolean,
})

defineEmits(['click'])

const buttonClasses = computed(() => {
  const base = 'inline-flex items-center justify-center gap-2 font-medium transition-colors rounded-md border disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-offset-2'

  const variants = {
    default: 'bg-surface border-border text-on-surface hover:bg-surface-container focus:ring-primary',
    primary: 'bg-primary border-primary text-on-primary hover:bg-primary-hover focus:ring-primary',
    danger: 'bg-error border-error text-on-error hover:bg-red-700 focus:ring-error',
    secondary: 'bg-secondary border-secondary text-on-secondary hover:bg-gray-600 focus:ring-secondary',
  }

  const sizes = {
    small: 'px-3 py-1.5 text-sm',
    default: 'px-4 py-2 text-base',
    large: 'px-6 py-3 text-lg',
  }

  return `${base} ${variants[props.variant]} ${sizes[props.size]}`
})
</script>

<style scoped>
@keyframes spin {
  to { transform: rotate(360deg); }
}
.animate-spin {
  animation: spin 1s linear infinite;
}
</style>
