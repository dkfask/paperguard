<template>
  <div class="workflow-page">
    <section class="workflow-hero">
      <div>
        <h1>{{ sectionTitle }}</h1>
        <p class="workflow-subtitle">{{ t('workflow.description') }}</p>
      </div>
      <div class="workflow-identity">
        <span class="material-symbols-outlined" aria-hidden="true">verified_user</span>
        <div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ roleLabel }}</span></div>
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div v-if="notice" class="ok-banner" role="status">{{ notice }}</div>
    <div v-if="!m2WorkflowEnabled && ['review', 'submissions', 'archives'].includes(panelTarget)" class="notice-banner" role="status">{{ t('workflow.m2Unavailable') }}</div>

    <nav class="workflow-tabs" :aria-label="t('workflow.menu')">
      <button
        v-for="tab in workflowTabs"
        :key="tab.key"
        type="button"
        class="workflow-tab"
        :class="{ active: activeTabKey === tab.key }"
        :aria-current="activeTabKey === tab.key ? 'page' : undefined"
        @click="selectSection(tab.key)"
      >
        <span class="material-symbols-outlined" aria-hidden="true">{{ tab.icon }}</span>
        <span>{{ tab.label }}</span>
        <strong v-if="tab.key === 'messages' && notifications.unread" class="workflow-tab-count">{{ notifications.unread }}</strong>
      </button>
    </nav>

    <section class="workflow-summary">
      <div><span>{{ t('workflow.openTasks') }}</span><strong>{{ openTaskCount }}</strong></div>
      <div><span>{{ t('workflow.pendingSubmissions') }}</span><strong>{{ reviewQueue.length }}</strong></div>
      <div><span>{{ t('workflow.unreadMessages') }}</span><strong>{{ notifications.unread || 0 }}</strong></div>
      <div v-if="canManage"><span>{{ t('workflow.managedCohorts') }}</span><strong>{{ cohorts.length }}</strong></div>
      <div v-if="tasks[0]" class="workflow-next-deadline"><span>{{ t('workflow.nearestDeadline') }}</span><DeadlineLabel :deadline="tasks[0].revision_deadline_at || tasks[0].effective_deadline_at" :revision="Boolean(tasks[0].revision_deadline_at)" :late="tasks[0].is_late" /></div>
    </section>

    <div v-if="loading" class="workflow-sync-state">
      <StatusBadge status="submitted" :label="t('workflow.syncing')" icon="sync" />
    </div>

    <div
      class="workflow-layout"
      :class="{
        'workflow-layout-single': activeSection !== 'overview',
        'workflow-layout-side-only': activeSection === 'messages' || activeSection === 'reports' || (activeSection === 'submissions' && !canManage && !isTeacher),
        'workflow-layout-main-only': activeSection === 'submissions' && canManage,
      }"
    >
      <ScreenDeck class="workflow-main">
        <template #header><div class="section-title-row">
          <div><p class="workflow-kicker">{{ sectionKicker }}</p></div><button v-if="canConfigure && activeSection === 'overview' && !panelFocused" class="small primary" @click="createCohortOpen = true"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('workflow.createCohort') }}</button>
          <button class="icon-button" :title="t('workflow.refresh')" :aria-label="t('workflow.refresh')" @click="loadAll" :disabled="loading"><span class="material-symbols-outlined" aria-hidden="true">refresh</span></button>
        </div></template>

        <section v-if="processInbox.length && activeSection === 'overview'" class="panel process-inbox"><div class="panel-heading"><div><h3>{{ t('workflow.processInboxTitle') }}</h3><p>{{ t('workflow.processInboxDetail') }}</p></div><span class="unread-count">{{ processInbox.length }}</span></div><label class="process-reason-field">{{ t('workflow.returnReason') }}<input v-model="processDecisionReason" :placeholder="t('workflow.returnReasonPlaceholder')" /></label><div class="process-inbox-list"><ScreenCollection :items="processInbox" :row-height="72" v-slot="{ items: visibleItems }"><div v-for="record in visibleItems" :key="record.id" class="process-inbox-row"><div><strong>{{ record.artifact_name }}</strong><span>{{ record.data?.student_id || record.student_id || record.cohort_id }} · {{ t('workflow.waitingApproval') }} {{ record.pending_approvals?.join(' / ') || t('workflow.waitingApproval') }}</span></div><div class="stage-row-actions"><button v-if="isTeacher && record.artifact_type === 'midterm_check'" class="tiny" @click="openMidtermTeacherFields(record)"><span class="material-symbols-outlined" aria-hidden="true">edit_note</span>{{ t('workflow.fillMidtermTeacherFields') }}</button><button class="tiny" @click="handleProcessDecision(record, 'returned')"><span class="material-symbols-outlined" aria-hidden="true">undo</span>{{ t('workflow.return') }}</button><button class="tiny primary" @click="handleProcessDecision(record, 'approved')"><span class="material-symbols-outlined" aria-hidden="true">verified</span>{{ t('workflow.approve') }}</button></div></div></ScreenCollection></div></section>

        <template v-if="canConfigure && !panelFocused">


          <div v-if="!cohorts.length && activeSection === 'overview' && !panelFocused" class="empty-state"><span class="material-symbols-outlined" aria-hidden="true">folder_open</span><h3>{{ t('workflow.noCohorts') }}</h3><p>{{ t('workflow.noCohortsDetail') }}</p></div>
          <section v-if="activeSection === 'overview' && !panelFocused" class="panel screen-panel"><h3>{{ t('cohorts.list') }}</h3><ScreenCollection :items="cohorts" :row-height="66" v-slot="{ items }"><button v-for="cohort in items" :key="cohort.id" type="button" class="screen-summary-row" @click="cohortDrawer = cohort"><div><strong>{{ cohort.name }}</strong><small>{{ cohort.member_ids?.length || 0 }} {{ t('workspace.students') }}</small></div><span class="material-symbols-outlined" aria-hidden="true">chevron_right</span></button></ScreenCollection></section><BaseModal presentation="drawer" :open="Boolean(cohortDrawer)" :title="cohortDrawer?.name" @close="cohortDrawer = null"><template v-if="cohortDrawer"><section v-for="cohort in [cohortDrawer]" v-if="activeSection === 'overview' && !panelFocused" :key="cohort.id" class="panel cohort-panel">
            <div class="panel-heading"><div><div class="eyebrow-row"><span class="status-dot" :class="cohort.status"></span><span>{{ cohort.status === 'published' ? t('workflow.publishedStatus') : cohort.status === 'closed' ? t('workflow.closedStatus') : t('workflow.draftStatus') }}</span></div><h3>{{ cohort.name }}</h3><p>{{ cohort.academic_year || t('workflow.academicYearMissing') }} · {{ t('workflow.studentCount', { count: cohort.member_ids?.length || 0 }) }} · {{ cohort.organization_id }}</p></div><div class="stage-row-actions"><button class="small" @click="selectCohort(cohort)"><span class="material-symbols-outlined" aria-hidden="true">tune</span>{{ t('workflow.configure') }}</button><!-- 关闭/恢复批次仅系统管理员可执行，教师走独立的审核操作 -->
<button v-if="canManage && cohort.status === 'published'" class="tiny" @click="handleCloseCohort(cohort)"><span class="material-symbols-outlined" aria-hidden="true">lock</span>{{ t('workflow.close') }}</button><button v-if="canManage && cohort.status === 'closed'" class="tiny primary" @click="handleReopenCohort(cohort)"><span class="material-symbols-outlined" aria-hidden="true">lock_open</span>{{ t('workflow.reopen') }}</button></div></div>
            <div class="cohort-stages"><template v-for="stage in cohort.stages || []" :key="stage.id"><div class="stage-row"><div><strong>{{ stage.name }}</strong><span>v{{ stage.config?.version || 1 }} · {{ stage.config?.open_at ? formatDate(stage.config.open_at) : t('workflow.openTimeMissing') }} · {{ stage.config?.deadline_at ? formatDate(stage.config.deadline_at) : t('workflow.deadlineMissing') }}</span></div><div class="stage-row-actions"><span class="state-tag" :class="stage.status">{{ stage.status === 'published' ? t('workflow.publishedStatus') : t('workflow.draftStatus') }}</span><button v-if="stage.status === 'draft'" class="tiny primary" @click="previewAndPublish(stage)"><span class="material-symbols-outlined" aria-hidden="true">publish</span>{{ t('workflow.previewPublish') }}</button><button v-if="m2WorkflowEnabled && stage.status === 'published'" class="tiny" @click="openStageUpgrade(stage)"><span class="material-symbols-outlined" aria-hidden="true">published_with_changes</span>{{ t('workflow.upgradeConfig') }}</button></div></div><div v-if="upgradeStage?.id === stage.id" class="stage-upgrade-box"><div class="panel-heading"><div><strong>{{ t('workflow.stageUpgradeTitle') }}</strong><p>{{ t('workflow.stageUpgradeDetail') }}</p></div><button class="icon-button" :title="t('workflow.closeUpgrade')" :aria-label="t('workflow.closeUpgrade')" @click="closeStageUpgrade"><span class="material-symbols-outlined" aria-hidden="true">close</span></button></div><div class="form-grid"><label>{{ t('workflow.newDeadline') }}<input v-model="stageUpgradeForm.deadline_at" type="datetime-local" /></label><label>{{ t('workflow.revisionDays') }}<input v-model.number="stageUpgradeForm.revision_days" type="number" min="0" max="365" /></label><label>{{ t('workflow.upgradeReason') }}<input v-model="stageUpgradeForm.reason" :placeholder="t('workflow.upgradeReasonPlaceholder')" /></label></div><div class="form-actions"><button class="small" :disabled="saving || !stageUpgradeForm.reason" @click="previewStageUpgrade(stage)"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('workflow.previewChange') }}</button></div><div v-if="stageUpgradePreview" class="preview-box"><div class="preview-heading"><strong>{{ t('workflow.changeDiff') }}</strong><span :class="stageUpgradePreview.preflight?.ok ? 'preview-ok' : 'preview-error'">{{ stageUpgradePreview.preflight?.ok ? t('workflow.publishable') : t('workflow.blocked') }}</span></div><p v-for="item in stageUpgradePreview.diff" :key="item.field" class="upgrade-diff"><strong>{{ item.field }}</strong>：{{ item.before ?? t('workflow.emptyValue') }} → {{ item.after ?? t('workflow.emptyValue') }}</p><p v-for="item in stageUpgradePreview.preflight?.errors || []" :key="item" class="preview-error">{{ item }}</p><p v-for="item in stageUpgradePreview.preflight?.warnings || []" :key="item" class="preview-warning">{{ item }}</p><div v-if="stageUpgradePreview.affected_participants?.length" class="migration-list"><div class="migration-list-heading"><strong>{{ t('workflow.selectMigration') }}</strong><button class="tiny" @click="selectAllStageParticipants">{{ t('workflow.selectAll') }} / {{ t('workflow.clearAll') }}</button></div><label v-for="item in stageUpgradePreview.affected_participants" :key="item.participant_id"><input v-model="stageUpgradeParticipantIds" type="checkbox" :value="item.participant_id" />{{ item.student_id }} · {{ item.state }} · {{ t('workflow.currentVersion') }} v{{ item.current_config_id === stage.config_version_id ? stage.config?.version || 1 : t('workflow.previousVersion') }}</label></div><p v-else class="muted-row">{{ t('workflow.noMigration') }}</p><div class="form-actions"><button class="small primary" :disabled="saving || !stageUpgradePreview.preflight?.ok" @click="publishStageUpgrade(stage)"><span class="material-symbols-outlined" aria-hidden="true">publish</span>{{ t('workflow.publishAndMigrate') }}</button></div></div></div></template><div v-if="!(cohort.stages || []).length" class="muted-row">{{ t('workflow.noStages') }}</div></div>
          </section></template></BaseModal>

          <section v-if="isTeacher && reopenCandidates.length && activeSection === 'overview' && !panelFocused" class="panel correction-panel"><div class="panel-heading"><div><h3>{{ t('workflow.correctionTitle') }}</h3><p>{{ t('workflow.correctionDetail') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">history</span></div><div class="archive-list"><ScreenCollection :items="reopenCandidates" :row-height="72" v-slot="{ items: visibleItems }"><div v-for="task in visibleItems" :key="task.id" class="archive-row"><div><strong>{{ task.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ task.cohort_name }} · {{ task.stage_name }} · {{ stateLabel(task.state) }}</span></div><div class="stage-row-actions"><button class="tiny primary" @click="handleReopenParticipant(task)"><span class="material-symbols-outlined" aria-hidden="true">edit_note</span>{{ t('workflow.reopen') }}</button></div></div></ScreenCollection></div></section>

            <BaseModal presentation="drawer" :open="Boolean(selectedCohort) && activeSection === 'overview' && !panelFocused" :title="selectedCohort?.name" @close="selectedCohort = null"><ScreenDeck ><section v-if="selectedCohort && activeSection === 'overview' && !panelFocused" class="panel setup-panel">
            <div class="panel-heading"><div><h3>{{ selectedCohort.name }} · {{ t('workflow.stageConfig') }}</h3></div><button class="icon-button" :title="t('common.close')" :aria-label="t('common.close')" @click="selectedCohort = null"><span class="material-symbols-outlined" aria-hidden="true">close</span></button></div>
            <StageConfigForm :form="stageForm" :templates="templates" :saving="saving" @save="handleCreateStage" />
            <div class="member-quick-add"><h4>{{ t('workflow.addAdvising') }}</h4><div class="form-grid"><label>{{ t('workflow.studentIdName') }}<input v-model="memberForm.student_ref" :placeholder="t('workflow.studentIdPlaceholder')" /></label><label>{{ t('workflow.teacherIdName') }}<input v-model="memberForm.teacher_ref" :placeholder="t('workflow.teacherIdPlaceholder')" /></label><label>{{ t('workflow.changeReason') }}<input v-model="memberForm.reason" :placeholder="t('workflow.changeReasonPlaceholder')" /></label></div><button class="small" :disabled="saving || !memberForm.student_ref || !memberForm.teacher_ref" @click="handleAddMember"><span class="material-symbols-outlined" aria-hidden="true">person_add</span>{{ t('workflow.saveRelationship') }}</button></div>
            <div v-if="preview" class="preview-box"><div class="preview-heading"><strong>{{ t('workflow.publishPreview') }}</strong><span :class="preview.ok ? 'preview-ok' : 'preview-error'">{{ preview.ok ? t('workflow.publishable') : t('workflow.blocked') }}</span></div><p v-for="item in preview.errors" :key="item" class="preview-error">{{ item }}</p><p v-for="item in preview.warnings" :key="item" class="preview-warning">{{ item }}</p><p v-if="preview.ok">{{ t('workflow.memberFreeze', { count: preview.member_snapshot?.length || 0 }) }}</p></div>
            <div class="management-records"><div class="panel-heading"><div><h3>{{ t('workflow.managementMaterials') }}</h3><p>{{ t('workflow.managementMaterialsDetail') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">assignment</span></div><div class="management-catalog"><div v-for="item in processCatalog.cohort" :key="item.artifact_type"><strong>{{ item.name }}</strong><span class="state-tag" :class="cohortMaterialRecord(item.artifact_type)?.state || 'missing'">{{ cohortMaterialRecord(item.artifact_type) ? recordState(cohortMaterialRecord(item.artifact_type).state) : t('workflow.notSubmittedStatus') }}</span></div></div><div class="form-grid"><label>{{ t('workflow.materialType') }}<select v-model="managementForm.artifact_type"><option v-for="item in processCatalog.cohort" :key="item.artifact_type" :value="item.artifact_type">{{ item.name }}</option></select></label><label>{{ t('workflow.materialTitle') }}<input v-model="managementForm.title" :placeholder="t('workflow.materialTitlePlaceholder')" /></label></div><label class="material-content-field">{{ t('workflow.structuredContent') }}<textarea v-model="managementForm.content" rows="3" :placeholder="t('workflow.structuredContentPlaceholder')" /></label><button class="small primary" :disabled="saving" @click="saveManagementMaterial"><span class="material-symbols-outlined" aria-hidden="true">upload_file</span>{{ t('workflow.saveManagement') }}</button></div>
          </section><section v-if="selectedCohort && managementSchema?.fields?.length && activeSection === 'overview' && !panelFocused" class="panel structured-management-panel"><div class="panel-heading"><div><h3>{{ managementSchema.title }}</h3><p>{{ t('workflow.structuredManagementDetail') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">table_view</span></div><div class="structured-material-form"><label v-for="field in managementSchema.fields.filter((item) => item.type !== 'table')" :key="field.key" class="structured-field"><span>{{ field.label }}<em v-if="field.required">{{ t('workflow.required') }}</em></span><textarea v-if="field.type === 'textarea'" v-model="managementData[field.key]" rows="3" :placeholder="field.help || field.label" /><textarea v-else-if="field.type === 'json'" :value="managementJsonDrafts[field.key]" rows="5" :placeholder="field.help || t('workflow.enterJson')" @input="managementJsonDrafts[field.key] = $event.target.value" /><input v-else v-model="managementData[field.key]" :type="field.type === 'number' ? 'number' : 'text'" :placeholder="field.help || field.label" /></label></div><div v-for="field in managementSchema.fields.filter((item) => item.type === 'table')" :key="field.key" class="structured-table-editor"><div class="table-editor-heading"><div><strong>{{ field.label }}<em v-if="field.required">{{ t('workflow.required') }}</em></strong><small>{{ field.help }}</small></div><button type="button" class="tiny" @click="addManagementTableRow(field)"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('workflow.addRow') }}</button></div><div class="table-editor-scroll"><table><thead><tr><th>#</th><th v-for="column in field.columns || []" :key="column.key">{{ column.label }}</th><th></th></tr></thead><tbody><tr v-for="(row, rowIndex) in (managementTableDrafts[field.key] || [])" :key="rowIndex"><td>{{ rowIndex + 1 }}</td><td v-for="column in field.columns || []" :key="column.key"><textarea v-if="column.type === 'textarea'" v-model="row[column.key]" rows="2" :placeholder="column.label" /><input v-else v-model="row[column.key]" :type="column.type === 'number' ? 'number' : 'text'" :placeholder="column.label" /></td><td><button type="button" class="icon-button" :title="t('common.remove')" :aria-label="t('common.remove')" @click="removeManagementTableRow(field, rowIndex)"><span class="material-symbols-outlined" aria-hidden="true">delete</span></button></td></tr><tr v-if="!(managementTableDrafts[field.key] || []).length"><td :colspan="(field.columns || []).length + 2" class="muted-row">{{ t('workflow.noDetails') }}</td></tr></tbody></table></div></div><button class="small primary" :disabled="saving" @click="saveManagementMaterial"><span class="material-symbols-outlined" aria-hidden="true">upload_file</span>{{ t('workflow.saveStructured') }}</button></section><MemberImportWizard :aria-label="t('memberImport.title')"
        v-if="canConfigure && selectedCohort && activeSection === 'overview' && !panelFocused"
        v-model="memberImportText"
        :rows="memberImportRows"
        :result="memberImportResult"
        :busy="saving"
        @preview="previewMemberImport"
        @commit="commitMemberImport"
      /></ScreenDeck></BaseModal>



          <section v-if="activeSection === 'submissions'" class="panel archive-panel"><div class="panel-heading"><div><h3>{{ t('workflow.submissionsTitle') }}</h3><p>{{ t('workflow.submissionsDetail') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">inventory_2</span></div><div v-if="reviewQueue.length" class="archive-list"><ScreenCollection :items="reviewQueue" :row-height="72" v-slot="{ items: visibleItems }"><div v-for="task in visibleItems" :key="'submission-' + task.id" class="archive-row"><div><strong>{{ task.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ task.cohort_name }} · {{ task.stage_name }} · {{ task.current_submission?.number || t('workflow.formalSubmission') }}</span></div><span class="state-tag submitted">{{ t('workflow.pendingApproval') }}</span></div></ScreenCollection></div><div v-if="archiveCandidates.length" class="archive-list"><ScreenCollection :items="archiveCandidates" :row-height="72" v-slot="{ items: visibleItems }"><div v-for="task in visibleItems" :key="task.id" class="archive-row"><div><strong>{{ task.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ task.cohort_name }} · {{ task.stage_name }} · {{ task.current_submission.number }}</span></div><button class="small primary" :disabled="saving" @click="handleArchive(task)"><span class="material-symbols-outlined" aria-hidden="true">archive</span>{{ t('workflow.generateArchive') }}</button></div></ScreenCollection></div><div v-if="!reviewQueue.length && !archiveCandidates.length && !archives.length" class="muted-row">{{ t('workflow.noSubmissionsArchives') }}</div><div v-if="archives.length" class="archive-list"><ScreenCollection :items="archives" :row-height="72" v-slot="{ items: visibleItems }"><div v-for="archive in visibleItems" :key="archive.id" class="archive-row"><div><strong>{{ archiveTask(archive)?.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ archiveTask(archive)?.student_name }} · {{ archiveTask(archive)?.stage_name }} · {{ archiveSubmission(archive)?.submission_number || t('workflow.formalSubmission') }}<br />{{ archiveStatusLabel(archive.status) }}<template v-if="archive.manual_review?.status === 'required'"> · {{ t('workflow.archiveReviewRequired') }}</template><template v-else-if="archive.status === 'succeeded'"> · {{ formatDate(archive.completed_at || archive.created_at) }}</template></span></div><div class="stage-row-actions"><button v-if="archive.status === 'succeeded' && archive.download_url" class="tiny" @click="downloadArchiveFile(archive)"><span class="material-symbols-outlined" aria-hidden="true">download</span>{{ t('common.download') }}</button><button v-if="archive.status === 'failed'" class="tiny" @click="handleRetryArchive(archive)"><span class="material-symbols-outlined" aria-hidden="true">refresh</span>{{ t('workflow.retry') }}</button></div></div></ScreenCollection></div></section>
        </template>

        <template v-if="isTeacher && (activeSection === 'submissions' || (activeSection === 'overview' && panelTarget === 'review' && m2WorkflowEnabled))">
          <AsyncState v-if="!reviewQueue.length" state="empty" :title="t('workflow.noReviewSubmissions')" :detail="t('workflow.noReviewSubmissionsDetail')" />
          <ScreenCollection v-if="reviewQueue.length" :aria-label="t('workflow.pendingApproval')" :items="reviewQueue" :row-height="88" v-slot="{ items }"><button type="button" v-for="task in items" :key="task.id" class="panel task-row" :class="{ selected: selectedTask?.id === task.id }" @click="selectReviewTask(task)"><div class="task-state" :class="task.state"><span class="material-symbols-outlined" aria-hidden="true">rate_review</span></div><div class="task-copy"><div class="eyebrow-row"><span>{{ task.cohort_name }}</span><span>·</span><span>{{ task.stage_name }}</span></div><h3>{{ task.project_title || t('workflow.unnamedThesis') }}</h3><p>{{ task.current_submission?.number }} · {{ task.is_late ? t('workflow.late') : t('workflow.onTime') }}</p></div><div class="task-side"><span class="state-tag submitted">{{ t('workflow.pendingApproval') }}</span><span>{{ formatDate(task.updated_at) }}</span></div></button></ScreenCollection>
          <section v-if="archiveCandidates.length || archives.length" class="panel archive-panel" data-test="teacher-archives"><div class="panel-heading"><div><h3>{{ t('workflow.submissionsTitle') }}</h3><p>{{ t('workflow.archiveReviewDetail') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">inventory_2</span></div><div v-if="archiveCandidates.length" class="archive-list"><div v-for="task in archiveCandidates" :key="'archive-candidate-' + task.id" class="archive-row"><div><strong>{{ task.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ task.cohort_name }} · {{ task.stage_name }} · {{ task.current_submission?.number || t('workflow.formalSubmission') }}</span></div><button class="small primary" :disabled="saving" @click="handleArchive(task)">{{ saving ? t('workflow.archiveReviewWorking') : t('workflow.generateArchive') }}</button></div></div><div v-if="archives.length" class="archive-list"><div v-for="archive in archives" :key="'teacher-archive-' + archive.id" class="archive-row"><div><strong>{{ archiveTask(archive)?.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ archiveTask(archive)?.student_name }} · {{ archiveTask(archive)?.stage_name }} · {{ archiveSubmission(archive)?.submission_number || t('workflow.formalSubmission') }}<br />{{ archiveStatusLabel(archive.status) }}<template v-if="archive.manual_review?.status === 'required'"> · {{ t('workflow.archiveReviewRequired') }}</template></span></div><button v-if="archive.status === 'succeeded' && archive.download_url" class="tiny" @click="downloadArchiveFile(archive)">{{ t('common.download') }}</button><button v-if="archive.status === 'failed'" class="tiny" @click="handleRetryArchive(archive)">{{ t('workflow.retry') }}</button></div></div></section>
          <BaseModal presentation="drawer" :open="Boolean(selectedSubmission)" :title="t('workflow.pendingApproval')" @close="selectedSubmission = null"><ScreenDeck><section v-if="selectedSubmission" class="panel review-panel">
            <div class="panel-heading"><div><p class="workflow-kicker">{{ selectedSubmission.submission.submission_number }}</p><h3>{{ t('workflow.submitReview') }}</h3><p>{{ selectedSubmission.version.source_revision }} revision · {{ selectedSubmission.version.content_hash.slice(0, 12) }}</p></div><button class="small" @click="selectedSubmission = null">{{ t('workflow.backToList') }}</button></div>
            <div class="gate-strip"><div v-for="gate in (selectedSubmission.candidate?.gates || [])" :key="gate.key"><span>{{ gate.label }}</span><strong>{{ gate.passed ? t('workflow.approve') : t('workflow.handleGates') }}</strong></div><div><span>{{ t('workflow.bodyVersion') }}</span><strong>{{ t('workflow.readonlySnapshot') }}</strong></div></div>
            <div v-if="selectedSubmission.process_records_error" id="review-materials-error" class="error-banner" data-test="review-materials-error" role="alert">
              {{ t(requiredMaterialsUnavailable ? 'workflow.requiredMaterialsUnavailable' : 'workflow.reviewMaterialsUnavailable') }}
              <button class="small" :disabled="reviewMaterialsLoading" @click="retryReviewMaterials">{{ t('workflow.retryReviewMaterials') }}</button>
            </div>
            <ReviewEvidence
              :submission="selectedSubmission"
              :format-date="formatDate"
              :record-state="recordState"
              :document-downloadable="m2WorkflowEnabled && Boolean(selectedSubmission.candidate?.artifact?.docx_sha256)"
              :pdf-downloadable="optionalPdfEnabled"
              :selectable="selectedSubmission.submission.state === 'submitted'"
              :selected-block-id="commentForm.block_id"
              @download-material="downloadReviewMaterial"
              @download-document="downloadReviewDocument"
              @download-pdf="downloadReviewPdf"
              @select-block="selectCommentBlock"
            />
            <section v-if="selectedSubmission.prior_unresolved_comments?.length" class="prior-feedback" data-test="prior-feedback">
              <h4>{{ t('workflow.priorFeedback') }}</h4>
              <div v-for="comment in selectedSubmission.prior_unresolved_comments" :key="comment.id" class="comment-item" :class="comment.severity">
                <div><span class="state-tag" :class="comment.status">{{ comment.severity === 'blocking' ? t('workflow.blocking') : t('workflow.suggestion') }}</span><span class="comment-anchor">{{ comment.source_submission_number }} · {{ comment.source_section_title || t('workflow.fullText') }} · {{ t(`comment.${comment.status}`) }}</span></div>
                <p v-if="comment.source_text" class="prior-source">{{ t('workflow.priorSource', { text: comment.source_text }) }}</p>
                <p v-if="comment.block_id && commentAnchorLabel(comment) === t('workflow.commentAnchorMissing')">{{ t('workflow.currentAnchorMissing') }}</p>
                <blockquote v-if="comment.quoted_text" class="comment-quote">{{ comment.quoted_text }}</blockquote>
                <p>{{ comment.body }}</p>
                <div v-for="response in comment.responses || []" :key="response.id" class="comment-response"><small>{{ t('role.student') }} · {{ formatDate(response.created_at) }}</small><p>{{ response.body }}</p></div>
                <button class="tiny" type="button" @click.stop="resolveComment(comment)"><span class="material-symbols-outlined" aria-hidden="true">check</span>{{ t('workflow.markResolved') }}</button>
              </div>
            </section>
            <div class="comment-list"><div v-for="comment in selectedSubmission.comments" :key="comment.id" class="comment-item" :class="comment.severity"><div><span class="state-tag" :class="comment.status">{{ comment.severity === 'blocking' ? t('workflow.blocking') : t('workflow.suggestion') }}</span><span class="comment-anchor">{{ commentAnchorLabel(comment) }}</span></div><blockquote v-if="comment.quoted_text" class="comment-quote">{{ comment.quoted_text }}</blockquote><p>{{ comment.body }}</p><small>{{ t('workflow.replies', { count: comment.responses?.length || 0 }) }} · {{ comment.status }}</small><div v-for="response in comment.responses || []" :key="response.id" class="comment-response"><small>{{ t('role.student') }} · {{ formatDate(response.created_at) }}</small><p>{{ response.body }}</p></div><button v-if="comment.status !== 'resolved'" class="tiny" @click.stop="resolveComment(comment)"><span class="material-symbols-outlined" aria-hidden="true">check</span>{{ t('workflow.markResolved') }}</button></div><div v-if="!selectedSubmission.comments.length" class="muted-row">{{ t('workflow.noComments') }}</div></div>
            <template v-if="selectedSubmission.submission.state === 'submitted'">
              <div class="review-compose"><textarea v-model="commentForm.body" :aria-label="t('workflow.addComment')" rows="3" :placeholder="t('workflow.commentPlaceholder')" /><div v-if="selectedCommentTarget" class="comment-target"><span>{{ t('workflow.selectedCommentTarget', { section: selectedCommentTarget.sectionTitle }) }}<template v-if="selectedCommentTarget.text">：{{ selectedCommentTarget.text.slice(0, 80) }}</template></span><button type="button" class="tiny" @click="clearCommentTarget">{{ t('workflow.clearCommentTarget') }}</button></div><div class="review-compose-actions"><select v-model="commentForm.severity" :aria-label="t('workflow.commentSeverity')"><option value="info">{{ t('workflow.normalSuggestion') }}</option><option value="blocking">{{ t('workflow.blockingIssue') }}</option></select><button class="small" @click="handleComment"><span class="material-symbols-outlined" aria-hidden="true">add_comment</span>{{ t('workflow.addComment') }}</button></div></div><div class="review-decision"><textarea v-model="reviewForm.reason" :aria-label="t('workflow.reviewReasonPlaceholder')" rows="2" :placeholder="t('workflow.reviewReasonPlaceholder')" /><p v-if="unresolvedBlockingComments" id="review-blocking-feedback" role="status">{{ t('workflow.resolveBlockingBeforeApproval', { count: unresolvedBlockingComments }) }}</p><div class="review-decision-actions"><button class="danger" @click="handleReview('returned')"><span class="material-symbols-outlined" aria-hidden="true">undo</span>{{ t('workflow.returnForRevision') }}</button><button class="primary" :disabled="unresolvedBlockingComments > 0 || requiredMaterialsUnavailable" :aria-describedby="requiredMaterialsUnavailable ? 'review-materials-error' : unresolvedBlockingComments ? 'review-blocking-feedback' : undefined" @click="handleReview('approved')"><span class="material-symbols-outlined" aria-hidden="true">verified</span>{{ t('workflow.approveSubmission') }}</button></div></div>
            </template>
          </section>
          <section v-if="selectedSubmission && selectedSubmission.config?.required_manual_checks?.length" class="panel manual-check-panel"><div class="panel-heading"><div><p class="workflow-kicker">{{ t('workflow.reviewChecklist') }}</p><h3>{{ t('workflow.reviewChecklist') }}</h3><p>{{ t('workflow.reviewChecklistDetail') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">fact_check</span></div><div class="manual-checks"><label v-for="check in selectedSubmission.config.required_manual_checks" :key="check"><input v-model="manualConfirmations[check]" type="checkbox" />{{ check }}</label></div></section></ScreenDeck></BaseModal>
        </template>

        <template v-else-if="activeSection === 'overview'">
          <AsyncState v-if="!tasks.length" state="empty" :title="t('workflow.noStageTasks')" :detail="t('workflow.noStageTasksDetail')" />
          <ScreenCollection v-if="tasks.length" :aria-label="t('workflow.myTasks')" :items="tasks" :row-height="78" v-slot="{ items }" class="workflow-task-pages"><button v-for="task in items" :key="task.id" type="button" class="panel student-task student-task-summary" @click="taskDrawer = task" :aria-label="t('screen.details') + '：' + task.stage_name"><div class="task-copy"><strong>{{ task.project_title || t('workflow.thesisNotBound') }}</strong><span>{{ task.cohort_name }} · {{ task.stage_name }}</span></div><div class="task-summary-state"><span class="state-tag" :class="task.state">{{ stateLabel(task.state) }}</span><small>{{ deadlineLabel(task) }}</small></div><span class="material-symbols-outlined" aria-hidden="true">chevron_right</span></button></ScreenCollection><BaseModal presentation="drawer" :open="Boolean(taskDrawer)" :title="taskDrawer?.stage_name" @close="taskDrawer = null; candidateError = ''"><template v-if="taskDrawer"><section v-for="task in [taskDrawer]" :key="task.id" class="panel student-task" :class="task.state"><div class="task-topline"><div><div class="eyebrow-row"><span>{{ task.cohort_name }}</span><span>·</span><span>{{ task.stage_name }}</span><span v-if="task.is_late" class="late-label">{{ t('workflow.overdue') }}</span></div><h3>{{ task.project_title || t('workflow.thesisNotBound') }}</h3></div><span class="state-tag" :class="task.state">{{ stateLabel(task.state) }}</span></div><div class="task-meta"><span><span class="material-symbols-outlined" aria-hidden="true">schedule</span>{{ deadlineLabel(task) }}</span><span><span class="material-symbols-outlined" aria-hidden="true">description</span>{{ task.current_submission ? task.current_submission.number : t('workflow.noFormalSubmission') }}</span></div><div class="task-actions"><button v-if="!task.project_id" class="primary" @click="bindTask(task)"><span class="material-symbols-outlined" aria-hidden="true">edit_document</span>{{ t('workflow.startWriting') }}</button><RouterLink v-else class="small primary link-button" :to="'/write/' + task.project_id"><span class="material-symbols-outlined" aria-hidden="true">edit_note</span>{{ t('workflow.continueWriting') }}</RouterLink><button v-if="task.project_id && !task.acknowledged_snapshot_hash" class="small" @click="acknowledgeTask(task)"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('workflow.acknowledgeRequirements') }}</button><button v-if="m2WorkflowEnabled && task.project_id && ['in_progress', 'needs_revision'].includes(task.state)" class="small" :disabled="preparingCandidate" @click="prepareCandidate(task)"><span class="material-symbols-outlined" aria-hidden="true">send</span>{{ preparingCandidate ? t('workflow.precheckingSubmission') : t('workflow.precheckSubmission') }}</button><button class="small" @click="openMaterials(task)"><span class="material-symbols-outlined" aria-hidden="true">folder_managed</span>{{ t('workflow.processMaterials') }}</button><button v-if="task.current_submission" class="small" @click="viewHistory(task.current_submission.id)"><span class="material-symbols-outlined" aria-hidden="true">history</span>{{ t('workflow.viewRecords') }}</button><button v-if="canConfigure && task.effective_deadline_at && ['not_started', 'in_progress', 'needs_revision', 'submitted', 'approved', 'pending_archive', 'archived'].includes(task.state)" class="small" @click="openExtension(task)"><span class="material-symbols-outlined" aria-hidden="true">event_repeat</span>{{ t('workflow.extend') }}</button></div><p v-if="selectedTask?.id === task.id && candidateError" class="error-banner" role="alert">{{ candidateError }}</p><div v-if="extensionTask?.id === task.id" class="candidate-box extension-box"><div class="candidate-heading"><strong>{{ t('workflow.extendTitle') }}</strong><span class="muted-row">{{ t('workflow.extendDeadline') }} {{ deadlineLabel(task) }}</span></div><div class="candidate-gates"><input v-model="extensionForm.deadline" type="datetime-local" :aria-label="t('workflow.extendDeadline')" /><input v-model="extensionForm.reason" :placeholder="t('workflow.extendReason')" /></div><div class="form-actions"><button class="primary" :disabled="saving" @click="handleExtend(task)">{{ t('workflow.extendConfirm') }}</button><button class="small" @click="cancelExtension">{{ t('common.cancel') }}</button></div></div><div v-if="selectedTask?.id === task.id && selectedCandidate" class="candidate-box"><div class="candidate-heading"><strong>{{ t('workflow.precheck') }} {{ selectedCandidate.id.slice(-8) }}</strong><span class="state-tag" :class="selectedCandidate.status">{{ selectedCandidate.status === 'ready' ? t('workflow.readyToSubmit') : t('workflow.handleGates') }}</span></div><div class="candidate-gates"><div v-for="gate in selectedCandidate.gates" :key="gate.key" :class="{ passed: gate.passed, blocked: gate.blocking }"><span class="material-symbols-outlined" aria-hidden="true">{{ gate.passed ? 'check_circle' : 'error' }}</span><div><strong>{{ gate.label }}</strong><small>{{ gate.passed ? t('workflow.gateSatisfied') : (Array.isArray(gate.detail) ? gate.detail.join(' / ') : gate.detail) }}</small></div></div></div><label v-if="selectedCandidate.warning_count"> <input v-model="warningAcknowledged" type="checkbox" />{{ t('workflow.warningAcknowledged') }}</label><button class="primary" :disabled="selectedCandidate.status !== 'ready' || (selectedCandidate.warning_count && !warningAcknowledged) || submitting" @click="handleConfirmSubmission"><span class="material-symbols-outlined" aria-hidden="true">check</span>{{ submitting ? t('workflow.submitting') : t('workflow.confirmFormalSubmission') }}</button></div></section></template></BaseModal>
          <BaseModal presentation="drawer" :open="Boolean(materialsTask)" :title="t('screen.details')" @close="materialsTask = null"><section v-if="materialsTask" class="panel materials-panel"><div class="panel-heading"><div><p class="workflow-kicker">{{ t('workflow.processMaterials') }}</p><h3>{{ materialsTask.project_title || t('workflow.currentStage') }} · {{ t('workflow.materialList') }}</h3><p>{{ t('workflow.materialsDetail') }}</p></div><button class="icon-button" :title="t('workflow.closeMaterials')" :aria-label="t('workflow.closeMaterials')" @click="materialsTask = null"><span class="material-symbols-outlined" aria-hidden="true">close</span></button></div><div v-if="qualification" class="qualification-strip"><div><span>{{ t('workflow.defenseEligibility') }}</span><strong :class="qualification.eligible_for_application ? 'qualification-ok' : 'qualification-warn'">{{ qualification.eligible_for_application ? t('workflow.defenseEligible') : t('workflow.defenseIncomplete') }}</strong></div><span>{{ qualification.gates.filter((item) => item.passed).length }} / {{ qualification.gates.length }} {{ t('common.items') }}</span></div><div v-if="scoreSummary" class="score-summary"><div><span>{{ t('workflow.advisor') }}</span><strong>{{ scoreSummary.scores.advisor_score ?? '-' }}</strong></div><div><span>{{ t('workflow.reviewer') }}</span><strong>{{ scoreSummary.scores.reviewer_score ?? '-' }}</strong></div><div><span>{{ t('workflow.defenseGroup') }}</span><strong>{{ scoreSummary.scores.defense_score ?? '-' }}</strong></div><div><span>{{ t('workflow.overallScore') }}</span><strong>{{ scoreSummary.total_score ?? '-' }}</strong></div></div><div class="material-catalog"><div v-for="item in processCatalog.participant" :key="item.artifact_type" class="material-catalog-row"><div><strong>{{ item.name }}</strong><small>{{ item.phase }} · {{ item.description || item.source }}</small></div><span class="state-tag" :class="materialStateClass(item.artifact_type)">{{ materialState(item.artifact_type) }}</span></div></div><div class="material-compose"><div class="form-grid"><label>{{ t('workflow.materialType') }}<select v-model="materialForm.artifact_type" @change="loadMaterialForm(materialForm.artifact_type)"><option v-for="item in processCatalog.participant" :key="item.artifact_type" :value="item.artifact_type">{{ item.name }}</option></select></label><label>{{ t('workflow.materialTitle') }}<input v-model="materialForm.title" :placeholder="t('workflow.materialNamePlaceholder')" /></label></div><div v-if="materialSchema?.fields?.length" class="structured-material-form"><label v-for="field in (materialSchema?.fields || []).filter((item) => item.type !== 'table')" :key="field.key" class="structured-field"><span>{{ field.label }}<em v-if="field.required">{{ t('workflow.required') }}</em></span><select v-if="field.type === 'select'" v-model="materialData[field.key]"><option value="">{{ t('workflow.choose') }}</option><option v-for="option in field.options" :key="option" :value="option">{{ option }}</option></select><textarea v-else-if="field.type === 'textarea'" v-model="materialData[field.key]" rows="3" :placeholder="field.help || field.label" /><textarea v-else-if="field.type === 'json'" :value="materialJsonDrafts[field.key]" rows="4" :placeholder="field.help || t('workflow.enterJson')" @input="materialJsonDrafts[field.key] = $event.target.value" /><input v-else v-model="materialData[field.key]" :type="field.type === 'number' ? 'number' : field.type === 'datetime' ? 'datetime-local' : 'text'" :placeholder="field.help || field.label" /></label></div><div v-for="field in (materialSchema?.fields || []).filter((item) => item.type === 'table')" :key="field.key" class="structured-table-editor"><div class="table-editor-heading"><div><strong>{{ field.label }}<em v-if="field.required">{{ t('workflow.required') }}</em></strong><small>{{ field.help }}</small></div><button type="button" class="tiny" @click="addMaterialTableRow(field)"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('workflow.addRow') }}</button></div><div class="table-editor-scroll"><table><thead><tr><th>#</th><th v-for="column in field.columns || []" :key="column.key">{{ column.label }}</th><th></th></tr></thead><tbody><tr v-for="(row, rowIndex) in (materialTableDrafts[field.key] || [])" :key="rowIndex"><td>{{ rowIndex + 1 }}</td><td v-for="column in field.columns || []" :key="column.key"><textarea v-if="column.type === 'textarea'" v-model="row[column.key]" rows="2" :placeholder="column.label" /><input v-else v-model="row[column.key]" :type="column.type === 'number' ? 'number' : 'text'" :placeholder="column.label" /></td><td><button type="button" class="icon-button" :title="t('common.remove')" :aria-label="t('common.remove')" @click="removeMaterialTableRow(field, rowIndex)"><span class="material-symbols-outlined" aria-hidden="true">delete</span></button></td></tr><tr v-if="!(materialTableDrafts[field.key] || []).length"><td :colspan="(field.columns || []).length + 2" class="muted-row">{{ t('workflow.noDetails') }}</td></tr></tbody></table></div></div><label class="material-content-field">{{ t('workflow.additionalNotes') }}<textarea v-model="materialForm.content" rows="3" :placeholder="t('workflow.additionalNotesPlaceholder')" /></label><label class="file-picker">{{ t('workflow.originalAttachment') }}<input id="process-material-file" type="file" accept=".doc,.docx,.pdf,.zip,.pptx,.xlsx,.xls,.png,.jpg,.jpeg,.txt" @change="handleMaterialFile" /><small>{{ materialFile?.name || t('workflow.optionalUpload') }}</small></label><button class="primary" :disabled="saving" @click="saveMaterial"><span class="material-symbols-outlined" aria-hidden="true">upload_file</span>{{ t('workflow.saveSubmitMaterial') }}</button></div><div v-if="processRecords.length" class="record-history"><h4>{{ t('workflow.myMaterialRecords') }}</h4><div v-for="record in processRecords" :key="record.id" class="record-row"><div><strong>{{ record.artifact_name }}</strong><span>{{ recordState(record.state) }} · {{ formatDate(record.updated_at || record.created_at) }}</span></div><div class="record-attachments"><button v-if="record.structured_form" class="tiny" @click.stop="exportMaterial(record)"><span class="material-symbols-outlined" aria-hidden="true">description</span>{{ t('workflow.exportFilledForm') }}</button><button v-for="attachment in record.attachments" :key="attachment.id" class="tiny" @click.stop="downloadMaterial(attachment)"><span class="material-symbols-outlined" aria-hidden="true">download</span>{{ attachment.name }}</button></div></div></div></section></BaseModal>
        </template>
        <section v-if="processCatalog.reference?.length && activeSection === 'overview' && !panelFocused" class="panel reference-panel">
          <div class="panel-heading"><div><h3>{{ t('workflow.referencesTitle') }}</h3><p>{{ t('workflow.referencesDetail') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">menu_book</span></div>
          <div class="reference-list"><ScreenCollection :items="processCatalog.reference" :row-height="64" v-slot="{ items: visibleItems }"><div v-for="item in visibleItems" :key="item.artifact_type" class="reference-row"><div><strong>{{ item.name }}</strong><span>{{ item.phase }}</span></div><small>{{ item.source }}</small></div></ScreenCollection></div>
        </section>

      </ScreenDeck>

      <aside class="workflow-side">
        <section v-if="activeSection === 'messages'" class="panel notification-panel workflow-wide-panel">
          <div class="panel-heading"><div><h3>{{ t('workflow.messagesTitle') }}</h3><p>{{ t('workflow.messagesDetail') }}</p></div><span class="unread-count">{{ notifications.unread || 0 }}</span></div>
          <div v-if="!notifications.items?.length" class="empty-state"><span class="material-symbols-outlined" aria-hidden="true">notifications_none</span><h3>{{ t('notification.empty') }}</h3><p>{{ t('workflow.messagesDetail') }}</p></div>
          <ScreenCollection :items="notifications.items" :row-height="64" v-slot="{ items: visibleItems }"><button v-for="item in visibleItems" :key="item.id" class="notification-row" :class="{ unread: !item.read_at }" @click="readNotification(item)"><span class="material-symbols-outlined" aria-hidden="true">{{ item.data?.event_type?.includes('review') ? 'rate_review' : 'notifications' }}</span><span><strong>{{ item.data?.title || t('workflow.workflowMessage') }}</strong><small>{{ formatDate(item.created_at) }}</small></span></button></ScreenCollection>
        </section>
        <section v-if="canManage && activeSection === 'reports'" class="panel report-panel workflow-wide-panel">
          <div class="panel-heading"><div><h3>{{ t('workflow.realtimeData') }}</h3><p>{{ t('workflow.realtimeDataDetail') }}</p></div><button class="icon-button" :title="t('workflow.refreshStats')" :aria-label="t('workflow.refreshStats')" @click="loadReport"><span class="material-symbols-outlined" aria-hidden="true">refresh</span></button></div>
          <label class="report-filter">{{ t('workflow.reportCohort') }}<select v-model="reportCohortId" @change="loadReport"><option value="">{{ t('workflow.selectCohort') }}</option><option v-for="cohort in cohorts" :key="cohort.id" :value="cohort.id">{{ cohort.name }}</option></select></label>
          <div v-if="report" class="report-grid"><div><strong>{{ report.member_count }}</strong><span>{{ t('workflow.member') }}</span></div><div><strong>{{ report.state_counts?.submitted || 0 }}</strong><span>{{ t('workflow.pendingApproval') }}</span></div><div><strong>{{ report.state_counts?.needs_revision || 0 }}</strong><span>{{ t('workflow.needsRevision') }}</span></div><div><strong>{{ report.state_counts?.archived || 0 }}</strong><span>{{ t('workflow.archiveCompleted') }}</span></div></div>
          <div v-if="report" class="report-metrics"><span>{{ t('workflow.onTimeRate') }} <strong>{{ rate(report.metrics?.on_time_first_submission_rate) }}</strong></span><span>{{ t('workflow.archiveRate') }} <strong>{{ rate(report.metrics?.archive_completion_rate) }}</strong></span></div>
          <AsyncState v-else-if="cohorts.length" state="empty" :title="t('workflow.selectReportCohort')" :detail="t('workflow.selectReportCohortDetail')" />
          <AsyncState v-else state="empty" :title="t('workflow.noReportCohorts')" :detail="t('workflow.noReportCohortsDetail')" />
        </section>
        <section v-if="!canManage && activeSection === 'submissions'" class="panel history-panel workflow-wide-panel">
          <div class="panel-heading"><div><h3>{{ t('workflow.historyTitle') }}</h3><p>{{ t('workflow.historyDetail') }}</p></div><span class="material-symbols-outlined panel-icon" aria-hidden="true">history</span></div>
          <div v-if="!submissions.length" class="empty-state"><span class="material-symbols-outlined" aria-hidden="true">history</span><h3>{{ t('workflow.noSubmissionRecords') }}</h3><p>{{ t('workflow.noSubmissionRecordsDetail') }}</p></div>
          <ScreenCollection :items="submissions" :row-height="64" v-slot="{ items: visibleItems }"><button v-for="submission in visibleItems" :key="submission.id" type="button" class="history-row history-row-button" @click="viewHistory(submission.id)"><div><strong>{{ submission.submission_number }}</strong><span>{{ formatDate(submission.submitted_at) }}</span></div><span class="state-tag" :class="submission.state">{{ submissionStateLabel(submission.state) }}</span></button></ScreenCollection>
          <div v-if="!isTeacher && archives.length" class="student-archives" data-test="student-archives">
            <h3>{{ t('workflow.studentArchives') }}</h3>
            <div v-for="archive in archives" :key="archive.id" class="archive-row">
              <div><strong>{{ archiveTask(archive)?.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ archiveSubmission(archive)?.submission_number || t('workflow.formalSubmission') }} · {{ archiveStatusLabel(archive.status) }}<template v-if="archive.manual_review?.status === 'required'"> · {{ t('workflow.studentArchiveUnavailable') }}</template></span></div>
              <button v-if="archive.status === 'succeeded' && archive.download_url" class="tiny" @click="downloadArchiveFile(archive)"><span class="material-symbols-outlined" aria-hidden="true">download</span>{{ t('common.download') }}</button>
            </div>
          </div>
        </section>
        <section v-if="activeSection === 'overview'" class="panel workspace-shortcuts">
          <div class="panel-heading"><div><h3>{{ t('workflow.shortcuts') }}</h3></div></div>
          <RouterLink v-if="canManage" to="/organizations"><span class="material-symbols-outlined" aria-hidden="true">groups</span>{{ t('workflow.membersOrg') }}</RouterLink><RouterLink v-if="canManage" to="/templates"><span class="material-symbols-outlined" aria-hidden="true">library_books</span>{{ t('workflow.templatesStandards') }}</RouterLink><RouterLink v-if="canManage" to="/audit"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('workflow.audit') }}</RouterLink><RouterLink v-if="auth.user?.role === 'admin'" to="/operations"><span class="material-symbols-outlined" aria-hidden="true">monitor_heart</span>{{ t('workflow.operations') }}</RouterLink>
          <RouterLink v-if="isTeacher && m2WorkflowEnabled" :to="{ path: '/workflow', query: { panel: 'submissions' } }"><span class="material-symbols-outlined" aria-hidden="true">rate_review</span>{{ t('workflow.submitReview') }}</RouterLink><RouterLink v-if="isTeacher" :to="{ path: '/workflow', query: { panel: 'progress' } }"><span class="material-symbols-outlined" aria-hidden="true">groups</span>{{ t('workflow.studentProgress') }}</RouterLink><RouterLink v-if="isTeacher" to="/policies"><span class="material-symbols-outlined" aria-hidden="true">menu_book</span>{{ t('workflow.writingStandards') }}</RouterLink>
          <RouterLink v-if="!canManage && !isTeacher" :to="{ path: '/workflow', query: { panel: 'materials' } }"><span class="material-symbols-outlined" aria-hidden="true">folder_managed</span>{{ t('workflow.processMaterials') }}</RouterLink><RouterLink v-if="!canManage && !isTeacher" to="/policies"><span class="material-symbols-outlined" aria-hidden="true">menu_book</span>{{ t('workflow.writingStandards') }}</RouterLink>
        </section>
      </aside>
    </div>

    <BaseModal :open="createCohortOpen" size="small" :title="t('workflow.createCohortTitle')" @close="createCohortOpen = false">          <section class="create-panel">
            <p class="hint">{{ t('workflow.createCohortDetail') }}</p>
            <div class="form-grid cohort-create-fields">
              <label>{{ t('workflow.cohortName') }}<input v-model="cohortForm.name" :placeholder="t('workflow.cohortNamePlaceholder')" /></label>
              <label>{{ t('workflow.organizationScope') }}<select v-model="cohortForm.organization_id"><option value="">{{ t('workflow.selectOrganization') }}</option><option v-for="unit in organizations" :key="unit.id" :value="unit.id">{{ unit.name }}</option></select></label>
              <label>{{ t('workflow.academicYear') }}<input v-model="cohortForm.academic_year" placeholder="2027" /></label>
            </div>
            <div class="form-actions"><button class="primary" :disabled="saving || !cohortForm.name || !cohortForm.organization_id" @click="handleCreateCohort"><span class="material-symbols-outlined" aria-hidden="true">add</span>{{ t('workflow.createCohort') }}</button></div>
          </section></BaseModal>
    <BaseModal :open="Boolean(bindTarget)" size="small" :title="t('workflow.bindThesisTitle')" @close="bindTarget = null"><form v-if="bindTarget" @submit.prevent="confirmBind"><label class="field">{{ t('workflow.thesisTitle') }}<input v-model="bindTitle" :placeholder="t('workflow.thesisTitlePlaceholder')" required /></label><div class="form-actions"><button class="primary" :disabled="saving"><span class="material-symbols-outlined" aria-hidden="true">link</span>{{ t('workflow.createBind') }}</button></div></form></BaseModal>

    <BaseModal :open="Boolean(teacherFieldsRecord)" :title="t('workflow.midtermTeacherFieldsTitle')" @close="closeMidtermTeacherFields">
      <section v-if="teacherFieldsRecord">
        <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
        <p class="hint">{{ teacherFieldsRecord.student_id }} · {{ teacherFieldsRecord.data?.topic || '' }}</p>
        <label class="structured-field"><span>{{ t('workflow.midtermTeacherOpinion') }}</span><textarea v-model="midtermTeacherFields.advisor_comment" rows="4" maxlength="4000" /></label>
        <label class="structured-field"><span>{{ t('workflow.midtermTeacherScore') }}</span><input v-model.number="midtermTeacherFields.score" type="number" min="0" max="100" step="1" /></label>
        <div class="form-actions teacher-fields-actions"><button class="primary" :disabled="saving" @click="saveMidtermTeacherFields"><span class="material-symbols-outlined" aria-hidden="true">save</span>{{ t('workflow.saveMidtermTeacherFields') }}</button></div>
      </section>
    </BaseModal>
    <ReasonDialog
      :open="reasonDialog.open"
      :title="reasonDialog.title"
      :description="reasonDialog.description"
      :default-value="reasonDialog.defaultValue"
      :confirm-label="reasonDialog.confirmLabel"
      @confirm="confirmReason"
      @cancel="closeReasonDialog"
    />
    <ConfirmDialog
      :open="confirmDialog.open"
      :title="confirmDialog.title"
      :description="confirmDialog.description"
      :confirm-label="confirmDialog.confirmLabel"
      :danger="confirmDialog.danger"
      :busy="confirmBusy"
      @confirm="confirmAction"
      @cancel="closeConfirmDialog"
    />
  </div>
  <BaseModal presentation="drawer" :open="Boolean(selectedSubmission && !isTeacher)" :title="t('screen.details')" @close="selectedSubmission = null"><section v-if="selectedSubmission && !isTeacher" class="panel history-detail">
    <div class="panel-heading"><div><h3>{{ selectedSubmission.submission.submission_number }}</h3><p>{{ submissionStateLabel(selectedSubmission.submission.state) }} · {{ formatDate(selectedSubmission.submission.submitted_at) }} · revision {{ selectedSubmission.version.source_revision }}</p></div><button class="small" @click="selectedSubmission = null">{{ t('workflow.closeDetail') }}</button></div>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="selectedSubmission.version_history_error" class="error-banner" role="alert">{{ t('workflow.versionHistoryUnavailable') }}</p>
    <div v-if="selectedSubmission.process_records_error" class="error-banner" role="alert">
      {{ t('workflow.submittedMaterialsUnavailable') }}
      <button class="small" :disabled="reviewMaterialsLoading" @click="retryReviewMaterials">{{ t('workflow.retryReviewMaterials') }}</button>
    </div>
    <button v-if="m2WorkflowEnabled && auth.user?.role === 'student' && selectedSubmission.submission.state === 'submitted'" type="button" class="small" @click="handleWithdrawSubmission"><span class="material-symbols-outlined" aria-hidden="true">undo</span>{{ t('workflow.withdrawSubmission') }}</button>
    <div class="gate-strip"><div v-for="gate in (selectedSubmission.candidate?.gates || [])" :key="gate.key"><span>{{ gate.label }}</span><strong>{{ gate.passed ? t('workflow.approve') : t('workflow.handleGates') }}</strong></div><div><span>{{ t('workflow.bodySummary') }}</span><strong>{{ selectedSubmission.version.content_hash.slice(0, 12) }}</strong></div></div>
    <SubmissionFeedback :key="selectedSubmission.submission.id" :detail="selectedSubmission" :can-reply="m2WorkflowEnabled && auth.user?.role === 'student'" @replied="updateFeedbackComment" />
    <ReviewEvidence :submission="selectedSubmission" :format-date="formatDate" :record-state="recordState"
                    :document-downloadable="m2WorkflowEnabled && Boolean(selectedSubmission.candidate?.artifact?.docx_sha256)"
                    :pdf-downloadable="optionalPdfEnabled"
                    @download-material="downloadReviewMaterial" @download-document="downloadReviewDocument" @download-pdf="downloadReviewPdf" />
    <div v-if="versionHistory.length > 1" class="version-compare"><div class="panel-heading"><div><h4>{{ t('workflow.versionCompare') }}</h4></div><button class="small" @click="compareSelectedVersions"><span class="material-symbols-outlined" aria-hidden="true">compare</span>{{ t('workflow.compare') }}</button></div><div class="compare-selects"><label>{{ t('workflow.baselineVersion') }}<select v-model="versionSelection.left"><option v-for="version in versionHistory" :key="version.id" :value="version.id">revision {{ version.source_revision }} · {{ formatDate(version.created_at) }}</option></select></label><label>{{ t('workflow.compareVersion') }}<select v-model="versionSelection.right"><option v-for="version in versionHistory" :key="version.id" :value="version.id">revision {{ version.source_revision }} · {{ formatDate(version.created_at) }}</option></select></label></div><div v-if="versionCompare" class="compare-result"><p>{{ versionCompare.diff.changed ? t('workflow.detectedChanges') : t('workflow.sameContent') }}</p><div v-for="section in versionCompare.diff.sections" :key="section.key" class="compare-section"><strong>{{ section.key }} · {{ versionDiffLabel(section.status) }}</strong><div v-for="block in (section.blocks || [])" :key="block.id" class="compare-block"><span>{{ block.id }} · {{ versionDiffLabel(block.status) }}</span><p v-if="block.status === 'changed'">{{ diffBlockText(block.before) }} → {{ diffBlockText(block.after) }}</p></div></div><div v-if="versionCompare.diff.attachments.added.length || versionCompare.diff.attachments.removed.length || versionCompare.diff.attachments.changed.length" class="compare-attachments">{{ t('workflow.attachmentChanges', { added: versionCompare.diff.attachments.added.length, removed: versionCompare.diff.attachments.removed.length, changed: versionCompare.diff.attachments.changed.length }) }}</div></div></div>
    <div v-if="selectedSubmission.submission.state === 'returned' && versionHistory.length" class="history-restore"><span>{{ t('workflow.restoreReturned') }}</span><button class="small" @click="restoreHistoricalVersion(versionHistory.find((version) => version.id === versionSelection.right))"><span class="material-symbols-outlined" aria-hidden="true">restore</span>{{ t('workflow.restoreSelected') }}</button></div>
  </section></BaseModal>
</template>

<script setup>
import BaseModal from '../components/base/BaseModal.vue'
import ScreenCollection from '../components/ScreenCollection.vue'
import ScreenDeck from '../components/ScreenDeck.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { auth } from '../auth'
import AsyncState from '../components/AsyncState.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import DeadlineLabel from '../components/DeadlineLabel.vue'
import MemberImportWizard from '../components/MemberImportWizard.vue'
import ReasonDialog from '../components/ReasonDialog.vue'
import ReviewEvidence from '../components/ReviewEvidence.vue'
import SubmissionFeedback from '../components/SubmissionFeedback.vue'
import StageConfigForm from '../components/StageConfigForm.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { workflowPageTitle } from '../shared/pageTitles'
import { useI18n } from '../i18n'
import {
  addCohortMember, acknowledgeTaskRequirements, bindTaskProject, confirmSubmission, createArchive,
  extendTaskDeadline,
  createCohort, createCohortProcessRecord, createCohortStage, createParticipantProcessRecord, createSubmissionCandidate, createSubmissionComment,
  decideProcessRecord, downloadProcessRecordAttachment, downloadSubmissionDocument, downloadSubmissionPdf, downloadSubmissionMaterial, updateMidtermTeacherFields,
  exportProcessRecord, fetchProcessRecordForm, fetchCohortProcessRecordForm, fetchParticipantScoreSummary,
  fetchCohort, fetchCohortProcessRecords, fetchCohorts, fetchMyTasks, fetchOrganizations, fetchProjectVersions, fetchSubmission, fetchSubmissions, fetchWorkflowCapabilities,
  compareProjectVersions,
  fetchArchive, fetchArchives, fetchParticipantProcessRecords, fetchParticipantQualification, fetchProcessCatalog,
  fetchProcessRecordInbox, fetchTemplates, fetchWorkflowNotifications, fetchWorkflowReport, markWorkflowNotificationRead,
  previewCohortStage, publishCohortStage, createStageVersion, publishStageVersion, reviewSubmission, retryArchive, updateSubmissionCommentStatus, withdrawSubmission,
  submitProcessRecord, uploadProcessRecordAttachment, downloadArchive, reopenStageParticipant,
  closeCohort, importCohortMembers, reopenCohort, restoreProjectVersion,
} from '../api'

const { t, formatDate: formatLocaleDate } = useI18n()

const loading = ref(false)
const m2WorkflowEnabled = ref(false)
const optionalPdfEnabled = ref(false)
const saving = ref(false)
const submitting = ref(false)
const preparingCandidate = ref(false)
const candidateError = ref('')
const error = ref('')
const notice = ref('')
const tasks = ref([])
const cohorts = ref([])
const organizations = ref([])
const templates = ref([])
const submissions = ref([])
const archives = ref([])
const notifications = ref({ items: [], unread: 0 })
const processCatalog = ref({ participant: [], cohort: [] })
const processRecords = ref([])
const cohortRecords = ref([])
const processInbox = ref([])
const qualification = ref(null)
const scoreSummary = ref(null)
const materialsTask = ref(null)
const materialFile = ref(null)
const materialSchema = ref(null)
const materialData = reactive({})
const materialJsonDrafts = reactive({})
const materialTableDrafts = reactive({})
const managementSchema = ref(null)
const managementData = reactive({})
const managementJsonDrafts = reactive({})
const managementTableDrafts = reactive({})
const selectedCohort = ref(null)
const cohortDrawer = ref(null)
const taskDrawer = ref(null)
const selectedTask = ref(null)
const selectedCandidate = ref(null)
const submissionAttempt = ref(null)
const selectedSubmission = ref(null)
const unresolvedBlockingComments = computed(() => [
  ...(selectedSubmission.value?.comments || []),
  ...(selectedSubmission.value?.prior_unresolved_comments || []),
].filter((comment) => comment.severity === 'blocking' && comment.status !== 'resolved').length)
const requiredMaterialsUnavailable = computed(() => Boolean(selectedSubmission.value?.process_records_error))
const reviewMaterialsLoading = ref(false)
const versionHistory = ref([])
const versionCompare = ref(null)
const versionSelection = reactive({ left: '', right: '' })
const bindTarget = ref(null)
const bindTitle = ref('')
const preview = ref(null)
const upgradeStage = ref(null)
const stageUpgradePreview = ref(null)
const stageUpgradeParticipantIds = ref([])
const report = ref(null)
const reportCohortId = ref('')
const warningAcknowledged = ref(false)
const manualConfirmations = reactive({})
const createCohortOpen = ref(false)
const cohortForm = reactive({ name: '', organization_id: '', academic_year: '' })
const memberForm = reactive({ student_ref: '', teacher_ref: '', reason: '' })
const memberImportText = ref('')
const memberImportResult = ref(null)
const stageForm = reactive({ key: 'draft', name: t('workflow.defaultStageName'), open_at: '', deadline_at: '', revision_days: 7, template_id: '', required_manual_checks: [], required_materials_text: '', manual_checks_text: '', is_final: false, allow_early_write: false })
const stageUpgradeForm = reactive({ deadline_at: '', revision_days: 7, reason: '' })
const commentForm = reactive({ body: '', severity: 'info', block_id: '' })
const selectedCommentTarget = ref(null)
const reviewForm = reactive({ reason: '' })
const extensionTask = ref(null)
const extensionForm = reactive({ deadline: '', reason: '' })
const materialForm = reactive({ artifact_type: 'task_book', title: '', content: '' })
const managementForm = reactive({ artifact_type: 'topic_review_summary', title: '', content: '' })
const processDecisionReason = ref('')
const teacherFieldsRecord = ref(null)
const midtermTeacherFields = reactive({ advisor_comment: '', score: '' })
const reasonDialog = reactive({ open: false, title: '', description: '', defaultValue: '', confirmLabel: '', action: null })
const confirmBusy = ref(false)
const confirmDialog = reactive({ open: false, title: '', description: '', confirmLabel: '', danger: false, action: null })

const route = useRoute()
const router = useRouter()
const isTeacher = computed(() => auth.user?.role === 'teacher')
const canManage = computed(() => auth.user?.role === 'admin')
const canReviewArchives = computed(() => auth.user?.role === 'admin' || (auth.user?.role === 'teacher' && auth.user?.status === 'active'))
const canConfigure = computed(() => canManage.value || (auth.user?.role === 'teacher' && auth.user?.status === 'active'))
const roleLabel = computed(() => t(`role.${auth.user?.role || 'user'}`))
const panelTarget = computed(() => String(route.query.panel || ''))
const activeSection = computed(() => {
  if (panelTarget.value === 'messages') return 'messages'
  if (panelTarget.value === 'reports' && canManage.value) return 'reports'
  if (!m2WorkflowEnabled.value && ['submissions', 'archives'].includes(panelTarget.value)) return 'overview'
  // 侧边栏「待审核」指向的就是审核队列，它就渲染在 overview 区（教师 tab 的
  // 「待审核」、管理员的批次与阶段同处一页）。这里必须落回 overview：
  // 曾把它映射到 submissions，结果 URL 写 review、高亮的却是「提交与归档」，
  // 内容也是提交与归档，URL 与内容依旧错位。
  if (['submissions', 'archives'].includes(panelTarget.value)) return 'submissions'
  return 'overview'
})
// tab 高亮不能直接复用 activeSection：section 与 tab 不是一一对应——
// 教师/学生的「待审核」和「我的任务」共用 overview section，两者的 tab key 都是
// 'overview'，而 'review' 这个 URL 值对应的正是 overview 那个 tab。
// 这里按 URL 语义单独算出应当高亮的 tab key，避免再次出现 URL 与界面错位。
const activeTabKey = computed(() => {
  const panel = panelTarget.value
  if (!m2WorkflowEnabled.value && ['review', 'submissions', 'archives'].includes(panel)) return 'overview'
  if (panel === 'messages') return 'messages'
  if (panel === 'reports') return 'reports'
  if (panel === 'submissions' || panel === 'archives') return canManage.value ? 'archives' : 'submissions'
  // review / progress / materials 都没有独立 tab，内容都在 overview 区
  return 'overview'
})
// 侧边栏还有 review / progress / materials 三个入口，它们都不是独立 section：
// 内容都渲染在 overview 区，但都不该让批次配置区挡在前面——标题写着「审核台」
// 而正文第一屏是「新建批次」，同样属于 URL/标题与内容错位。
const panelFocused = computed(() => ['review', 'progress', 'materials'].includes(panelTarget.value))
const workflowTabs = computed(() => {
  const tabs = [{
    key: 'overview',
    section: 'overview',
    label: canManage.value ? t('workflow.cohortsStages') : isTeacher.value ? t('workflow.review') : t('workflow.myTasks'),
    icon: canManage.value ? 'dashboard' : isTeacher.value ? 'rate_review' : 'assignment',
  }]
  if (m2WorkflowEnabled.value) tabs.push({
    key: canManage.value ? 'archives' : 'submissions', section: 'submissions',
    label: t('workflow.submissionsArchives'), icon: 'inventory_2',
  })
  if (canManage.value) tabs.push({ key: 'reports', section: 'reports', label: t('workflow.reports'), icon: 'monitoring' })
  tabs.push({ key: 'messages', section: 'messages', label: t('workflow.messages'), icon: 'notifications' })
  return tabs
})
const sectionKicker = computed(() => activeSection.value === 'reports' ? t('workflow.reports') : activeSection.value === 'messages' ? t('workflow.messages') : activeSection.value === 'submissions' ? t('workflow.submissionsArchives') : panelTarget.value === 'review' ? t('review.pending') : panelTarget.value === 'progress' ? t('workspace.studentProgressKicker') : panelTarget.value === 'materials' ? t('workflow.processInboxTitle') : canConfigure.value ? t('workflow.cohortsStages') : isTeacher.value ? t('workflow.teacherTodo') : t('workflow.myTasks'))
const sectionTitle = computed(() => workflowPageTitle(panelTarget.value, auth.user, t))
const openTaskCount = computed(() => tasks.value.filter((item) => !['approved', 'archived'].includes(item.state)).length)
const reviewQueue = computed(() => tasks.value.filter((item) => item.state === 'submitted'))
const archiveCandidates = computed(() => canReviewArchives.value && tasks.value.filter((item) =>
  item.state === 'pending_archive' && item.current_submission
  && !archives.value.some((archive) => archive.submission_id === item.current_submission.id && ['queued', 'running', 'succeeded'].includes(archive.status)),
))
const reopenCandidates = computed(() => m2WorkflowEnabled.value ? tasks.value.filter((item) => ['approved', 'pending_archive', 'archived'].includes(item.state)) : [])

function selectSection(key) {
  const query = { ...route.query }
  if (key === 'overview' && isTeacher.value && m2WorkflowEnabled.value) query.panel = 'review'
  else if (key === 'overview') delete query.panel
  else query.panel = key
  router.replace({ query })
}

async function loadAll() {
  loading.value = true; error.value = ''; notice.value = ''
  try {
    const capabilities = await fetchWorkflowCapabilities().catch(() => ({}))
    m2WorkflowEnabled.value = capabilities?.m2_workflow_enabled === true
    optionalPdfEnabled.value = capabilities?.optional_pdf_enabled === true
    const inboxPromise = isTeacher.value || canManage.value ? fetchProcessRecordInbox() : Promise.resolve([])
    const archivePromise = m2WorkflowEnabled.value
      ? fetchArchives().then(items => ({ items })).catch(archiveError => ({ items: [], error: archiveError }))
      : Promise.resolve({ items: [] })
    const [taskData, notificationData, submissionData, catalogData, inboxData, archiveResult] = await Promise.all([fetchMyTasks(), fetchWorkflowNotifications(), fetchSubmissions(), fetchProcessCatalog(), inboxPromise, archivePromise])
    tasks.value = taskData; notifications.value = notificationData; submissions.value = submissionData; processCatalog.value = catalogData; processInbox.value = inboxData; archives.value = archiveResult.items
    if (archiveResult.error) error.value = archiveResult.error.message || t('workflow.archivesLoadFailed')
    if (taskDrawer.value) taskDrawer.value = tasks.value.find(item => item.id === taskDrawer.value.id) || null
    if (canConfigure.value) {
      const [cohortData, orgData, templateData] = await Promise.all([
        fetchCohorts(), fetchOrganizations(), fetchTemplates(),
      ])
      cohorts.value = await Promise.all(cohortData.map(async (item) => fetchCohort(item.id)))
      // Refresh open details by identity without reopening a drawer the user closed.
      if (cohortDrawer.value) cohortDrawer.value = cohorts.value.find(item => item.id === cohortDrawer.value.id) || null
      if (selectedCohort.value) selectedCohort.value = cohorts.value.find(item => item.id === selectedCohort.value.id) || null
      organizations.value = orgData; templates.value = templateData
      if (!stageForm.template_id && templateData.length) stageForm.template_id = templateData.find((item) => item.admission?.can_use_for_draft !== false)?.id || ''
      if (canManage.value) {
        if (!reportCohortId.value && cohorts.value[0]?.id) reportCohortId.value = cohorts.value[0].id
        if (reportCohortId.value) await loadReport()
      }
    }
  } catch (e) { error.value = e.message } finally { loading.value = false }
}

async function handleCreateCohort() {
  saving.value = true
  try { await createCohort(cohortForm); createCohortOpen.value = false; cohortForm.name = ''; await loadAll(); notice.value = t('workflow.cohortCreated') } catch (e) { error.value = e.message } finally { saving.value = false }
}

function openReasonDialog(config, action) {
  Object.assign(reasonDialog, { ...config, open: true, action })
}

function closeReasonDialog() {
  reasonDialog.open = false
  reasonDialog.action = null
}

async function confirmReason(reason) {
  const action = reasonDialog.action
  closeReasonDialog()
  if (action) await action(reason)
}

function openConfirmDialog(config, action) {
  Object.assign(confirmDialog, { ...config, open: true, action })
}

function closeConfirmDialog() {
  if (confirmBusy.value) return
  confirmDialog.open = false
  confirmDialog.action = null
}

async function confirmAction() {
  const action = confirmDialog.action
  if (!action || confirmBusy.value) return
  confirmBusy.value = true
  try { await action() } catch (e) { error.value = e.message } finally { confirmBusy.value = false; closeConfirmDialog() }
}

function handleCloseCohort(cohort) {
  openReasonDialog({ title: t('workflow.closeCohort'), description: t('workflow.closeCohortDescription', { name: cohort.name }), defaultValue: t('workflow.closeCohortDefault'), confirmLabel: t('workflow.confirmClose') }, async (reason) => {
    saving.value = true
    try { await closeCohort(cohort.id, reason); await loadAll(); notice.value = t('workflow.cohortClosed') } catch (e) { error.value = e.message } finally { saving.value = false }
  })
}

function handleReopenCohort(cohort) {
  openReasonDialog({ title: t('workflow.reopenCohort'), description: t('workflow.reopenCohortDescription', { name: cohort.name }), defaultValue: t('workflow.reopenCohortDefault'), confirmLabel: t('workflow.confirmReopen') }, async (reason) => {
    saving.value = true
    try { await reopenCohort(cohort.id, reason); await loadAll(); notice.value = t('workflow.cohortReopened') } catch (e) { error.value = e.message } finally { saving.value = false }
  })
}

function handleReopenParticipant(task) {
  openReasonDialog({ title: t('workflow.reopenTask'), description: t('workflow.reopenTaskDescription', { name: task.project_title || t('home.untitledThesis') }), defaultValue: t('workflow.reopenTaskDefault'), confirmLabel: t('workflow.confirmReopen') }, async (reason) => {
    saving.value = true
    try { await reopenStageParticipant(task.id, reason); await loadAll(); notice.value = t('workflow.taskReopened') } catch (e) { error.value = e.message } finally { saving.value = false }
  })
}

async function selectCohort(cohort) {
  cohortDrawer.value = null
  try {
    selectedCohort.value = await fetchCohort(cohort.id)
    preview.value = null
    if (canManage.value) {
      cohortRecords.value = await fetchCohortProcessRecords(cohort.id)
      await loadManagementForm(managementForm.artifact_type)
      await loadReport()
    }
  } catch (e) { error.value = e.message }
}

async function loadManagementForm(artifactType) {
  if (!selectedCohort.value) return
  try {
    const result = await fetchCohortProcessRecordForm(selectedCohort.value.id, artifactType)
    managementSchema.value = result.schema
    Object.keys(managementData).forEach((key) => delete managementData[key])
    Object.keys(managementJsonDrafts).forEach((key) => delete managementJsonDrafts[key])
    Object.keys(managementTableDrafts).forEach((key) => delete managementTableDrafts[key])
    Object.assign(managementData, result.prefill || {})
    for (const field of result.schema?.fields || []) {
      if (field.type === 'json') managementJsonDrafts[field.key] = JSON.stringify(managementData[field.key] ?? [], null, 2)
      if (field.type === 'table') managementTableDrafts[field.key] = Array.isArray(managementData[field.key])
        ? managementData[field.key].map((row) => ({ ...row }))
        : []
    }
  } catch (e) { error.value = e.message }
}

async function handleCreateStage() {
  saving.value = true
  try {
    const payload = { ...stageForm, required_manual_checks: stageForm.manual_checks_text.split(',').map((item) => item.trim()).filter(Boolean), required_materials: stageForm.required_materials_text.split(',').map((item) => item.trim()).filter(Boolean), open_at: new Date(stageForm.open_at).toISOString(), deadline_at: new Date(stageForm.deadline_at).toISOString() }
    await createCohortStage(selectedCohort.value.id, payload)
    selectedCohort.value = await fetchCohort(selectedCohort.value.id)
    stageForm.key = ''; stageForm.name = ''
    await loadAll()
    notice.value = t('workflow.stageSaved')
  } catch (e) { error.value = e.message } finally { saving.value = false }
}

async function handleAddMember() {
  saving.value = true
  try {
    await addCohortMember(selectedCohort.value.id, memberForm)
    selectedCohort.value = await fetchCohort(selectedCohort.value.id)
    memberForm.student_ref = ''; memberForm.teacher_ref = ''; memberForm.reason = ''
    await loadAll()
    notice.value = t('workflow.relationshipSaved')
  } catch (e) { error.value = e.message } finally { saving.value = false }
}

const memberImportRows = computed(() => memberImportText.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).filter((line, index) => !(index === 0 && /(student[_ ]?(id|ref)|学号|学生)/i.test(line))).map((line) => {
  const [student_ref = '', teacher_ref = '', reason = ''] = line.split(/\t|,/)
  return { student_ref: student_ref.trim(), teacher_ref: teacher_ref.trim(), reason: reason.trim() }
}))

async function previewMemberImport() {
  if (!selectedCohort.value) return
  try { memberImportResult.value = await importCohortMembers(selectedCohort.value.id, memberImportRows.value, true); notice.value = t('workflow.memberPreviewDone') } catch (e) { error.value = e.message }
}

async function commitMemberImport() {
  if (!selectedCohort.value) return
  saving.value = true
  try { memberImportResult.value = await importCohortMembers(selectedCohort.value.id, memberImportRows.value); selectedCohort.value = await fetchCohort(selectedCohort.value.id); cohorts.value = await Promise.all((await fetchCohorts()).map(async (item) => fetchCohort(item.id))); notice.value = t('workflow.memberImportDone') } catch (e) { error.value = e.message } finally { saving.value = false }
}

function cohortMaterialRecord(artifactType) {
  return cohortRecords.value
    .filter((record) => record.artifact_type === artifactType)
    .sort((a, b) => new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at))[0]
}

function emptyManagementTableRow(field) {
  return Object.fromEntries((field.columns || []).map((column) => [column.key, '']))
}

function addManagementTableRow(field) {
  managementTableDrafts[field.key] ||= []
  managementTableDrafts[field.key].push(emptyManagementTableRow(field))
}

function removeManagementTableRow(field, rowIndex) {
  managementTableDrafts[field.key]?.splice(rowIndex, 1)
}

async function saveManagementMaterial() {
  const hasTableValue = Object.values(managementTableDrafts).some((rows) => Array.isArray(rows) && rows.length > 0)
  const hasJsonValue = Object.values(managementJsonDrafts).some((value) => value?.trim() && !['[]', '{}'].includes(value.trim()))
  const hasStructuredValue = hasTableValue || hasJsonValue || Object.entries(managementData).some(([key, value]) => key !== 'cohort_id' && value !== '' && value !== null && value !== undefined && value !== 0 && !(Array.isArray(value) && !value.length))
  if (!selectedCohort.value || (!managementForm.title.trim() && !managementForm.content.trim() && !hasStructuredValue)) {
    error.value = t('workflow.managementContentRequired'); return
  }
  saving.value = true
  try {
    const data = { ...managementData, title: managementForm.title.trim(), content: managementForm.content.trim() }
    delete data.cohort_id
    for (const field of managementSchema.value?.fields || []) {
      if (field.type === 'table') {
        data[field.key] = managementTableDrafts[field.key] || []
        continue
      }
      if (field.type !== 'json') continue
      try { data[field.key] = JSON.parse(managementJsonDrafts[field.key] || 'null') } catch (e) { error.value = t('workflow.invalidJson', { label: field.label }); saving.value = false; return }
    }
    const record = await createCohortProcessRecord(selectedCohort.value.id, {
      artifact_type: managementForm.artifact_type,
      data,
      structured_form: Boolean(managementSchema.value?.fields?.length),
    })
    await submitProcessRecord(record.id)
    cohortRecords.value = await fetchCohortProcessRecords(selectedCohort.value.id)
    managementForm.title = ''; managementForm.content = ''; notice.value = t('workflow.managementSubmitted')
  } catch (e) { error.value = e.message } finally { saving.value = false }
}

watch(() => managementForm.artifact_type, (value) => { if (selectedCohort.value) loadManagementForm(value) })

async function handleProcessDecision(record, decision) {
  if (decision !== 'approved' && !processDecisionReason.value.trim()) {
    error.value = t('workflow.returnReasonRequired'); return
  }
  saving.value = true
  try {
    await decideProcessRecord(record.id, { decision, reason: processDecisionReason.value.trim() })
    processDecisionReason.value = ''
    notice.value = decision === 'approved' ? t('workflow.materialApproved') : t('workflow.materialReturned')
    processInbox.value = await fetchProcessRecordInbox()
  } catch (e) { error.value = e.message } finally { saving.value = false }
}

function openMidtermTeacherFields(record) {
  teacherFieldsRecord.value = record
  midtermTeacherFields.advisor_comment = record.data?.advisor_comment || ''
  midtermTeacherFields.score = record.data?.score ?? ''
  error.value = ''
}

function closeMidtermTeacherFields() {
  if (saving.value) return
  teacherFieldsRecord.value = null
}

async function saveMidtermTeacherFields() {
  const record = teacherFieldsRecord.value
  if (!record) return
  const data = {
    advisor_comment: midtermTeacherFields.advisor_comment.trim(),
    score: midtermTeacherFields.score === '' || midtermTeacherFields.score === null
      ? null : Number(midtermTeacherFields.score),
  }
  saving.value = true
  error.value = ''
  try {
    const updated = await updateMidtermTeacherFields(record.id, { data, base_revision: record.revision })
    processInbox.value = processInbox.value.map((item) => item.id === updated.id ? updated : item)
    teacherFieldsRecord.value = null
    notice.value = t('workflow.midtermTeacherFieldsSaved')
  } catch (e) { error.value = e.message } finally { saving.value = false }
}

async function previewAndPublish(stage) {
  try {
    const result = await previewCohortStage(stage.id); preview.value = result
    if (result.ok) openConfirmDialog({ title: t('workflow.publishStageTitle', { name: stage.name }), description: t('workflow.publishStageDescription', { count: result.member_snapshot.length }), confirmLabel: t('workflow.confirmPublish') }, async () => {
      await publishCohortStage(stage.id); await loadAll(); notice.value = t('workflow.stagePublished')
    })
  } catch (e) { error.value = e.message }
}

function toLocalDateTime(value) {
  if (!value) return ''
  const date = new Date(value)
  const pad = (item) => String(item).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function openStageUpgrade(stage) {
  upgradeStage.value = stage
  stageUpgradePreview.value = null
  stageUpgradeParticipantIds.value = []
  stageUpgradeForm.deadline_at = toLocalDateTime(stage.config?.deadline_at)
  stageUpgradeForm.revision_days = stage.config?.revision_days ?? 7
  stageUpgradeForm.reason = ''
}

function closeStageUpgrade() {
  upgradeStage.value = null
  stageUpgradePreview.value = null
  stageUpgradeParticipantIds.value = []
}

async function previewStageUpgrade(stage) {
  saving.value = true
  try {
    const payload = { deadline_at: stageUpgradeForm.deadline_at ? new Date(stageUpgradeForm.deadline_at).toISOString() : undefined, revision_days: stageUpgradeForm.revision_days, reason: stageUpgradeForm.reason.trim(), base_revision: stage.config?.revision }
    stageUpgradePreview.value = await createStageVersion(stage.id, payload)
    stageUpgradeParticipantIds.value = []
    notice.value = t('workflow.stageUpgradePreview')
  } catch (e) { error.value = e.message } finally { saving.value = false }
}

function selectAllStageParticipants() {
  const ids = (stageUpgradePreview.value?.affected_participants || []).map((item) => item.participant_id)
  stageUpgradeParticipantIds.value = stageUpgradeParticipantIds.value.length === ids.length ? [] : ids
}

async function publishStageUpgrade(stage) {
  const configId = stageUpgradePreview.value?.new_config?.id
  if (!configId) return
  saving.value = true
  try {
    await publishStageVersion(stage.id, configId, { participant_ids: stageUpgradeParticipantIds.value, reason: stageUpgradeForm.reason.trim() })
    notice.value = stageUpgradeParticipantIds.value.length ? t('workflow.stageUpgradePublished', { count: stageUpgradeParticipantIds.value.length }) : t('workflow.stageUpgradePublishedNoMigration')
    closeStageUpgrade()
    cohorts.value = await Promise.all((await fetchCohorts()).map(async (item) => fetchCohort(item.id)))
    await loadAll()
  } catch (e) { error.value = e.message } finally { saving.value = false }
}

async function loadReport() {
  const cohortId = selectedCohort.value?.id || reportCohortId.value || cohorts.value[0]?.id
  if (!cohortId) { report.value = null; return }
  reportCohortId.value = cohortId
  try { report.value = await fetchWorkflowReport(cohortId) } catch (e) { error.value = e.message }
}

function openExtension(task) {
  extensionTask.value = task
  extensionForm.deadline = ''
  extensionForm.reason = ''
}

function cancelExtension() {
  extensionTask.value = null
}

async function handleExtend(task) {
  if (!extensionForm.reason.trim()) { error.value = t('workflow.extendReasonRequired'); return }
  if (!extensionForm.deadline) { error.value = t('workflow.extendDeadlineRequired'); return }
  // datetime-local 是本地时间，转成带时区的 ISO 再发送；后端会统一换算到 UTC 比较。
  const next = new Date(extensionForm.deadline).toISOString()
  const current = task.revision_deadline_at || task.effective_deadline_at || task.deadline_at || ''
  if (current && new Date(next) <= new Date(current)) { error.value = t('workflow.extendMustBeLater'); return }
  saving.value = true
  try {
    await extendTaskDeadline(task.id, { effective_deadline_at: next, reason: extensionForm.reason.trim() })
    await loadAll()
    notice.value = t('workflow.extended')
    extensionTask.value = null
  } catch (e) { error.value = e.message } finally { saving.value = false }
}

async function handleArchive(task) {
  saving.value = true
  try { await createArchive(task.current_submission.id); await loadAll(); notice.value = t('workflow.archiveQueued') } catch (e) { error.value = e.message } finally { saving.value = false }
}

async function handleRetryArchive(archive) {
  saving.value = true
  try { await retryArchive(archive.id, t('workflow.archiveRetryReason')); await loadAll(); notice.value = t('workflow.archiveRetried') } catch (e) { error.value = e.message } finally { saving.value = false }
}

async function downloadArchiveFile(archive) {
  try { await downloadArchive(archive.id); notice.value = t('workflow.archiveDownloadStarted') } catch (e) { error.value = e.message }
}

function bindTask(task) {
  taskDrawer.value = null; bindTarget.value = task; bindTitle.value = `${task.cohort_name} · ${task.stage_name}` }
async function confirmBind() {
  saving.value = true
  try { await bindTaskProject(bindTarget.value.id, { title: bindTitle.value }); bindTarget.value = null; await loadAll(); notice.value = t('workflow.thesisBound') } catch (e) { error.value = e.message } finally { saving.value = false }
}

async function acknowledgeTask(task) {
  try { await acknowledgeTaskRequirements(task.id); await loadAll(); notice.value = t('workflow.requirementsAcknowledged') } catch (e) { error.value = e.message }
}

async function openMaterials(task) {
  taskDrawer.value = null;
  materialsTask.value = task
  materialForm.artifact_type = task.stage_key === 'final' ? 'final_report' : 'task_book'
  materialForm.title = ''; materialForm.content = ''; materialFile.value = null
  try {
    const [records, summary, scores] = await Promise.all([fetchParticipantProcessRecords(task.id), fetchParticipantQualification(task.id), fetchParticipantScoreSummary(task.id)])
    processRecords.value = records; qualification.value = summary; scoreSummary.value = scores
    await loadMaterialForm(materialForm.artifact_type)
  } catch (e) { error.value = e.message }
}

async function loadMaterialForm(artifactType) {
  if (!materialsTask.value) return
  try {
    const result = await fetchProcessRecordForm(materialsTask.value.id, artifactType)
    materialSchema.value = result.schema
    Object.keys(materialData).forEach((key) => delete materialData[key])
    Object.keys(materialJsonDrafts).forEach((key) => delete materialJsonDrafts[key])
    Object.keys(materialTableDrafts).forEach((key) => delete materialTableDrafts[key])
    Object.assign(materialData, result.prefill || {})
    for (const field of result.schema?.fields || []) {
      if (field.type === 'json') materialJsonDrafts[field.key] = JSON.stringify(materialData[field.key] ?? (field.key === 'entries' || field.key === 'questions' ? [] : {}), null, 2)
      if (field.type === 'table') materialTableDrafts[field.key] = Array.isArray(materialData[field.key])
        ? materialData[field.key].map((row) => ({ ...row }))
        : []
    }
  } catch (e) { error.value = e.message }
}

function handleMaterialFile(event) { materialFile.value = event.target.files?.[0] || null }

function emptyMaterialTableRow(field) {
  return Object.fromEntries((field.columns || []).map((column) => [column.key, '']))
}

function addMaterialTableRow(field) {
  materialTableDrafts[field.key] ||= []
  materialTableDrafts[field.key].push(emptyMaterialTableRow(field))
}

function removeMaterialTableRow(field, rowIndex) {
  materialTableDrafts[field.key]?.splice(rowIndex, 1)
}

function materialRecord(artifactType) {
  return processRecords.value
    .filter((record) => record.artifact_type === artifactType)
    .sort((a, b) => new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at))[0]
}

function materialState(artifactType) {
  const record = materialRecord(artifactType)
  if (!record) return t('workflow.notSubmitted')
  return recordState(record.state)
}

function recordState(state) { return { draft: t('status.draft'), returned: t('workflow.needsSupplement'), submitted: t('workflow.pendingApproval'), approved: t('workflow.confirmed'), rejected: t('status.rejected') }[state] || state }

function materialStateClass(artifactType) {
  const record = materialRecord(artifactType)
  return record?.state || 'missing'
}

async function saveMaterial() {
  const hasTableValue = Object.values(materialTableDrafts).some((rows) => Array.isArray(rows) && rows.length > 0)
  if (!materialsTask.value || (!materialForm.title.trim() && !materialForm.content.trim() && !materialFile.value && !hasTableValue)) {
    error.value = t('workflow.materialRequired'); return
  }
  saving.value = true
  try {
    const structuredContent = materialData.content
    const additionalNotes = materialForm.content.trim()
    const data = { ...materialData, title: materialForm.title.trim() }
    if (structuredContent === undefined || structuredContent === null || structuredContent === '') {
      data.content = additionalNotes
    } else if (additionalNotes) {
      data.content = `${structuredContent}\n\n${additionalNotes}`
    }
    delete data.artifact_type
    for (const field of materialSchema.value?.fields || []) {
      if (field.type === 'table') {
        data[field.key] = materialTableDrafts[field.key] || []
        continue
      }
      if (field.type !== 'json') continue
      try { data[field.key] = JSON.parse(materialJsonDrafts[field.key] || 'null') } catch (e) { error.value = t('workflow.invalidJson', { label: field.label }); saving.value = false; return }
    }
    const submissionData = auth.user?.role === 'student' && materialForm.artifact_type === 'midterm_check'
      ? Object.fromEntries(Object.entries(data).filter(([key]) => (
        (materialSchema.value?.fields || []).some((field) => field.key === key) || key === 'title' || key === 'content'
      )))
      : data
    const record = await createParticipantProcessRecord(materialsTask.value.id, {
      artifact_type: materialForm.artifact_type,
      data: submissionData,
      structured_form: Boolean(materialSchema.value?.fields?.length),
    })
    if (materialFile.value) await uploadProcessRecordAttachment(record.id, materialFile.value)
    await submitProcessRecord(record.id)
    notice.value = t('workflow.materialSubmitted')
    materialForm.title = ''; materialForm.content = ''; materialFile.value = null
    const input = document.querySelector('#process-material-file')
    if (input) input.value = ''
    await openMaterials(materialsTask.value)
  } catch (e) { error.value = e.message } finally { saving.value = false }
}

async function downloadMaterial(attachment) {
  try { await downloadProcessRecordAttachment(attachment.id); notice.value = t('workflow.materialDownloadStarted') } catch (e) { error.value = e.message }
}

async function downloadReviewMaterial(attachment) {
  const submissionId = selectedSubmission.value?.submission.id
  if (!submissionId) return
  try { await downloadSubmissionMaterial(submissionId, attachment.id, attachment.name); notice.value = t('workflow.materialDownloadStarted') } catch (e) { error.value = e.message }
}

async function downloadReviewDocument() {
  const submissionId = selectedSubmission.value?.submission.id
  if (!submissionId) return
  try { await downloadSubmissionDocument(submissionId); notice.value = t('workflow.submittedWordDownloadStarted') } catch (e) { error.value = e.message }
}

async function downloadReviewPdf() {
  const submissionId = selectedSubmission.value?.submission.id
  if (!submissionId || !optionalPdfEnabled.value) return
  try { await downloadSubmissionPdf(submissionId); notice.value = t('workflow.submittedPdfDownloadStarted') } catch (e) { error.value = e.message }
}

async function exportMaterial(record) {
  try { await exportProcessRecord(record.id); notice.value = t('workflow.materialExported') } catch (e) { error.value = e.message }
}

async function prepareCandidate(task) {
  if (preparingCandidate.value) return
  preparingCandidate.value = true
  selectedTask.value = task
  selectedCandidate.value = null
  submissionAttempt.value = null
  warningAcknowledged.value = false
  error.value = ''
  candidateError.value = ''
  try { selectedCandidate.value = await createSubmissionCandidate({ participant_id: task.id, modification_note: task.state === 'needs_revision' ? t('workflow.revisionComplete') : t('workflow.firstSubmission') }) } catch (e) { candidateError.value = e.message } finally { preparingCandidate.value = false }
}

async function handleConfirmSubmission() {
  if (submitting.value || !selectedCandidate.value) return
  if (submissionAttempt.value?.candidate_id !== selectedCandidate.value.id) {
    submissionAttempt.value = {
      candidate_id: selectedCandidate.value.id,
      idempotency_key: crypto.randomUUID(),
      warning_acknowledged: warningAcknowledged.value,
    }
  }
  submitting.value = true
  error.value = ''
  candidateError.value = ''
  try {
    await confirmSubmission(submissionAttempt.value)
    selectedCandidate.value = null; submissionAttempt.value = null
    await loadAll()
    if (error.value) {
      taskDrawer.value = null
      notice.value = t('workflow.submissionConfirmedRefreshFailed')
    } else notice.value = t('workflow.submissionConfirmed')
  } catch (e) { candidateError.value = e.message } finally { submitting.value = false }
}

async function selectReviewTask(task) {
  selectedTask.value = task
  try {
    selectedSubmission.value = await loadSubmissionDetail(task.current_submission.id, task.id)
    Object.keys(manualConfirmations).forEach((key) => delete manualConfirmations[key])
    ;(selectedSubmission.value.config?.required_manual_checks || []).forEach((key) => { manualConfirmations[key] = false })
    reviewForm.reason = ''; commentForm.body = ''; clearCommentTarget()
  } catch (e) { error.value = e.message }
}

async function handleComment() {
  if (!commentForm.body.trim()) return
  const payload = { ...commentForm }
  if (selectedCommentTarget.value?.textStart != null && selectedCommentTarget.value?.textEnd != null) {
    payload.quoted_text = selectedCommentTarget.value.quotedText
    payload.text_start = selectedCommentTarget.value.textStart
    payload.text_end = selectedCommentTarget.value.textEnd
  }
  try { await createSubmissionComment(selectedSubmission.value.submission.id, payload); selectedSubmission.value = await loadSubmissionDetail(selectedSubmission.value.submission.id, selectedSubmission.value.participant.id); commentForm.body = ''; clearCommentTarget(); notice.value = t('workflow.commentSaved') } catch (e) { error.value = e.message }
}

function selectCommentBlock(target) {
  selectedCommentTarget.value = target
  commentForm.block_id = target.blockId
}

function clearCommentTarget() {
  selectedCommentTarget.value = null
  commentForm.block_id = ''
}

function commentAnchorLabel(comment) {
  if (!comment.block_id) return t('workflow.fullText')
  const sections = selectedSubmission.value?.version?.content_snapshot?.sections || []
  const matches = sections.flatMap(section => (section.paragraphs || [])
    .filter(block => block.id === comment.block_id).map(() => section))
  return matches.length === 1 ? (matches[0].title || matches[0].key) : t('workflow.commentAnchorMissing')
}

async function resolveComment(comment) {
  try { await updateSubmissionCommentStatus(comment.id, 'resolved'); selectedSubmission.value = await loadSubmissionDetail(selectedSubmission.value.submission.id, selectedSubmission.value.participant.id) } catch (e) { error.value = e.message }
}

async function handleReview(decision) {
  if (decision === 'returned' && !reviewForm.reason.trim()) { error.value = t('workflow.reviewReasonRequired'); return }
  if (decision === 'approved' && requiredMaterialsUnavailable.value) { error.value = t('workflow.requiredMaterialsUnavailable'); return }
  try { await reviewSubmission(selectedSubmission.value.submission.id, { decision, reason: reviewForm.reason, manual_confirmations: { ...manualConfirmations } }); selectedSubmission.value = null; await loadAll(); notice.value = decision === 'approved' ? t('workflow.submissionApproved') : t('workflow.submissionReturned') } catch (e) { error.value = e.message }
}

async function viewHistory(submissionId) {
  if (!submissionId) return
  error.value = ''
  try {
    const detail = await loadSubmissionDetail(submissionId)
    let versions = []
    let versionHistoryError = false
    if (detail.participant.project_id) {
      try {
        versions = [...await fetchProjectVersions(detail.participant.project_id)].sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
      } catch {
        versionHistoryError = true
      }
    }
    await router.replace({ query: { ...route.query, panel: 'submissions' } })
    taskDrawer.value = null
    selectedTask.value = detail.participant
    selectedSubmission.value = { ...detail, version_history_error: versionHistoryError }
    if (isTeacher.value) {
      Object.keys(manualConfirmations).forEach((key) => delete manualConfirmations[key])
      ;(detail.config?.required_manual_checks || []).forEach((key) => { manualConfirmations[key] = false })
      reviewForm.reason = ''; commentForm.body = ''; clearCommentTarget()
    }
    versionHistory.value = versions
    versionCompare.value = null
    versionSelection.left = versions[0]?.id || ''
    // Default to the version the user opened, including earlier returned submissions.
    versionSelection.right = detail.version.id
  } catch (e) { error.value = e.message }
}

function handleWithdrawSubmission() {
  const submissionId = selectedSubmission.value?.submission.id
  const participantId = selectedSubmission.value?.participant.id
  if (!submissionId || !participantId) return
  openConfirmDialog({ title: t('workflow.withdrawSubmission'), description: t('workflow.withdrawSubmissionDescription'), confirmLabel: t('workflow.confirmWithdraw') }, async () => {
    try {
      await withdrawSubmission(submissionId)
    } catch (withdrawError) {
      await loadAll()
      if (selectedSubmission.value?.submission.id === submissionId) {
        try { selectedSubmission.value = await loadSubmissionDetail(submissionId, participantId) } catch { /* Preserve the original withdrawal error. */ }
      }
      throw withdrawError
    }
    await loadAll()
    if (selectedSubmission.value?.submission.id === submissionId) {
      selectedSubmission.value = await loadSubmissionDetail(submissionId, participantId)
    }
    notice.value = t('workflow.submissionWithdrawn')
  })
}

async function loadSubmissionDetail(submissionId) {
  const detail = await fetchSubmission(submissionId)
  return {
    ...detail,
    process_records: detail.submitted_materials || [],
    process_records_error: detail.submitted_materials_available === false ||
      (detail.submitted_materials_available !== true && Boolean(detail.config?.required_materials?.length)),
  }
}

async function retryReviewMaterials() {
  const submissionId = selectedSubmission.value?.submission.id
  const participantId = selectedSubmission.value?.participant.id
  if (!submissionId || !participantId || reviewMaterialsLoading.value) return
  reviewMaterialsLoading.value = true
  try {
    const detail = await loadSubmissionDetail(submissionId)
    if (selectedSubmission.value?.submission.id === submissionId) {
      selectedSubmission.value = { ...selectedSubmission.value, ...detail }
    }
  } catch {
    // Keep the retry control and blocked approval visible.
  } finally {
    reviewMaterialsLoading.value = false
  }
}

function updateFeedbackComment(comment) {
  if (!selectedSubmission.value) return
  for (const key of ['comments', 'prior_unresolved_comments']) {
    if (selectedSubmission.value[key]) {
      selectedSubmission.value[key] = selectedSubmission.value[key].map(item => item.id === comment.id ? { ...item, ...comment } : item)
    }
  }
}

function archiveSubmission(archive) { return submissions.value.find(item => item.id === archive.submission_id) }
function archiveTask(archive) {
  const submission = archiveSubmission(archive)
  return tasks.value.find(item => item.id === submission?.participant_id || item.current_submission?.id === archive.submission_id)
}

async function compareSelectedVersions() {
  if (!selectedTask.value?.project_id || !versionSelection.left || !versionSelection.right) return
  try { versionCompare.value = await compareProjectVersions(selectedTask.value.project_id, versionSelection.left, versionSelection.right) } catch (e) { error.value = e.message }
}

async function restoreHistoricalVersion(version) {
  if (!version || !selectedTask.value?.project_id) return
  openConfirmDialog({ title: t('workflow.restoreVersion'), description: t('workflow.restoreVersionDescription'), confirmLabel: t('workflow.confirmRestore') }, async () => {
    await restoreProjectVersion(selectedTask.value.project_id, version.id); notice.value = t('workflow.versionRestored'); selectedSubmission.value = null; versionCompare.value = null; await loadAll()
  })
}

function versionDiffLabel(status) { return { added: t('workflow.added'), removed: t('workflow.removed'), changed: t('workflow.changed') }[status] || status }
function diffBlockText(block) {
  if (!block) return t('workflow.missingBlock')
  if (block.type === 'image') return block.image?.caption || block.image?.url || t('workflow.image')
  if (block.type === 'table') return block.table?.caption || t('workflow.table')
  return block.text || t('workflow.emptyParagraph')
}

async function readNotification(item) {
  error.value = ''
  const markRead = item.read_at ? Promise.resolve('') : Promise.resolve()
    .then(() => markWorkflowNotificationRead(item.id))
    .then(() => fetchWorkflowNotifications())
    .then((result) => { notifications.value = result; return '' })
    .catch((e) => e.message)
  if (item.resource_type === 'submission') {
    if (!m2WorkflowEnabled.value) {
      error.value = t('workflow.m2Unavailable')
      return
    }
    await viewHistory(item.resource_id)
  } else if (item.resource_type === 'archive') {
    if (!m2WorkflowEnabled.value) {
      error.value = t('workflow.m2Unavailable')
    } else {
      await router.replace({ query: { ...route.query, panel: 'archives' } })
    }
  }
  const notificationError = await markRead
  if (notificationError && !error.value) error.value = notificationError
}

function stateLabel(state) { return t(`task.${state}`) === `task.${state}` ? state : t(`task.${state}`) }
function submissionStateLabel(state) { return t(`submission.${state}`) === `submission.${state}` ? state : t(`submission.${state}`) }
function archiveStatusLabel(status) { return t(`archive.${status}`) === `archive.${status}` ? status : t(`archive.${status}`) }
function formatDate(value) { return value ? formatLocaleDate(value, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-' }
function deadlineLabel(task) { return `${task.revision_deadline_at ? t('deadline.revision') : t('deadline.initial')} ${formatDate(task.revision_deadline_at || task.effective_deadline_at)}` }
function rate(value) { return value === null || value === undefined ? '-' : `${Math.round(Number(value) * 100)}%` }

onMounted(async () => {
  await loadAll()
  if (route.query.submission && m2WorkflowEnabled.value && !canManage.value) await viewHistory(String(route.query.submission))
})
</script>

<style scoped>
.prior-feedback { display: grid; gap: 9px; margin: 16px 0; }
.prior-feedback h4 { margin: 0; font-size: 14px; }
.prior-feedback .comment-item button { float: none; margin: 8px 0 0; }
.prior-feedback .prior-source { color: var(--on-surface-variant); white-space: pre-wrap; overflow-wrap: anywhere; }
.workflow-page { max-width: 1360px; margin: 0 auto; padding: 24px 0 56px; }
.workflow-hero { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 22px; color: var(--on-surface); }
.workflow-kicker { color: var(--primary); font-size: 10px; letter-spacing: .09em; font-weight: 700; margin: 0 0 6px; }
.workflow-hero .workflow-kicker { color: var(--primary); }
.workflow-hero h1 { font: 700 25px/1.25 var(--font-ui); margin: 0 0 7px; letter-spacing: -.02em; }
.workflow-subtitle { color: var(--on-surface-variant); margin: 0; max-width: 650px; line-height: 1.65; }
.workflow-identity { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-left: 1px solid var(--border); }
.workflow-identity .material-symbols-outlined { color: var(--primary); font-size: 24px; }
.workflow-identity div { display: grid; gap: 2px; }.workflow-identity span { color: var(--on-surface-variant); font-size: 12px; }
.workflow-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin: 18px 0 24px; }
.workflow-summary > div { padding: 17px 18px; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface-container-lowest); box-shadow: var(--shadow-card); display: grid; gap: 8px; }
.workflow-summary span { color: var(--on-surface-variant); font-size: 12px; }.workflow-summary strong { font-size: 26px; color: var(--on-surface); }
.workflow-layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 22px; }.workflow-main, .workflow-side { min-width: 0; }
.section-title-row, .panel-heading, .task-topline, .preview-heading, .candidate-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.section-title-row { margin-bottom: 14px; }.section-title-row h2 { margin: 0; font-size: 22px; }.panel-heading h3, .task-topline h3 { margin: 0; font-size: 17px; }.panel-heading p, .task-copy p, .task-topline p { color: var(--on-surface-variant); font-size: 12px; margin: 4px 0 0; }
.panel { background: var(--surface-container-lowest); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 18px; margin-bottom: 14px; box-shadow: var(--shadow-card); }.create-panel { border-top: 3px solid var(--primary); }.panel-icon { color: var(--primary); font-size: 26px; }.icon-button { width: 32px; height: 32px; padding: 0; border-radius: 50%; }.link-button { text-decoration: none; }
.form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }.form-grid label, .field { display: grid; gap: 5px; font-size: 12px; color: var(--on-surface-variant); }.form-grid input, .form-grid select, .field input { width: 100%; min-width: 0; }.form-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 15px; }.check-grid { display: flex; gap: 20px; margin-top: 14px; font-size: 12px; color: var(--on-surface-variant); }.check-grid input { accent-color: var(--primary); }
.cohort-panel { padding-bottom: 0; max-height: 600px; display: flex; flex-direction: column; }.cohort-panel .panel-heading { flex-shrink: 0; }.eyebrow-row { display: flex; align-items: center; gap: 7px; color: var(--on-surface-variant); font-size: 11px; }.status-dot { width: 7px; height: 7px; background: var(--outline); border-radius: 50%; display: inline-block; }.status-dot.published { background: #0f9d6e; }.cohort-stages { margin: 16px -18px 0; border-top: 1px solid var(--border); overflow-y: auto; flex: 1; min-height: 0; }.stage-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 18px; border-bottom: 1px solid var(--border); }.stage-row strong, .stage-row span { display: block; }.stage-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }.stage-row-actions { display: flex; align-items: center; gap: 8px; }.stage-row-actions .state-tag { display: inline-block; }.muted-row { color: var(--on-surface-variant); font-size: 12px; padding: 10px 0; }.setup-panel { border-left: 3px solid var(--tertiary); }.member-quick-add { border-top: 1px solid var(--border); margin-top: 20px; padding-top: 15px; }.member-quick-add h4 { margin: 0; font-size: 13px; }.preview-box { margin-top: 16px; padding: 12px; border: 1px solid var(--border); background: var(--surface-container-low); border-radius: var(--radius-sm); font-size: 12px; }.preview-box p { margin: 5px 0 0; }.preview-ok { color: #087f5b; }.preview-error { color: var(--error); }.preview-warning { color: var(--tertiary); }
.task-row { display: flex; align-items: center; gap: 13px; cursor: pointer; }.task-row.selected { border-color: var(--primary); }.task-state { width: 36px; height: 36px; border-radius: 8px; display: grid; place-items: center; background: var(--surface-container); color: var(--primary); flex: 0 0 auto; }.task-copy { min-width: 0; flex: 1; }.task-copy h3 { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin: 2px 0 0; }.task-side { display: grid; justify-items: end; gap: 4px; color: var(--on-surface-variant); font-size: 11px; }.review-panel { margin-top: 14px; }.gate-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 16px 0; }.gate-strip div { background: var(--surface-container-low); padding: 10px; }.gate-strip span, .gate-strip strong { display: block; font-size: 11px; }.gate-strip span { color: var(--on-surface-variant); }.gate-strip strong { margin-top: 3px; }.comment-list { display: grid; gap: 9px; }.comment-item { padding: 11px; border-left: 3px solid var(--outline); background: var(--surface-container-low); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }.comment-item.blocking { border-left-color: var(--error); }.comment-item p { margin: 7px 0 4px; font-size: 13px; }.comment-quote { margin: 8px 0; padding-left: 10px; border-left: 2px solid var(--border); white-space: pre-wrap; overflow-wrap: anywhere; }.comment-item small, .comment-anchor { color: var(--on-surface-variant); font-size: 11px; }.comment-anchor { margin-left: 8px; }.comment-item button { float: right; margin-top: -22px; }.review-compose, .review-decision { border-top: 1px solid var(--border); margin-top: 15px; padding-top: 15px; }.review-compose textarea, .review-decision textarea { width: 100%; resize: vertical; }.review-compose-actions, .review-decision-actions { display: flex; align-items: center; gap: 8px; margin-top: 8px; }.review-compose-actions input { flex: 1; }.review-decision-actions { justify-content: flex-end; }
.review-evidence { border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); padding: 15px 0; margin-bottom: 15px; }.evidence-heading { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }.evidence-heading h4 { margin: 0; font-size: 18px; }.evidence-heading > span { color: var(--on-surface-variant); font-size: 11px; }.check-summary { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; color: var(--on-surface-variant); font-size: 11px; }.snapshot-section { border-top: 1px solid var(--border); padding: 12px 0 0; margin-top: 12px; }.snapshot-section h5, .review-materials h5 { margin: 0 0 8px; font-size: 13px; }.snapshot-block { border-left: 2px solid var(--surface-container); padding: 5px 10px; margin: 5px 0; }.snapshot-block p { margin: 0; white-space: pre-wrap; line-height: 1.65; font-size: 13px; }.snapshot-table { overflow-x: auto; color: var(--on-surface-variant); font-size: 12px; }.snapshot-table strong { display: block; color: var(--on-surface); margin-bottom: 4px; }.snapshot-table div { border-bottom: 1px solid var(--border); padding: 4px 0; white-space: nowrap; }.snapshot-image { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 11px; }.snapshot-image img { max-width: min(100%, 560px); max-height: 280px; object-fit: contain; object-position: left center; background: var(--surface-container-low); }.review-materials { border-top: 1px solid var(--border); margin-top: 15px; padding-top: 12px; }.review-material-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); }.review-material-row strong, .review-material-row span { display: block; }.review-material-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }
.student-task { border-left: 3px solid var(--primary); }.student-task.needs_revision { border-left-color: var(--tertiary); }.task-meta { display: flex; gap: 18px; flex-wrap: wrap; margin: 15px 0; color: var(--on-surface-variant); font-size: 12px; }.task-meta > span { display: inline-flex; align-items: center; gap: 5px; }.task-meta .material-symbols-outlined { font-size: 16px; }.task-actions { display: flex; flex-wrap: wrap; gap: 7px; }.late-label { color: var(--error); font-weight: 600; }.candidate-box { margin-top: 15px; padding: 13px; background: var(--surface-container-low); border-top: 1px solid var(--border); }.candidate-gates { display: grid; gap: 7px; margin: 12px 0; }.candidate-gates > div { display: flex; gap: 8px; align-items: flex-start; padding: 8px; background: #fff; border: 1px solid var(--border); }.candidate-gates > div .material-symbols-outlined { color: var(--tertiary); }.candidate-gates > div.passed .material-symbols-outlined { color: #087f5b; }.candidate-gates strong, .candidate-gates small { display: block; }.candidate-gates small { color: var(--on-surface-variant); margin-top: 2px; }.candidate-box > label { display: flex; gap: 7px; align-items: flex-start; font-size: 12px; margin: 10px 0; }.candidate-box input { accent-color: var(--primary); }
.notification-panel, .history-panel, .report-panel { position: sticky; top: 78px; }.notification-row { width: 100%; justify-content: flex-start; border: 0; border-bottom: 1px solid var(--border); border-radius: 0; padding: 10px 0; background: transparent; text-align: left; }.notification-row:last-child { border-bottom: 0; }.notification-row > span:first-child { color: var(--outline); }.notification-row.unread > span:first-child { color: var(--primary); }.notification-row > span:nth-child(2) { display: grid; gap: 2px; text-align: left; min-width: 0; }.notification-row strong { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }.notification-row small { color: var(--on-surface-variant); font-size: 11px; }.unread-count { min-width: 23px; height: 23px; display: grid; place-items: center; border-radius: 50%; background: var(--primary-fixed); color: var(--on-primary-fixed); font-size: 11px; }.report-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 15px; }.report-grid div { padding: 10px; background: var(--surface-container-low); }.report-grid strong, .report-grid span { display: block; }.report-grid strong { font-size: 20px; }.report-grid span { color: var(--on-surface-variant); font-size: 11px; }.history-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 0; border-bottom: 1px solid var(--border); }.history-row:last-child { border-bottom: 0; }.history-row strong, .history-row span { display: block; }.history-row div span { color: var(--on-surface-variant); font-size: 11px; margin-top: 2px; }
.empty-state { text-align: center; border: 1px dashed var(--border); padding: 42px 20px; color: var(--on-surface-variant); }.empty-state .material-symbols-outlined { font-size: 34px; color: var(--outline); }.empty-state h3 { color: var(--on-surface); margin: 8px 0 4px; }.empty-state p { font-size: 12px; margin: 0; }
.modal-head { display: flex; justify-content: space-between; align-items: center; }.modal-head h3 { margin: 0; }.hint { color: var(--on-surface-variant); font-size: 12px; }
@media (max-width: 900px) { .workflow-page { padding: 16px 0 40px; }.workflow-hero { align-items: flex-start; flex-direction: column; }.workflow-hero h1 { font-size: 21px; }.workflow-identity { border-left: 0; border-top: 1px solid var(--border); width: 100%; padding: 12px 0 0; }.workflow-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }.workflow-layout { grid-template-columns: 1fr; }.notification-panel, .history-panel, .report-panel { position: static; }.form-grid { grid-template-columns: 1fr 1fr; } }
@media (max-width: 560px) { .workflow-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }.workflow-summary > div { padding: 12px; gap: 4px; }.form-grid { grid-template-columns: 1fr; }.stage-row, .task-row { align-items: flex-start; flex-direction: column; }.stage-row-actions, .task-side { width: 100%; justify-items: start; display: flex; justify-content: space-between; }.gate-strip { grid-template-columns: 1fr; }.review-compose-actions, .review-decision-actions { flex-wrap: wrap; }.review-compose-actions input { flex-basis: 100%; }.panel { padding: 14px; }.cohort-stages { margin-left: -14px; margin-right: -14px; }.workflow-hero { margin-left: 0; margin-right: 0; }.workflow-page { overflow: hidden; } }
.manual-check-panel { border-left: 3px solid var(--tertiary); }.manual-checks { display: grid; gap: 8px; }.manual-checks label { display: flex; align-items: flex-start; gap: 7px; color: var(--on-surface-variant); font-size: 12px; }.manual-checks input { accent-color: var(--primary); }
.archive-panel { border-left: 3px solid var(--primary); }.archive-list { display: grid; gap: 0; margin-top: 14px; border-top: 1px solid var(--border); }.archive-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--border); }.archive-row strong, .archive-row span { display: block; }.archive-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }
.materials-panel { border-left: 3px solid var(--tertiary); }.qualification-strip { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin: 15px 0; padding: 11px 12px; background: var(--surface-container-low); color: var(--on-surface-variant); font-size: 12px; }.qualification-strip span, .qualification-strip strong { display: block; }.qualification-strip strong { margin-top: 3px; }.qualification-ok { color: #087f5b; }.qualification-warn { color: var(--tertiary); }.material-catalog { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }.material-catalog-row { display: flex; justify-content: space-between; gap: 8px; align-items: center; padding: 9px; border: 1px solid var(--border); background: #fff; }.material-catalog-row strong, .material-catalog-row small { display: block; }.material-catalog-row strong { font-size: 12px; }.material-catalog-row small { color: var(--on-surface-variant); font-size: 10px; margin-top: 3px; }.material-compose { border-top: 1px solid var(--border); margin-top: 16px; padding-top: 15px; }.material-content-field, .file-picker { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 12px; margin-top: 12px; }.material-content-field textarea { width: 100%; resize: vertical; }.file-picker input { font: inherit; }.file-picker small { color: var(--on-surface-variant); }.material-compose > button { margin-top: 13px; }.record-history { border-top: 1px solid var(--border); margin-top: 18px; padding-top: 14px; }.record-history h4 { margin: 0 0 8px; font-size: 13px; }.record-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--border); }.record-row strong, .record-row span { display: block; }.record-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }.record-attachments { display: flex; flex-wrap: wrap; gap: 5px; justify-content: flex-end; }
.management-records { border-top: 1px solid var(--border); margin-top: 20px; padding-top: 16px; }.management-catalog { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; margin-top: 13px; }.management-catalog > div { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 8px 9px; border: 1px solid var(--border); background: #fff; }.management-catalog strong { font-size: 11px; }
.reference-panel { border-left: 3px solid var(--outline); }.reference-list { display: grid; gap: 0; margin-top: 12px; }.reference-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--border); }.reference-row:last-child { border-bottom: 0; }.reference-row strong, .reference-row span { display: block; }.reference-row span, .reference-row small { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }.reference-row small { text-align: right; }
.structured-material-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-top: 14px; padding: 13px; background: var(--surface-container-low); border: 1px solid var(--border); }.structured-field { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 12px; }.structured-field span { display: flex; gap: 6px; align-items: center; }.structured-field em { color: var(--error); font-size: 10px; font-style: normal; }.structured-field textarea, .structured-field input, .structured-field select { width: 100%; }.structured-field:nth-child(odd):last-child { grid-column: 1 / -1; }.score-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 7px; margin: 12px 0; }.score-summary div { padding: 9px; background: var(--surface-container-low); }.score-summary span, .score-summary strong { display: block; }.score-summary span { color: var(--on-surface-variant); font-size: 11px; }.score-summary strong { margin-top: 3px; font-size: 18px; }
.structured-table-editor { margin-top: 14px; padding: 13px; background: var(--surface-container-low); border: 1px solid var(--border); }.table-editor-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.table-editor-heading strong, .table-editor-heading small { display: block; }.table-editor-heading strong { color: var(--on-surface); font-size: 12px; }.table-editor-heading small { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }.table-editor-heading em { color: var(--error); font-size: 10px; font-style: normal; margin-left: 6px; }.table-editor-scroll { overflow-x: auto; margin-top: 10px; }.table-editor-scroll table { width: 100%; min-width: 1180px; border-collapse: collapse; font-size: 11px; }.table-editor-scroll th, .table-editor-scroll td { border: 1px solid var(--border); padding: 6px; vertical-align: top; text-align: left; }.table-editor-scroll th { background: var(--surface-container); color: var(--on-surface-variant); white-space: nowrap; }.table-editor-scroll td:first-child { width: 30px; text-align: center; color: var(--on-surface-variant); }.table-editor-scroll input, .table-editor-scroll textarea { min-width: 110px; width: 100%; box-sizing: border-box; font: inherit; font-size: 11px; }.table-editor-scroll textarea { resize: vertical; }.table-editor-scroll .icon-button { width: 28px; height: 28px; }
.process-inbox { border-left: 3px solid var(--primary); }.process-reason-field { display: grid; gap: 5px; margin: 14px 0 8px; color: var(--on-surface-variant); font-size: 12px; }.process-reason-field input { width: 100%; }.process-inbox-list { border-top: 1px solid var(--border); }.process-inbox-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 11px 0; border-bottom: 1px solid var(--border); }.process-inbox-row strong, .process-inbox-row span { display: block; }.process-inbox-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }
.correction-panel { border-left: 3px solid var(--tertiary); }
@media (max-width: 700px) { .material-catalog, .structured-material-form { grid-template-columns: 1fr; }.score-summary { grid-template-columns: repeat(2, 1fr); }.record-row { align-items: flex-start; flex-direction: column; }.record-attachments { justify-content: flex-start; } }
.member-import-panel { border-left: 3px solid var(--primary); }.member-import-panel > textarea { width: 100%; margin-top: 14px; resize: vertical; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }.member-import-result { border-top: 1px solid var(--border); margin-top: 14px; padding-top: 10px; }.import-summary { display: flex; gap: 14px; color: var(--on-surface-variant); font-size: 11px; margin-bottom: 7px; }.import-row { display: grid; grid-template-columns: 80px 60px minmax(0, 1fr); gap: 8px; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 11px; }.import-row small { color: var(--on-surface-variant); overflow-wrap: anywhere; }
.stage-upgrade-box { margin: 0 18px 14px; padding: 14px; border: 1px solid var(--primary); background: var(--surface-container-low); }.stage-upgrade-box .panel-heading p { max-width: 620px; }.upgrade-diff { display: inline-block; margin-right: 14px !important; }.migration-list { border-top: 1px solid var(--border); margin-top: 12px; padding-top: 10px; display: grid; gap: 7px; }.migration-list-heading { display: flex; justify-content: space-between; gap: 10px; align-items: center; }.migration-list label { display: flex; align-items: flex-start; gap: 7px; font-size: 12px; color: var(--on-surface-variant); }.migration-list input { accent-color: var(--primary); }
.history-detail { margin-top: 14px; }.history-detail .gate-strip { margin-bottom: 10px; }.history-snapshot { border-top: 1px solid var(--border); padding-top: 14px; }.history-snapshot > h4 { margin: 0; font-size: 18px; }.version-compare { border-top: 1px solid var(--border); margin-top: 16px; padding-top: 14px; }.version-compare h4 { margin: 0; font-size: 14px; }.compare-selects { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }.compare-selects label { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 11px; }.compare-selects select { min-width: 0; }.compare-result { border-top: 1px solid var(--border); margin-top: 12px; padding-top: 10px; color: var(--on-surface-variant); font-size: 12px; }.compare-result > p { margin: 0 0 8px; }.compare-section { padding: 8px 0; border-bottom: 1px solid var(--border); }.compare-section > strong { color: var(--on-surface); }.compare-block { padding: 5px 0 0 12px; }.compare-block span { font-size: 11px; }.compare-block p { margin: 3px 0 0; white-space: pre-wrap; }.compare-attachments { margin-top: 9px; }.history-restore { display: flex; align-items: center; justify-content: space-between; gap: 10px; border-top: 1px solid var(--border); margin-top: 14px; padding-top: 12px; color: var(--on-surface-variant); font-size: 12px; }
.workflow-summary { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
@media (max-width: 560px) { .workflow-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.workflow-tabs { display: flex; gap: 4px; align-items: stretch; overflow-x: auto; margin: 0 0 22px; border-bottom: 1px solid var(--border); }
.workflow-tab { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 40px; padding: 0 16px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--on-surface-variant); font: inherit; font-size: 12px; white-space: nowrap; cursor: pointer; }
.workflow-tab:hover { color: var(--on-surface); background: var(--surface-container); }
.workflow-tab.active { color: var(--primary); font-weight: 700; box-shadow: inset 0 -2px var(--primary); }
.workflow-tab .material-symbols-outlined { font-size: 18px; }
.workflow-tab-count { min-width: 19px; height: 19px; display: grid; place-items: center; padding: 0 5px; border-radius: 50%; background: var(--primary-fixed); color: var(--on-primary-fixed); font-size: 10px; }
.workflow-layout-single .workflow-main { min-width: 0; }
.workflow-layout-side-only .workflow-main { display: none; }
.workflow-layout-side-only .workflow-side { grid-column: 1 / -1; }
.workflow-layout-main-only .workflow-side { display: none; }
.workflow-layout-main-only .workflow-main { grid-column: 1 / -1; }
.workflow-wide-panel { position: static; }
.report-filter { display: grid; grid-template-columns: minmax(160px, 320px); gap: 6px; margin-top: 18px; color: var(--on-surface-variant); font-size: 12px; }
.report-filter select { width: 100%; }
.report-metrics { display: flex; flex-wrap: wrap; gap: 9px 22px; margin-top: 14px; padding-top: 13px; border-top: 1px solid var(--border); color: var(--on-surface-variant); font-size: 12px; }
.report-metrics strong { margin-left: 5px; color: var(--on-surface); font-size: 16px; }
.history-row-button { width: 100%; border: 0; background: transparent; font: inherit; text-align: left; cursor: pointer; }
.history-row-button:hover { background: var(--surface-container-low); }
.process-inbox-row > div:first-child { min-width: 0; flex: 1; }.process-inbox-row .stage-row-actions { flex-wrap: wrap; justify-content: flex-end; }
.teacher-fields-actions { flex-wrap: wrap; justify-content: flex-end; }
@media (max-width: 560px) { .process-inbox-row { align-items: flex-start; flex-direction: column; }.process-inbox-row .stage-row-actions { width: 100%; justify-content: flex-start; } }
.cohort-create-fields { grid-template-columns: 1fr; }
.comment-response { margin-top: 10px; padding-left: 12px; border-left: 2px solid var(--border); }
</style>
