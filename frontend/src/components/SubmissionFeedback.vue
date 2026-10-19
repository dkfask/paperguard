<template>
  <section class="submission-feedback" :aria-label="t('workflow.reviewFeedback')">
    <h4>{{ t('workflow.reviewFeedback') }}</h4>
    <div v-if="detail.review" class="review-result">
      <strong>{{ t(`submission.${detail.review.decision}`) }}</strong>
      <small>{{ formatDate(detail.review.created_at) }}</small>
      <p v-if="detail.review.reason">{{ detail.review.reason }}</p>
    </div>
    <p v-else class="hint">{{ t('workflow.noReviewYet') }}</p>
    <article v-for="comment in detail.comments || []" :key="comment.id" class="feedback-comment">
      <strong>{{ comment.severity === 'blocking' ? t('workflow.blockingIssue') : t('workflow.normalSuggestion') }}</strong>
      <small>{{ formatDate(comment.created_at) }} · {{ t(`comment.${comment.status}`) }}</small>
      <p>{{ comment.body }}</p>
      <div v-for="response in comment.responses || []" :key="response.id" class="comment-response">
        <small>{{ t('role.student') }} · {{ formatDate(response.created_at) }}</small>
        <p>{{ response.body }}</p>
      </div>
      <form v-if="canReply && comment.status !== 'resolved'" @submit.prevent="reply(comment)">
        <label>{{ t('workflow.replyComment') }}<textarea v-model="drafts[comment.id]" rows="2" required /></label>
        <button class="small" :disabled="Boolean(busy) || !drafts[comment.id]?.trim()">{{ busy === comment.id ? t('common.saving') : t('workflow.sendReply') }}</button>
      </form>
    </article>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
  </section>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { respondSubmissionComment } from '../api'
import { useI18n } from '../i18n'

const props = defineProps({ detail: { type: Object, required: true }, canReply: Boolean })
const emit = defineEmits(['replied'])
const { t, formatDate } = useI18n()
const drafts = reactive({})
const busy = ref('')
const error = ref('')
async function reply(comment) {
  const body = drafts[comment.id]?.trim()
  if (!props.canReply || !body || busy.value) return
  busy.value = comment.id; error.value = ''
  try {
    const updated = await respondSubmissionComment(comment.id, body)
    drafts[comment.id] = ''
    emit('replied', updated)
  } catch (e) { error.value = e.message } finally { busy.value = '' }
}
</script>

<style scoped>
.submission-feedback { border-top: 1px solid var(--border); padding: 16px 0; }
.submission-feedback h4 { margin: 0 0 12px; }
.review-result, .feedback-comment { padding: 12px; background: var(--surface-container-low); border-left: 3px solid var(--primary); margin-top: 10px; }
small { display: block; color: var(--on-surface-variant); margin-top: 5px; }
p { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6; }
form, label { display: grid; gap: 8px; }
textarea { width: 100%; }
form button { justify-self: end; }
.comment-response { padding-left: 12px; border-left: 2px solid var(--border); margin-top: 12px; }
</style>
