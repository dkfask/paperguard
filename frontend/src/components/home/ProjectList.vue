<template>
  <div class="project-list">
    <div
      v-for="project in projects"
      :key="project.id"
      class="project-card"
      @click="$emit('open', project.id)"
    >
      <div class="project-card-header">
        <h3 class="project-title">{{ project.title || t('homeComponents.unnamed') }}</h3>
        <div class="project-meta">
          <span class="template-badge">{{ project.template_name }}</span>
          <span class="date-text">{{ formatDate(project.updated_at) }}</span>
        </div>
      </div>

      <div class="project-card-body">
        <div class="progress-info">
          <span>{{ project.done_count || 0 }}/{{ project.required_count || 0 }} {{ t('homeComponents.sectionsDone') }}</span>
          <span>{{ project.total_chars || 0 }} {{ t('homeComponents.characters') }}</span>
        </div>
        <div class="progress-bar">
          <div
            class="progress-fill"
            :style="{ width: getProgress(project) + '%' }"
          ></div>
        </div>
      </div>

      <div class="project-card-actions" @click.stop>
        <button class="tiny" @click="$emit('export', project.id)" :title="t('homeComponents.export')">
          📥 {{ t('homeComponents.export') }}
        </button>
        <button class="tiny danger" @click="$emit('delete', project.id)" :title="t('homeComponents.delete')">
          🗑️ {{ t('homeComponents.delete') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '../../i18n'

const { t, formatDate: formatLocaleDate } = useI18n()

defineProps({
  projects: { type: Array, default: () => [] },
})

defineEmits(['open', 'export', 'delete'])

function getProgress(project) {
  if (!project.required_count) return 0
  return Math.round((project.done_count / project.required_count) * 100)
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  const date = new Date(dateStr)
  return formatLocaleDate(date)
}
</script>
