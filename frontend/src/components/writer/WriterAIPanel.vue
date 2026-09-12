<template>
  <aside v-if="open" class="ai-panel context-panel">
    <div class="panel-header">
      <h3><span aria-hidden="true">🤖</span> {{ t('writer.aiAssistant') }}</h3>
      <button class="tiny" :title="t('common.close')" :aria-label="t('common.close')" @click="$emit('close')"><span class="material-symbols-outlined" aria-hidden="true">close</span></button>
    </div>

    <div class="ai-config-section">
      <h4>{{ t('writer.aiConfig') }}</h4>
      <div class="field">
        <label>API Base URL</label>
        <input
          :value="config.base_url"
          @input="updateConfig('base_url', $event.target.value)"
          placeholder="https://api.openai.com/v1"
        />
      </div>
      <div class="field">
        <label>API Key</label>
        <input
          type="password"
          :value="config.api_key"
          @input="updateConfig('api_key', $event.target.value)"
          placeholder="sk-..."
        />
      </div>
      <div class="field">
        <label>{{ t('writer.model') }}</label>
        <input
          :value="config.model"
          @input="updateConfig('model', $event.target.value)"
          placeholder="gpt-4o-mini"
        />
      </div>
      <button class="small primary" @click="$emit('save-config')" :disabled="saving">
        {{ saving ? t('common.saving') : t('writer.saveConfig') }}
      </button>
    </div>

    <div class="ai-actions-section">
      <h4>{{ t('writer.quickActions') }}</h4>
      <div class="action-buttons">
        <button class="action-btn" @click="$emit('ai-action', 'expand')">
          <span aria-hidden="true">📝</span> {{ t('writer.aiExpand') }}
        </button>
        <button class="action-btn" @click="$emit('ai-action', 'polish')">
          <span aria-hidden="true">✨</span> {{ t('writer.aiPolish') }}
        </button>
        <button class="action-btn" @click="$emit('ai-action', 'summarize')">
          <span aria-hidden="true">📋</span> {{ t('writer.aiSummarize') }}
        </button>
        <button class="action-btn" @click="$emit('ai-action', 'translate')">
          <span aria-hidden="true">🌐</span> {{ t('writer.aiTranslate') }}
        </button>
      </div>
    </div>

    <div v-if="result" class="ai-result-section">
      <h4>{{ t('writer.aiResult') }}</h4>
      <div class="result-content">
        <pre>{{ result.text }}</pre>
      </div>
      <div class="result-actions">
        <button class="small" @click="$emit('apply-result')">{{ t('writer.applyResult') }}</button>
        <button class="small" @click="$emit('discard-result')">{{ t('writer.discardResult') }}</button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { useI18n } from '../../i18n'

const { t } = useI18n()

const props = defineProps({
  open: Boolean,
  config: { type: Object, default: () => ({}) },
  saving: Boolean,
  result: Object,
})

defineEmits([
  'close',
  'save-config',
  'ai-action',
  'apply-result',
  'discard-result',
])

function updateConfig(key, value) {
  // Parent handles config updates
}
</script>
