<template>
  <section class="review-evidence">
    <div class="evidence-heading">
      <div>
        <p class="workflow-kicker">IMMUTABLE SNAPSHOT</p>
        <h4>{{ submission.version.content_snapshot?.title || t('reviewEvidence.unnamed') }}</h4>
      </div>
      <div class="evidence-actions">
        <span>{{ t('reviewEvidence.attachments', { count: submission.version.attachment_refs?.length || 0 }) }}</span>
        <button v-if="documentDownloadable" type="button" class="small" data-test="download-submission-document" @click="$emit('download-document')">
          <span class="material-symbols-outlined" aria-hidden="true">download</span>{{ t('reviewEvidence.downloadSubmittedWord') }}
        </button>
        <button v-if="documentDownloadable && pdfDownloadable" type="button" class="small" data-test="download-submission-pdf" @click="$emit('download-pdf')">
          <span class="material-symbols-outlined" aria-hidden="true">download</span>{{ t('reviewEvidence.downloadOptionalPdf') }}
        </button>
      </div>
    </div>

    <div v-if="submission.candidate?.check_report?.summary" class="check-summary">
      <span>{{ t('reviewEvidence.autoChecks', { count: submission.candidate.check_report.summary.total || 0 }) }}</span>
      <span>{{ t('reviewEvidence.hardFailures', { count: submission.candidate.check_report.summary.hard_failures || 0 }) }}</span>
      <span>{{ t('reviewEvidence.warnings', { count: submission.candidate.check_report.summary.warning || 0 }) }}</span>
      <span v-if="submission.candidate.check_report.summary.not_checked">{{ t('reviewEvidence.unchecked', { count: submission.candidate.check_report.summary.not_checked }) }}</span>
    </div>

    <section v-if="submission.candidate?.check_report?.quality_advisories?.length" class="quality-advisories" data-test="review-quality-advisories">
      <h5>{{ t('writer.referenceQuality') }}</h5>
      <p v-for="(item, index) in submission.candidate.check_report.quality_advisories" :key="item.rule_id || index">{{ item.message }}</p>
    </section>

    <section v-if="coverFields.length" class="snapshot-section" data-test="review-cover">
      <h5>{{ t('writer.coverInfo') }}</h5>
      <dl class="snapshot-cover">
        <div v-for="field in coverFields" :key="field.key">
          <dt>{{ field.label }}</dt>
          <dd>{{ field.value || t('home.notSet') }}</dd>
        </div>
      </dl>
    </section>

    <div
      v-for="section in (submission.version.content_snapshot?.sections || [])"
      :key="section.key"
      class="snapshot-section"
      v-show="section.type !== 'cover' || section.paragraphs?.length"
    >
      <h5>{{ section.title || section.key }}</h5>
      <div v-for="block in (section.paragraphs || [])" :key="block.id" class="snapshot-block" :class="{ selected: selectedBlockId === block.id }">
        <button v-if="selectable && block.id" type="button" class="tiny select-block" data-test="select-review-block"
                :aria-pressed="selectedBlockId === block.id" @click="selectWholeBlock(block, section)">
          <span class="material-symbols-outlined" aria-hidden="true">add_comment</span>{{ t('workflow.commentOnBlock') }}
        </button>
        <button v-if="selectable && selectedText?.blockId === block.id" type="button" class="tiny select-text" data-test="select-review-text"
                @mousedown.prevent @click="selectText">
          <span class="material-symbols-outlined" aria-hidden="true">format_quote</span>{{ t('workflow.commentOnSelectedText') }}
        </button>
        <p v-if="block.type === 'paragraph' && reviewRuns(block).length" @mouseup="captureTextSelection(block, section, $event)"
           @keyup="captureTextSelection(block, section, $event)"><component
             :is="reviewRunTag(block, run)" v-for="(run, index) in reviewRuns(block)" :key="index"
             :class="reviewRunClasses(block, run)" :style="{ backgroundColor: reviewRunHighlight(block, run) }">{{ run.text }}</component></p>
        <p v-else-if="block.type === 'paragraph'" @mouseup="captureTextSelection(block, section, $event)"
           @keyup="captureTextSelection(block, section, $event)">{{ reviewBlockText(block) || t('reviewEvidence.emptyParagraph') }}</p>
        <div v-else-if="block.type === 'table'" class="snapshot-table" tabindex="0" role="region" :aria-label="block.table?.caption || t('reviewEvidence.table')">
          <strong>{{ block.table?.caption || t('reviewEvidence.table') }}</strong>
          <table><tbody><tr v-for="(row, rowIndex) in (block.table?.cells || [])" :key="rowIndex">
            <td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td>
          </tr></tbody></table>
        </div>
        <div v-else-if="block.type === 'image'" class="snapshot-image">
          <img v-if="imageSrc(block)" :src="imageSrc(block)" :alt="block.image?.caption || t('reviewEvidence.bodyImage')" />
          <span v-if="imageUnavailable(block)" role="status">{{ t('reviewEvidence.imageUnavailable') }}</span>
          <span>{{ block.image?.caption || t('reviewEvidence.image') }}</span>
        </div>
      </div>
    </div>

    <div v-if="submission.process_records?.length" class="review-materials">
      <h5>{{ t('reviewEvidence.materials') }}</h5>
      <div v-for="record in submission.process_records" :key="record.id" class="review-material-row">
        <div>
          <strong>{{ record.artifact_name }}</strong>
          <span>{{ recordState(record.state) }} · {{ formatDate(record.updated_at || record.created_at) }}</span>
        </div>
        <div class="record-attachments">
          <button
            v-for="attachment in record.attachments"
            :key="attachment.id"
            class="tiny"
            @click.stop="$emit('download-material', attachment)"
          >
            <span class="material-symbols-outlined" aria-hidden="true">download</span>{{ attachment.name }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { getSubmissionImageUrl } from '../api'
import { useI18n } from '../i18n'
import { formatReference, referenceNumber } from '../utils/references'

const { t } = useI18n()
const props = defineProps({
  submission: { type: Object, required: true },
  formatDate: { type: Function, required: true },
  recordState: { type: Function, required: true },
  selectable: { type: Boolean, default: false },
  selectedBlockId: { type: String, default: '' },
  documentDownloadable: { type: Boolean, default: false },
  pdfDownloadable: { type: Boolean, default: false },
})

const imageUrls = reactive(new Map())
const loadingImages = new Set()
const selectedText = ref(null)
const coverFields = computed(() => {
  const snapshot = props.submission.version?.content_snapshot || {}
  const meta = snapshot.meta || {}
  const template = snapshot.template_snapshot || snapshot.template_source_snapshot || {}
  const fields = Array.isArray(template.meta_fields) ? template.meta_fields : []
  const labels = new Map(fields.filter(field => field.key).map(field => [field.key, field.label || field.key]))
  const keys = [...new Set([...labels.keys(), ...Object.keys(meta)])]
  return keys.map(key => ({ key, label: labels.get(key) || key, value: meta[key] || '' }))
})
let disposed = false

const emit = defineEmits(['download-material', 'download-document', 'download-pdf', 'select-block'])

watch(() => [props.submission.submission?.id, props.submission.version?.id], () => { selectedText.value = null })

function selectWholeBlock(block, section) {
  selectedText.value = null
  emit('select-block', { blockId: block.id, sectionTitle: section.title || section.key,
    text: reviewBlockText(block) || block.image?.caption || block.table?.caption || '' })
}

function reviewBlockText(block) {
  return block.reference ? formatReference(block.reference, referenceNumber(block)) : block.text || ''
}

function reviewRuns(block) {
  if (block.reference || !block.text) return []
  const runs = Array.isArray(block.inline_runs) && block.inline_runs.length ? block.inline_runs : [{ text: block.text }]
  if (runs.some(run => typeof run.text !== 'string') || runs.map(run => run.text).join('') !== block.text) return []
  const formatted = run => ['bold', 'italic', 'underline', 'highlight', 'vert_align'].some(key => run[key])
  return formatted(block) || runs.some(formatted) ? runs : []
}

function reviewRunTag(block, run) {
  const alignment = run.vert_align ?? block.vert_align
  return alignment === 'superscript' ? 'sup' : alignment === 'subscript' ? 'sub' : 'span'
}

function reviewRunClasses(block, run) {
  return {
    'review-bold': run.bold || block.bold,
    'review-italic': run.italic || block.italic,
    'review-underline': run.underline || block.underline,
  }
}

function reviewRunHighlight(block, run) {
  const color = String(run.highlight ?? block.highlight ?? '')
  const wordColors = {
    green: 'lime', darkblue: 'navy', darkcyan: 'teal', darkgreen: 'green',
    darkmagenta: 'purple', darkred: 'maroon', darkyellow: 'olive', lightgray: 'silver', darkgray: 'gray',
  }
  return wordColors[color.toLowerCase()] || (/^(?:[a-z]{1,32}|#[0-9a-f]{3,8})$/i.test(color) ? color : undefined)
}

function captureTextSelection(block, section, event) {
  selectedText.value = null
  if (!props.selectable || !block.id || !block.text || block.reference) return
  const selection = window.getSelection()
  if (!selection || selection.isCollapsed || selection.rangeCount !== 1) return
  const range = selection.getRangeAt(0)
  const paragraph = event.currentTarget
  if (!paragraph.contains(range.startContainer) || !paragraph.contains(range.endContainer)) return
  const quote = range.toString()
  const quoteLength = Array.from(quote).length
  if (!quote.trim() || quoteLength > 4000) return
  const prefix = document.createRange()
  prefix.selectNodeContents(paragraph)
  prefix.setEnd(range.startContainer, range.startOffset)
  const start = Array.from(prefix.toString()).length
  const end = start + quoteLength
  if (Array.from(block.text).slice(start, end).join('') !== quote) return
  selectedText.value = { blockId: block.id, sectionTitle: section.title || section.key,
    text: quote, quotedText: quote, textStart: start, textEnd: end }
}

function selectText() {
  if (!selectedText.value) return
  emit('select-block', selectedText.value)
  selectedText.value = null
}

function imageKey(block) {
  const submissionId = props.submission.submission?.id
  return submissionId && block.id ? JSON.stringify([submissionId, block.id]) : ''
}

function imageSrc(block) {
  const key = imageKey(block)
  if (!key) return ''
  if (!imageUrls.has(key) && !loadingImages.has(key)) {
    loadingImages.add(key)
    getSubmissionImageUrl(props.submission.submission.id, block.id)
      .then((url) => {
        if (disposed) URL.revokeObjectURL(url)
        else imageUrls.set(key, url)
      })
      .catch(() => { if (!disposed) imageUrls.set(key, '') })
      .finally(() => loadingImages.delete(key))
  }
  return imageUrls.get(key) || ''
}

function imageUnavailable(block) {
  const key = imageKey(block)
  return !key || (imageUrls.has(key) && !imageUrls.get(key))
}

onBeforeUnmount(() => {
  disposed = true
  for (const url of imageUrls.values()) {
    if (url) URL.revokeObjectURL(url)
  }
})

</script>

<style scoped>
.review-evidence { border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); padding: 15px 0; margin-bottom: 15px; }
.evidence-heading { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.evidence-heading h4 { margin: 0; font-size: 18px; }
.evidence-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; }
.evidence-actions > span { color: var(--on-surface-variant); font-size: 11px; }
.check-summary { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; color: var(--on-surface-variant); font-size: 11px; }
.quality-advisories { border-top: 1px solid var(--border); padding-top: 12px; margin-top: 12px; }
.quality-advisories h5 { margin: 0 0 8px; font-size: 13px; }
.quality-advisories p { margin: 4px 0; color: var(--on-surface-variant); font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.snapshot-section { border-top: 1px solid var(--border); padding: 12px 0 0; margin-top: 12px; }
.snapshot-section h5, .review-materials h5 { margin: 0 0 8px; font-size: 13px; }
.snapshot-cover { display: grid; gap: 6px; margin: 0; font-size: 13px; }
.snapshot-cover div { display: grid; grid-template-columns: minmax(72px, 120px) minmax(0, 1fr); gap: 12px; }
.snapshot-cover dt { color: var(--on-surface-variant); }
.snapshot-cover dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.snapshot-block { border-left: 2px solid var(--surface-container); padding: 5px 10px; margin: 5px 0; }
.snapshot-block.selected { border-left-color: var(--primary); background: var(--surface-container-low); }
.select-block { float: right; margin: 0 0 5px 8px; }
.select-text { float: right; margin: 0 0 5px 8px; }
.snapshot-block::after { content: ''; display: block; clear: both; }
.snapshot-block p { margin: 0; white-space: pre-wrap; line-height: 1.65; font-size: 13px; }
.review-bold { font-weight: bold; }
.review-italic { font-style: italic; }
.review-underline { text-decoration: underline; }
.snapshot-table { overflow-x: auto; color: var(--on-surface-variant); font-size: 12px; }
.snapshot-table strong { display: block; color: var(--on-surface); margin-bottom: 4px; }
.snapshot-table table { border-collapse: collapse; }
.snapshot-table td { min-width: 4em; border: 1px solid var(--border); padding: 4px 7px; white-space: pre-wrap; overflow-wrap: anywhere; }
.snapshot-image { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 11px; }
.snapshot-image img { max-width: min(100%, 560px); max-height: 280px; object-fit: contain; object-position: left center; background: var(--surface-container-low); }
.review-materials { border-top: 1px solid var(--border); margin-top: 15px; padding-top: 12px; }
.review-material-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); }
.review-material-row strong, .review-material-row span { display: block; }
.review-material-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }
@media (max-width: 560px) { .review-material-row { align-items: flex-start; flex-direction: column; } .record-attachments { justify-content: flex-start; } }
</style>
