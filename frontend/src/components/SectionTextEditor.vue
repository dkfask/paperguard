<template>
  <div class="section-text-editor">
    <div class="section-text-editor-head">
      <div>
        <strong>{{ t('sectionTextEditor.title') }}</strong>
        <span class="hint">{{ t('sectionTextEditor.help') }}</span>
      </div>
      <button class="primary small" @click="apply">{{ t('sectionTextEditor.apply') }}</button>
    </div>
    <TiptapSectionEditor
      v-model="text"
      :document="document"
      :style-vars="styleVars"
      @update:document="emit('update:document', $event)"
    />
    <div class="section-text-editor-foot">{{ t('sectionTextEditor.detail') }}</div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import TiptapSectionEditor from '../features/writer/components/TiptapSectionEditor.vue'
import { useI18n } from '../i18n'

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: String, default: '' },
  document: { type: Object, required: true },
  resetKey: { type: String, default: '' },
  styleVars: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue', 'update:document', 'apply'])
const text = ref(props.modelValue)

watch(() => props.modelValue, (value) => {
  if (value !== text.value) text.value = value
})
watch(() => props.resetKey, () => { text.value = props.modelValue })
watch(text, (value) => emit('update:modelValue', value))

function apply() {
  emit('apply', props.document)
}
</script>

<style scoped>
.section-text-editor { border: 1px solid var(--primary-200, #bfdbfe); border-radius: 10px; padding: 12px; margin-bottom: 14px; background: var(--primary-50, #eff6ff); }
.section-text-editor-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.section-text-editor-head > div { display: flex; flex-direction: column; gap: 3px; }
.section-text-editor .hint, .section-text-editor-foot { color: var(--muted); font-size: 12px; }
.section-text-editor-foot { margin-top: 7px; }
@media (max-width: 600px) { .section-text-editor-head { align-items: flex-start; flex-direction: column; } }
</style>
