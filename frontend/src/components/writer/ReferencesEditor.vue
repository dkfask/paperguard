<template>
  <div class="references-editor" data-test="references-editor">
    <div class="references-editor-head">
      <div>
        <h3>{{ t('writer.referencesEditorTitle') }}</h3>
        <p class="hint">{{ t('writer.referencesEditorDetail') }}</p>
      </div>
      <div class="reference-actions">
        <button class="small" type="button" @click="pasteOpen = !pasteOpen">
          <span class="material-symbols-outlined" aria-hidden="true">content_paste</span>
          {{ t('writer.pasteReferences') }}
        </button>
        <button class="small" type="button" @click="addReference">
          <span class="material-symbols-outlined" aria-hidden="true">add</span>
          {{ t('writer.addReference') }}
        </button>
      </div>
    </div>
    <div v-if="pasteOpen" class="reference-paste">
      <label class="field">
        {{ t('writer.pasteReferences') }}
        <textarea v-model="pasteText" data-test="reference-paste-input" rows="6" :placeholder="t('writer.referencePastePlaceholder')"></textarea>
      </label>
      <label class="field reference-paste-mode">
        {{ t('writer.referencePasteMode') }}
        <select v-model="pasteMode" data-test="reference-paste-mode">
          <option value="auto">{{ t('writer.referencePasteAuto') }}</option>
          <option value="lines">{{ t('writer.referencePasteLines') }}</option>
          <option value="numbered">{{ t('writer.referencePasteNumbered') }}</option>
        </select>
      </label>
      <p v-if="pasteError" class="paste-error" role="alert">{{ pasteError }}</p>
      <details v-if="pasteEntries.length" open>
        <summary>{{ t('writer.referencePastePreview', { count: pasteEntries.length }) }}</summary>
        <p data-test="reference-paste-recognition">{{ t('writer.referencePasteRecognition', { count: pasteAutoRecognized }) }}</p>
        <ol class="reference-paste-preview"><li v-for="(entry, index) in pasteEntries" :key="index"><pre>{{ entry }}</pre></li></ol>
      </details>
      <button type="button" class="small" data-test="reference-paste-add" :disabled="!pasteEntries.length || !!pasteError" @click="addPastedReferences">
        {{ t('writer.referencePasteAdd', { count: pasteEntries.length }) }}
      </button>
    </div>
    <div v-if="!paragraphBlocks.length" class="hint">{{ t('writer.referencesEmpty') }}</div>
    <article v-for="(block, index) in paragraphBlocks" :key="block.id" class="reference-card" :data-review-block-id="block.id">
      <header class="reference-card-head">
        <strong>{{ referenceNumber(block) ? `[${referenceNumber(block)}]` : t('writer.refNumberPending') }}</strong>
        <select :value="block.reference?.kind || 'legacy'" @change="setKind(block, $event.target.value)">
          <option v-if="!block.reference" value="legacy">{{ t('writer.refLegacy') }}</option>
          <option v-if="block.reference?.kind === 'unparsed'" value="unparsed">{{ t('writer.refUnparsed') }}</option>
          <option value="journal">{{ t('writer.refJournal') }}</option>
          <option value="book">{{ t('writer.refBook') }}</option>
          <option value="thesis">{{ t('writer.refThesis') }}</option>
          <option value="conference">{{ t('writer.refConference') }}</option>
          <option value="online">{{ t('writer.refOnline') }}</option>
          <option value="standard">{{ t('writer.refStandard') }}</option>
        </select>
        <button class="block-move-btn" type="button" :disabled="index === 0" :title="t('writer.moveUp')" @click="moveReference(index, -1)">
          <span class="material-symbols-outlined" aria-hidden="true">arrow_upward</span>
        </button>
        <button class="block-move-btn" type="button" :disabled="index === paragraphBlocks.length - 1" :title="t('writer.moveDown')" @click="moveReference(index, 1)">
          <span class="material-symbols-outlined" aria-hidden="true">arrow_downward</span>
        </button>
        <button class="tiny danger" type="button" @click="removeReference(index)">{{ t('common.remove') }}</button>
      </header>
      <textarea v-if="!block.reference" v-model="block.text" rows="3" :placeholder="t('writer.referencePlaceholder')" @input="changed"></textarea>
      <template v-else>
      <p v-if="block.reference.kind === 'unparsed'" class="hint">{{ t('writer.refNeedsFacts') }}</p>
      <div v-if="suggestionFor(block)" class="reference-suggestion">
        <p>{{ t('writer.referenceSuggested', { type: t(kindLabel(suggestionFor(block).kind)) }) }}</p>
        <ul><li v-for="(value, field) in suggestionFor(block).fields" :key="field">{{ t(fieldLabel(field)) }}: {{ value }}</li></ul>
        <button type="button" class="small" data-test="reference-apply-suggestion" @click="applySuggestion(block)">{{ t('writer.referenceApplySuggestion') }}</button>
      </div>
      <div class="reference-grid">
        <label v-for="field in fieldsFor(block.reference.kind)" :key="field" class="field">
          {{ t(fieldLabel(field)) }}
          <input v-model="block.reference[field]" :data-test="`reference-field-${field}`" :placeholder="placeholder(field)" @input="changeReferenceField(block, field)" />
        </label>
      </div>
      <fieldset v-if="fieldsFor(block.reference.kind).includes('year')" class="reference-year-fact">
        <legend>{{ t('writer.refYearFact') }}</legend>
        <label class="field">
          {{ t('writer.refYearEvidence') }}
          <input :value="block.reference.year_fact?.evidence || ''" @input="setYearEvidence(block, $event.target.value)" />
        </label>
        <div class="reference-language-actions">
          <span data-test="reference-year-status">{{ t(block.reference.year_fact?.status === 'confirmed' ? 'writer.refYearConfirmed' : 'writer.refYearUnconfirmed') }}</span>
          <button type="button" class="small" data-test="reference-year-confirm"
                  :disabled="!validYear(block.reference.year)"
                  @click="confirmYear(block)">{{ t('writer.refYearConfirm') }}</button>
        </div>
      </fieldset>
      <fieldset class="reference-language">
        <legend>{{ t('writer.refLanguage') }}</legend>
        <div class="reference-language-options">
          <label v-for="language in languageOptions" :key="language.code">
            <input type="checkbox" :data-test="`reference-language-${language.code}`"
                   :checked="block.reference.language_fact?.languages?.includes(language.code) || false"
                   @change="setLanguage(block, language.code, $event.target.checked)" />
            {{ t(language.label) }}
          </label>
        </div>
        <label class="field">
          {{ t('writer.refLanguageEvidence') }}
          <input :value="block.reference.language_fact?.evidence || ''" @input="setLanguageEvidence(block, $event.target.value)" />
        </label>
        <div class="reference-language-actions">
          <span data-test="reference-language-status">{{ t(block.reference.language_fact?.status === 'confirmed' ? 'writer.refLanguageConfirmed' : 'writer.refLanguageUnconfirmed') }}</span>
          <button type="button" class="small" data-test="reference-language-confirm"
                  :disabled="!block.reference.language_fact?.languages?.length"
                  @click="emit('confirm-language', block)">{{ t('writer.refLanguageConfirm') }}</button>
        </div>
      </fieldset>
      <label class="field reference-free-text">
        {{ t('writer.refLegacyText') }}
        <textarea v-model="block.reference.unformatted_text" rows="2" :placeholder="t('writer.referencePlaceholder')" @input="changeReferenceField(block, 'unformatted_text')"></textarea>
      </label>
      <details v-if="block.reference.original_pasted_text != null" class="reference-original">
        <summary>{{ t('writer.refOriginal') }}</summary>
        <pre>{{ block.reference.original_pasted_text }}</pre>
      </details>
      <div class="reference-preview"><span>{{ t('writer.refPreview') }}</span><code>{{ formatReference(block.reference, referenceNumber(block)) || t('writer.refIncomplete') }}</code></div>
      </template>
      <div v-if="!block.reference" class="reference-preview"><span>{{ t('writer.refPreview') }}</span><code>{{ formatReference(createReference('journal', block.text), referenceNumber(block)) || t('writer.refIncomplete') }}</code></div>
    </article>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useI18n } from '../../i18n'
import { createPastedReference, createReference, formatReference, referenceNumber, splitPastedReferences, suggestPastedReference, REFERENCE_FIELDS, REFERENCE_FIELD_LABELS } from '../../utils/references.js'

const props = defineProps({ paragraphs: { type: Array, default: () => [] } })
const emit = defineEmits(['change', 'confirm-language', 'confirm-year'])
const { t } = useI18n()
const languageOptions = [
  { code: 'zh', label: 'writer.refLanguageZh' }, { code: 'en', label: 'writer.refLanguageEn' },
  { code: 'ja', label: 'writer.refLanguageJa' }, { code: 'ko', label: 'writer.refLanguageKo' },
  { code: 'fr', label: 'writer.refLanguageFr' }, { code: 'de', label: 'writer.refLanguageDe' },
  { code: 'ru', label: 'writer.refLanguageRu' }, { code: 'oth', label: 'writer.refLanguageOther' },
]
const languageIdentityFields = new Set(['authors', 'title', 'source', 'collection', 'publisher', 'institution', 'organization', 'number', 'doi', 'url', 'unformatted_text'])
const yearIdentityFields = new Set([...languageIdentityFields, 'place', 'volume', 'issue', 'pages', 'publish_date', 'access_date'])
const paragraphBlocks = computed(() => (props.paragraphs || []).filter((block) => block.type === 'paragraph'))
const pasteOpen = ref(false)
const pasteText = ref('')
const pasteMode = ref('auto')
const pasteResult = computed(() => {
  try { return { entries: splitPastedReferences(pasteText.value, pasteMode.value), error: '' } }
  catch (error) { return { entries: [], error: error.message } }
})
const pasteEntries = computed(() => pasteResult.value.entries)
const pasteAutoRecognized = computed(() => pasteEntries.value.filter(entry => createPastedReference(entry).kind !== 'unparsed').length)
const pasteError = computed(() => pasteResult.value.error ? t(`writer.referencePasteError.${pasteResult.value.error}`) : '')
function fieldsFor(kind) { return kind === 'unparsed' ? [] : (REFERENCE_FIELDS[kind] || REFERENCE_FIELDS.journal) }
function fieldLabel(field) { return REFERENCE_FIELD_LABELS[field] || field }
function kindLabel(kind) { return ({ journal: 'writer.refJournal', book: 'writer.refBook', thesis: 'writer.refThesis', conference: 'writer.refConference', online: 'writer.refOnline', standard: 'writer.refStandard' })[kind] }
function suggestionFor(block) {
  if (block.reference?.original_pasted_text == null) return null
  const suggestion = suggestPastedReference(block.reference.original_pasted_text)
  if (!suggestion || !['unparsed', suggestion.kind].includes(block.reference.kind)) return null
  return block.reference.kind === 'unparsed' || Object.keys(suggestion.fields).some(field => !String(block.reference[field] || '').trim())
    ? suggestion : null
}
function applySuggestion(block) {
  const suggestion = suggestionFor(block)
  if (!suggestion) return
  let modified = false
  if (block.reference.kind === 'unparsed') { block.reference.kind = suggestion.kind; modified = true }
  for (const [field, value] of Object.entries(suggestion.fields)) {
    if (!String(block.reference[field] || '').trim()) { block.reference[field] = value; modified = true }
  }
  if (modified) { invalidateLanguage(block); invalidateYear(block) }
  changed()
}
function placeholder(field) { return ({ authors: '张三；李四', title: '题名', source: '期刊 / 来源', year: '2026', pages: '1-8', url: 'https://…' })[field] || '' }
function changed() { emit('change') }
function invalidateLanguage(block) {
  if (block.reference) delete block.reference.language_confirmation_requested
  const fact = block.reference?.language_fact
  if (fact) Object.assign(fact, { status: 'unconfirmed', confirmed_by: '', confirmed_at: '' })
}
function invalidateYear(block) {
  if (block.reference) delete block.reference.year_confirmation_requested
  const fact = block.reference?.year_fact
  if (fact) Object.assign(fact, { value: String(block.reference.year || '').trim(), status: 'unconfirmed', confirmed_by: '', confirmed_at: '' })
}
function changeReferenceField(block, field) {
  if (languageIdentityFields.has(field)) invalidateLanguage(block)
  if (field === 'year' || yearIdentityFields.has(field)) invalidateYear(block)
  changed()
}
function ensureLanguageFact(block) {
  if (!block.reference.language_fact) block.reference.language_fact = { languages: [], evidence: '', status: 'unconfirmed' }
  return block.reference.language_fact
}
function setLanguage(block, code, selected) {
  const fact = ensureLanguageFact(block)
  fact.languages = selected ? [...new Set([...fact.languages, code])].sort() : fact.languages.filter(value => value !== code)
  invalidateLanguage(block)
  changed()
}
function setLanguageEvidence(block, value) {
  const fact = ensureLanguageFact(block)
  fact.evidence = value
  invalidateLanguage(block)
  changed()
}
function ensureYearFact(block) {
  if (!block.reference.year_fact) block.reference.year_fact = { value: '', evidence: '', status: 'unconfirmed' }
  block.reference.year_fact.value = String(block.reference.year || '').trim()
  return block.reference.year_fact
}
function setYearEvidence(block, value) {
  ensureYearFact(block).evidence = value
  invalidateYear(block)
  changed()
}
function validYear(value) { return /^(19|20)\d{2}$/.test(String(value || '').trim()) }
function confirmYear(block) {
  if (!validYear(block.reference.year)) return
  ensureYearFact(block)
  emit('confirm-year', block)
}
function addReference() {
  props.paragraphs.push({ id: `ref-${Date.now()}-${Math.random().toString(16).slice(2)}`, type: 'paragraph', text: '', reference: createReference() })
  changed()
}
function addPastedReferences() {
  if (pasteError.value || !pasteEntries.value.length) return
  for (const text of pasteEntries.value) {
    props.paragraphs.push({ id: `ref-${Date.now()}-${Math.random().toString(16).slice(2)}`, type: 'paragraph', text: '', reference: createPastedReference(text) })
  }
  pasteText.value = ''
  pasteOpen.value = false
  changed()
}
function removeReference(index) {
  const block = paragraphBlocks.value[index]
  const actualIndex = props.paragraphs.indexOf(block)
  if (actualIndex >= 0) props.paragraphs.splice(actualIndex, 1)
  changed()
}
function setKind(block, kind) {
  if (kind !== 'legacy') {
    if (block.reference?.kind !== kind) { invalidateLanguage(block); invalidateYear(block) }
    block.reference = block.reference
      ? { ...block.reference, kind }
      : {
          ...createReference(kind, block.text),
          ...(String(block.text || '').trim() ? { original_pasted_text: block.text } : {}),
        }
    block.text = ''
  }
  changed()
}
function moveReference(index, offset) {
  const target = index + offset
  if (target < 0 || target >= paragraphBlocks.value.length) return
  const reordered = [...paragraphBlocks.value]
  const [item] = reordered.splice(index, 1)
  reordered.splice(target, 0, item)
  const positions = props.paragraphs.map((block, actualIndex) => block.type === 'paragraph' ? actualIndex : -1).filter(index => index >= 0)
  positions.forEach((position, paragraphIndex) => { props.paragraphs[position] = reordered[paragraphIndex] })
  changed()
}
</script>

<style scoped>
.references-editor { display: grid; gap: 14px; }
.references-editor-head, .reference-card-head { display: flex; align-items: center; gap: 10px; }
.references-editor-head { justify-content: space-between; }
.references-editor-head > button { flex-shrink: 0; white-space: nowrap; }
.reference-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.reference-paste { display: grid; gap: 10px; border: 1px solid var(--border, #d7dce4); padding: 12px; }
.reference-paste-mode { max-width: 260px; }
.reference-paste-preview { max-height: 240px; overflow: auto; padding-left: 28px; }
.reference-paste-preview pre, .reference-original pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
.paste-error { color: #b91c1c; margin: 0; }
.references-editor-head h3 { margin: 0 0 4px; }
.reference-card { border: 1px solid var(--border, #d7dce4); border-radius: 8px; padding: 14px; background: var(--surface, #fff); }
.reference-card-head { justify-content: space-between; margin-bottom: 12px; }
.reference-card-head select { margin-right: auto; }
.reference-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.reference-suggestion { margin: 10px 0; padding: 10px; border-left: 3px solid #3b82f6; background: #f1f5f9; }
.reference-suggestion p { margin: 0; }
.reference-suggestion ul { margin: 6px 0; padding-left: 22px; }
.reference-language { margin: 12px 0 0; padding: 10px; border: 1px solid var(--border, #d7dce4); }
.reference-language-options, .reference-language-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.reference-language-options label { display: inline-flex; align-items: center; gap: 4px; }
.reference-language .field { margin-top: 10px; }
.reference-language-actions { margin-top: 10px; }
.reference-free-text { margin-top: 10px; }
.reference-preview { display: grid; gap: 4px; margin-top: 10px; font-size: 12px; color: var(--muted); }
.reference-preview code { white-space: pre-wrap; color: var(--text, #1f2937); }
@media (max-width: 720px) { .reference-grid { grid-template-columns: 1fr; } }
</style>
