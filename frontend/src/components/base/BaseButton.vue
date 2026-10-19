<template>
  <button
    :type="type"
    :class="buttonClasses"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
    @click="$emit('click', $event)"
  >
    <span v-if="loading" class="animate-spin" aria-hidden="true">⏳</span>
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
  const base = 'base-button'

  const variants = {
    default: '',
    primary: 'primary bg-primary',
    danger: 'danger',
    secondary: 'secondary',
  }

  const sizes = {
    small: 'small',
    default: '',
    large: 'large',
  }

  return `${base} ${variants[props.variant]} ${sizes[props.size]}`
})
</script>

<style scoped>
.base-button { min-height: 38px; }
.base-button.small { min-height: 32px; }
.base-button.large { min-height: 46px; padding: 12px 20px; font-size: 15px; }
.base-button.secondary { color: var(--on-secondary); background: var(--secondary); }
@keyframes spin {
  to { transform: rotate(360deg); }
}
.animate-spin {
  display: inline-block;
  animation: spin 1s linear infinite;
}
@media (prefers-reduced-motion: reduce) { .animate-spin { animation: none; } }
</style>
