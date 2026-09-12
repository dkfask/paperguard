<template>
  <aside class="sidebar">
    <div class="tree-nav-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
      <span style="font-size: 12px; font-weight: 600; color: var(--muted)">{{ t('writer.outline') }}</span>
      <button v-if="canEditStructure" class="tiny" @click="$emit('add-section')" :title="t('writer.addSection')">
        <span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('writer.addSection') }}
      </button>
      <span v-else class="tiny" style="font-size: 11px; color: var(--muted); opacity: 0.8;" :title="t('writer.lockedTitle')">
        <span class="material-symbols-outlined" aria-hidden="true">lock</span>{{ t('writer.locked') }}
      </span>
    </div>

    <nav class="tree-nav">
      <div
        v-for="node in displaySections"
        :key="node.key"
        class="nav-row"
        :class="{ active: currentSection === node.key }"
        :style="{ paddingLeft: (8 + node.depth * 14) + 'px' }"
        @click="$emit('select-section', node)"
      >
        <span v-if="isContentType(node.type) && node.key !== 'cover'">✍️</span>
        <span v-else-if="node.type === 'cover'">📋</span>
        <span class="nav-title">{{ getLabel(node) }}</span>
        <span v-if="isRequired(node) && !isDone(node)" class="dot" :title="t('writer.incomplete')">●</span>

        <span v-if="canEditStructure" class="nav-action-move">
          <button
            class="nav-move-btn"
            :disabled="structureBusy || isFirstSection(node)"
            @click.stop="$emit('move-section', node, -1)"
            :title="t('writer.moveUp')"
            :aria-label="`${t('writer.moveUp')} ${getLabel(node)}`"
          >↑</button>
          <button
            class="nav-move-btn"
            :disabled="structureBusy || isLastSection(node)"
            @click.stop="$emit('move-section', node, 1)"
            :title="t('writer.moveDown')"
            :aria-label="`${t('writer.moveDown')} ${getLabel(node)}`"
          >↓</button>
        </span>

        <span
          v-if="canEditStructure && isCustomSection(node)"
          class="nav-action-del"
          @click.stop="$emit('remove-section', node)"
          :title="t('writer.deleteSection')"
        ><span class="material-symbols-outlined" aria-hidden="true">delete</span></span>
      </div>
    </nav>

    <!-- 字数与进度 -->
    <div class="stats-panel">
      <div class="progress-row">
        <span class="stats-label">{{ t('writer.completion') }}</span>
        <span class="stats-value">{{ doneCount }}/{{ requiredCount }}</span>
      </div>
      <div class="progress-bar">
        <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
      </div>
      <div class="stats-row">
        <span class="stats-label">{{ t('writer.totalCharacters') }}</span>
        <span class="stats-value">{{ totalChars }}</span>
      </div>
    </div>

    <!-- 缺失必填章节提示 -->
    <div v-if="missingRequired.length" class="missing-panel">
      <p style="margin: 0 0 6px 0; font-size: 12px; font-weight: 600">{{ t('writer.missingRequired') }}</p>
      <ul style="margin: 0; padding-left: 16px; font-size: 12px">
        <li v-for="m in missingRequired" :key="m">{{ m }}</li>
      </ul>
    </div>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '../../i18n'

const { t } = useI18n()

const props = defineProps({
  displaySections: { type: Array, required: true },
  currentSection: String,
  canEditStructure: Boolean,
  structureBusy: Boolean,
  doneCount: { type: Number, default: 0 },
  requiredCount: { type: Number, default: 0 },
  totalChars: { type: Number, default: 0 },
  missingRequired: { type: Array, default: () => [] },
})

defineEmits([
  'add-section',
  'select-section',
  'move-section',
  'remove-section',
])

const progressPct = computed(() => {
  if (props.requiredCount === 0) return 0
  return Math.round((props.doneCount / props.requiredCount) * 100)
})

function isContentType(type) {
  return ['section', 'references'].includes(type)
}

function isRequired(node) {
  return node.required
}

function isDone(node) {
  return node.done
}

function getLabel(node) {
  return node.label || t('home.untitledThesis')
}

function isFirstSection(node) {
  const siblings = props.displaySections.filter(n => n.depth === node.depth && n.parent === node.parent)
  return siblings[0]?.key === node.key
}

function isLastSection(node) {
  const siblings = props.displaySections.filter(n => n.depth === node.depth && n.parent === node.parent)
  return siblings[siblings.length - 1]?.key === node.key
}

function isCustomSection(node) {
  return node.custom === true
}
</script>
