<template>
  <section class="route-state-page" role="status">
    <span class="material-symbols-outlined route-state-icon" aria-hidden="true">search_off</span>
    <p class="workspace-kicker">404</p>
    <h1>{{ t('state.notFound') }}</h1>
    <p>{{ t('notFound.description') }}</p>
    <div class="route-state-actions">
      <button class="primary" type="button" @click="router.push('/')">{{ t('notFound.home') }}</button>
      <button type="button" @click="router.back()">{{ t('notFound.back') }}</button>
    </div>
  </section>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useI18n } from '../i18n'

const router = useRouter()
const { t } = useI18n()
</script>

<style scoped>
.route-state-page { display: grid; justify-items: center; gap: 8px; min-height: 50vh; padding: 80px 20px; text-align: center; }
.route-state-icon { color: var(--primary); font-size: 48px; }
.route-state-page h1 { font-size: 24px; }
.route-state-page p:not(.workspace-kicker) { color: var(--on-surface-variant); }
.route-state-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
</style>
