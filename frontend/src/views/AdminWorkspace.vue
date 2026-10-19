<template>
  <div class="workspace-page admin-workspace">
    <section class="workspace-hero"><div><p class="workspace-kicker">{{ auth.user?.role === 'admin' ? t('workspace.schoolKicker') : t('workspace.collegeKicker') }}</p><h1>{{ auth.user?.role === 'admin' ? t('workspace.schoolTitle') : t('workspace.collegeTitle') }}</h1><p>{{ t('workspace.adminDescription') }}</p></div><div class="workspace-identity"><span class="material-symbols-outlined" aria-hidden="true">admin_panel_settings</span><div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ roleLabel(auth.user?.role) }}</span></div></div></section>
    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <AsyncState v-if="loading" state="loading" :title="t('workspace.syncingOperations')" :detail="t('workspace.adminLoading')" />
    <AsyncState v-else-if="loadError" state="error" :title="t('workspace.loadFailed')" :detail="loadError" :action-label="t('common.retry')" @action="load" />
    <template v-else>
      <section class="workspace-stats" :aria-label="t('workspace.operationsSummary')"><div><span>{{ t('workspace.managedCohorts') }}</span><strong>{{ cohorts.length }}</strong><small>{{ t('workspace.currentScope') }}</small></div><div><span>{{ t('workspace.activeCohorts') }}</span><strong>{{ activeCohorts.length }}</strong><small>{{ t('workspace.canContinue') }}</small></div><div><span>{{ t('workspace.pendingArchive') }}</span><strong>{{ pendingArchives.length }}</strong><small>{{ t('workspace.needsArtifacts') }}</small></div><div><span>{{ t('workspace.unreadMessages') }}</span><strong>{{ notifications.unread || 0 }}</strong><small>{{ t('workspace.inboxReminder') }}</small></div></section>
      <div class="workspace-layout">
        <ScreenDeck class="workspace-main" :class="{ 'has-report': report }">
          <section class="panel workspace-focus-panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.operationsControl') }}</p><h2>{{ t('workspace.todayTasks') }}</h2></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">task_alt</span></div><div class="workspace-operations-grid"><RouterLink class="workspace-operation-card" to="/cohorts"><span class="material-symbols-outlined" aria-hidden="true">calendar_month</span><strong>{{ t('workspace.cohortsStages') }}</strong><small>{{ t('workspace.cohortsStagesDetail') }}</small><span class="material-symbols-outlined arrow" aria-hidden="true">arrow_forward</span></RouterLink><RouterLink class="workspace-operation-card" :to="{ path: '/workflow', query: { panel: 'archives' } }"><span class="material-symbols-outlined" aria-hidden="true">inventory_2</span><strong>{{ t('workspace.submissionsArchives') }}</strong><small>{{ pendingArchives.length ? t('workspace.pendingItems', { count: pendingArchives.length }) : t('workspace.archiveStatus') }}</small><span class="material-symbols-outlined arrow" aria-hidden="true">arrow_forward</span></RouterLink><RouterLink class="workspace-operation-card" :to="{ path: '/workflow', query: { panel: 'reports' } }"><span class="material-symbols-outlined" aria-hidden="true">monitoring</span><strong>{{ t('workspace.reports') }}</strong><small>{{ t('workspace.reportDetail') }}</small><span class="material-symbols-outlined arrow" aria-hidden="true">arrow_forward</span></RouterLink></div></section>
          <section class="panel workspace-cohorts-panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.cohortsKicker') }}</p><h2>{{ t('workspace.cohortOverview') }}</h2></div><RouterLink class="workspace-text-link" to="/cohorts">{{ t('workspace.configureCohorts') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div><div class="workspace-panel-body" tabindex="0" role="region" :aria-label="t('workspace.cohortOverview')"><div v-if="cohorts.length" class="workspace-cohort-list"><ScreenCollection :items="cohorts" :row-height="64" v-slot="{ items: visibleItems }"><article v-for="cohort in visibleItems" :key="cohort.id" class="workspace-cohort-row"><div><strong>{{ cohort.name }}</strong><span>{{ cohort.academic_year || t('workspace.academicYearMissing') }} · {{ cohort.member_ids?.length || 0 }} {{ t('workspace.students') }}</span></div><div><StatusBadge :status="cohort.status" :label="cohortStateLabel(cohort.status)" /><RouterLink class="icon-button workspace-row-link" to="/cohorts" :aria-label="t('workspace.configureNamed', { name: cohort.name })" :title="t('workspace.configureNamed', { name: cohort.name })"><span class="material-symbols-outlined" aria-hidden="true">tune</span></RouterLink></div></article></ScreenCollection></div><AsyncState v-else state="empty" :title="t('workspace.noManagedCohorts')" :detail="t('workspace.noManagedCohortsDetail')" /></div></section>
          <section v-if="report" class="panel workspace-report-panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.liveReportKicker') }}</p><h2 :title="report.cohort_name + t('workspace.progressSuffix')">{{ report.cohort_name }}{{ t('workspace.progressSuffix') }}</h2></div><RouterLink class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'reports' } }">{{ t('workspace.detailedReport') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div><div class="workspace-panel-body" tabindex="0" role="region" :aria-label="t('workspace.liveReportKicker')"><div class="workspace-report-grid"><div><strong>{{ report.member_count }}</strong><span>{{ t('workspace.members') }}</span></div><div><strong>{{ report.state_counts?.submitted || 0 }}</strong><span>{{ t('workspace.pendingReview') }}</span></div><div><strong>{{ report.state_counts?.needs_revision || 0 }}</strong><span>{{ t('workspace.needsRevision') }}</span></div><div><strong>{{ report.state_counts?.archived || 0 }}</strong><span>{{ t('workspace.archived') }}</span></div></div><p class="workspace-report-note">{{ t('workspace.firstSubmissionRate') }}: {{ rate(report.metrics?.on_time_first_submission_rate) }} · {{ t('workspace.archiveCompletionRate') }}: {{ rate(report.metrics?.archive_completion_rate) }}</p></div></section>
          <section class="panel workspace-archives-panel"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.archiveJobsKicker') }}</p><h2>{{ t('workspace.archiveJobs') }}</h2></div><RouterLink class="workspace-text-link" :to="{ path: '/workflow', query: { panel: 'archives' } }">{{ t('workspace.openArchiveCenter') }} <span class="material-symbols-outlined" aria-hidden="true">arrow_forward</span></RouterLink></div><div class="workspace-panel-body" tabindex="0" role="region" :aria-label="t('workspace.archiveJobs')"><ArchiveJobStatus :items="archives" /></div></section>
        </ScreenDeck>
        <ScreenDeck class="workspace-side"><NotificationInbox tabindex="0" role="region" :aria-label="t('notification.title')" :items="notifications.items" :unread="notifications.unread" @read="readNotification" /><section class="panel workspace-shortcuts"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('workspace.governanceKicker') }}</p><h3>{{ t('workspace.governanceEntries') }}</h3></div></div><RouterLink to="/organizations"><span class="material-symbols-outlined" aria-hidden="true">groups</span>{{ t('templates.organizations') }}</RouterLink><RouterLink to="/templates"><span class="material-symbols-outlined" aria-hidden="true">library_books</span>{{ t('templates.title') }}</RouterLink><RouterLink to="/audit"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('templates.audit') }}</RouterLink><RouterLink v-if="auth.user?.role === 'admin'" to="/operations"><span class="material-symbols-outlined" aria-hidden="true">monitor_heart</span>{{ t('route.operations') }}</RouterLink></section></ScreenDeck>
      </div>
    </template>
  </div>
</template>

<script setup>
import ScreenCollection from '../components/ScreenCollection.vue'
import ScreenDeck from '../components/ScreenDeck.vue'
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { auth } from '../auth'
import { fetchMyTasks, fetchSubmissions, fetchArchives, fetchCohorts, fetchWorkflowNotifications, fetchWorkflowReport, markWorkflowNotificationRead } from '../api'
import AsyncState from '../components/AsyncState.vue'
import ArchiveJobStatus from '../components/ArchiveJobStatus.vue'
import NotificationInbox from '../components/NotificationInbox.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { roleLabel, useI18n } from '../i18n'

const { t } = useI18n()

const loading = ref(true)
const error = ref('')
const loadError = ref('')
const cohorts = ref([])
const archives = ref([])
const report = ref(null)
const notifications = ref({ items: [], unread: 0 })
const activeCohorts = computed(() => cohorts.value.filter((item) => item.status === 'published'))
const pendingArchives = computed(() => archives.value.filter((item) => ['queued', 'running', 'failed'].includes(item.status)))

async function load() {
  loading.value = true; error.value = ''; loadError.value = ''
  try {
    const [cohortData, archiveData, notificationData] = await Promise.all([fetchCohorts(), fetchArchives(), fetchWorkflowNotifications()])
    cohorts.value = cohortData; archives.value = archiveData; notifications.value = notificationData
    if (archiveData.some(item => !item.submission_number)) {
      const [submissions, tasks] = await Promise.all([fetchSubmissions(), fetchMyTasks()])
      archives.value = archiveData.map(archive => {
        const submission = submissions.find(item => item.id === archive.submission_id)
        const task = tasks.find(item => item.id === submission?.participant_id || item.current_submission?.id === archive.submission_id)
        return { ...archive, submission_number: submission?.submission_number || archive.submission_number, project_title: task?.project_title, student_name: task?.student_name, stage_name: task?.stage_name }
      })
    }
    if (cohortData[0]?.id) report.value = await fetchWorkflowReport(cohortData[0].id)
  } catch (exception) { loadError.value = exception.message } finally { loading.value = false }
}

async function readNotification(item) {
  if (item.read_at) return
  try { await markWorkflowNotificationRead(item.id); notifications.value = await fetchWorkflowNotifications() } catch (exception) { error.value = exception.message }
}

function cohortStateLabel(status) { return t(`cohort.${status}`) === `cohort.${status}` ? status : t(`cohort.${status}`) }
function rate(value) { return value === null || value === undefined ? '-' : `${Math.round(Number(value) * 100)}%` }

onMounted(load)
</script>

<style scoped>
@media (min-width: 769px) {
  .workspace-page.admin-workspace {
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 16px 0;
  }
  .workspace-hero, .workspace-stats, .error-banner { flex-shrink: 0; margin: 0; }
  .workspace-hero { gap: 16px; }
  .workspace-hero h1 { font-size: 24px; }
  .workspace-stats { gap: 12px; }
  .workspace-stats > div { min-height: 0; padding: 12px 16px; gap: 4px; }
  .workspace-stats strong { font-size: 26px; }
  .workspace-layout { flex: 1; min-height: 0; gap: 12px; }
  .workspace-main {
    min-height: 0;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    grid-template-rows: auto minmax(0, 1fr) minmax(0, .8fr);
    gap: 12px;
  }
  .workspace-focus-panel, .workspace-cohorts-panel, .workspace-archives-panel { grid-column: 1 / -1; }
  .has-report .workspace-cohorts-panel { grid-column: auto; }
  .workspace-side { min-height: 0; display: flex; flex-direction: column; gap: 12px; }
  .admin-workspace :deep(.panel) { min-height: 0; padding: 14px; margin: 0; }
  .workspace-cohorts-panel, .workspace-report-panel, .workspace-archives-panel { display: flex; flex-direction: column; }
  .admin-workspace :deep(.panel-heading) { flex-shrink: 0; padding-bottom: 10px; }
  .admin-workspace :deep(.panel-heading > div) { min-width: 0; }
  .workspace-report-panel { position: static; }
  .workspace-report-panel h2 { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .workspace-panel-body { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior-y: contain; padding: 0 4px; margin: 0 -4px; }
  .workspace-cohort-row, .admin-workspace :deep(.workspace-archive-row) { padding: 8px 0; }
  .workspace-cohort-row { justify-content: space-between; }
  .workspace-cohort-row > div:first-child { min-width: 0; }
  .workspace-operations-grid { margin-top: 10px; gap: 10px; }
  .workspace-operation-card { grid-template-columns: 20px minmax(0, 1fr); padding: 12px 30px 12px 12px; column-gap: 10px; row-gap: 2px; }
  .workspace-operation-card strong, .workspace-operation-card small { grid-column: 2; }
  .workspace-operation-card > .material-symbols-outlined:first-child { grid-row: 1 / 3; align-self: center; font-size: 20px; margin-bottom: 0; }
  .workspace-operation-card .arrow { top: 50%; transform: translateY(-50%); }
  .workspace-report-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin-top: 10px; }
  .workspace-report-grid div { min-width: 0; padding: 8px 6px; }
  .workspace-report-grid strong { font-size: 20px; }
  .workspace-report-note { margin-top: 8px; }
  .admin-workspace :deep(.workspace-notification-panel) { flex: 1; position: static; overflow-y: auto; overscroll-behavior-y: contain; }
  .admin-workspace :deep(.workspace-notification-panel .panel-heading) { position: sticky; top: 0; background: var(--surface-container-lowest); z-index: 1; }
  .workspace-shortcuts { flex-shrink: 0; }
  .workspace-shortcuts a { padding: 8px 0; }
  .admin-workspace > .async-state { flex: 1; min-height: 0; overflow-y: auto; }
}
@media (min-width: 769px) and (max-height: 760px) {
  .workspace-page.admin-workspace { gap: 10px; padding: 12px 0; }
  .workspace-hero h1 { font-size: 22px; margin-bottom: 4px; }
  .workspace-hero .workspace-kicker { margin-bottom: 3px; }
  .workspace-hero p:not(.workspace-kicker) { font-size: 13px; line-height: 1.5; }
  .workspace-stats > div { padding: 8px 12px; gap: 2px; }
  .workspace-stats strong { font-size: 24px; }
  .admin-workspace :deep(.panel) { padding: 12px; }
  .workspace-focus-panel .panel-heading > div { display: flex; align-items: baseline; gap: 10px; }
  .workspace-focus-panel .workspace-kicker { margin: 0; }
  .workspace-operations-grid { margin-top: 8px; }
  .workspace-operation-card { padding: 10px 28px 10px 10px; }
  .workspace-report-grid { margin-top: 8px; }
  .workspace-report-grid div { padding-top: 6px; padding-bottom: 6px; }
  .workspace-report-note { margin-top: 6px; }
  .workspace-archives-panel :deep(.workspace-archive-row) { padding: 4px 0; }
}
@media (min-width: 769px) and (max-width: 1100px) {
  .workspace-main { display: flex; flex-direction: column; }
  .workspace-main > .panel { flex: 0 0 auto; }
  .workspace-panel-body { max-height: 200px; }
  .workspace-cohort-row { flex-wrap: wrap; }
}
</style>
