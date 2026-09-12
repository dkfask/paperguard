<template>
  <div class="workspace-page">
    <section class="workspace-hero">
      <div>
        <p class="workspace-kicker">{{ t('workspace.studentKicker') }}</p>
        <h1>{{ t('workspace.studentTitle') }}</h1>
        <p>{{ t('workspace.studentDescription') }}</p>
      </div>
      <div class="workspace-identity">
        <span class="material-symbols-outlined" aria-hidden="true">school</span>
        <div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ t('role.student') }}</span></div>
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <AsyncState v-if="loading" state="loading" :title="t('workspace.syncing')" :detail="t('workspace.studentLoading')" />
    <template v-else>
      <section class="workspace-stats" :aria-label="t('workspace.summary')">
        <div><span>{{ t('workspace.openStages') }}</span><strong>{{ openTasks.length }}</strong><small>{{ t('workspace.incompleteTasks') }}</small></div>
        <div><span>{{ t('workspace.actionNeeded') }}</span><strong>{{ actionTasks.length }}</strong><small>{{ t('workspace.priorityItems') }}</small></div>
        <div><span>{{ t('workspace.submissionHistory') }}</span><strong>{{ submissions.length }}</strong><small>{{ t('workspace.traceableSubmissions') }}</small></div>
        <div><span>{{ t('workspace.unreadMessages') }}</span><strong>{{ notifications.unread || 0 }}</strong><small>{{ t('workspace.inboxReminder') }}</small></div>
      </section>

      <div class="workspace-layout">
        <main class="workspace-main">
          <section class="panel workspace-focus-panel">
            <div class="panel-heading">
              <div><p class="workspace-kicker">{{ t('workspace.nextActionKicker') }}</p><h2>{{ t('workspace.nextAction') }}</h2></div>
              <span class="material-symbols-outlined panel-icon" aria-hidden="true">flag</span>
            </div>
            <div v-if="nextTask" class="workspace-focus-content">
              <div class="workspace-focus-copy">
                <span class="workspace-overline">{{ nextTask.cohort_name }} · {{ nextTask.stage_name }}</span>
                <h3>{{ nextTask.project_title || t('task.unboundThesis') }}</h3>
                <p>{{ actionText(nextTask) }}</p>
                <DeadlineLabel :deadline="deadlineFor(nextTask)" :revision="Boolean(nextTask.revision_deadline_at)" :late="nextTask.is_late" />
              </div>
              <RouterLink class="primary workspace-action" :to="nextTask.project_id ? `/write/${nextTask.project_id}` : { path: '/workflow', query: { panel: 'progress' } }">
                <span class="material-symbols-outlined" aria-hidden="true">{{ nextTask.project_id ? 'edit_note' : 'open_in_new' }}</span>
                <span>{{ nextTask.project_id ? t('workspace.continueWriting') : t('workspace.viewStageTask') }}</span>
              </RouterLink>
            </div>
            <AsyncState v-else state="empty" :title="t('workspace.noPendingStage')" :detail="t('workspace.noPendingStageDetail')" />
          </section>

          <section class="panel">
            <div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.myTasksKicker') }}</p><h2>{{ t('workspace.stageTasks') }}</h2></div><RouterLink class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'progress' } }">{{ t('workspace.viewDetails') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div>
            <div v-if="tasks.length" class="workspace-task-list">
              <TaskCard v-for="task in tasks" :key="task.id" :task="task" :to="task.project_id ? `/write/${task.project_id}` : { path: '/workflow', query: { panel: 'progress' } }" :action-label="task.project_id ? t('workspace.openThesis') : t('workspace.viewStageTask')" />
            </div>
            <AsyncState v-else state="empty" :title="t('workspace.noStageTasks')" :detail="t('workspace.noStageTasksDetail')" />
          </section>

          <section class="panel">
            <div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.submissionHistoryKicker') }}</p><h2>{{ t('workspace.recentSubmissions') }}</h2></div><RouterLink class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'submissions' } }">{{ t('workspace.allRecords') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div>
            <div v-if="submissions.length" class="workspace-history-list">
              <div v-for="submission in submissions.slice(0, 5)" :key="submission.id" class="workspace-history-row"><div><strong>{{ submission.submission_number }}</strong><span>{{ formatDate(submission.submitted_at) }}</span></div><StatusBadge :status="submission.state" :label="submissionStateLabel(submission.state)" /></div>
            </div>
            <AsyncState v-else state="empty" :title="t('workspace.noSubmissions')" :detail="t('workspace.noSubmissionsDetail')" />
          </section>
        </main>
        <aside class="workspace-side"><NotificationInbox :items="notifications.items" :unread="notifications.unread" @read="readNotification" /><section class="panel workspace-shortcuts"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.shortcuts') }}</p><h3>{{ t('workspace.commonEntries') }}</h3></div></div><RouterLink :to="{ path: '/workflow', query: { panel: 'materials' } }"><span class="material-symbols-outlined" aria-hidden="true">folder_managed</span>{{ t('sidebar.materials') }}</RouterLink><RouterLink to="/policies"><span class="material-symbols-outlined" aria-hidden="true">menu_book</span>{{ t('sidebar.policies') }}</RouterLink></section></aside>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { auth } from '../auth'
import { useI18n } from '../i18n'
import { fetchMyTasks, fetchSubmissions, fetchWorkflowNotifications, markWorkflowNotificationRead } from '../api'
import AsyncState from '../components/AsyncState.vue'
import NotificationInbox from '../components/NotificationInbox.vue'
import TaskCard from '../components/TaskCard.vue'

const { t, formatDate: formatLocaleDate } = useI18n()

const loading = ref(true)
const error = ref('')
const tasks = ref([])
const submissions = ref([])
const notifications = ref({ items: [], unread: 0 })
const openTasks = computed(() => tasks.value.filter((item) => !['approved', 'archived', 'exited', 'withdrawn'].includes(item.state)))
const actionTasks = computed(() => openTasks.value.filter((item) => ['not_started', 'needs_revision', 'in_progress'].includes(item.state)))
const nextTask = computed(() => [...actionTasks.value].sort((a, b) => new Date(deadlineFor(a) || 0) - new Date(deadlineFor(b) || 0))[0] || openTasks.value[0])

async function load() {
  loading.value = true; error.value = ''
  try {
    const [taskData, submissionData, notificationData] = await Promise.all([fetchMyTasks(), fetchSubmissions(), fetchWorkflowNotifications()])
    tasks.value = taskData; submissions.value = submissionData; notifications.value = notificationData
  } catch (exception) { error.value = exception.message } finally { loading.value = false }
}

async function readNotification(item) {
  if (item.read_at) return
  try { await markWorkflowNotificationRead(item.id); notifications.value = await fetchWorkflowNotifications() } catch (exception) { error.value = exception.message }
}

function deadlineFor(task) { return task.revision_deadline_at || task.effective_deadline_at || task.initial_deadline_at }
function actionText(task) { return task.state === 'needs_revision' ? t('workspace.actionRevision') : task.state === 'not_started' ? t('workspace.actionStart') : t('workspace.actionContinue') }
function submissionStateLabel(state) { return t(`submission.${state}`) === `submission.${state}` ? state : t(`submission.${state}`) }
function formatDate(value) { return value ? formatLocaleDate(value, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-' }

onMounted(load)
</script>
