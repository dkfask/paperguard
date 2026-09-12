<template>
  <div class="async-state" :class="state" role="status" :aria-live="state === 'error' ? 'assertive' : 'polite'">
    <span class="material-symbols-outlined async-state-icon" aria-hidden="true">{{ icon }}</span>
    <h3>{{ title }}</h3>
    <p v-if="detail">{{ detail }}</p>
    <button v-if="actionLabel" type="button" class="small" @click="$emit('action')">
      <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
      {{ actionLabel }}
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  state: { type: String, default: 'empty' },
  title: { type: String, required: true },
  detail: { type: String, default: '' },
  actionLabel: { type: String, default: '' },
})

defineEmits(['action'])

const icon = computed(() => ({
  loading: 'progress_activity',
  error: 'error_outline',
  empty: 'inbox',
}[props.state] || 'info'))
</script>
