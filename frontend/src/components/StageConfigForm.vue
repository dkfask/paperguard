<template>
  <div class="stage-config-form">
    <div class="form-grid">
      <label>{{ t('stageConfig.key') }}<input v-model="form.key" :placeholder="t('stageConfig.keyPlaceholder')" /></label>
      <label>{{ t('stageConfig.name') }}<input v-model="form.name" :placeholder="t('stageConfig.namePlaceholder')" /></label>
      <label>{{ t('stageConfig.openAt') }}<input v-model="form.open_at" type="datetime-local" /></label>
      <label>{{ t('stageConfig.deadline') }}<input v-model="form.deadline_at" type="datetime-local" /></label>
      <label>{{ t('stageConfig.revisionDays') }}<input v-model.number="form.revision_days" type="number" min="0" max="365" /></label>
      <label>{{ t('stageConfig.template') }}<select v-model="form.template_id"><option v-for="tpl in templates" :key="tpl.id" :value="tpl.id">{{ tpl.name }}</option></select></label>
    </div>
    <div class="form-grid">
      <label>{{ t('stageConfig.manualChecks') }}<input v-model="form.manual_checks_text" :placeholder="t('stageConfig.manualChecksPlaceholder')" /></label>
      <label>{{ t('stageConfig.requiredMaterials') }}<input v-model="form.required_materials_text" :placeholder="t('stageConfig.requiredMaterialsPlaceholder')" /></label>
    </div>
    <div class="check-grid">
      <label><input v-model="form.is_final" type="checkbox" />{{ t('stageConfig.finalStage') }}</label>
      <label><input v-model="form.allow_early_write" type="checkbox" />{{ t('stageConfig.earlyWriting') }}</label>
    </div>
    <div class="form-actions">
      <button class="primary" :disabled="saving || !form.key || !form.name || !form.open_at || !form.deadline_at" @click="$emit('save')">
        <span class="material-symbols-outlined" aria-hidden="true">add_task</span>{{ t('stageConfig.saveDraft') }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '../i18n'

const { t } = useI18n()

defineProps({
  form: { type: Object, required: true },
  templates: { type: Array, default: () => [] },
  saving: { type: Boolean, default: false },
})

defineEmits(['save'])
</script>

<style scoped>
.stage-config-form { display: grid; gap: 2px; }
.form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }
.form-grid label { display: grid; gap: 5px; font-size: 12px; color: var(--on-surface-variant); }
.form-grid input, .form-grid select { width: 100%; min-width: 0; }
.form-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 15px; }
.check-grid { display: flex; gap: 20px; margin-top: 14px; font-size: 12px; color: var(--on-surface-variant); }
.check-grid input { accent-color: var(--primary); }
.stage-config-form .form-grid { margin-top: 0; }
@media (max-width: 900px) { .form-grid { grid-template-columns: 1fr 1fr; } }
@media (max-width: 560px) { .stage-config-form .form-grid { grid-template-columns: 1fr; } }
</style>
