<template>
  <header class="writer-command-bar">
    <div class="writer-command-leading">
      <button class="icon-action" :title="t('writer.backToTheses')" :aria-label="t('writer.backToTheses')" @click="$emit('go-back')">
        <span class="material-symbols-outlined">arrow_back</span>
      </button>
      <div class="writer-command-title">
        <span>{{ t('route.writer') }}</span>
        <strong :title="displayProjectTitle">{{ displayProjectTitle }}</strong>
      </div>
    </div>

    <div class="writer-command-metrics">
      <span class="writer-save-status" :class="{ pending: saving || dirtyCount }">
        <span class="save-dot"></span>
        <span v-if="saving">{{ t('common.saving') }}</span>
        <span v-else-if="dirtyCount">{{ t('writer.unsaved', { count: dirtyCount }) }}</span>
        <span v-else>{{ t('writer.savedAt', { time: savedAt || t('common.justNow') }) }}</span>
      </span>
      <span><b>{{ totalChars }}</b> {{ t('writer.characters') }}</span>
      <span><b>{{ doneCount }}/{{ requiredCount }}</b> {{ t('writer.sectionsDone') }}</span>
    </div>

    <div class="writer-command-actions">
      <button class="small" @click="$emit('open-ai')" :title="t('writer.aiSettings')">
        <span class="material-symbols-outlined">auto_awesome</span>
        <span>{{ t('writer.aiAssistant') }}</span>
      </button>

      <button
        v-if="canManage"
        class="small"
        @click="$emit('open-template')"
        :disabled="saving"
        :title="t('writer.switchTemplateTitle')"
      >
        <span class="material-symbols-outlined">swap_horiz</span>
        <span>{{ t('writer.switchTemplate') }}</span>
      </button>

      <button class="writer-review-action" @click="$emit('toggle-check')">
        <span class="material-symbols-outlined">rule</span>
        <span>{{ t('writer.check') }}</span>
      </button>

      <button
        class="writer-fix-action"
        :disabled="fixing || checking || exporting || loading"
        @click="$emit('fix')"
        :title="t('writer.fixTitle')"
      >
        <span class="material-symbols-outlined">build</span>
        <span>{{ fixing ? t('writer.fixing') : t('writer.fix') }}</span>
      </button>

      <button
        class="writer-import-action"
        @click="$emit('import')"
        :disabled="importing || saving"
        :title="t('writer.importTitle')"
      >
        <span class="material-symbols-outlined">upload_file</span>
        <span>{{ importing ? t('writer.importing') : t('writer.importBody') }}</span>
      </button>

      <button
        class="writer-preview-action"
        @click="$emit('preview')"
        :disabled="exporting || saving || dirtyCount > 0"
        :title="t('writer.previewTitle')"
      >
        <span class="material-symbols-outlined">visibility</span>
        <span>{{ t('writer.preview') }}</span>
      </button>

      <button
        class="primary writer-export-action"
        @click="$emit('export')"
        :disabled="exporting || saving || dirtyCount > 0"
      >
        <span class="material-symbols-outlined">download</span>
        <span>{{ exporting ? t('writer.exporting') : t('writer.export') }}</span>
      </button>
    </div>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '../../i18n'

const { t } = useI18n()

const props = defineProps({
  projectTitle: { type: String, default: '' },
  saving: Boolean,
  dirtyCount: { type: Number, default: 0 },
  savedAt: String,
  totalChars: { type: Number, default: 0 },
  doneCount: { type: Number, default: 0 },
  requiredCount: { type: Number, default: 0 },
  canManage: Boolean,
  fixing: Boolean,
  checking: Boolean,
  exporting: Boolean,
  importing: Boolean,
  loading: Boolean,
})

const displayProjectTitle = computed(() => props.projectTitle || t('home.untitledThesis'))

defineEmits([
  'go-back',
  'open-ai',
  'open-template',
  'toggle-check',
  'fix',
  'import',
  'preview',
  'export',
])
</script>
