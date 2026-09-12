<template>
  <slot v-if="!error" />
  <section v-else class="app-error-boundary" role="alert">
    <span class="material-symbols-outlined app-error-icon" aria-hidden="true">error</span>
    <h1>{{ t('errorBoundary.title') }}</h1>
    <p>{{ t('errorBoundary.description') }}</p>
    <div class="app-error-actions">
      <button class="primary" type="button" @click="retry">{{ t('errorBoundary.retry') }}</button>
      <button type="button" @click="goHome">{{ t('errorBoundary.home') }}</button>
    </div>
  </section>
</template>

<script setup>
import { onErrorCaptured, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from '../i18n'

const router = useRouter()
const { t } = useI18n()
const error = ref(null)

onErrorCaptured((exception) => {
  error.value = exception
  console.error('[PaperGuard] render error', exception)
  return false
})

function retry() {
  window.location.reload()
}

function goHome() {
  error.value = null
  router.push('/')
}
</script>
