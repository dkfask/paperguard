<template>
  <div v-if="items.length" class="workspace-review-list">
    <RouterLink v-for="task in items.slice(0, limit)" :key="task.id" class="workspace-review-row" :to="to">
      <div><strong>{{ task.project_title || t('home.untitledThesis') }}</strong><span>{{ task.cohort_name }} · {{ task.stage_name }}</span></div>
      <div><StatusBadge status="submitted" :label="t('review.pending')" /><span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></div>
    </RouterLink>
  </div>
  <AsyncState v-else state="empty" :title="t('review.emptyTitle')" :detail="t('review.emptyDetail')" />
</template>

<script setup>
import { RouterLink } from 'vue-router'
import AsyncState from './AsyncState.vue'
import StatusBadge from './StatusBadge.vue'
import { useI18n } from '../i18n'

const { t } = useI18n()

defineProps({
  items: { type: Array, default: () => [] },
  limit: { type: Number, default: 5 },
  to: { type: [String, Object], default: () => ({ path: '/workflow', query: { panel: 'review' } }) },
})
</script>
