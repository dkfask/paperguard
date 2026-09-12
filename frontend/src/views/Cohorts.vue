<template>
  <div class="workspace-page cohorts-page">
    <section class="workspace-hero">
      <div>
        <p class="workspace-kicker">{{ t('cohorts.kicker') }}</p>
        <h1>{{ t('cohorts.title') }}</h1>
        <p>{{ t('cohorts.description') }}</p>
      </div>
      <div class="workspace-identity"><span class="material-symbols-outlined" aria-hidden="true">calendar_month</span><div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ auth.user?.role === 'admin' ? t('role.admin') : t('role.college_admin') }}</span></div></div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div v-if="notice" class="ok-banner" role="status">{{ notice }}</div>
    <AsyncState v-if="loading" state="loading" :title="t('cohorts.loading')" :detail="t('cohorts.loadingDetail')" />
    <template v-else>
      <section class="workspace-stats" :aria-label="t('cohorts.summary')"><div><span>{{ t('cohorts.managed') }}</span><strong>{{ cohorts.length }}</strong><small>{{ t('workspace.currentScope') }}</small></div><div><span>{{ t('cohorts.published') }}</span><strong>{{ publishedCount }}</strong><small>{{ t('cohorts.running') }}</small></div><div><span>{{ t('cohorts.memberTotal') }}</span><strong>{{ memberCount }}</strong><small>{{ t('cohorts.included') }}</small></div><div><span>{{ t('cohorts.currentConfig') }}</span><strong>{{ selectedCohort?.stages?.length || 0 }}</strong><small>{{ t('cohorts.selectedStages') }}</small></div></section>

      <div class="workspace-layout">
        <main class="workspace-main">
          <section class="panel">
            <div class="panel-heading"><div><p class="workspace-kicker">{{ t('cohorts.createKicker') }}</p><h2>{{ t('cohorts.create') }}</h2><p>{{ t('cohorts.createDetail') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">add_box</span></div>
            <form class="cohort-create-form" @submit.prevent="createNewCohort">
              <label>{{ t('cohorts.name') }}<input v-model="cohortForm.name" required maxlength="120" :placeholder="t('cohorts.namePlaceholder')" /></label>
              <label>{{ t('cohorts.organization') }}<select v-model="cohortForm.organization_id" required><option value="">{{ t('cohorts.selectOrganization') }}</option><option v-for="unit in organizations" :key="unit.id" :value="unit.id">{{ unit.name }}</option></select></label>
              <label>{{ t('cohorts.academicYear') }}<input v-model="cohortForm.academic_year" maxlength="20" placeholder="2027" /></label>
              <button class="small primary" type="submit" :disabled="saving || !cohortForm.name || !cohortForm.organization_id"><span class="material-symbols-outlined">add</span>{{ t('cohorts.createButton') }}</button>
            </form>
          </section>

          <section class="panel">
            <div class="panel-heading"><div><p class="workspace-kicker">{{ t('cohorts.listKicker') }}</p><h2>{{ t('cohorts.list') }}</h2></div><button class="icon-button" type="button" :title="t('cohorts.refresh')" :aria-label="t('cohorts.refresh')" :disabled="loading" @click="load"><span class="material-symbols-outlined">refresh</span></button></div>
            <AsyncState v-if="!cohorts.length" state="empty" :title="t('cohorts.empty')" :detail="t('cohorts.emptyDetail')" />
            <div v-else class="cohort-list">
              <button v-for="cohort in cohorts" :key="cohort.id" type="button" class="cohort-row" :class="{ selected: selectedCohort?.id === cohort.id }" @click="select(cohort)">
                <span class="status-dot" :class="cohort.status"></span><span class="cohort-row-copy"><strong>{{ cohort.name }}</strong><small>{{ cohort.academic_year || t('cohorts.academicYearMissing') }} · {{ cohort.member_ids?.length || 0 }} {{ t('cohorts.students') }}</small></span><StatusBadge :status="cohort.status" :label="cohortLabel(cohort.status)" /><span class="material-symbols-outlined">arrow_forward</span>
              </button>
            </div>
          </section>

          <section v-if="selectedCohort" class="panel cohort-config-panel">
            <div class="panel-heading"><div><p class="workspace-kicker">{{ selectedCohort.name }}</p><h2>{{ t('cohorts.stageDraft') }}</h2><p>{{ t('cohorts.stageDraftDetail') }}</p></div><button class="icon-button" type="button" :title="t('cohorts.closeConfig')" :aria-label="t('cohorts.closeConfig')" @click="selectedCohort = null"><span class="material-symbols-outlined">close</span></button></div>
            <StageConfigForm :form="stageForm" :templates="templates" :saving="saving" @save="createStage" />
            <div class="configured-stages"><h3>{{ t('cohorts.existingStages') }}</h3><div v-if="!selectedCohort.stages?.length" class="muted-row">{{ t('cohorts.noStages') }}</div><div v-for="stage in selectedCohort.stages || []" :key="stage.id" class="configured-stage-row"><div><strong>{{ stage.name }}</strong><span>{{ stage.key }} · v{{ stage.config?.version || 1 }} · {{ stage.config?.deadline_at ? formatDate(stage.config.deadline_at) : t('cohorts.noDeadline') }}</span></div><div class="stage-actions"><StatusBadge :status="stage.status" :label="stage.status === 'published' ? t('cohorts.publishedStatus') : t('cohorts.draftStatus')" /><button v-if="stage.status === 'draft'" class="tiny primary" type="button" :disabled="saving" @click="previewAndPublish(stage)"><span class="material-symbols-outlined">publish</span>{{ t('cohorts.previewPublish') }}</button></div></div></div>
            <div v-if="stagePreview" class="preview-box"><div class="preview-heading"><strong>{{ t('cohorts.publishImpact') }}</strong><span :class="stagePreview.ok ? 'preview-ok' : 'preview-error'">{{ stagePreview.ok ? t('cohorts.publishable') : t('cohorts.blocked') }}</span></div><p v-for="item in stagePreview.errors || []" :key="item" class="preview-error">{{ item }}</p><p v-for="item in stagePreview.warnings || []" :key="item" class="preview-warning">{{ item }}</p><p v-if="stagePreview.ok">{{ t('cohorts.memberTasksNotice', { count: stagePreview.member_snapshot?.length || 0 }) }}</p><div class="form-actions"><button class="small primary" type="button" :disabled="saving || !stagePreview.ok" @click="publishStage"><span class="material-symbols-outlined">verified</span>{{ t('cohorts.confirmPublish') }}</button></div></div>
          </section>

          <MemberImportWizard v-if="selectedCohort" v-model="memberImportText" :rows="memberImportRows" :result="memberImportResult" :busy="saving" @preview="previewMembers" @commit="commitMembers" />
        </main>
        <aside class="workspace-side"><section class="panel workspace-shortcuts"><div class="panel-heading"><div><p class="workspace-kicker">{{ t('cohorts.relatedKicker') }}</p><h3>{{ t('cohorts.related') }}</h3></div></div><RouterLink to="/organizations"><span class="material-symbols-outlined" aria-hidden="true">account_tree</span>{{ t('route.organizations') }}</RouterLink><RouterLink to="/audit"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('route.audit') }}</RouterLink><RouterLink to="/operations"><span class="material-symbols-outlined" aria-hidden="true">monitor_heart</span>{{ t('route.operations') }}</RouterLink></section></aside>
      </div>
    </template>
    <ConfirmDialog :open="confirmDialog.open" :title="confirmDialog.title" :description="confirmDialog.description" :confirm-label="confirmDialog.confirmLabel" :busy="confirmBusy" @confirm="confirmPublish" @cancel="closeConfirm" />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { auth } from '../auth'
import { createCohort, createCohortStage, fetchCohort, fetchCohorts, fetchOrganizations, fetchTemplates, importCohortMembers, previewCohortStage, publishCohortStage } from '../api'
import AsyncState from '../components/AsyncState.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import MemberImportWizard from '../components/MemberImportWizard.vue'
import StageConfigForm from '../components/StageConfigForm.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useI18n } from '../i18n'

const { t, formatDate: formatLocaleDate } = useI18n()
const loading = ref(true); const saving = ref(false); const error = ref(''); const notice = ref('')
const cohorts = ref([]); const organizations = ref([]); const templates = ref([]); const selectedCohort = ref(null)
const stagePreview = ref(null); const stagePreviewStage = ref(null); const memberImportText = ref(''); const memberImportResult = ref(null)
const cohortForm = reactive({ name: '', organization_id: '', academic_year: '' })
const stageForm = reactive({ key: 'draft', name: '初稿', open_at: '', deadline_at: '', revision_days: 7, template_id: '', required_materials_text: '', manual_checks_text: '', is_final: false, allow_early_write: false })
const confirmBusy = ref(false); const confirmDialog = reactive({ open: false, title: '', description: '', confirmLabel: '' })
const publishedCount = computed(() => cohorts.value.filter((item) => item.status === 'published').length)
const memberCount = computed(() => cohorts.value.reduce((total, item) => total + (item.member_ids?.length || 0), 0))
const memberImportRows = computed(() => memberImportText.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).filter((line, index) => !(index === 0 && /(student[_ ]?(id|ref)|学号|学生)/i.test(line))).map((line) => { const [student_ref = '', teacher_ref = '', reason = ''] = line.split(/\t|,/); return { student_ref: student_ref.trim(), teacher_ref: teacher_ref.trim(), reason: reason.trim() } }))

async function load() {
  loading.value = true; error.value = ''
  try { const [cohortData, orgData, templateData] = await Promise.all([fetchCohorts(), fetchOrganizations(), fetchTemplates()]); cohorts.value = cohortData; organizations.value = orgData; templates.value = templateData; if (selectedCohort.value) await select(cohorts.value.find((item) => item.id === selectedCohort.value.id) || null) } catch (exception) { error.value = exception.message } finally { loading.value = false }
}
async function select(cohort) { if (!cohort) { selectedCohort.value = null; return }; try { selectedCohort.value = await fetchCohort(cohort.id); resetStage(); memberImportResult.value = null; stagePreview.value = null } catch (exception) { error.value = exception.message } }
function resetStage() { Object.assign(stageForm, { key: 'draft', name: '初稿', open_at: '', deadline_at: '', revision_days: 7, template_id: templates.value[0]?.id || '', required_materials_text: '', manual_checks_text: '', is_final: false, allow_early_write: false }) }
async function createNewCohort() { saving.value = true; error.value = ''; try { const created = await createCohort(cohortForm); Object.assign(cohortForm, { name: '', organization_id: '', academic_year: '' }); notice.value = t('cohorts.created'); await load(); await select(created) } catch (exception) { error.value = exception.message } finally { saving.value = false } }
async function createStage() { if (!selectedCohort.value) return; saving.value = true; error.value = ''; try { await createCohortStage(selectedCohort.value.id, { ...stageForm, required_manual_checks: stageForm.manual_checks_text.split(',').map((item) => item.trim()).filter(Boolean), required_materials: stageForm.required_materials_text.split(',').map((item) => item.trim()).filter(Boolean), open_at: new Date(stageForm.open_at).toISOString(), deadline_at: new Date(stageForm.deadline_at).toISOString() }); notice.value = t('cohorts.stageSaved'); await select(selectedCohort.value); } catch (exception) { error.value = exception.message } finally { saving.value = false } }
async function previewAndPublish(stage) { stagePreviewStage.value = stage; error.value = ''; try { stagePreview.value = await previewCohortStage(stage.id) } catch (exception) { error.value = exception.message } }
function publishStage() { if (!stagePreview.value?.ok || !stagePreviewStage.value) return; Object.assign(confirmDialog, { open: true, title: t('cohorts.publishTitle', { name: stagePreviewStage.value.name }), description: t('cohorts.publishDescription', { count: stagePreview.value.member_snapshot?.length || 0 }), confirmLabel: t('cohorts.confirmPublish') }) }
async function confirmPublish() { if (!stagePreviewStage.value) return; confirmBusy.value = true; try { await publishCohortStage(stagePreviewStage.value.id); notice.value = t('cohorts.publishedNotice'); confirmDialog.open = false; stagePreviewStage.value = null; await select(selectedCohort.value) } catch (exception) { error.value = exception.message } finally { confirmBusy.value = false } }
function closeConfirm() { if (confirmBusy.value) return; confirmDialog.open = false; stagePreviewStage.value = null }
async function previewMembers() { if (!selectedCohort.value) return; try { memberImportResult.value = await importCohortMembers(selectedCohort.value.id, memberImportRows.value, true); notice.value = t('cohorts.memberPreviewDone') } catch (exception) { error.value = exception.message } }
async function commitMembers() { if (!selectedCohort.value) return; saving.value = true; try { memberImportResult.value = await importCohortMembers(selectedCohort.value.id, memberImportRows.value); notice.value = t('cohorts.memberImportDone'); await load(); await select(selectedCohort.value) } catch (exception) { error.value = exception.message } finally { saving.value = false } }
function cohortLabel(status) { return t(`cohort.${status}`) === `cohort.${status}` ? status : t(`cohort.${status}`) }
function formatDate(value) { return value ? formatLocaleDate(value, { hour12: false }) : '' }
onMounted(load)
</script>

<style scoped>
.cohorts-page .workspace-layout { align-items: start; }
.cohort-create-form { display: grid; grid-template-columns: 2fr 1.5fr 1fr auto; gap: 10px; align-items: end; margin-top: 16px; }
.cohort-create-form label { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 12px; }
.cohort-create-form input, .cohort-create-form select { width: 100%; box-sizing: border-box; }
.cohort-list { display: grid; gap: 4px; margin-top: 14px; }
.cohort-row { display: flex; align-items: center; gap: 10px; width: 100%; border: 1px solid transparent; border-radius: var(--radius-sm); background: transparent; padding: 11px; text-align: left; color: var(--on-surface); }
.cohort-row:hover, .cohort-row.selected { border-color: var(--primary); background: var(--surface-container-low); }
.cohort-row-copy { flex: 1; min-width: 0; }.cohort-row-copy strong, .cohort-row-copy small { display: block; }.cohort-row-copy small { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }.cohort-row > .material-symbols-outlined { color: var(--outline); }
.cohort-config-panel { border-left: 3px solid var(--primary); }.configured-stages { border-top: 1px solid var(--border); margin-top: 18px; padding-top: 14px; }.configured-stages h3 { margin: 0 0 8px; font-size: 14px; }.configured-stage-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; border-bottom: 1px solid var(--border); padding: 10px 0; }.configured-stage-row strong, .configured-stage-row span { display: block; }.configured-stage-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }.stage-actions { display: flex; align-items: center; gap: 8px; }.form-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }.status-dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; background: var(--outline); }.status-dot.published { background: #14866d; }.status-dot.closed { background: var(--error); }.status-dot.draft { background: var(--tertiary); }
.preview-box { margin-top: 15px; padding: 12px; border: 1px solid var(--border); background: var(--surface-container-low); border-radius: var(--radius-sm); font-size: 12px; }.preview-box p { margin: 5px 0 0; }.preview-heading { display: flex; justify-content: space-between; gap: 10px; }.preview-ok { color: #087f5b; }.preview-error { color: var(--error); }.preview-warning { color: var(--tertiary); }
@media (max-width: 760px) { .cohort-create-form { grid-template-columns: 1fr 1fr; }.cohort-create-form button { grid-column: 1 / -1; justify-self: start; } }
@media (max-width: 560px) { .cohort-create-form { grid-template-columns: 1fr; }.cohort-create-form button { grid-column: auto; }.configured-stage-row { align-items: flex-start; flex-direction: column; width: 100%; }.stage-actions { width: 100%; justify-content: space-between; } }
</style>
