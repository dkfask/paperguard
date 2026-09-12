<template>
  <div class="editor-page">
    <div v-if="loading" class="editor-state">{{ t('common.loading') }}</div>
    <div v-else-if="error && !editor" class="editor-state error-banner" role="alert">{{ error }}</div>
    <template v-else>
      <header class="editor-header">
        <div>
          <RouterLink to="/document-projects" class="back-link">
            <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>
            {{ t('documentProjects.back') }}
          </RouterLink>
          <input v-model="draft.title" class="title-input" :aria-label="t('documentProjects.projectTitle')" maxlength="300" @input="scheduleSave" />
          <p class="editor-subtitle">{{ editor.template.name }} · {{ editor.package.document_type_id }} · {{ editor.project.template_version_id }}</p>
        </div>
        <div class="save-status" :class="{ saving }" role="status" aria-live="polite">
          <span class="status-dot" :class="saving ? 'working' : 'ok'"></span>
          {{ saving ? t('documentProjects.saving') : t('documentProjects.saved', { revision: draft.revision }) }}
        </div>
      </header>

      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

      <div class="editor-layout">
        <aside class="editor-aside">
          <div class="aside-block">
            <p class="workspace-kicker">{{ t('documentProjects.snapshot') }}</p>
            <dl class="snapshot-list">
              <div><dt>{{ t('documentProjects.templateVersion') }}</dt><dd>{{ draft.template_version_id }}</dd></div>
              <div><dt>{{ t('documentProjects.specVersion') }}</dt><dd>{{ draft.spec_version_id }}</dd></div>
              <div><dt>{{ t('documentProjects.renderer') }}</dt><dd>{{ draft.renderer_version }}</dd></div>
              <div><dt>{{ t('documentProjects.ruleEngine') }}</dt><dd>{{ draft.rule_engine_version }}</dd></div>
            </dl>
          </div>
          <div class="aside-block">
            <p class="workspace-kicker">{{ t('documentProjects.rules') }}</p>
            <ul class="rule-list">
              <li v-for="rule in editor.spec_version.rules" :key="rule.id">
                <span class="rule-kind" :class="rule.kind">{{ rule.kind }}</span>
                <span>{{ rule.description || rule.id }}</span>
              </li>
            </ul>
          </div>
        </aside>

        <main class="editor-main">
          <section v-if="editor.editor_schema.fields.length" class="editor-section">
            <div class="section-heading"><div><p class="workspace-kicker">{{ t('documentProjects.fieldsEyebrow') }}</p><h2>{{ t('documentProjects.fieldsTitle') }}</h2></div></div>
            <div class="field-grid">
              <label v-for="field in editor.editor_schema.fields" :key="field.key" class="document-field">
                <span>{{ field.label || field.key }} <em v-if="field.required">{{ t('documentProjects.required') }}</em></span>
                <textarea v-if="field.type === 'multiline'" v-model="draft.field_values[field.key]" rows="3" @input="scheduleSave"></textarea>
                <input v-else v-model="draft.field_values[field.key]" :type="field.type === 'date' ? 'date' : 'text'" @input="scheduleSave" />
              </label>
            </div>
          </section>

          <section v-for="section in editableSections" :key="section.key" class="editor-section">
            <div class="section-heading">
              <div><p class="workspace-kicker">{{ section.kind }}</p><h2>{{ section.title || section.key }}</h2></div>
              <button class="icon-action" type="button" :title="t('documentProjects.addBlock')" :aria-label="t('documentProjects.addBlock')" @click="addBlock(section.key)">
                <span class="material-symbols-outlined" aria-hidden="true">add</span>
              </button>
            </div>
            <div class="block-list">
              <article v-for="(block, index) in draft.sections[section.key]" :key="block.id" class="content-block">
                <div class="block-label"><span>{{ t('documentProjects.block') }} {{ index + 1 }}</span><span>{{ block.kind }}</span></div>
                <textarea v-model="block.text" :aria-label="`${section.title || section.key} ${index + 1}`" rows="7" @input="scheduleSave"></textarea>
              </article>
            </div>
          </section>
        </main>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../i18n'
import { getDocumentProjectEditor, updateDocumentProject } from '../api.js'

const route = useRoute()
const { t } = useI18n()
const editor = ref(null)
const draft = reactive({ title: '', revision: 0, field_values: {}, sections: {} })
const loading = ref(true)
const saving = ref(false)
const error = ref('')
let saveTimer = null

const editableSections = computed(() => (editor.value?.editor_schema.sections || []).filter((section) => draft.sections[section.key]))

function applyProject(project) {
  draft.title = project.title || ''
  draft.revision = project.revision
  draft.field_values = JSON.parse(JSON.stringify(project.field_values || {}))
  draft.sections = JSON.parse(JSON.stringify(project.sections || {}))
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    editor.value = await getDocumentProjectEditor(route.params.id)
    applyProject(editor.value.project)
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(save, 500)
}

async function save() {
  saveTimer = null
  saving.value = true
  error.value = ''
  try {
    const updated = await updateDocumentProject(route.params.id, {
      title: draft.title,
      field_values: draft.field_values,
      sections: draft.sections,
      base_revision: draft.revision,
    })
    applyProject(updated)
  } catch (exception) {
    error.value = exception.message
  } finally {
    saving.value = false
  }
}

function addBlock(sectionKey) {
  const id = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
  draft.sections[sectionKey].push({ id, kind: 'paragraph', text: '', data: {}, order: draft.sections[sectionKey].length })
  scheduleSave()
}

onMounted(load)
onBeforeUnmount(() => { if (saveTimer) clearTimeout(saveTimer) })
</script>

<style scoped>
.editor-page { max-width: 1280px; margin: 0 auto; padding: 22px 20px 56px; }
.editor-state { display: grid; place-items: center; min-height: 50vh; color: var(--on-surface-variant); }
.editor-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; padding: 10px 0 22px; border-bottom: 1px solid var(--border); }
.back-link { display: inline-flex; align-items: center; gap: 5px; color: var(--primary); font-size: 13px; text-decoration: none; }
.back-link .material-symbols-outlined { font-size: 18px; }
.title-input { display: block; width: min(720px, 100%); margin: 14px 0 6px; padding: 0; border: 0; outline: 0; background: transparent; color: var(--on-surface); font-size: 30px; font-weight: 700; }
.editor-subtitle { margin: 0; color: var(--on-surface-variant); font-size: 13px; }
.save-status { display: flex; align-items: center; gap: 7px; flex: none; color: var(--on-surface-variant); font-size: 12px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--success, #2f7d4a); }
.status-dot.working { background: var(--tertiary, #a56a00); }
.editor-layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 30px; padding-top: 26px; }
.editor-aside { align-self: start; position: sticky; top: 20px; display: grid; gap: 22px; }
.aside-block { padding-bottom: 18px; border-bottom: 1px solid var(--border); }
.snapshot-list { display: grid; gap: 10px; margin: 12px 0 0; }
.snapshot-list div { display: grid; gap: 3px; }
.snapshot-list dt { color: var(--on-surface-variant); font-size: 11px; }
.snapshot-list dd { margin: 0; font-size: 12px; overflow-wrap: anywhere; }
.rule-list { display: grid; gap: 9px; margin: 12px 0 0; padding: 0; list-style: none; }
.rule-list li { display: flex; gap: 7px; align-items: flex-start; font-size: 12px; line-height: 1.45; }
.rule-kind { flex: none; padding: 2px 5px; border-radius: 4px; background: color-mix(in srgb, var(--primary) 12%, var(--surface)); color: var(--primary); font-size: 10px; }
.rule-kind.manual { background: color-mix(in srgb, var(--tertiary) 13%, var(--surface)); color: var(--tertiary); }
.editor-main { min-width: 0; }
.editor-section { padding-bottom: 30px; margin-bottom: 26px; border-bottom: 1px solid var(--border); }
.section-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 14px; }
.section-heading h2 { margin: 3px 0 0; font-size: 20px; }
.field-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.document-field { display: grid; gap: 6px; color: var(--on-surface); font-size: 13px; font-weight: 600; }
.document-field em { color: var(--tertiary); font-size: 11px; font-style: normal; font-weight: 400; }
.document-field input, .document-field textarea, .content-block textarea { width: 100%; box-sizing: border-box; padding: 10px 11px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--on-surface); font: inherit; font-weight: 400; resize: vertical; }
.block-list { display: grid; gap: 12px; }
.content-block { padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
.block-label { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 8px; color: var(--on-surface-variant); font-size: 11px; }
.icon-action { display: inline-grid; place-items: center; width: 34px; height: 34px; padding: 0; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--primary); cursor: pointer; }
.icon-action:hover { border-color: var(--primary); }
@media (max-width: 780px) { .editor-page { padding: 14px 14px 42px; } .editor-header { align-items: flex-start; flex-direction: column; } .title-input { font-size: 26px; } .editor-layout { display: block; } .editor-aside { position: static; margin-bottom: 26px; } .field-grid { grid-template-columns: 1fr; } }
</style>