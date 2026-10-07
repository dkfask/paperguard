<template>
  <section class="review-evidence">
    <div class="evidence-heading">
      <div>
        <p class="workflow-kicker">IMMUTABLE SNAPSHOT</p>
        <h4>{{ submission.version.content_snapshot?.title || t('reviewEvidence.unnamed') }}</h4>
      </div>
      <span>{{ t('reviewEvidence.attachments', { count: submission.version.attachment_refs?.length || 0 }) }}</span>
    </div>

    <div v-if="submission.candidate?.check_report?.summary" class="check-summary">
      <span>{{ t('reviewEvidence.autoChecks', { count: submission.candidate.check_report.summary.total || 0 }) }}</span>
      <span>{{ t('reviewEvidence.hardFailures', { count: submission.candidate.check_report.summary.hard_failures || 0 }) }}</span>
      <span>{{ t('reviewEvidence.warnings', { count: submission.candidate.check_report.summary.warning || 0 }) }}</span>
    </div>

    <div
      v-for="section in (submission.version.content_snapshot?.sections || [])"
      :key="section.key"
      class="snapshot-section"
    >
      <h5>{{ section.title || section.key }}</h5>
      <div v-for="block in (section.paragraphs || [])" :key="block.id" class="snapshot-block">
        <p v-if="block.type === 'paragraph'">{{ block.text || t('reviewEvidence.emptyParagraph') }}</p>
        <div v-else-if="block.type === 'table'" class="snapshot-table">
          <strong>{{ block.table?.caption || t('reviewEvidence.table') }}</strong>
          <div v-for="(row, rowIndex) in (block.table?.cells || [])" :key="rowIndex">{{ row.join(' | ') }}</div>
        </div>
        <div v-else-if="block.type === 'image'" class="snapshot-image">
          <img v-if="block.image?.url" :src="imageSrc(block.image.url)" :alt="block.image.caption || t('reviewEvidence.bodyImage')" />
          <span>{{ block.image?.caption || block.image?.url || t('reviewEvidence.image') }}</span>
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
import { onBeforeUnmount, reactive } from 'vue'
import { getPrivateImageUrl } from '../api'
import { useI18n } from '../i18n'

const { t } = useI18n()

const imageUrls = reactive(new Map())
const loadingImages = new Set()

function imageSrc(path) {
  if (!path || !path.startsWith('/media/')) return path
  if (!imageUrls.has(path) && !loadingImages.has(path)) {
    loadingImages.add(path)
    getPrivateImageUrl(path)
      .then((url) => imageUrls.set(path, url))
      .catch(() => imageUrls.set(path, ''))
      .finally(() => loadingImages.delete(path))
  }
  return imageUrls.get(path) || ''
}

onBeforeUnmount(() => {
  for (const url of imageUrls.values()) {
    if (url) URL.revokeObjectURL(url)
  }
})

defineProps({
  submission: { type: Object, required: true },
  formatDate: { type: Function, required: true },
  recordState: { type: Function, required: true },
})

defineEmits(['download-material'])
</script>

<style scoped>
.review-evidence { border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); padding: 15px 0; margin-bottom: 15px; }
.evidence-heading { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.evidence-heading h4 { margin: 0; font-size: 18px; }
.evidence-heading > span { color: var(--on-surface-variant); font-size: 11px; }
.check-summary { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; color: var(--on-surface-variant); font-size: 11px; }
.snapshot-section { border-top: 1px solid var(--border); padding: 12px 0 0; margin-top: 12px; }
.snapshot-section h5, .review-materials h5 { margin: 0 0 8px; font-size: 13px; }
.snapshot-block { border-left: 2px solid var(--surface-container); padding: 5px 10px; margin: 5px 0; }
.snapshot-block p { margin: 0; white-space: pre-wrap; line-height: 1.65; font-size: 13px; }
.snapshot-table { overflow-x: auto; color: var(--on-surface-variant); font-size: 12px; }
.snapshot-table strong { display: block; color: var(--on-surface); margin-bottom: 4px; }
.snapshot-table div { border-bottom: 1px solid var(--border); padding: 4px 0; white-space: nowrap; }
.snapshot-image { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 11px; }
.snapshot-image img { max-width: min(100%, 560px); max-height: 280px; object-fit: contain; object-position: left center; background: var(--surface-container-low); }
.review-materials { border-top: 1px solid var(--border); margin-top: 15px; padding-top: 12px; }
.review-material-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); }
.review-material-row strong, .review-material-row span { display: block; }
.review-material-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }
@media (max-width: 560px) { .review-material-row { align-items: flex-start; flex-direction: column; } .record-attachments { justify-content: flex-start; } }
</style>
