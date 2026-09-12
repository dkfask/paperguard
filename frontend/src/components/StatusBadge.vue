<template>
  <span class="state-tag shared-status-badge" :class="status" :aria-label="label">
    <span v-if="icon" class="material-symbols-outlined" aria-hidden="true">{{ icon }}</span>
    {{ label }}
  </span>
</template>

<script setup>
defineProps({
  status: { type: String, default: '' },
  label: { type: String, required: true },
  icon: { type: String, default: '' },
})
</script>
