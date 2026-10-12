<template>
  <section class="panel workspace-notification-panel">
    <div class="panel-heading">
      <div>
        <p class="workflow-kicker">INBOX</p>
        <h3>{{ t('notification.title') }}</h3>
      </div>
      <span class="unread-count">{{ unread }}</span>
    </div>
    <div v-if="!items.length" class="muted-row">{{ t('notification.empty') }}</div>
    <ScreenCollection :items="items" :row-height="68" v-slot="{ items: visibleItems }"><button
      v-for="item in visibleItems"
      :key="item.id"
      type="button"
      class="notification-row"
      :class="{ unread: !item.read_at }"
      @click="$emit('read', item)"
    >
      <span class="material-symbols-outlined" aria-hidden="true">{{ iconFor(item) }}</span>
      <span>
        <strong>{{ item.data?.title || t('notification.workflow') }}</strong>
        <small>{{ formatDate(item.created_at) }}</small>
      </span>
    </button></ScreenCollection>
    <RouterLink v-if="items.length > 8" class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'messages' } }">
      {{ t('notification.viewAll') }}
      <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
    </RouterLink>
  </section>
</template>

<script setup>
import ScreenCollection from './ScreenCollection.vue'
import { RouterLink } from 'vue-router'
import { useI18n } from '../i18n'

const { t, formatDate: formatLocaleDate } = useI18n()

defineProps({
  items: { type: Array, default: () => [] },
  unread: { type: Number, default: 0 },
})

defineEmits(['read'])

function iconFor(item) {
  return item.data?.event_type?.includes('review') ? 'rate_review' : 'notifications'
}

function formatDate(value) {
  return value ? formatLocaleDate(value, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'
}
</script>
