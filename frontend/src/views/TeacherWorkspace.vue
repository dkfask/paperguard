<template>
  <div class="workspace-page">
    <section class="workspace-hero">
      <div><p class="workspace-kicker">{{ t('workspace.teacherKicker') }}</p><h1>{{ t('workspace.teacherTitle') }}</h1><p>{{ t('workspace.teacherDescription') }}</p></div>
      <div class="workspace-identity"><span class="material-symbols-outlined" aria-hidden="true">rate_review</span><div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ t('role.teacher') }}</span></div></div>
    </section>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <AsyncState v-if="loading" state="loading" :title="t('workspace.syncingGuidance')" :detail="t('workspace.teacherLoading')" />
    <AsyncState v-else-if="loadError" state="error" :title="t('workspace.loadFailed')" :detail="loadError" :action-label="t('common.retry')" @action="load" />
    <template v-else>
      <section class="workspace-stats" :aria-label="t('workspace.guidanceSummary')"><div><span>{{ t('workspace.pendingSubmissions') }}</span><strong>{{ reviewTasks.length }}</strong><small>{{ t('workspace.thesesToReview') }}</small></div><div><span>{{ t('workspace.materialsPending') }}</span><strong>{{ processInbox.length }}</strong><small>{{ t('workspace.processInbox') }}</small></div><div><span>{{ t('workspace.activeStages') }}</span><strong>{{ activeTasks.length }}</strong><small>{{ t('workspace.currentStudentTasks') }}</small></div><div><span>{{ t('workspace.unreadMessages') }}</span><strong>{{ notifications.unread || 0 }}</strong><small>{{ t('workspace.inboxReminder') }}</small></div></section>
      <div class="workspace-layout">
        <main class="workspace-main">
          <section class="panel workspace-focus-panel workspace-review-focus"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.reviewDesk') }}</p><h2>{{ t('workspace.priorityReview') }}</h2></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">fact_check</span></div><ReviewQueue :items="reviewTasks" /><div class="form-actions workspace-panel-actions"><RouterLink class="small primary" :to="{ path: '/workflow', query: { panel: 'review' } }"><span class="material-symbols-outlined" aria-hidden="true">rate_review</span>{{ t('workspace.openReviewCenter') }}</RouterLink></div></section>
          <section class="panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.studentProgressKicker') }}</p><h2>{{ t('workspace.studentProgress') }}</h2></div><RouterLink class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'progress' } }">{{ t('workspace.viewAll') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div><div v-if="activeTasks.length" class="workspace-task-list"><TaskCard v-for="task in activeTasks.slice(0, 8)" :key="task.id" :task="task" icon="person" to="/workflow?panel=progress" :action-label="t('workspace.viewStudentProgress')" /></div><AsyncState v-else state="empty" :title="t('workspace.noStudentTasks')" :detail="t('workspace.noStudentTasksDetail')" /></section>
          <section class="panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.materialReview') }}</p><h2>{{ t('sidebar.materials') }}</h2></div><RouterLink class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'materials' } }">{{ t('workspace.openInbox') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div><div v-if="processInbox.length" class="workspace-material-list"><div v-for="record in processInbox.slice(0, 6)" :key="record.id" class="workspace-material-row"><div><strong>{{ record.artifact_name }}</strong><span>{{ record.student_id || record.cohort_id }} · {{ t('workspace.waitingFor') }} {{ record.pending_approvals?.join('、') || t('workspace.approval') }}</span></div><StatusBadge status="submitted" :label="t('workspace.pendingApproval')" /></div></div><AsyncState v-else state="empty" :title="t('workspace.materialInboxEmpty')" :detail="t('workspace.materialInboxEmptyDetail')" /></section>
        </main>
        <aside class="workspace-side"><NotificationInbox :items="notifications.items" :unread="notifications.unread" @read="readNotification" /><section class="panel workspace-shortcuts"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.shortcuts') }}</p><h3>{{ t('workspace.commonEntries') }}</h3></div></div><RouterLink :to="{ path: '/workflow', query: { panel: 'review' } }"><span class="material-symbols-outlined" aria-hidden="true">rate_review</span>{{ t('workspace.submitReview') }}</RouterLink><RouterLink :to="{ path: '/workflow', query: { panel: 'progress' } }"><span class="material-symbols-outlined" aria-hidden="true">groups</span>{{ t('workspace.studentProgress') }}</RouterLink><RouterLink to="/policies"><span class="material-symbols-outlined" aria-hidden="true">menu_book</span>{{ t('sidebar.policies') }}</RouterLink></section></aside>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { auth } from '../auth'
import { fetchMyTasks, fetchProcessRecordInbox, fetchWorkflowNotifications, markWorkflowNotificationRead } from '../api'
import AsyncState from '../components/AsyncState.vue'
import NotificationInbox from '../components/NotificationInbox.vue'
import ReviewQueue from '../components/ReviewQueue.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TaskCard from '../components/TaskCard.vue'
import { useI18n } from '../i18n'

const { t } = useI18n()

const loading = ref(true)
const error = ref('')
const loadError = ref('')
const tasks = ref([])
const processInbox = ref([])
const notifications = ref({ items: [], unread: 0 })
const reviewTasks = computed(() => tasks.value.filter((item) => item.state === 'submitted'))
const activeTasks = computed(() => tasks.value.filter((item) => !['approved', 'archived', 'exited', 'withdrawn'].includes(item.state)))

async function load() {
  loading.value = true; error.value = ''; loadError.value = ''
  try {
    const [taskData, inboxData, notificationData] = await Promise.all([fetchMyTasks(), fetchProcessRecordInbox(), fetchWorkflowNotifications()])
    tasks.value = taskData; processInbox.value = inboxData; notifications.value = notificationData
  } catch (exception) { loadError.value = exception.message } finally { loading.value = false }
}

async function readNotification(item) {
  if (item.read_at) return
  try { await markWorkflowNotificationRead(item.id); notifications.value = await fetchWorkflowNotifications() } catch (exception) { error.value = exception.message }
}

onMounted(load)

</script>
