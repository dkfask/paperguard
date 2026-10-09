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
        >
          {{ tpl.name }}
        </option>
      </select>
    </div>

    <div class="form-row" style="margin-top: 20px; justify-content: flex-end; gap: 10px">
      <button @click="$emit('close')">{{ t('homeComponents.cancel') }}</button>
      <button
        class="primary"
        @click="handleCreate"
        :disabled="!form.title || !form.templateId || creating"
      >
        {{ creating ? t('homeComponents.creating') : t('homeComponents.create') }}
      </button>
    </div>
  </BaseModal>
</template>

<script setup>
import { reactive } from 'vue'
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

function handleCreate() {
  if (!form.title || !form.templateId) return
  emit('create', {
    title: form.title,
    templateId: form.templateId
  })
  // Reset form
  form.title = ''
  form.templateId = ''
}
</script>
