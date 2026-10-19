<template>
  <div v-if="items.length" class="workspace-archive-list">
    <ScreenCollection :items="items" :row-height="60" v-slot="{ items: visibleItems }"><div v-for="archive in visibleItems" :key="archive.id" class="workspace-archive-row">
      <div><strong>{{ archive.project_title || archive.submission_number || t('workflow.formalSubmission') }}</strong><span v-if="archive.project_title">{{ archive.student_name }} · {{ archive.stage_name }} · {{ archive.submission_number }}</span><span>{{ formatDate(archive.created_at) }}</span></div>
      <StatusBadge :status="archive.status" :label="statusLabel(archive.status)" />
    </div></ScreenCollection>
  </div>
  <AsyncState v-else state="empty" :title="t('archive.empty')" :detail="t('archive.emptyDetail')" />
</template>

<script setup>
import ScreenCollection from './ScreenCollection.vue'
import AsyncState from './AsyncState.vue'
import StatusBadge from './StatusBadge.vue'
import { useI18n } from '../i18n'

const { t, formatDate: formatLocaleDate } = useI18n()

defineProps({
  items: { type: Array, default: () => [] },
  limit: { type: Number, default: 6 },
})

function statusLabel(status) { return t(`archive.${status}`) === `archive.${status}` ? status : t(`archive.${status}`) }
function formatDate(value) { return value ? formatLocaleDate(value, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-' }
</script>
