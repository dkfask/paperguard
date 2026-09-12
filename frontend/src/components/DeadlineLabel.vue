<template>
  <span class="deadline-label" :class="{ late }">
    <span class="material-symbols-outlined" aria-hidden="true">schedule</span>
    {{ prefix }} {{ formatted }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '../i18n'

const { t, formatDate } = useI18n()

const props = defineProps({
  deadline: { type: [String, Date, Number], default: null },
  revision: { type: Boolean, default: false },
  late: { type: Boolean, default: false },
})

const prefix = computed(() => (props.revision ? t('deadline.revision') : t('deadline.initial')))
const formatted = computed(() => props.deadline
  ? formatDate(props.deadline, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  : '-')
</script>
