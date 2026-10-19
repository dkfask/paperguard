<template>
  <div class="base-field">
    <label v-if="label" :for="inputId">
      {{ label }}
      <span v-if="required" class="required-mark" aria-hidden="true">*</span>
    </label>
    <input
      :id="inputId"
      :type="type"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      :required="required"
      :aria-invalid="error ? 'true' : undefined"
      :aria-describedby="error || hint ? `${inputId}-detail` : undefined"
      :class="inputClasses"
      @input="$emit('update:modelValue', $event.target.value)"
      @blur="$emit('blur', $event)"
    />
    <p v-if="error" :id="`${inputId}-detail`" class="field-error" role="alert">{{ error }}</p>
    <p v-else-if="hint" :id="`${inputId}-detail`" class="field-hint">{{ hint }}</p>
  </div>
</template>

<script setup>
import { computed, useId } from 'vue'

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

const inputId = `input-${useId()}`

const inputClasses = computed(() => {
  return props.error ? 'base-input invalid' : 'base-input'
})
</script>

<style scoped>
.base-field { display: grid; gap: 6px; }
.base-field label { font-size: 12px; font-weight: 600; }
.base-input { width: 100%; min-height: 38px; }
.base-input.invalid { border-color: var(--error); }
.base-input:disabled { background: var(--surface-container-low); cursor: not-allowed; }
.field-error, .required-mark { color: var(--error); }
.field-error, .field-hint { margin: 0; font-size: 12px; }
.field-hint { color: var(--on-surface-variant); }
</style>
