<template>
  <div class="workspace-page ai-settings-page">
    <section class="workspace-hero"><div><p class="workspace-kicker">{{ t('aiSettings.kicker') }}</p><h1>{{ t('aiSettings.title') }}</h1><p>{{ t('aiSettings.description') }}</p></div><div class="workspace-identity"><span class="material-symbols-outlined" aria-hidden="true">tune</span><div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ t('role.admin') }}</span></div></div></section>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div><div v-if="notice" class="ok-banner" role="status">{{ notice }}</div>
    <AsyncState v-if="loading" state="loading" :title="t('aiSettings.loading')" :detail="t('aiSettings.loadingDetail')" />
    <template v-else><div class="workspace-layout"><main class="workspace-main"><section class="panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('aiSettings.modelKicker') }}</p><h2>{{ t('aiSettings.model') }}</h2><p>{{ configured ? (source === 'database' ? t('aiSettings.databaseSource') : t('aiSettings.envSource')) : t('aiSettings.notConfigured') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">smart_toy</span></div><div class="ai-disclosure" role="note"><p class="ai-disclosure-title"><span class="material-symbols-outlined" aria-hidden="true">public</span>{{ t('aiSettings.disclosureTitle') }}</p><p>{{ t('aiSettings.disclosureBody') }}</p><p v-if="dataRecipient" class="ai-disclosure-recipient">{{ t('aiSettings.disclosureRecipient', { host: dataRecipient }) }}</p></div><form class="ai-form" @submit.prevent="save"><label>{{ t('aiSettings.baseUrl') }}<input v-model.trim="form.base_url" type="url" required placeholder="https://api.openai.com/v1" /></label><label>{{ t('aiSettings.modelName') }}<input v-model.trim="form.model" required placeholder="gpt-4o-mini" /></label><label>API Key<input v-model="form.api_key" type="password" autocomplete="new-password" :placeholder="configured ? t('aiSettings.configuredKey', { key: maskedKey }) : t('aiSettings.keyPlaceholder')" /></label><p v-if="maskedKey" class="masked-key">{{ t('aiSettings.currentKey') }}: {{ maskedKey }}</p><div class="form-actions"><button class="small" type="button" :disabled="testing" @click="test">{{ testing ? t('aiSettings.testing') : t('aiSettings.test') }}</button><button class="small primary" type="submit" :disabled="saving">{{ saving ? t('aiSettings.saving') : t('aiSettings.save') }}</button><button v-if="source === 'database'" class="small danger" type="button" :disabled="saving" @click="clear">{{ t('aiSettings.restoreEnv') }}</button></div><p v-if="feedback" :class="feedbackOk ? 'preview-ok' : 'preview-error'" role="status">{{ feedback }}</p></form></section></main><aside class="workspace-side"><section class="panel workspace-shortcuts"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.governanceEntries') }}</p><h3>{{ t('aiSettings.related') }}</h3></div></div><RouterLink to="/operations"><span class="material-symbols-outlined" aria-hidden="true">monitor_heart</span>{{ t('route.operations') }}</RouterLink><RouterLink to="/audit"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('route.audit') }}</RouterLink></section></aside></div></template>
    <ConfirmDialog :open="confirmOpen" :title="t('aiSettings.restoreTitle')" :description="t('aiSettings.restoreDescription')" :confirm-label="t('aiSettings.restoreConfirm')" :busy="confirmBusy" :danger="true" @confirm="confirmClear" @cancel="confirmOpen = false" />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { auth } from '../auth'
import { clearAdminAiConfig, getAdminAiConfig, saveAdminAiConfig, testAdminAiConfig } from '../api'
import AsyncState from '../components/AsyncState.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { useI18n } from '../i18n'

const { t } = useI18n()
const loading = ref(true); const saving = ref(false); const testing = ref(false); const error = ref(''); const notice = ref(''); const feedback = ref(''); const feedbackOk = ref(false)
const configured = ref(false); const source = ref(''); const maskedKey = ref(''); const form = reactive({ base_url: '', model: '', api_key: '' }); const confirmOpen = ref(false); const confirmBusy = ref(false)
async function load() { loading.value = true; error.value = ''; try { const config = await getAdminAiConfig(); configured.value = config.configured; source.value = config.source || ''; maskedKey.value = config.api_key_masked || ''; form.base_url = config.base_url || ''; form.model = config.model || ''; form.api_key = '' } catch (exception) { error.value = exception.message } finally { loading.value = false } }
function valid() { if (!form.base_url || !form.model || (!form.api_key && !maskedKey.value)) { feedbackOk.value = false; feedback.value = configured.value ? t('aiSettings.validationConfigured') : t('aiSettings.validationRequired'); return false } return true }
async function test() { if (!valid()) return; testing.value = true; feedback.value = ''; try { const result = await testAdminAiConfig({ ...form }); feedbackOk.value = Boolean(result.ok); feedback.value = result.ok ? t('aiSettings.testSuccess', { model: result.model }) : (result.error || t('aiSettings.testFailed')) } catch (exception) { feedbackOk.value = false; feedback.value = exception.message } finally { testing.value = false } }
async function save() { if (!valid()) return; saving.value = true; error.value = ''; feedback.value = ''; try { await saveAdminAiConfig({ ...form }); notice.value = t('aiSettings.saved'); await load() } catch (exception) { error.value = exception.message } finally { saving.value = false } }
function clear() { confirmOpen.value = true }
async function confirmClear() { confirmBusy.value = true; try { await clearAdminAiConfig(); notice.value = t('aiSettings.restored'); confirmOpen.value = false; await load() } catch (exception) { error.value = exception.message } finally { confirmBusy.value = false } }
onMounted(load)

// 数据出境披露：把 Base URL 的主机名显式回显给管理员。
// 改一个 base_url 就等于把整份论文正文改道到另一个外部处理者——
// 这一点在填表时看不见，所以必须显式说出来（数据出境披露 S6）。
// 只取 host，不展示路径与密钥。
const dataRecipient = computed(() => {
  const raw = (form.base_url || '').trim()
  if (!raw) return ''
  try { return new URL(raw).host } catch { return '' }
})
</script>

<style scoped>
.ai-disclosure { display: grid; gap: 6px; margin-top: 16px; padding: 12px 14px; max-width: 720px; border: 1px solid var(--outline); border-left: 3px solid var(--primary); border-radius: 10px; background: var(--surface-container-low, rgba(127, 127, 127, 0.06)); font-size: 12px; line-height: 1.6; color: var(--on-surface-variant); }.ai-disclosure p { margin: 0; }.ai-disclosure-title { display: flex; align-items: center; gap: 6px; font-weight: 600; color: var(--on-surface); }.ai-disclosure-title .material-symbols-outlined { font-size: 18px; }.ai-disclosure-recipient { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
.ai-form { display: grid; gap: 13px; margin-top: 16px; max-width: 720px; }.ai-form label { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 12px; }.ai-form input { width: 100%; box-sizing: border-box; }.masked-key { margin: -4px 0 0; color: var(--on-surface-variant); font-size: 11px; }.form-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }.preview-ok { color: #087f5b; }.preview-error { color: var(--error); }
</style>
