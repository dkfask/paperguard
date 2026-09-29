<template>
  <div class="workflow-page">
    <section class="workflow-hero">
      <div>
        <h1>{{ t('workflow.title') }}</h1>
        <p class="workflow-subtitle">{{ t('workflow.description') }}</p>
      </div>
      <div class="workflow-identity">
        <span class="material-symbols-outlined">verified_user</span>
        <div><strong>{{ auth.user?.display_name || auth.user?.username }}</strong><span>{{ roleLabel }}</span></div>
      </div>
    </section>

    <div v-if="error" class="error-banner" role="alert">{{ error }}</div>
    <div v-if="notice" class="ok-banner" role="status">{{ notice }}</div>

    <nav class="workflow-tabs" :aria-label="t('workflow.menu')">
      <button
        v-for="tab in workflowTabs"
        :key="tab.key"
        type="button"
        class="workflow-tab"
        :class="{ active: activeSection === tab.section }"
        :aria-current="activeSection === tab.section ? 'page' : undefined"
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
      <main class="workflow-main">
        <div class="section-title-row">
          <div><p class="workflow-kicker">{{ sectionKicker }}</p><h2>{{ sectionTitle }}</h2></div>
          <button class="icon-button" :title="t('workflow.refresh')" :aria-label="t('workflow.refresh')" @click="loadAll" :disabled="loading"><span class="material-symbols-outlined">refresh</span></button>
        </div>

        <section v-if="processInbox.length && activeSection === 'overview'" class="panel process-inbox"><div class="panel-heading"><div><h3>{{ t('workflow.processInboxTitle') }}</h3><p>{{ t('workflow.processInboxDetail') }}</p></div><span class="unread-count">{{ processInbox.length }}</span></div><label class="process-reason-field">{{ t('workflow.returnReason') }}<input v-model="processDecisionReason" :placeholder="t('workflow.returnReasonPlaceholder')" /></label><div class="process-inbox-list"><div v-for="record in processInbox" :key="record.id" class="process-inbox-row"><div><strong>{{ record.artifact_name }}</strong><span>{{ record.student_id || record.cohort_id }} · {{ t('workflow.waitingApproval') }} {{ record.pending_approvals?.join(' / ') || t('workflow.waitingApproval') }}</span></div><div class="stage-row-actions"><button class="tiny" @click="handleProcessDecision(record, 'returned')"><span class="material-symbols-outlined">undo</span>{{ t('workflow.return') }}</button><button class="tiny primary" @click="handleProcessDecision(record, 'approved')"><span class="material-symbols-outlined">verified</span>{{ t('workflow.approve') }}</button></div></div></div></section>

        <template v-if="canConfigure">
          <section v-if="activeSection === 'overview'" class="panel create-panel">
            <div class="panel-heading"><div><h3>{{ t('workflow.createCohortTitle') }}</h3><p>{{ t('workflow.createCohortDetail') }}</p></div><span class="material-symbols-outlined panel-icon">calendar_month</span></div>
            <div class="form-grid">
              <label>{{ t('workflow.cohortName') }}<input v-model="cohortForm.name" :placeholder="t('workflow.cohortNamePlaceholder')" /></label>
              <label>{{ t('workflow.organizationScope') }}<select v-model="cohortForm.organization_id"><option value="">{{ t('workflow.selectOrganization') }}</option><option v-for="unit in organizations" :key="unit.id" :value="unit.id">{{ unit.name }}</option></select></label>
              <label>{{ t('workflow.academicYear') }}<input v-model="cohortForm.academic_year" placeholder="2027" /></label>
            </div>
            <div class="form-actions"><button class="primary" :disabled="saving || !cohortForm.name || !cohortForm.organization_id" @click="handleCreateCohort"><span class="material-symbols-outlined">add</span>{{ t('workflow.createCohort') }}</button></div>
          </section>

          <div v-if="!cohorts.length && activeSection === 'overview'" class="empty-state"><span class="material-symbols-outlined">folder_open</span><h3>{{ t('workflow.noCohorts') }}</h3><p>{{ t('workflow.noCohortsDetail') }}</p></div>
          <section v-for="cohort in cohorts" v-if="activeSection === 'overview'" :key="cohort.id" class="panel cohort-panel">
            <div class="panel-heading"><div><div class="eyebrow-row"><span class="status-dot" :class="cohort.status"></span><span>{{ cohort.status === 'published' ? t('workflow.publishedStatus') : cohort.status === 'closed' ? t('workflow.closedStatus') : t('workflow.draftStatus') }}</span></div><h3>{{ cohort.name }}</h3><p>{{ cohort.academic_year || t('workflow.academicYearMissing') }} · {{ t('workflow.studentCount', { count: cohort.member_ids?.length || 0 }) }} · {{ cohort.organization_id }}</p></div><div class="stage-row-actions"><button class="small" @click="selectCohort(cohort)"><span class="material-symbols-outlined">tune</span>{{ t('workflow.configure') }}</button><button v-if="cohort.status === 'published'" class="tiny" @click="handleCloseCohort(cohort)"><span class="material-symbols-outlined">lock</span>{{ t('workflow.close') }}</button><button v-if="cohort.status === 'closed'" class="tiny primary" @click="handleReopenCohort(cohort)"><span class="material-symbols-outlined">lock_open</span>{{ t('workflow.reopen') }}</button></div></div>
            <div class="cohort-stages"><template v-for="stage in cohort.stages || []" :key="stage.id"><div class="stage-row"><div><strong>{{ stage.name }}</strong><span>v{{ stage.config?.version || 1 }} · {{ stage.config?.open_at ? formatDate(stage.config.open_at) : t('workflow.openTimeMissing') }} · {{ stage.config?.deadline_at ? formatDate(stage.config.deadline_at) : t('workflow.deadlineMissing') }}</span></div><div class="stage-row-actions"><span class="state-tag" :class="stage.status">{{ stage.status === 'published' ? t('workflow.publishedStatus') : t('workflow.draftStatus') }}</span><button v-if="stage.status === 'draft'" class="tiny primary" @click="previewAndPublish(stage)"><span class="material-symbols-outlined">publish</span>{{ t('workflow.previewPublish') }}</button><button v-if="stage.status === 'published'" class="tiny" @click="openStageUpgrade(stage)"><span class="material-symbols-outlined">published_with_changes</span>{{ t('workflow.upgradeConfig') }}</button></div></div><div v-if="upgradeStage?.id === stage.id" class="stage-upgrade-box"><div class="panel-heading"><div><strong>{{ t('workflow.stageUpgradeTitle') }}</strong><p>{{ t('workflow.stageUpgradeDetail') }}</p></div><button class="icon-button" :title="t('workflow.closeUpgrade')" :aria-label="t('workflow.closeUpgrade')" @click="closeStageUpgrade"><span class="material-symbols-outlined">close</span></button></div><div class="form-grid"><label>{{ t('workflow.newDeadline') }}<input v-model="stageUpgradeForm.deadline_at" type="datetime-local" /></label><label>{{ t('workflow.revisionDays') }}<input v-model.number="stageUpgradeForm.revision_days" type="number" min="0" max="365" /></label><label>{{ t('workflow.upgradeReason') }}<input v-model="stageUpgradeForm.reason" :placeholder="t('workflow.upgradeReasonPlaceholder')" /></label></div><div class="form-actions"><button class="small" :disabled="saving || !stageUpgradeForm.reason" @click="previewStageUpgrade(stage)"><span class="material-symbols-outlined">fact_check</span>{{ t('workflow.previewChange') }}</button></div><div v-if="stageUpgradePreview" class="preview-box"><div class="preview-heading"><strong>{{ t('workflow.changeDiff') }}</strong><span :class="stageUpgradePreview.preflight?.ok ? 'preview-ok' : 'preview-error'">{{ stageUpgradePreview.preflight?.ok ? t('workflow.publishable') : t('workflow.blocked') }}</span></div><p v-for="item in stageUpgradePreview.diff" :key="item.field" class="upgrade-diff"><strong>{{ item.field }}</strong>：{{ item.before ?? t('workflow.emptyValue') }} → {{ item.after ?? t('workflow.emptyValue') }}</p><p v-for="item in stageUpgradePreview.preflight?.errors || []" :key="item" class="preview-error">{{ item }}</p><p v-for="item in stageUpgradePreview.preflight?.warnings || []" :key="item" class="preview-warning">{{ item }}</p><div v-if="stageUpgradePreview.affected_participants?.length" class="migration-list"><div class="migration-list-heading"><strong>{{ t('workflow.selectMigration') }}</strong><button class="tiny" @click="selectAllStageParticipants">{{ t('workflow.selectAll') }} / {{ t('workflow.clearAll') }}</button></div><label v-for="item in stageUpgradePreview.affected_participants" :key="item.participant_id"><input v-model="stageUpgradeParticipantIds" type="checkbox" :value="item.participant_id" />{{ item.student_id }} · {{ item.state }} · {{ t('workflow.currentVersion') }} v{{ item.current_config_id === stage.config_version_id ? stage.config?.version || 1 : t('workflow.previousVersion') }}</label></div><p v-else class="muted-row">{{ t('workflow.noMigration') }}</p><div class="form-actions"><button class="small primary" :disabled="saving || !stageUpgradePreview.preflight?.ok" @click="publishStageUpgrade(stage)"><span class="material-symbols-outlined">publish</span>{{ t('workflow.publishAndMigrate') }}</button></div></div></div></template><div v-if="!(cohort.stages || []).length" class="muted-row">{{ t('workflow.noStages') }}</div></div>
          </section>

          <section v-if="reopenCandidates.length && activeSection === 'overview'" class="panel correction-panel"><div class="panel-heading"><div><h3>{{ t('workflow.correctionTitle') }}</h3><p>{{ t('workflow.correctionDetail') }}</p></div><span class="material-symbols-outlined panel-icon">history</span></div><div class="archive-list"><div v-for="task in reopenCandidates" :key="task.id" class="archive-row"><div><strong>{{ task.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ task.cohort_name }} · {{ task.stage_name }} · {{ stateLabel(task.state) }}</span></div><div class="stage-row-actions"><button class="tiny primary" @click="handleReopenParticipant(task)"><span class="material-symbols-outlined">edit_note</span>{{ t('workflow.reopen') }}</button><button class="tiny" @click="handleExitParticipant(task)"><span class="material-symbols-outlined">person_off</span>{{ t('workflow.exit') }}</button></div></div></div></section>

            <section v-if="selectedCohort && activeSection === 'overview'" class="panel setup-panel">
            <div class="panel-heading"><div><h3>{{ selectedCohort.name }} · {{ t('workflow.stageConfig') }}</h3></div><button class="icon-button" :title="t('common.close')" :aria-label="t('common.close')" @click="selectedCohort = null"><span class="material-symbols-outlined">close</span></button></div>
            <StageConfigForm :form="stageForm" :templates="templates" :saving="saving" @save="handleCreateStage" />
            <div class="member-quick-add"><h4>{{ t('workflow.addAdvising') }}</h4><div class="form-grid"><label>{{ t('workflow.studentIdName') }}<input v-model="memberForm.student_ref" :placeholder="t('workflow.studentIdPlaceholder')" /></label><label>{{ t('workflow.teacherIdName') }}<input v-model="memberForm.teacher_ref" :placeholder="t('workflow.teacherIdPlaceholder')" /></label><label>{{ t('workflow.changeReason') }}<input v-model="memberForm.reason" :placeholder="t('workflow.changeReasonPlaceholder')" /></label></div><button class="small" :disabled="saving || !memberForm.student_ref || !memberForm.teacher_ref" @click="handleAddMember"><span class="material-symbols-outlined">person_add</span>{{ t('workflow.saveRelationship') }}</button></div>
            <div v-if="preview" class="preview-box"><div class="preview-heading"><strong>{{ t('workflow.publishPreview') }}</strong><span :class="preview.ok ? 'preview-ok' : 'preview-error'">{{ preview.ok ? t('workflow.publishable') : t('workflow.blocked') }}</span></div><p v-for="item in preview.errors" :key="item" class="preview-error">{{ item }}</p><p v-for="item in preview.warnings" :key="item" class="preview-warning">{{ item }}</p><p v-if="preview.ok">{{ t('workflow.memberFreeze', { count: preview.member_snapshot?.length || 0 }) }}</p></div>
            <div class="management-records"><div class="panel-heading"><div><h3>{{ t('workflow.managementMaterials') }}</h3><p>{{ t('workflow.managementMaterialsDetail') }}</p></div><span class="material-symbols-outlined panel-icon">assignment</span></div><div class="management-catalog"><div v-for="item in processCatalog.cohort" :key="item.artifact_type"><strong>{{ item.name }}</strong><span class="state-tag" :class="cohortMaterialRecord(item.artifact_type)?.state || 'missing'">{{ cohortMaterialRecord(item.artifact_type) ? recordState(cohortMaterialRecord(item.artifact_type).state) : t('workflow.notSubmittedStatus') }}</span></div></div><div class="form-grid"><label>{{ t('workflow.materialType') }}<select v-model="managementForm.artifact_type"><option v-for="item in processCatalog.cohort" :key="item.artifact_type" :value="item.artifact_type">{{ item.name }}</option></select></label><label>{{ t('workflow.materialTitle') }}<input v-model="managementForm.title" :placeholder="t('workflow.materialTitlePlaceholder')" /></label></div><label class="material-content-field">{{ t('workflow.structuredContent') }}<textarea v-model="managementForm.content" rows="3" :placeholder="t('workflow.structuredContentPlaceholder')" /></label><button class="small primary" :disabled="saving" @click="saveManagementMaterial"><span class="material-symbols-outlined">upload_file</span>{{ t('workflow.saveManagement') }}</button></div>
          </section>

          <section v-if="selectedCohort && managementSchema?.fields?.length && activeSection === 'overview'" class="panel structured-management-panel"><div class="panel-heading"><div><h3>{{ managementSchema.title }}</h3><p>{{ t('workflow.structuredManagementDetail') }}</p></div><span class="material-symbols-outlined panel-icon">table_view</span></div><div class="structured-material-form"><label v-for="field in managementSchema.fields.filter((item) => item.type !== 'table')" :key="field.key" class="structured-field"><span>{{ field.label }}<em v-if="field.required">{{ t('workflow.required') }}</em></span><textarea v-if="field.type === 'textarea'" v-model="managementData[field.key]" rows="3" :placeholder="field.help || field.label" /><textarea v-else-if="field.type === 'json'" :value="managementJsonDrafts[field.key]" rows="5" :placeholder="field.help || t('workflow.enterJson')" @input="managementJsonDrafts[field.key] = $event.target.value" /><input v-else v-model="managementData[field.key]" :type="field.type === 'number' ? 'number' : 'text'" :placeholder="field.help || field.label" /></label></div><div v-for="field in managementSchema.fields.filter((item) => item.type === 'table')" :key="field.key" class="structured-table-editor"><div class="table-editor-heading"><div><strong>{{ field.label }}<em v-if="field.required">{{ t('workflow.required') }}</em></strong><small>{{ field.help }}</small></div><button type="button" class="tiny" @click="addManagementTableRow(field)"><span class="material-symbols-outlined">add</span>{{ t('workflow.addRow') }}</button></div><div class="table-editor-scroll"><table><thead><tr><th>#</th><th v-for="column in field.columns || []" :key="column.key">{{ column.label }}</th><th></th></tr></thead><tbody><tr v-for="(row, rowIndex) in (managementTableDrafts[field.key] || [])" :key="rowIndex"><td>{{ rowIndex + 1 }}</td><td v-for="column in field.columns || []" :key="column.key"><textarea v-if="column.type === 'textarea'" v-model="row[column.key]" rows="2" :placeholder="column.label" /><input v-else v-model="row[column.key]" :type="column.type === 'number' ? 'number' : 'text'" :placeholder="column.label" /></td><td><button type="button" class="icon-button" :title="t('common.remove')" :aria-label="t('common.remove')" @click="removeManagementTableRow(field, rowIndex)"><span class="material-symbols-outlined">delete</span></button></td></tr><tr v-if="!(managementTableDrafts[field.key] || []).length"><td :colspan="(field.columns || []).length + 2" class="muted-row">{{ t('workflow.noDetails') }}</td></tr></tbody></table></div></div><button class="small primary" :disabled="saving" @click="saveManagementMaterial"><span class="material-symbols-outlined">upload_file</span>{{ t('workflow.saveStructured') }}</button></section>

          <section v-if="activeSection === 'submissions'" class="panel archive-panel"><div class="panel-heading"><div><h3>{{ t('workflow.submissionsTitle') }}</h3><p>{{ t('workflow.submissionsDetail') }}</p></div><span class="material-symbols-outlined panel-icon">inventory_2</span></div><div v-if="reviewQueue.length" class="archive-list"><div v-for="task in reviewQueue" :key="'submission-' + task.id" class="archive-row"><div><strong>{{ task.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ task.cohort_name }} · {{ task.stage_name }} · {{ task.current_submission?.number || t('workflow.formalSubmission') }}</span></div><span class="state-tag submitted">{{ t('workflow.pendingApproval') }}</span></div></div><div v-if="archiveCandidates.length" class="archive-list"><div v-for="task in archiveCandidates" :key="task.id" class="archive-row"><div><strong>{{ task.project_title || t('workflow.unnamedThesis') }}</strong><span>{{ task.cohort_name }} · {{ task.stage_name }} · {{ task.current_submission.number }}</span></div><button class="small primary" :disabled="saving" @click="handleArchive(task)"><span class="material-symbols-outlined">archive</span>{{ t('workflow.generateArchive') }}</button></div></div><div v-if="!reviewQueue.length && !archiveCandidates.length && !archives.length" class="muted-row">{{ t('workflow.noSubmissionsArchives') }}</div><div v-if="archives.length" class="archive-list"><div v-for="archive in archives" :key="archive.id" class="archive-row"><div><strong>{{ archive.submission_id }}</strong><span>{{ archiveStatusLabel(archive.status) }} · {{ formatDate(archive.completed_at || archive.created_at) }}</span></div><div class="stage-row-actions"><button v-if="archive.status === 'succeeded'" class="tiny" @click="downloadArchiveFile(archive)"><span class="material-symbols-outlined">download</span>{{ t('common.download') }}</button><button v-if="archive.status === 'failed'" class="tiny" @click="handleRetryArchive(archive)"><span class="material-symbols-outlined">refresh</span>{{ t('workflow.retry') }}</button></div></div></div></section>
        </template>

        <template v-if="isTeacher && activeSection === 'submissions'">
          <AsyncState v-if="!reviewQueue.length" state="empty" :title="t('workflow.noReviewSubmissions')" :detail="t('workflow.noReviewSubmissionsDetail')" />
          <section v-for="task in reviewQueue" :key="task.id" class="panel task-row" :class="{ selected: selectedTask?.id === task.id }" @click="selectReviewTask(task)"><div class="task-state" :class="task.state"><span class="material-symbols-outlined">rate_review</span></div><div class="task-copy"><div class="eyebrow-row"><span>{{ task.cohort_name }}</span><span>·</span><span>{{ task.stage_name }}</span></div><h3>{{ task.project_title || t('workflow.unnamedThesis') }}</h3><p>{{ task.current_submission?.number }} · {{ task.is_late ? t('workflow.late') : t('workflow.onTime') }}</p></div><div class="task-side"><span class="state-tag submitted">{{ t('workflow.pendingApproval') }}</span><span>{{ formatDate(task.updated_at) }}</span></div></section>
          <section v-if="selectedSubmission" class="panel review-panel">
            <div class="panel-heading"><div><p class="workflow-kicker">{{ selectedSubmission.submission.submission_number }}</p><h3>{{ t('workflow.submitReview') }}</h3><p>{{ selectedSubmission.version.source_revision }} revision · {{ selectedSubmission.version.content_hash.slice(0, 12) }}</p></div><button class="small" @click="selectedSubmission = null">{{ t('workflow.backToList') }}</button></div>
            <div class="gate-strip"><div v-for="gate in (selectedSubmission.candidate?.gates || [])" :key="gate.key"><span>{{ gate.label }}</span><strong>{{ gate.passed ? t('workflow.approve') : t('workflow.handleGates') }}</strong></div><div><span>{{ t('workflow.bodyVersion') }}</span><strong>{{ t('workflow.readonlySnapshot') }}</strong></div></div>
            <ReviewEvidence
              :submission="selectedSubmission"
              :format-date="formatDate"
              :record-state="recordState"
              @download-material="downloadMaterial"
            />
            <div class="comment-list"><div v-for="comment in selectedSubmission.comments" :key="comment.id" class="comment-item" :class="comment.severity"><div><span class="state-tag" :class="comment.status">{{ comment.severity === 'blocking' ? t('workflow.blocking') : t('workflow.suggestion') }}</span><span class="comment-anchor">{{ comment.block_id || t('workflow.fullText') }}</span></div><p>{{ comment.body }}</p><small>{{ t('workflow.replies', { count: comment.responses?.length || 0 }) }} · {{ comment.status }}</small><button v-if="comment.status !== 'resolved'" class="tiny" @click.stop="resolveComment(comment)"><span class="material-symbols-outlined">check</span>{{ t('workflow.markResolved') }}</button></div><div v-if="!selectedSubmission.comments.length" class="muted-row">{{ t('workflow.noComments') }}</div></div>
            <div class="review-compose"><textarea v-model="commentForm.body" rows="3" :placeholder="t('workflow.commentPlaceholder')" /><div class="review-compose-actions"><select v-model="commentForm.severity"><option value="info">{{ t('workflow.normalSuggestion') }}</option><option value="blocking">{{ t('workflow.blockingIssue') }}</option></select><input v-model="commentForm.block_id" :placeholder="t('workflow.paragraphIdOptional')" /><button class="small" @click="handleComment"><span class="material-symbols-outlined">add_comment</span>{{ t('workflow.addComment') }}</button></div></div><div class="review-decision"><textarea v-model="reviewForm.reason" rows="2" :placeholder="t('workflow.reviewReasonPlaceholder')" /><div class="review-decision-actions"><button class="danger" @click="handleReview('returned')"><span class="material-symbols-outlined">undo</span>{{ t('workflow.returnForRevision') }}</button><button class="primary" @click="handleReview('approved')"><span class="material-symbols-outlined">verified</span>{{ t('workflow.approveSubmission') }}</button></div></div>
          </section>
          <section v-if="selectedSubmission && selectedSubmission.config?.required_manual_checks?.length" class="panel manual-check-panel"><div class="panel-heading"><div><p class="workflow-kicker">{{ t('workflow.reviewChecklist') }}</p><h3>{{ t('workflow.reviewChecklist') }}</h3><p>{{ t('workflow.reviewChecklistDetail') }}</p></div><span class="material-symbols-outlined panel-icon">fact_check</span></div><div class="manual-checks"><label v-for="check in selectedSubmission.config.required_manual_checks" :key="check"><input v-model="manualConfirmations[check]" type="checkbox" />{{ check }}</label></div></section>
        </template>

        <template v-else-if="activeSection === 'overview'">
          <AsyncState v-if="!tasks.length" state="empty" :title="t('workflow.noStageTasks')" :detail="t('workflow.noStageTasksDetail')" />
          <section v-for="task in tasks" :key="task.id" class="panel student-task" :class="task.state"><div class="task-topline"><div><div class="eyebrow-row"><span>{{ task.cohort_name }}</span><span>·</span><span>{{ task.stage_name }}</span><span v-if="task.is_late" class="late-label">{{ t('workflow.overdue') }}</span></div><h3>{{ task.project_title || t('workflow.thesisNotBound') }}</h3></div><span class="state-tag" :class="task.state">{{ stateLabel(task.state) }}</span></div><div class="task-meta"><span><span class="material-symbols-outlined">schedule</span>{{ deadlineLabel(task) }}</span><span><span class="material-symbols-outlined">description</span>{{ task.current_submission ? task.current_submission.number : t('workflow.noFormalSubmission') }}</span></div><div class="task-actions"><button v-if="!task.project_id" class="primary" @click="bindTask(task)"><span class="material-symbols-outlined">edit_document</span>{{ t('workflow.startWriting') }}</button><RouterLink v-else class="small primary link-button" :to="'/write/' + task.project_id"><span class="material-symbols-outlined">edit_note</span>{{ t('workflow.continueWriting') }}</RouterLink><button v-if="task.project_id && !task.acknowledged_snapshot_hash" class="small" @click="acknowledgeTask(task)"><span class="material-symbols-outlined">fact_check</span>{{ t('workflow.acknowledgeRequirements') }}</button><button v-if="task.project_id && ['in_progress', 'needs_revision'].includes(task.state)" class="small" @click="prepareCandidate(task)"><span class="material-symbols-outlined">send</span>{{ t('workflow.precheckSubmission') }}</button><button class="small" @click="openMaterials(task)"><span class="material-symbols-outlined">folder_managed</span>{{ t('workflow.processMaterials') }}</button><button v-if="task.current_submission" class="small" @click="viewHistory(task)"><span class="material-symbols-outlined">history</span>{{ t('workflow.viewRecords') }}</button></div><div v-if="selectedTask?.id === task.id && selectedCandidate" class="candidate-box"><div class="candidate-heading"><strong>{{ t('workflow.precheck') }} {{ selectedCandidate.id.slice(-8) }}</strong><span class="state-tag" :class="selectedCandidate.status">{{ selectedCandidate.status === 'ready' ? t('workflow.readyToSubmit') : t('workflow.handleGates') }}</span></div><div class="candidate-gates"><div v-for="gate in selectedCandidate.gates" :key="gate.key" :class="{ passed: gate.passed, blocked: gate.blocking }"><span class="material-symbols-outlined">{{ gate.passed ? 'check_circle' : 'error' }}</span><div><strong>{{ gate.label }}</strong><small>{{ gate.passed ? t('workflow.gateSatisfied') : (Array.isArray(gate.detail) ? gate.detail.join(' / ') : gate.detail) }}</small></div></div></div><label v-if="selectedCandidate.warning_count"> <input v-model="warningAcknowledged" type="checkbox" />{{ t('workflow.warningAcknowledged') }}</label><button class="primary" :disabled="selectedCandidate.status !== 'ready' || (selectedCandidate.warning_count && !warningAcknowledged) || submitting" @click="handleConfirmSubmission"><span class="material-symbols-outlined">check</span>{{ submitting ? t('workflow.submitting') : t('workflow.confirmFormalSubmission') }}</button></div></section>
          <section v-if="materialsTask" class="panel materials-panel"><div class="panel-heading"><div><p class="workflow-kicker">{{ t('workflow.processMaterials') }}</p><h3>{{ materialsTask.project_title || t('workflow.currentStage') }} · {{ t('workflow.materialList') }}</h3><p>{{ t('workflow.materialsDetail') }}</p></div><button class="icon-button" :title="t('workflow.closeMaterials')" :aria-label="t('workflow.closeMaterials')" @click="materialsTask = null"><span class="material-symbols-outlined">close</span></button></div><div v-if="qualification" class="qualification-strip"><div><span>{{ t('workflow.defenseEligibility') }}</span><strong :class="qualification.eligible_for_application ? 'qualification-ok' : 'qualification-warn'">{{ qualification.eligible_for_application ? t('workflow.defenseEligible') : t('workflow.defenseIncomplete') }}</strong></div><span>{{ qualification.gates.filter((item) => item.passed).length }} / {{ qualification.gates.length }} {{ t('common.items') }}</span></div><div v-if="scoreSummary" class="score-summary"><div><span>{{ t('workflow.advisor') }}</span><strong>{{ scoreSummary.scores.advisor_score ?? '-' }}</strong></div><div><span>{{ t('workflow.reviewer') }}</span><strong>{{ scoreSummary.scores.reviewer_score ?? '-' }}</strong></div><div><span>{{ t('workflow.defenseGroup') }}</span><strong>{{ scoreSummary.scores.defense_score ?? '-' }}</strong></div><div><span>{{ t('workflow.overallScore') }}</span><strong>{{ scoreSummary.total_score ?? '-' }}</strong></div></div><div class="material-catalog"><div v-for="item in processCatalog.participant" :key="item.artifact_type" class="material-catalog-row"><div><strong>{{ item.name }}</strong><small>{{ item.phase }} · {{ item.description || item.source }}</small></div><span class="state-tag" :class="materialStateClass(item.artifact_type)">{{ materialState(item.artifact_type) }}</span></div></div><div class="material-compose"><div class="form-grid"><label>{{ t('workflow.materialType') }}<select v-model="materialForm.artifact_type" @change="loadMaterialForm(materialForm.artifact_type)"><option v-for="item in processCatalog.participant" :key="item.artifact_type" :value="item.artifact_type">{{ item.name }}</option></select></label><label>{{ t('workflow.materialTitle') }}<input v-model="materialForm.title" :placeholder="t('workflow.materialNamePlaceholder')" /></label></div><div v-if="materialSchema?.fields?.length" class="structured-material-form"><label v-for="field in (materialSchema?.fields || []).filter((item) => item.type !== 'table')" :key="field.key" class="structured-field"><span>{{ field.label }}<em v-if="field.required">{{ t('workflow.required') }}</em></span><select v-if="field.type === 'select'" v-model="materialData[field.key]"><option value="">{{ t('workflow.choose') }}</option><option v-for="option in field.options" :key="option" :value="option">{{ option }}</option></select><textarea v-else-if="field.type === 'textarea'" v-model="materialData[field.key]" rows="3" :placeholder="field.help || field.label" /><textarea v-else-if="field.type === 'json'" :value="materialJsonDrafts[field.key]" rows="4" :placeholder="field.help || t('workflow.enterJson')" @input="materialJsonDrafts[field.key] = $event.target.value" /><input v-else v-model="materialData[field.key]" :type="field.type === 'number' ? 'number' : field.type === 'datetime' ? 'datetime-local' : 'text'" :placeholder="field.help || field.label" /></label></div><div v-for="field in (materialSchema?.fields || []).filter((item) => item.type === 'table')" :key="field.key" class="structured-table-editor"><div class="table-editor-heading"><div><strong>{{ field.label }}<em v-if="field.required">{{ t('workflow.required') }}</em></strong><small>{{ field.help }}</small></div><button type="button" class="tiny" @click="addMaterialTableRow(field)"><span class="material-symbols-outlined">add</span>{{ t('workflow.addRow') }}</button></div><div class="table-editor-scroll"><table><thead><tr><th>#</th><th v-for="column in field.columns || []" :key="column.key">{{ column.label }}</th><th></th></tr></thead><tbody><tr v-for="(row, rowIndex) in (materialTableDrafts[field.key] || [])" :key="rowIndex"><td>{{ rowIndex + 1 }}</td><td v-for="column in field.columns || []" :key="column.key"><textarea v-if="column.type === 'textarea'" v-model="row[column.key]" rows="2" :placeholder="column.label" /><input v-else v-model="row[column.key]" :type="column.type === 'number' ? 'number' : 'text'" :placeholder="column.label" /></td><td><button type="button" class="icon-button" :title="t('common.remove')" :aria-label="t('common.remove')" @click="removeMaterialTableRow(field, rowIndex)"><span class="material-symbols-outlined">delete</span></button></td></tr><tr v-if="!(materialTableDrafts[field.key] || []).length"><td :colspan="(field.columns || []).length + 2" class="muted-row">{{ t('workflow.noDetails') }}</td></tr></tbody></table></div></div><label class="material-content-field">{{ t('workflow.additionalNotes') }}<textarea v-model="materialForm.content" rows="3" :placeholder="t('workflow.additionalNotesPlaceholder')" /></label><label class="file-picker">{{ t('workflow.originalAttachment') }}<input id="process-material-file" type="file" accept=".doc,.docx,.pdf,.zip,.pptx,.xlsx,.xls,.png,.jpg,.jpeg,.txt" @change="handleMaterialFile" /><small>{{ materialFile?.name || t('workflow.optionalUpload') }}</small></label><button class="primary" :disabled="saving" @click="saveMaterial"><span class="material-symbols-outlined">upload_file</span>{{ t('workflow.saveSubmitMaterial') }}</button></div><div v-if="processRecords.length" class="record-history"><h4>{{ t('workflow.myMaterialRecords') }}</h4><div v-for="record in processRecords" :key="record.id" class="record-row"><div><strong>{{ record.artifact_name }}</strong><span>{{ recordState(record.state) }} · {{ formatDate(record.updated_at || record.created_at) }}</span></div><div class="record-attachments"><button v-if="record.structured_form" class="tiny" @click.stop="exportMaterial(record)"><span class="material-symbols-outlined">description</span>{{ t('workflow.exportFilledForm') }}</button><button v-for="attachment in record.attachments" :key="attachment.id" class="tiny" @click.stop="downloadMaterial(attachment)"><span class="material-symbols-outlined">download</span>{{ attachment.name }}</button></div></div></div></section>
        </template>
        <section v-if="processCatalog.reference?.length && activeSection === 'overview'" class="panel reference-panel">
          <div class="panel-heading"><div><h3>{{ t('workflow.referencesTitle') }}</h3><p>{{ t('workflow.referencesDetail') }}</p></div><span class="material-symbols-outlined panel-icon">menu_book</span></div>
          <div class="reference-list"><div v-for="item in processCatalog.reference" :key="item.artifact_type" class="reference-row"><div><strong>{{ item.name }}</strong><span>{{ item.phase }}</span></div><small>{{ item.source }}</small></div></div>
        </section>
      <MemberImportWizard
        v-if="canConfigure && selectedCohort && activeSection === 'overview'"
        v-model="memberImportText"
        :rows="memberImportRows"
        :result="memberImportResult"
        :busy="saving"
        @preview="previewMemberImport"
        @commit="commitMemberImport"
      />
      </main>

      <aside class="workflow-side">
        <section v-if="activeSection === 'messages'" class="panel notification-panel workflow-wide-panel">
          <div class="panel-heading"><div><h3>{{ t('workflow.messagesTitle') }}</h3><p>{{ t('workflow.messagesDetail') }}</p></div><span class="unread-count">{{ notifications.unread || 0 }}</span></div>
          <div v-if="!notifications.items?.length" class="empty-state"><span class="material-symbols-outlined">notifications_none</span><h3>{{ t('notification.empty') }}</h3><p>{{ t('workflow.messagesDetail') }}</p></div>
          <button v-for="item in notifications.items" :key="item.id" class="notification-row" :class="{ unread: !item.read_at }" @click="readNotification(item)"><span class="material-symbols-outlined">{{ item.data?.event_type?.includes('review') ? 'rate_review' : 'notifications' }}</span><span><strong>{{ item.data?.title || t('workflow.workflowMessage') }}</strong><small>{{ formatDate(item.created_at) }}</small></span></button>
        </section>
        <section v-if="canManage && activeSection === 'reports'" class="panel report-panel workflow-wide-panel">
          <div class="panel-heading"><div><h3>{{ t('workflow.realtimeData') }}</h3><p>{{ t('workflow.realtimeDataDetail') }}</p></div><button class="icon-button" :title="t('workflow.refreshStats')" :aria-label="t('workflow.refreshStats')" @click="loadReport"><span class="material-symbols-outlined">refresh</span></button></div>
          <label class="report-filter">{{ t('workflow.reportCohort') }}<select v-model="reportCohortId" @change="loadReport"><option value="">{{ t('workflow.selectCohort') }}</option><option v-for="cohort in cohorts" :key="cohort.id" :value="cohort.id">{{ cohort.name }}</option></select></label>
          <div v-if="report" class="report-grid"><div><strong>{{ report.member_count }}</strong><span>{{ t('workflow.member') }}</span></div><div><strong>{{ report.state_counts?.submitted || 0 }}</strong><span>{{ t('workflow.pendingApproval') }}</span></div><div><strong>{{ report.state_counts?.needs_revision || 0 }}</strong><span>{{ t('workflow.needsRevision') }}</span></div><div><strong>{{ report.state_counts?.archived || 0 }}</strong><span>{{ t('workflow.archiveCompleted') }}</span></div></div>
          <div v-if="report" class="report-metrics"><span>{{ t('workflow.onTimeRate') }} <strong>{{ rate(report.metrics?.on_time_first_submission_rate) }}</strong></span><span>{{ t('workflow.archiveRate') }} <strong>{{ rate(report.metrics?.archive_completion_rate) }}</strong></span></div>
          <AsyncState v-else-if="cohorts.length" state="empty" :title="t('workflow.selectReportCohort')" :detail="t('workflow.selectReportCohortDetail')" />
          <AsyncState v-else state="empty" :title="t('workflow.noReportCohorts')" :detail="t('workflow.noReportCohortsDetail')" />
        </section>
        <section v-if="!canManage && activeSection === 'submissions'" class="panel history-panel workflow-wide-panel">
          <div class="panel-heading"><div><h3>{{ t('workflow.historyTitle') }}</h3><p>{{ t('workflow.historyDetail') }}</p></div><span class="material-symbols-outlined panel-icon">history</span></div>
          <div v-if="!submissions.length" class="empty-state"><span class="material-symbols-outlined">history</span><h3>{{ t('workflow.noSubmissionRecords') }}</h3><p>{{ t('workflow.noSubmissionRecordsDetail') }}</p></div>
          <button v-for="submission in submissions" :key="submission.id" type="button" class="history-row history-row-button" @click="viewHistory(tasks.find((task) => task.current_submission?.id === submission.id))"><div><strong>{{ submission.submission_number }}</strong><span>{{ formatDate(submission.submitted_at) }}</span></div><span class="state-tag" :class="submission.state">{{ submissionStateLabel(submission.state) }}</span></button>
        </section>
        <section v-if="activeSection === 'overview'" class="panel workspace-shortcuts">
          <div class="panel-heading"><div><h3>{{ t('workflow.shortcuts') }}</h3></div></div>
          <RouterLink v-if="canManage" to="/organizations"><span class="material-symbols-outlined" aria-hidden="true">groups</span>{{ t('workflow.membersOrg') }}</RouterLink><RouterLink v-if="canManage" to="/templates"><span class="material-symbols-outlined" aria-hidden="true">library_books</span>{{ t('workflow.templatesStandards') }}</RouterLink><RouterLink v-if="canManage" to="/audit"><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('workflow.audit') }}</RouterLink><RouterLink v-if="canManage" to="/operations"><span class="material-symbols-outlined" aria-hidden="true">monitor_heart</span>{{ t('workflow.operations') }}</RouterLink>
          <RouterLink v-if="isTeacher" :to="{ path: '/workflow', query: { panel: 'submissions' } }"><span class="material-symbols-outlined" aria-hidden="true">rate_review</span>{{ t('workflow.submitReview') }}</RouterLink><RouterLink v-if="isTeacher" :to="{ path: '/workflow', query: { panel: 'progress' } }"><span class="material-symbols-outlined" aria-hidden="true">groups</span>{{ t('workflow.studentProgress') }}</RouterLink><RouterLink v-if="isTeacher" to="/policies"><span class="material-symbols-outlined" aria-hidden="true">menu_book</span>{{ t('workflow.writingStandards') }}</RouterLink>
          <RouterLink v-if="!canManage && !isTeacher" :to="{ path: '/workflow', query: { panel: 'materials' } }"><span class="material-symbols-outlined" aria-hidden="true">folder_managed</span>{{ t('workflow.processMaterials') }}</RouterLink><RouterLink v-if="!canManage && !isTeacher" to="/policies"><span class="material-symbols-outlined" aria-hidden="true">menu_book</span>{{ t('workflow.writingStandards') }}</RouterLink>
        </section>
      </aside>
    </div>

    <div v-if="bindTarget" class="modal-mask" @click.self="bindTarget = null"><div class="modal card"><div class="modal-head"><h3>{{ t('workflow.bindThesisTitle') }}</h3><button class="icon-button" :title="t('common.close')" :aria-label="t('common.close')" @click="bindTarget = null"><span class="material-symbols-outlined">close</span></button></div><p class="hint">{{ t('workflow.stageTemplate') }}：{{ bindTarget.config?.template_id }}</p><label class="field">{{ t('workflow.thesisTitle') }}<input v-model="bindTitle" :placeholder="t('workflow.thesisTitlePlaceholder')" /></label><div class="form-actions"><button class="primary" :disabled="saving" @click="confirmBind"><span class="material-symbols-outlined">link</span>{{ t('workflow.createBind') }}</button></div></div></div>
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
  <section v-if="selectedSubmission && !isTeacher && activeSection === 'submissions'" class="panel history-detail">
    <div class="panel-heading"><div><h3>{{ selectedSubmission.submission.submission_number }}</h3><p>{{ submissionStateLabel(selectedSubmission.submission.state) }} · {{ formatDate(selectedSubmission.submission.submitted_at) }} · revision {{ selectedSubmission.version.source_revision }}</p></div><button class="small" @click="selectedSubmission = null">{{ t('workflow.closeDetail') }}</button></div>
    <div class="gate-strip"><div v-for="gate in (selectedSubmission.candidate?.gates || [])" :key="gate.key"><span>{{ gate.label }}</span><strong>{{ gate.passed ? t('workflow.approve') : t('workflow.handleGates') }}</strong></div><div><span>{{ t('workflow.bodySummary') }}</span><strong>{{ selectedSubmission.version.content_hash.slice(0, 12) }}</strong></div></div>
    <div class="history-snapshot"><h4>{{ selectedSubmission.version.content_snapshot?.title || t('workflow.unnamedThesis') }}</h4><div v-for="section in (selectedSubmission.version.content_snapshot?.sections || [])" :key="section.key" class="snapshot-section"><h5>{{ section.title || section.key }}</h5><div v-for="block in (section.paragraphs || [])" :key="block.id" class="snapshot-block"><p v-if="block.type === 'paragraph'">{{ block.text || t('workflow.emptyParagraph') }}</p><div v-else-if="block.type === 'table'" class="snapshot-table"><strong>{{ block.table?.caption || t('workflow.table') }}</strong><div v-for="(row, rowIndex) in (block.table?.cells || [])" :key="rowIndex">{{ row.join(' | ') }}</div></div><div v-else class="snapshot-image"><span>{{ block.image?.caption || block.image?.url || t('workflow.image') }}</span></div></div></div></div>
    <div v-if="versionHistory.length > 1" class="version-compare"><div class="panel-heading"><div><h4>{{ t('workflow.versionCompare') }}</h4></div><button class="small" @click="compareSelectedVersions"><span class="material-symbols-outlined">compare</span>{{ t('workflow.compare') }}</button></div><div class="compare-selects"><label>{{ t('workflow.baselineVersion') }}<select v-model="versionSelection.left"><option v-for="version in versionHistory" :key="version.id" :value="version.id">revision {{ version.source_revision }} · {{ formatDate(version.created_at) }}</option></select></label><label>{{ t('workflow.compareVersion') }}<select v-model="versionSelection.right"><option v-for="version in versionHistory" :key="version.id" :value="version.id">revision {{ version.source_revision }} · {{ formatDate(version.created_at) }}</option></select></label></div><div v-if="versionCompare" class="compare-result"><p>{{ versionCompare.diff.changed ? t('workflow.detectedChanges') : t('workflow.sameContent') }}</p><div v-for="section in versionCompare.diff.sections" :key="section.key" class="compare-section"><strong>{{ section.key }} · {{ versionDiffLabel(section.status) }}</strong><div v-for="block in (section.blocks || [])" :key="block.id" class="compare-block"><span>{{ block.id }} · {{ versionDiffLabel(block.status) }}</span><p v-if="block.status === 'changed'">{{ diffBlockText(block.before) }} → {{ diffBlockText(block.after) }}</p></div></div><div v-if="versionCompare.diff.attachments.added.length || versionCompare.diff.attachments.removed.length || versionCompare.diff.attachments.changed.length" class="compare-attachments">{{ t('workflow.attachmentChanges', { added: versionCompare.diff.attachments.added.length, removed: versionCompare.diff.attachments.removed.length, changed: versionCompare.diff.attachments.changed.length }) }}</div></div></div>
    <div v-if="selectedSubmission.submission.state === 'returned'" class="history-restore"><span>{{ t('workflow.restoreReturned') }}</span><button class="small" @click="restoreHistoricalVersion(versionHistory.find((version) => version.id === versionSelection.right))"><span class="material-symbols-outlined">restore</span>{{ t('workflow.restoreSelected') }}</button></div>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { auth } from '../auth'
import AsyncState from '../components/AsyncState.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import DeadlineLabel from '../components/DeadlineLabel.vue'
import MemberImportWizard from '../components/MemberImportWizard.vue'
import ReasonDialog from '../components/ReasonDialog.vue'
import ReviewEvidence from '../components/ReviewEvidence.vue'
import StageConfigForm from '../components/StageConfigForm.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useI18n } from '../i18n'
import {
  addCohortMember, acknowledgeTaskRequirements, bindTaskProject, confirmSubmission, createArchive,
  createCohort, createCohortProcessRecord, createCohortStage, createParticipantProcessRecord, createSubmissionCandidate, createSubmissionComment,
  decideProcessRecord, downloadProcessRecordAttachment,
  exportProcessRecord, fetchProcessRecordForm, fetchCohortProcessRecordForm, fetchParticipantScoreSummary,
  fetchCohort, fetchCohortProcessRecords, fetchCohorts, fetchMyTasks, fetchOrganizations, fetchProjectVersions, fetchSubmission, fetchSubmissions,
  compareProjectVersions,
  fetchArchive, fetchArchives, fetchParticipantProcessRecords, fetchParticipantQualification, fetchProcessCatalog,
  fetchProcessRecordInbox, fetchTemplates, fetchWorkflowNotifications, fetchWorkflowReport, markWorkflowNotificationRead,
  previewCohortStage, publishCohortStage, createStageVersion, publishStageVersion, reviewSubmission, retryArchive, updateSubmissionCommentStatus,
  submitProcessRecord, uploadProcessRecordAttachment, downloadArchive, reopenStageParticipant, exitStageParticipant,
  closeCohort, importCohortMembers, reopenCohort, restoreProjectVersion,
} from '../api'

const { t, formatDate: formatLocaleDate } = useI18n()

const loading = ref(false)
const saving = ref(false)
const submitting = ref(false)
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
const selectedTask = ref(null)
const selectedCandidate = ref(null)
const selectedSubmission = ref(null)
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
const cohortForm = reactive({ name: '', organization_id: '', academic_year: '' })
const memberForm = reactive({ student_ref: '', teacher_ref: '', reason: '' })
const memberImportText = ref('')
const memberImportResult = ref(null)
const stageForm = reactive({ key: 'draft', name: t('workflow.defaultStageName'), open_at: '', deadline_at: '', revision_days: 7, template_id: '', required_manual_checks: [], required_materials_text: '', manual_checks_text: '', is_final: false, allow_early_write: false })
const stageUpgradeForm = reactive({ deadline_at: '', revision_days: 7, reason: '' })
const commentForm = reactive({ body: '', severity: 'info', block_id: '' })
const reviewForm = reactive({ reason: '' })
const materialForm = reactive({ artifact_type: 'task_book', title: '', content: '' })
const managementForm = reactive({ artifact_type: 'topic_review_summary', title: '', content: '' })
const processDecisionReason = ref('')
const reasonDialog = reactive({ open: false, title: '', description: '', defaultValue: '', confirmLabel: '', action: null })
const confirmBusy = ref(false)
const confirmDialog = reactive({ open: false, title: '', description: '', confirmLabel: '', danger: false, action: null })

const route = useRoute()
const router = useRouter()
const isTeacher = computed(() => auth.user?.role === 'teacher')
const canManage = computed(() => ['admin', 'college_admin'].includes(auth.user?.role))
const canConfigure = computed(() => canManage.value || (auth.user?.role === 'teacher' && auth.user?.status === 'active'))
const roleLabel = computed(() => t(`role.${auth.user?.role || 'user'}`))
const panelTarget = computed(() => String(route.query.panel || ''))
const activeSection = computed(() => {
  if (panelTarget.value === 'messages') return 'messages'
  if (panelTarget.value === 'reports' && canManage.value) return 'reports'
  if (['submissions', 'archives'].includes(panelTarget.value)) return 'submissions'
  return 'overview'
})
const workflowTabs = computed(() => {
  const tabs = [{
    key: 'overview',
    section: 'overview',
    label: canManage.value ? t('workflow.cohortsStages') : isTeacher.value ? t('workflow.review') : t('workflow.myTasks'),
    icon: canManage.value ? 'dashboard' : isTeacher.value ? 'rate_review' : 'assignment',
  }, {
    key: canManage.value ? 'archives' : 'submissions',
    section: 'submissions',
    label: t('workflow.submissionsArchives'),
    icon: 'inventory_2',
  }]
  if (canManage.value) tabs.push({ key: 'reports', section: 'reports', label: t('workflow.reports'), icon: 'monitoring' })
  tabs.push({ key: 'messages', section: 'messages', label: t('workflow.messages'), icon: 'notifications' })
  return tabs
})
const sectionKicker = computed(() => activeSection.value === 'reports' ? t('workflow.reports') : activeSection.value === 'messages' ? t('workflow.messages') : activeSection.value === 'submissions' ? t('workflow.submissionsArchives') : canConfigure.value ? t('workflow.cohortsStages') : isTeacher.value ? t('workflow.teacherTodo') : t('workflow.myTasks'))
const sectionTitle = computed(() => activeSection.value === 'reports' ? t('workflow.reports') : activeSection.value === 'messages' ? t('workflow.messages') : activeSection.value === 'submissions' ? t('workflow.submissionsArchives') : canConfigure.value ? t('workflow.cohortsStages') : isTeacher.value ? t('workflow.teacherTodo') : t('workflow.myStageTasks'))
const openTaskCount = computed(() => tasks.value.filter((item) => !['approved', 'archived'].includes(item.state)).length)
const reviewQueue = computed(() => tasks.value.filter((item) => item.state === 'submitted'))
const archiveCandidates = computed(() => tasks.value.filter((item) => item.state === 'pending_archive' && item.current_submission))
const reopenCandidates = computed(() => tasks.value.filter((item) => ['approved', 'pending_archive', 'archived'].includes(item.state)))

function selectSection(key) {
  const query = { ...route.query }
  if (key === 'overview') delete query.panel
  else query.panel = key
  router.replace({ query })
}

async function loadAll() {
  loading.value = true; error.value = ''; notice.value = ''
  try {
    const inboxPromise = isTeacher.value || canManage.value ? fetchProcessRecordInbox() : Promise.resolve([])
    const [taskData, notificationData, submissionData, catalogData, inboxData] = await Promise.all([fetchMyTasks(), fetchWorkflowNotifications(), fetchSubmissions(), fetchProcessCatalog(), inboxPromise])
    tasks.value = taskData; notifications.value = notificationData; submissions.value = submissionData; processCatalog.value = catalogData; processInbox.value = inboxData
    if (canConfigure.value) {
      const [cohortData, orgData, templateData, archiveData] = await Promise.all([
        fetchCohorts(), fetchOrganizations(), fetchTemplates(), canManage.value ? fetchArchives() : Promise.resolve([]),
      ])
      cohorts.value = await Promise.all(cohortData.map(async (item) => fetchCohort(item.id)))
      organizations.value = orgData; templates.value = templateData; archives.value = canManage.value ? archiveData : []
      if (!stageForm.template_id && templateData.length) stageForm.template_id = templateData[0].id
      if (canManage.value) {
        if (!reportCohortId.value && cohorts.value[0]?.id) reportCohortId.value = cohorts.value[0].id
        if (reportCohortId.value) await loadReport()
      }
    }
  } catch (e) { error.value = e.message } finally { loading.value = false }
}

async function handleCreateCohort() {
  saving.value = true
  try { await createCohort(cohortForm); cohortForm.name = ''; await loadAll(); notice.value = t('workflow.cohortCreated') } catch (e) { error.value = e.message } finally { saving.value = false }
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

function handleExitParticipant(task) {
  openReasonDialog({ title: t('workflow.exitTask'), description: t('workflow.exitTaskDescription', { name: task.project_title || t('home.untitledThesis') }), defaultValue: t('workflow.exitTaskDefault'), confirmLabel: t('workflow.confirmExit') }, async (reason) => {
    saving.value = true
    try { await exitStageParticipant(task.id, reason); await loadAll(); notice.value = t('workflow.taskExited') } catch (e) { error.value = e.message } finally { saving.value = false }
  })
}

async function selectCohort(cohort) {
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

async function previewAndPublish(stage) {
  try {
    const result = await previewCohortStage(stage.id); preview.value = result
    if (result.ok) openConfirmDialog({ title: t('workflow.publishStageTitle', { name: stage.name }), description: t('workflow.publishStageDescription', { count: result.member_snapshot.length }), confirmLabel: t('workflow.confirmPublish') }, async () => {
      await publishCohortStage(stage.id); selectedCohort.value = await fetchCohort(selectedCohort.value.id); await loadAll(); notice.value = t('workflow.stagePublished')
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

function bindTask(task) { bindTarget.value = task; bindTitle.value = `${task.cohort_name} · ${task.stage_name}` }
async function confirmBind() {
  saving.value = true
  try { await bindTaskProject(bindTarget.value.id, { title: bindTitle.value }); bindTarget.value = null; await loadAll(); notice.value = t('workflow.thesisBound') } catch (e) { error.value = e.message } finally { saving.value = false }
}

async function acknowledgeTask(task) {
  try { await acknowledgeTaskRequirements(task.id); await loadAll(); notice.value = t('workflow.requirementsAcknowledged') } catch (e) { error.value = e.message }
}

async function openMaterials(task) {
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
    const record = await createParticipantProcessRecord(materialsTask.value.id, {
      artifact_type: materialForm.artifact_type,
      data,
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

async function exportMaterial(record) {
  try { await exportProcessRecord(record.id); notice.value = t('workflow.materialExported') } catch (e) { error.value = e.message }
}

async function prepareCandidate(task) {
  try { selectedTask.value = task; selectedCandidate.value = await createSubmissionCandidate({ participant_id: task.id, modification_note: task.state === 'needs_revision' ? t('workflow.revisionComplete') : t('workflow.firstSubmission') }); warningAcknowledged.value = false } catch (e) { error.value = e.message }
}

async function handleConfirmSubmission() {
  submitting.value = true
  try { await confirmSubmission({ candidate_id: selectedCandidate.value.id, idempotency_key: crypto.randomUUID(), warning_acknowledged: warningAcknowledged.value }); selectedCandidate.value = null; await loadAll(); notice.value = t('workflow.submissionConfirmed') } catch (e) { error.value = e.message } finally { submitting.value = false }
}

async function selectReviewTask(task) {
  selectedTask.value = task
  try {
    selectedSubmission.value = await loadSubmissionDetail(task.current_submission.id, task.id)
    Object.keys(manualConfirmations).forEach((key) => delete manualConfirmations[key])
    ;(selectedSubmission.value.config?.required_manual_checks || []).forEach((key) => { manualConfirmations[key] = false })
    reviewForm.reason = ''; commentForm.body = ''
  } catch (e) { error.value = e.message }
}

async function handleComment() {
  if (!commentForm.body.trim()) return
  try { await createSubmissionComment(selectedSubmission.value.submission.id, commentForm); selectedSubmission.value = await loadSubmissionDetail(selectedSubmission.value.submission.id, selectedSubmission.value.participant.id); commentForm.body = ''; notice.value = t('workflow.commentSaved') } catch (e) { error.value = e.message }
}

async function resolveComment(comment) {
  try { await updateSubmissionCommentStatus(comment.id, 'resolved'); selectedSubmission.value = await loadSubmissionDetail(selectedSubmission.value.submission.id, selectedSubmission.value.participant.id) } catch (e) { error.value = e.message }
}

async function handleReview(decision) {
  if (decision === 'returned' && !reviewForm.reason.trim()) { error.value = t('workflow.reviewReasonRequired'); return }
  try { await reviewSubmission(selectedSubmission.value.submission.id, { decision, reason: reviewForm.reason, manual_confirmations: { ...manualConfirmations } }); selectedSubmission.value = null; await loadAll(); notice.value = decision === 'approved' ? t('workflow.submissionApproved') : t('workflow.submissionReturned') } catch (e) { error.value = e.message }
}

async function viewHistory(task) {
  if (task?.current_submission?.id) {
    try {
      selectedSubmission.value = await loadSubmissionDetail(task.current_submission.id, task.id)
      versionHistory.value = task.project_id ? [...await fetchProjectVersions(task.project_id)].sort((a, b) => new Date(a.created_at) - new Date(b.created_at)) : []
      versionCompare.value = null
      versionSelection.left = versionHistory.value[0]?.id || ''
      versionSelection.right = versionHistory.value[versionHistory.value.length - 1]?.id || ''
    } catch (e) { error.value = e.message }
  }
}

async function loadSubmissionDetail(submissionId, participantId) {
  const [detail, records] = await Promise.all([fetchSubmission(submissionId), fetchParticipantProcessRecords(participantId)])
  return { ...detail, process_records: records }
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
  if (!item.read_at) { try { await markWorkflowNotificationRead(item.id); notifications.value = await fetchWorkflowNotifications() } catch (e) { error.value = e.message } }
}

function stateLabel(state) { return t(`task.${state}`) === `task.${state}` ? state : t(`task.${state}`) }
function submissionStateLabel(state) { return t(`submission.${state}`) === `submission.${state}` ? state : t(`submission.${state}`) }
function archiveStatusLabel(status) { return t(`archive.${status}`) === `archive.${status}` ? status : t(`archive.${status}`) }
function formatDate(value) { return value ? formatLocaleDate(value, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-' }
function deadlineLabel(task) { return `${task.revision_deadline_at ? t('deadline.revision') : t('deadline.initial')} ${formatDate(task.revision_deadline_at || task.effective_deadline_at)}` }
function rate(value) { return value === null || value === undefined ? '-' : `${Math.round(Number(value) * 100)}%` }

onMounted(loadAll)
</script>

<style scoped>
.workflow-page { max-width: 1360px; margin: 0 auto; padding: 24px 0 56px; }
.workflow-hero { display: flex; justify-content: space-between; align-items: center; gap: 24px; padding: 26px 28px; background: var(--inverse-surface); color: var(--inverse-on-surface); border-radius: var(--radius-lg); }
.workflow-kicker { color: var(--primary); font-size: 10px; letter-spacing: .09em; font-weight: 700; margin: 0 0 6px; }
.workflow-hero .workflow-kicker { color: var(--primary-fixed-dim); }
.workflow-hero h1 { font-family: "Source Serif 4", Georgia, serif; font-size: 30px; line-height: 1.2; margin: 0 0 7px; }
.workflow-subtitle { color: #c9d4e7; margin: 0; max-width: 650px; }
.workflow-identity { display: flex; align-items: center; gap: 10px; min-width: 180px; padding: 10px 12px; border-left: 1px solid rgba(255,255,255,.18); }
.workflow-identity .material-symbols-outlined { color: var(--primary-fixed-dim); font-size: 26px; }
.workflow-identity div { display: grid; gap: 2px; }.workflow-identity span { color: #b9c6db; font-size: 12px; }
.workflow-summary { display: grid; grid-template-columns: repeat(4, 1fr); margin: 18px 0 24px; border-bottom: 1px solid var(--border); }
.workflow-summary > div { padding: 0 18px 16px; border-right: 1px solid var(--border); display: grid; gap: 2px; }.workflow-summary > div:first-child { padding-left: 0; }.workflow-summary > div:last-child { border-right: 0; }
.workflow-summary span { color: var(--on-surface-variant); font-size: 12px; }.workflow-summary strong { font-size: 26px; color: var(--on-surface); }
.workflow-layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 22px; }.workflow-main, .workflow-side { min-width: 0; }
.section-title-row, .panel-heading, .task-topline, .preview-heading, .candidate-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.section-title-row { margin-bottom: 14px; }.section-title-row h2 { margin: 0; font-size: 22px; }.panel-heading h3, .task-topline h3 { margin: 0; font-size: 17px; }.panel-heading p, .task-copy p, .task-topline p { color: var(--on-surface-variant); font-size: 12px; margin: 4px 0 0; }
.panel { background: var(--surface-container-lowest); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 18px; margin-bottom: 14px; box-shadow: var(--shadow-card); }.create-panel { border-top: 3px solid var(--primary); }.panel-icon { color: var(--primary); font-size: 26px; }.icon-button { width: 32px; height: 32px; padding: 0; border-radius: 50%; }.link-button { text-decoration: none; }
.form-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }.form-grid label, .field { display: grid; gap: 5px; font-size: 12px; color: var(--on-surface-variant); }.form-grid input, .form-grid select, .field input { width: 100%; min-width: 0; }.form-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 15px; }.check-grid { display: flex; gap: 20px; margin-top: 14px; font-size: 12px; color: var(--on-surface-variant); }.check-grid input { accent-color: var(--primary); }
.cohort-panel { padding-bottom: 0; max-height: 600px; display: flex; flex-direction: column; }.cohort-panel .panel-heading { flex-shrink: 0; }.eyebrow-row { display: flex; align-items: center; gap: 7px; color: var(--on-surface-variant); font-size: 11px; }.status-dot { width: 7px; height: 7px; background: var(--outline); border-radius: 50%; display: inline-block; }.status-dot.published { background: #0f9d6e; }.cohort-stages { margin: 16px -18px 0; border-top: 1px solid var(--border); overflow-y: auto; flex: 1; min-height: 0; }.stage-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 18px; border-bottom: 1px solid var(--border); }.stage-row strong, .stage-row span { display: block; }.stage-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }.stage-row-actions { display: flex; align-items: center; gap: 8px; }.stage-row-actions .state-tag { display: inline-block; }.muted-row { color: var(--on-surface-variant); font-size: 12px; padding: 10px 0; }.setup-panel { border-left: 3px solid var(--tertiary); }.member-quick-add { border-top: 1px solid var(--border); margin-top: 20px; padding-top: 15px; }.member-quick-add h4 { margin: 0; font-size: 13px; }.preview-box { margin-top: 16px; padding: 12px; border: 1px solid var(--border); background: var(--surface-container-low); border-radius: var(--radius-sm); font-size: 12px; }.preview-box p { margin: 5px 0 0; }.preview-ok { color: #087f5b; }.preview-error { color: var(--error); }.preview-warning { color: var(--tertiary); }
.task-row { display: flex; align-items: center; gap: 13px; cursor: pointer; }.task-row.selected { border-color: var(--primary); }.task-state { width: 36px; height: 36px; border-radius: 8px; display: grid; place-items: center; background: var(--surface-container); color: var(--primary); flex: 0 0 auto; }.task-copy { min-width: 0; flex: 1; }.task-copy h3 { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin: 2px 0 0; }.task-side { display: grid; justify-items: end; gap: 4px; color: var(--on-surface-variant); font-size: 11px; }.review-panel { margin-top: 14px; }.gate-strip { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 16px 0; }.gate-strip div { background: var(--surface-container-low); padding: 10px; }.gate-strip span, .gate-strip strong { display: block; font-size: 11px; }.gate-strip span { color: var(--on-surface-variant); }.gate-strip strong { margin-top: 3px; }.comment-list { display: grid; gap: 9px; }.comment-item { padding: 11px; border-left: 3px solid var(--outline); background: var(--surface-container-low); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }.comment-item.blocking { border-left-color: var(--error); }.comment-item p { margin: 7px 0 4px; font-size: 13px; }.comment-item small, .comment-anchor { color: var(--on-surface-variant); font-size: 11px; }.comment-anchor { margin-left: 8px; }.comment-item button { float: right; margin-top: -22px; }.review-compose, .review-decision { border-top: 1px solid var(--border); margin-top: 15px; padding-top: 15px; }.review-compose textarea, .review-decision textarea { width: 100%; resize: vertical; }.review-compose-actions, .review-decision-actions { display: flex; align-items: center; gap: 8px; margin-top: 8px; }.review-compose-actions input { flex: 1; }.review-decision-actions { justify-content: flex-end; }
.review-evidence { border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); padding: 15px 0; margin-bottom: 15px; }.evidence-heading { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }.evidence-heading h4 { margin: 0; font-size: 18px; }.evidence-heading > span { color: var(--on-surface-variant); font-size: 11px; }.check-summary { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; color: var(--on-surface-variant); font-size: 11px; }.snapshot-section { border-top: 1px solid var(--border); padding: 12px 0 0; margin-top: 12px; }.snapshot-section h5, .review-materials h5 { margin: 0 0 8px; font-size: 13px; }.snapshot-block { border-left: 2px solid var(--surface-container); padding: 5px 10px; margin: 5px 0; }.snapshot-block p { margin: 0; white-space: pre-wrap; line-height: 1.65; font-size: 13px; }.snapshot-table { overflow-x: auto; color: var(--on-surface-variant); font-size: 12px; }.snapshot-table strong { display: block; color: var(--on-surface); margin-bottom: 4px; }.snapshot-table div { border-bottom: 1px solid var(--border); padding: 4px 0; white-space: nowrap; }.snapshot-image { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 11px; }.snapshot-image img { max-width: min(100%, 560px); max-height: 280px; object-fit: contain; object-position: left center; background: var(--surface-container-low); }.review-materials { border-top: 1px solid var(--border); margin-top: 15px; padding-top: 12px; }.review-material-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border); }.review-material-row strong, .review-material-row span { display: block; }.review-material-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }
.student-task { border-left: 3px solid var(--primary); }.student-task.needs_revision { border-left-color: var(--tertiary); }.task-meta { display: flex; gap: 18px; flex-wrap: wrap; margin: 15px 0; color: var(--on-surface-variant); font-size: 12px; }.task-meta > span { display: inline-flex; align-items: center; gap: 5px; }.task-meta .material-symbols-outlined { font-size: 16px; }.task-actions { display: flex; flex-wrap: wrap; gap: 7px; }.late-label { color: var(--error); font-weight: 600; }.candidate-box { margin-top: 15px; padding: 13px; background: var(--surface-container-low); border-top: 1px solid var(--border); }.candidate-gates { display: grid; gap: 7px; margin: 12px 0; }.candidate-gates > div { display: flex; gap: 8px; align-items: flex-start; padding: 8px; background: #fff; border: 1px solid var(--border); }.candidate-gates > div .material-symbols-outlined { color: var(--tertiary); }.candidate-gates > div.passed .material-symbols-outlined { color: #087f5b; }.candidate-gates strong, .candidate-gates small { display: block; }.candidate-gates small { color: var(--on-surface-variant); margin-top: 2px; }.candidate-box > label { display: flex; gap: 7px; align-items: flex-start; font-size: 12px; margin: 10px 0; }.candidate-box input { accent-color: var(--primary); }
.notification-panel, .history-panel, .report-panel { position: sticky; top: 78px; }.notification-row { width: 100%; justify-content: flex-start; border: 0; border-bottom: 1px solid var(--border); border-radius: 0; padding: 10px 0; background: transparent; text-align: left; }.notification-row:last-child { border-bottom: 0; }.notification-row > span:first-child { color: var(--outline); }.notification-row.unread > span:first-child { color: var(--primary); }.notification-row > span:nth-child(2) { display: grid; gap: 2px; text-align: left; min-width: 0; }.notification-row strong { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }.notification-row small { color: var(--on-surface-variant); font-size: 11px; }.unread-count { min-width: 23px; height: 23px; display: grid; place-items: center; border-radius: 50%; background: var(--primary-fixed); color: var(--on-primary-fixed); font-size: 11px; }.report-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 15px; }.report-grid div { padding: 10px; background: var(--surface-container-low); }.report-grid strong, .report-grid span { display: block; }.report-grid strong { font-size: 20px; }.report-grid span { color: var(--on-surface-variant); font-size: 11px; }.history-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 0; border-bottom: 1px solid var(--border); }.history-row:last-child { border-bottom: 0; }.history-row strong, .history-row span { display: block; }.history-row div span { color: var(--on-surface-variant); font-size: 11px; margin-top: 2px; }
.state-tag { display: inline-block; padding: 3px 7px; border-radius: var(--radius-xs); background: var(--surface-container); color: var(--on-surface-variant); font-size: 10px; white-space: nowrap; }.state-tag.submitted, .state-tag.ready { background: #dbeafe; color: #1d4ed8; }.state-tag.needs_revision, .state-tag.blocked, .state-tag.returned { background: #ffedd5; color: #9a3412; }.state-tag.approved, .state-tag.archived, .state-tag.resolved, .state-tag.published { background: #dcfce7; color: #166534; }.empty-state { text-align: center; border: 1px dashed var(--border); padding: 42px 20px; color: var(--on-surface-variant); }.empty-state .material-symbols-outlined { font-size: 34px; color: var(--outline); }.empty-state h3 { color: var(--on-surface); margin: 8px 0 4px; }.empty-state p { font-size: 12px; margin: 0; }
.modal-head { display: flex; justify-content: space-between; align-items: center; }.modal-head h3 { margin: 0; }.hint { color: var(--on-surface-variant); font-size: 12px; }
@media (max-width: 900px) { .workflow-page { padding: 16px 0 40px; }.workflow-hero { padding: 20px; align-items: flex-start; flex-direction: column; }.workflow-hero h1 { font-size: 26px; }.workflow-identity { border-left: 0; border-top: 1px solid rgba(255,255,255,.18); width: 100%; padding: 12px 0 0; }.workflow-summary { grid-template-columns: repeat(2, 1fr); gap: 14px 0; }.workflow-summary > div:nth-child(2) { border-right: 0; }.workflow-summary > div:nth-child(3), .workflow-summary > div:nth-child(4) { padding-top: 4px; }.workflow-layout { grid-template-columns: 1fr; }.notification-panel, .history-panel, .report-panel { position: static; }.form-grid { grid-template-columns: 1fr 1fr; } }
@media (max-width: 560px) { .workflow-summary > div { padding-left: 9px; padding-right: 9px; }.workflow-summary > div:first-child { padding-left: 0; }.form-grid { grid-template-columns: 1fr; }.stage-row, .task-row { align-items: flex-start; flex-direction: column; }.stage-row-actions, .task-side { width: 100%; justify-items: start; display: flex; justify-content: space-between; }.gate-strip { grid-template-columns: 1fr; }.review-compose-actions, .review-decision-actions { flex-wrap: wrap; }.review-compose-actions input { flex-basis: 100%; }.panel { padding: 14px; }.cohort-stages { margin-left: -14px; margin-right: -14px; }.workflow-hero { border-radius: 0; margin-left: -24px; margin-right: -24px; }.workflow-page { overflow: hidden; } }
.manual-check-panel { border-left: 3px solid var(--tertiary); }.manual-checks { display: grid; gap: 8px; }.manual-checks label { display: flex; align-items: flex-start; gap: 7px; color: var(--on-surface-variant); font-size: 12px; }.manual-checks input { accent-color: var(--primary); }
.archive-panel { border-left: 3px solid var(--primary); }.archive-list { display: grid; gap: 0; margin-top: 14px; border-top: 1px solid var(--border); }.archive-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--border); }.archive-row strong, .archive-row span { display: block; }.archive-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }
.materials-panel { border-left: 3px solid var(--tertiary); }.qualification-strip { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin: 15px 0; padding: 11px 12px; background: var(--surface-container-low); color: var(--on-surface-variant); font-size: 12px; }.qualification-strip span, .qualification-strip strong { display: block; }.qualification-strip strong { margin-top: 3px; }.qualification-ok { color: #087f5b; }.qualification-warn { color: var(--tertiary); }.material-catalog { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }.material-catalog-row { display: flex; justify-content: space-between; gap: 8px; align-items: center; padding: 9px; border: 1px solid var(--border); background: #fff; }.material-catalog-row strong, .material-catalog-row small { display: block; }.material-catalog-row strong { font-size: 12px; }.material-catalog-row small { color: var(--on-surface-variant); font-size: 10px; margin-top: 3px; }.state-tag.missing { background: #f1f5f9; color: var(--on-surface-variant); }.material-compose { border-top: 1px solid var(--border); margin-top: 16px; padding-top: 15px; }.material-content-field, .file-picker { display: grid; gap: 5px; color: var(--on-surface-variant); font-size: 12px; margin-top: 12px; }.material-content-field textarea { width: 100%; resize: vertical; }.file-picker input { font: inherit; }.file-picker small { color: var(--on-surface-variant); }.material-compose > button { margin-top: 13px; }.record-history { border-top: 1px solid var(--border); margin-top: 18px; padding-top: 14px; }.record-history h4 { margin: 0 0 8px; font-size: 13px; }.record-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--border); }.record-row strong, .record-row span { display: block; }.record-row span { color: var(--on-surface-variant); font-size: 11px; margin-top: 3px; }.record-attachments { display: flex; flex-wrap: wrap; gap: 5px; justify-content: flex-end; }
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
.workflow-tabs { display: flex; gap: 4px; align-items: stretch; overflow-x: auto; margin: 0 0 22px; padding: 4px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface-container-low); }
.workflow-tab { display: inline-flex; align-items: center; justify-content: center; gap: 7px; min-height: 40px; padding: 0 16px; border: 0; border-radius: var(--radius-sm); background: transparent; color: var(--on-surface-variant); font: inherit; font-size: 12px; white-space: nowrap; cursor: pointer; }
.workflow-tab:hover { color: var(--on-surface); background: var(--surface-container); }
.workflow-tab.active { background: var(--surface-container-lowest); color: var(--primary); font-weight: 700; box-shadow: var(--shadow-card); }
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
</style>
