<template>
  <BaseModal :open="open" :title="t('homeComponents.createTitle')" size="default" @close="$emit('close')">
    <h2 style="margin-top: 0">✨ {{ t('homeComponents.createTitle') }}</h2>

    <div class="field">
      <label>{{ t('homeComponents.thesisTitle') }} <span class="required-mark">*</span></label>
      <input
        v-model="form.title"
        :placeholder="t('homeComponents.titlePlaceholder')"
        @keyup.enter="handleCreate"
      />
    </div>

    <div class="field">
      <label>{{ t('homeComponents.chooseTemplate') }} <span class="required-mark">*</span></label>
      <select v-model="form.templateId">
        <option value="">{{ t('homeComponents.choose') }}</option>
        <option
          v-for="tpl in templates"
          :key="tpl.id"
          :value="tpl.id"
          :disabled="tpl.admission?.can_use_for_draft === false"
        >
          {{ tpl.name }}{{ tpl.admission?.can_use_for_draft === false ? ` · ${t('templateAdmission.blocked')}` : '' }}
        </option>
      </select>
      <p v-if="selectedTemplate?.admission?.can_use_for_draft === false" class="template-admission-hint blocked" role="alert">{{ t('templateAdmission.blockedUse') }}</p>
      <p v-else-if="selectedTemplate?.admission" class="template-admission-hint">{{ t('templateAdmission.draftAllowedOfficeReview') }}</p>
    </div>

    <div class="form-row" style="margin-top: 20px; justify-content: flex-end; gap: 10px">
      <button @click="$emit('close')">{{ t('homeComponents.cancel') }}</button>
      <button
        class="primary"
        @click="handleCreate"
        :disabled="!form.title || !form.templateId || selectedTemplate?.admission?.can_use_for_draft === false || creating"
      >
        {{ creating ? t('homeComponents.creating') : t('homeComponents.create') }}
      </button>
    </div>
  </BaseModal>
</template>

<script setup>
import { reactive, computed } from 'vue'
import BaseModal from '../base/BaseModal.vue'
import { useI18n } from '../../i18n'

const { t } = useI18n()

const props = defineProps({
  open: Boolean,
  templates: { type: Array, default: () => [] },
  creating: Boolean,
})
const emit = defineEmits(['close', 'create'])

const form = reactive({
  title: '',
  templateId: '',
})
const selectedTemplate = computed(() => props.templates.find((item) => item.id === form.templateId) || null)

function handleCreate() {
  if (!form.title || !form.templateId || selectedTemplate.value?.admission?.can_use_for_draft === false) return
  emit('create', {
    title: form.title,
    templateId: form.templateId
  })
  // Reset form
  form.title = ''
  form.templateId = ''
}
</script>

<style scoped>
.template-admission-hint { margin: 6px 0 0; color: var(--on-surface-variant); font-size: 12px; line-height: 1.5; }
.template-admission-hint.blocked { color: var(--error, #b42318); }
</style>
