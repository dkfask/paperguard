<template>
  <main class="editor-main">
    <!-- 封面编辑 -->
    <div v-if="currentNode?.type === 'cover'" class="cover-editor">
      <h2 class="editor-heading"><span aria-hidden="true">📋</span> {{ t('writer.coverInfo') }}</h2>
      <div class="cover-fields">
        <div v-for="field in coverFields" :key="field.key" class="cover-field">
          <label>{{ field.label }}<span v-if="field.required" class="required-mark">*</span></label>
          <input
            v-if="field.type === 'text'"
            :value="getCoverValue(field.key)"
            @input="updateCover(field.key, $event.target.value)"
            :placeholder="field.placeholder"
          />
          <textarea
            v-else-if="field.type === 'textarea'"
            :value="getCoverValue(field.key)"
            @input="updateCover(field.key, $event.target.value)"
            :placeholder="field.placeholder"
            rows="3"
          ></textarea>
        </div>
      </div>
    </div>

    <!-- 章节编辑 -->
    <div v-else-if="currentNode?.type === 'section'" class="section-editor">
      <div class="section-header">
        <h2 class="editor-heading">{{ currentNode.label }}</h2>
        <div class="section-meta">
          <span>{{ wordCount }} {{ t('writer.characters') }}</span>
          <span v-if="currentNode.required" class="required-badge">{{ t('editor.required') }}</span>
        </div>
      </div>

      <div class="editor-toolbar">
        <button class="tiny" @click="$emit('add-block')" :title="t('writer.addParagraph')">
          <span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('writer.addParagraph') }}
        </button>
        <button class="tiny" @click="$emit('ai-assist', 'expand')" :title="t('writer.aiExpand')">
          <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>{{ t('writer.aiExpand') }}
        </button>
      </div>

      <div class="blocks-container">
        <div
          v-for="(block, idx) in blocks"
          :key="block.id"
          class="block-item"
        >
          <div class="block-controls">
            <button
              class="block-move-btn"
              :disabled="idx === 0"
              @click="$emit('move-block', idx, -1)"
              :title="t('writer.moveUp')"
            ><span class="material-symbols-outlined" aria-hidden="true">arrow_upward</span></button>
            <button
              class="block-move-btn"
              :disabled="idx === blocks.length - 1"
              @click="$emit('move-block', idx, 1)"
              :title="t('writer.moveDown')"
            ><span class="material-symbols-outlined" aria-hidden="true">arrow_downward</span></button>
            <button
              class="block-delete-btn"
              @click="$emit('remove-block', idx)"
              :title="t('common.remove')"
            ><span class="material-symbols-outlined" aria-hidden="true">delete</span></button>
          </div>

          <textarea
            :value="block.text"
            @input="$emit('update-block', idx, $event.target.value)"
            :placeholder="t('writer.paragraphPlaceholder', { number: idx + 1 })"
            rows="4"
            class="block-textarea"
          ></textarea>
        </div>
      </div>
    </div>

    <!-- 参考文献编辑 -->
    <div v-else-if="currentNode?.type === 'references'" class="references-editor">
      <h2 class="editor-heading"><span aria-hidden="true">📚</span> {{ t('section.references') }}</h2>
      <div class="references-list">
        <div v-for="(ref, idx) in references" :key="idx" class="reference-item">
          <span class="ref-number">[{{ idx + 1 }}]</span>
          <textarea
            :value="ref.text"
            @input="$emit('update-reference', idx, $event.target.value)"
            :placeholder="t('writer.referencePlaceholder')"
            rows="2"
          ></textarea>
          <button class="tiny danger" @click="$emit('remove-reference', idx)">{{ t('common.remove') }}</button>
        </div>
      </div>
      <button class="small" @click="$emit('add-reference')"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('writer.addReference') }}</button>
    </div>

    <!-- 空状态 -->
    <div v-else class="empty-editor">
      <p>{{ t('writer.selectSection') }}</p>
    </div>
  </main>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '../../i18n'

const { t } = useI18n()

const props = defineProps({
  currentNode: Object,
  project: Object,
  template: Object,
  coverFields: { type: Array, default: () => [] },
  blocks: { type: Array, default: () => [] },
  references: { type: Array, default: () => [] },
})

defineEmits([
  'update-cover',
  'update-block',
  'add-block',
  'remove-block',
  'move-block',
  'update-reference',
  'add-reference',
  'remove-reference',
  'ai-assist',
])

const wordCount = computed(() => {
  if (!props.blocks) return 0
  return props.blocks.reduce((sum, b) => sum + (b.text?.length || 0), 0)
})

function getCoverValue(key) {
  return props.project?.cover?.[key] || ''
}

function updateCover(key, value) {
  // Emit to parent
}
</script>
