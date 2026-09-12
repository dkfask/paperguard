<template>
  <div class="workspace-page">
    <section class="workspace-hero"><div><p class="workspace-kicker">{{ auth.user?.role === 'admin' ? t('workspace.schoolKicker') : t('workspace.collegeKicker') }}</p><h1>{{ auth.user?.role === 'admin' ? t('workspace.schoolTitle') : t('workspace.collegeTitle') }}</h1><p>{{ t('workspace.adminDescription') }}</p></div><div class="workspace-identity"><span class="material-symbols-outlined" aria-hidden="true">admin_panel_settings</span><div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ auth.user?.role === 'admin' ? t('role.admin') : t('role.college_admin') }}</span></div></div></section>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <AsyncState v-if="loading" state="loading" :title="t('workspace.syncingOperations')" :detail="t('workspace.adminLoading')" />
    <template v-else>
      <section class="workspace-stats" :aria-label="t('workspace.operationsSummary')"><div><span>{{ t('workspace.managedCohorts') }}</span><strong>{{ cohorts.length }}</strong><small>{{ t('workspace.currentScope') }}</small></div><div><span>{{ t('workspace.activeCohorts') }}</span><strong>{{ activeCohorts.length }}</strong><small>{{ t('workspace.canContinue') }}</small></div><div><span>{{ t('workspace.pendingArchive') }}</span><strong>{{ pendingArchives.length }}</strong><small>{{ t('workspace.needsArtifacts') }}</small></div><div><span>{{ t('workspace.unreadMessages') }}</span><strong>{{ notifications.unread || 0 }}</strong><small>{{ t('workspace.inboxReminder') }}</small></div></section>
      <div class="workspace-layout">
        <main class="workspace-main">
          <section class="panel workspace-focus-panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.operationsControl') }}</p><h2>{{ t('workspace.todayTasks') }}</h2></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">task_alt</span></div><div class="workspace-operations-grid"><RouterLink class="workspace-operation-card" to="/cohorts"><span class="material-symbols-outlined" aria-hidden="true">calendar_month</span><strong>{{ t('workspace.cohortsStages') }}</strong><small>{{ t('workspace.cohortsStagesDetail') }}</small><span class="material-symbols-outlined arrow" aria-hidden="true">arrow_forward</span></RouterLink><RouterLink class="workspace-operation-card" :to="{ path: '/workflow', query: { panel: 'archives' } }"><span class="material-symbols-outlined" aria-hidden="true">inventory_2</span><strong>{{ t('workspace.submissionsArchives') }}</strong><small>{{ pendingArchives.length ? t('workspace.pendingItems', { count: pendingArchives.length }) : t('workspace.archiveStatus') }}</small><span class="material-symbols-outlined arrow" aria-hidden="true">arrow_forward</span></RouterLink><RouterLink class="workspace-operation-card" :to="{ path: '/workflow', query: { panel: 'reports' } }"><span class="material-symbols-outlined" aria-hidden="true">monitoring</span><strong>{{ t('workspace.reports') }}</strong><small>{{ t('workspace.reportDetail') }}</small><span class="material-symbols-outlined arrow" aria-hidden="true">arrow_forward</span></RouterLink></div></section>
          <section class="panel"><div class="panel-heading"><div><p class="workspace-kicker">COHORTS</p><h2>{{ t('workspace.cohortOverview') }}</h2></div><RouterLink class="workspace-text-link" to="/cohorts">{{ t('workspace.configureCohorts') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div><div v-if="cohorts.length" class="workspace-cohort-list"><article v-for="cohort in cohorts.slice(0, 8)" :key="cohort.id" class="workspace-cohort-row"><div><strong>{{ cohort.name }}</strong><span>{{ cohort.academic_year || t('workspace.academicYearMissing') }} · {{ cohort.member_ids?.length || 0 }} {{ t('workspace.students') }}</span></div><div><StatusBadge :status="cohort.status" :label="cohortStateLabel(cohort.status)" /><RouterLink class="icon-button workspace-row-link" to="/cohorts" :aria-label="t('workspace.configureNamed', { name: cohort.name })" :title="t('workspace.configureNamed', { name: cohort.name })"><span class="material-symbols-outlined" aria-hidden="true">tune</span></RouterLink></div></article></div><AsyncState v-else state="empty" :title="t('workspace.noManagedCohorts')" :detail="t('workspace.noManagedCohortsDetail')" /></section>
          <section v-if="report" class="panel workspace-report-panel"><div class="panel-heading"><div><p class="workspace-kicker">LIVE REPORT</p><h2>{{ report.cohort_name }}{{ t('workspace.progressSuffix') }}</h2></div><RouterLink class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'reports' } }">{{ t('workspace.detailedReport') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div><div class="workspace-report-grid"><div><strong>{{ report.member_count }}</strong><span>{{ t('workspace.members') }}</span></div><div><strong>{{ report.state_counts?.submitted || 0 }}</strong><span>{{ t('workspace.pendingReview') }}</span></div><div><strong>{{ report.state_counts?.needs_revision || 0 }}</strong><span>{{ t('workspace.needsRevision') }}</span></div><div><strong>{{ report.state_counts?.archived || 0 }}</strong><span>{{ t('workspace.archived') }}</span></div></div><p class="workspace-report-note">{{ t('workspace.firstSubmissionRate') }}: {{ rate(report.metrics?.on_time_first_submission_rate) }} · {{ t('workspace.archiveCompletionRate') }}: {{ rate(report.metrics?.archive_completion_rate) }}</p></section>
          <section class="panel"><div class="panel-heading"><div><p class="workspace-kicker">ARCHIVE JOBS</p><h2>{{ t('workspace.archiveJobs') }}</h2></div><RouterLink class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'archives' } }">{{ t('workspace.openArchiveCenter') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div><ArchiveJobStatus :items="archives" /></section>
        </main>
        <aside class="workspace-side"><NotificationInbox :items="notifications.items" :unread="notifications.unread" @read="readNotification" /><section class="panel workspace-shortcuts"><div class="panel-heading"><div><p class="workspace-kicker">GOVERNANCE</p><h3>{{ t('workspace.governanceEntries') }}</h3></div></div><RouterLink to="/organizations"><span class="material-symbols-outlined" aria-hidden="true">groups</span>{{ t('templates.organizations') }}</RouterLink><RouterLink to="/templates"><span class="material-symbols-outlined" aria-hidden="true">library_books</span>{{ t('templates.title') }}</RouterLink><RouterLink to="/audit"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('templates.audit') }}</RouterLink><RouterLink to="/operations"><span class="material-symbols-outlined" aria-hidden="true">monitor_heart</span>{{ t('route.operations') }}</RouterLink></section></aside>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { auth } from '../auth'
import { fetchArchives, fetchCohorts, fetchWorkflowNotifications, fetchWorkflowReport, markWorkflowNotificationRead } from '../api'
import AsyncState from '../components/AsyncState.vue'
import ArchiveJobStatus from '../components/ArchiveJobStatus.vue'
import NotificationInbox from '../components/NotificationInbox.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useI18n } from '../i18n'

const { t } = useI18n()

const loading = ref(true)
const error = ref('')
const cohorts = ref([])
const archives = ref([])
const report = ref(null)
const notifications = ref({ items: [], unread: 0 })
const activeCohorts = computed(() => cohorts.value.filter((item) => item.status === 'published'))
const pendingArchives = computed(() => archives.value.filter((item) => ['queued', 'running', 'failed'].includes(item.status)))

async function load() {
  loading.value = true; error.value = ''
  try {
    const [cohortData, archiveData, notificationData] = await Promise.all([fetchCohorts(), fetchArchives(), fetchWorkflowNotifications()])
    cohorts.value = cohortData; archives.value = archiveData; notifications.value = notificationData
    if (cohortData[0]?.id) report.value = await fetchWorkflowReport(cohortData[0].id)
  } catch (exception) { error.value = exception.message } finally { loading.value = false }
}

async function readNotification(item) {
  if (item.read_at) return
  try { await markWorkflowNotificationRead(item.id); notifications.value = await fetchWorkflowNotifications() } catch (exception) { error.value = exception.message }
}

function cohortStateLabel(status) { return t(`cohort.${status}`) === `cohort.${status}` ? status : t(`cohort.${status}`) }
function rate(value) { return value === null || value === undefined ? '-' : `${Math.round(Number(value) * 100)}%` }

onMounted(load)
</script>
