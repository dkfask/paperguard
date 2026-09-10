<template>
  <div class="section-text-editor">
    <div class="section-text-editor-head">
      <div>
        <strong>整节输入</strong>
        <span class="hint">按空行分段，单换行保留在同一段内</span>
      </div>
      <button class="primary small" :disabled="!text.trim()" @click="apply">应用并拆分</button>
    </div>
    <textarea
      v-model="text"
      class="section-textarea"
      :style="styleVars"
      rows="10"
      placeholder="可在这里一次性输入或粘贴本节全部内容。不同自然段之间请空一行。"
    />
    <div class="section-text-editor-foot">应用后会替换当前章节的文字段落，图片和表格块会保留。</div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  resetKey: { type: String, default: '' },
  styleVars: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue', 'apply'])
const text = ref(props.modelValue)

watch(() => props.modelValue, (value) => {
  if (value !== text.value) text.value = value
})
watch(() => props.resetKey, () => { text.value = props.modelValue })
watch(text, (value) => emit('update:modelValue', value))

function apply() {
  emit('apply', text.value)
}
</script>

<style scoped>
.section-text-editor { border: 1px solid var(--primary-200, #bfdbfe); border-radius: 10px; padding: 12px; margin-bottom: 14px; background: var(--primary-50, #eff6ff); }
.section-text-editor-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 8px; }
.section-text-editor-head > div { display: flex; flex-direction: column; gap: 3px; }
.section-text-editor .hint, .section-text-editor-foot { color: var(--muted); font-size: 12px; }
.section-textarea { display: block; width: 100%; min-height: 180px; box-sizing: border-box; resize: vertical; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; line-height: 1.7; font: inherit; background: var(--card); }
.section-textarea:focus { outline: 2px solid var(--primary-200, #bfdbfe); }
.section-text-editor-foot { margin-top: 7px; }
@media (max-width: 600px) { .section-text-editor-head { align-items: flex-start; flex-direction: column; } }
</style>
