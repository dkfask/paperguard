<template>
  <div class="inline-notice" :class="type === 'success' ? 'ok-banner' : 'error-banner'" :role="type === 'success' ? 'status' : 'alert'">
    <span class="material-symbols-outlined" aria-hidden="true">{{ type === 'success' ? 'check_circle' : 'warning' }}</span>
    <span class="inline-notice-summary">{{ summary }}</span>
    <button v-if="hasDetails" type="button" class="tiny" aria-haspopup="dialog" @click="detailsOpen = true">{{ t('screen.details') }}</button>
  </div>
  <BaseModal presentation="drawer" :open="detailsOpen" :title="t('screen.messageDetails')" @close="detailsOpen = false">
    <p class="inline-notice-detail">{{ message }}</p>
  </BaseModal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import BaseModal from './base/BaseModal.vue'
import { useI18n } from '../i18n'

const props = defineProps({ message: { type: String, required: true }, type: { type: String, default: 'error' } })
const { t } = useI18n()
const detailsOpen = ref(false)
const hasDetails = computed(() => props.message.length > 96 || props.message.includes('\n'))
const summary = computed(() => hasDetails.value ? `${props.message.slice(0, 64).replace(/\s+/g, ' ')}…` : props.message)
watch(() => props.message, () => { detailsOpen.value = false })
</script>

<style scoped>
.inline-notice.error-banner, .inline-notice.ok-banner { display: flex; align-items: center; gap: 8px; margin: 8px 0; padding: 8px 10px; border-radius: 8px; font-size: 13px; line-height: 1.5; }
.inline-notice-summary { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.inline-notice > .material-symbols-outlined { flex: 0 0 auto; font-size: 18px; }
.inline-notice > button { flex: 0 0 auto; }
.inline-notice-detail { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
