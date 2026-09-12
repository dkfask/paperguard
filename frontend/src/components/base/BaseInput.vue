<template>
  <div class="flex flex-col gap-1">
    <label v-if="label" :for="inputId" class="text-sm font-medium text-on-surface">
      {{ label }}
      <span v-if="required" class="text-error">*</span>
    </label>
    <input
      :id="inputId"
      :type="type"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      :required="required"
      :class="inputClasses"
      @input="$emit('update:modelValue', $event.target.value)"
      @blur="$emit('blur', $event)"
    />
    <p v-if="error" class="text-sm text-error">{{ error }}</p>
    <p v-else-if="hint" class="text-sm text-on-surface-variant">{{ hint }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: [String, Number],
  type: { type: String, default: 'text' },
  label: String,
  placeholder: String,
  error: String,
  hint: String,
  disabled: Boolean,
  required: Boolean,
})

defineEmits(['update:modelValue', 'blur'])

const inputId = computed(() => `input-${Math.random().toString(36).substr(2, 9)}`)

const inputClasses = computed(() => {
  const base = 'w-full px-3 py-2 rounded-md border transition-colors focus:outline-none focus:ring-2 focus:ring-offset-1 disabled:opacity-50 disabled:cursor-not-allowed'
  const state = props.error
    ? 'border-error focus:border-error focus:ring-error'
    : 'border-border focus:border-primary focus:ring-primary'
  return `${base} ${state}`
})
</script>
