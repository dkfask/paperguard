<template>
  <article class="workspace-task-row">
    <div class="workspace-task-icon"><span class="material-symbols-outlined" aria-hidden="true">{{ icon }}</span></div>
    <div class="workspace-task-copy">
      <strong>{{ task.project_title || t('task.unboundThesis') }}</strong>
      <span>{{ task.cohort_name }} · {{ task.stage_name }}</span>
      <DeadlineLabel :deadline="deadline" :revision="Boolean(task.revision_deadline_at)" :late="task.is_late" />
    </div>
    <StatusBadge :status="task.state" :label="stateLabel(task.state)" />
    <RouterLink class="icon-button workspace-row-link" :to="to" :aria-label="displayActionLabel" :title="displayActionLabel">
      <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
    </RouterLink>
  </article>
</template>

<script setup>
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import DeadlineLabel from './DeadlineLabel.vue'
import StatusBadge from './StatusBadge.vue'
import { useI18n } from '../i18n'

const { t } = useI18n()

const props = defineProps({
  task: { type: Object, required: true },
  to: { type: [String, Object], required: true },
  actionLabel: { type: String, default: '' },
  icon: { type: String, default: 'edit_document' },
})

const deadline = computed(() => props.task.revision_deadline_at || props.task.effective_deadline_at || props.task.initial_deadline_at)
const displayActionLabel = computed(() => props.actionLabel || t('task.view'))
function stateLabel(state) { return t(`task.${state}`) === `task.${state}` ? state : t(`task.${state}`) }
</script>
