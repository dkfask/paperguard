<template>
  <div v-if="loading" class="card loading-state">{{ t('common.loading') }}…</div>
  <div v-else-if="loadError" class="card">
    <div class="error-banner" role="alert">⚠ {{ loadError }}</div>
    <button @click="loadProject">{{ t('common.reload') }}</button>
    <button @click="$router.push('/')">{{ t('sidebar.theses') }}</button>
  </div>
  <div v-else-if="project && template" class="writer-shell">
    <header class="writer-command-bar">
      <div class="writer-command-leading">
        <button class="icon-action" :title="t('writer.backToTheses')" :aria-label="t('writer.backToTheses')" @click="router.push('/')">
          <span class="material-symbols-outlined" aria-hidden="true">arrow_back</span>
        </button>
        <div class="writer-command-title">
          <span>{{ t('route.writer') }}</span>
          <strong :title="project.title || t('home.untitledThesis')">{{ project.title || t('home.untitledThesis') }}</strong>
        </div>
      </div>
      <div class="writer-command-metrics">
        <span class="writer-save-status" :class="{ pending: saving || dirtyCount }">
          <span class="save-dot"></span>
          <span v-if="saving">{{ t('common.saving') }}</span>
          <span v-else-if="dirtyCount">{{ t('writer.unsaved', { count: dirtyCount }) }}</span>
          <span v-else>{{ t('writer.savedAt', { time: savedAt || t('common.justNow') }) }}</span>
        </span>
        <span><b>{{ totalChars }}</b> {{ t('writer.characters') }}</span>
        <span><b>{{ doneCount }}/{{ requiredCount }}</b> {{ t('writer.sectionsDone') }}</span>
      </div>
      <div class="writer-command-actions">
        <button class="small" @click="openAiSettings" :title="t('writer.aiSettings')" :aria-label="t('writer.aiAssistant')">
          <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
          <span>{{ t('writer.aiAssistant') }}</span>
        </button>
        <button v-if="isManager()" class="small" @click="openTemplateModal"
                :disabled="saving" :title="t('writer.switchTemplateTitle')">
          <span class="material-symbols-outlined" aria-hidden="true">swap_horiz</span>
          <span>{{ t('writer.switchTemplate') }}</span>
        </button>
        <button class="writer-review-action" :title="t('writer.check')" :aria-label="t('writer.check')" @click="checkOpen = !checkOpen">
          <span class="material-symbols-outlined" aria-hidden="true">rule</span>
          <span>{{ t('writer.check') }}</span>
        </button>
        <button class="writer-fix-action" :disabled="fixing || checking || exporting || loading"
                :title="t('writer.fixTitle')" :aria-label="t('writer.fix')" @click="runOneClickFix">
          <span class="material-symbols-outlined" aria-hidden="true">build</span>
          <span>{{ fixing ? t('writer.fixing') : t('writer.fix') }}</span>
        </button>
        <button class="writer-import-action" @click="chooseDocx" :disabled="importing || saving"
                :title="t('writer.importTitle')" :aria-label="t('writer.importBody')">
          <span class="material-symbols-outlined" aria-hidden="true">upload_file</span>
          <span>{{ importing ? t('writer.importing') : t('writer.importBody') }}</span>
        </button>
        <button class="writer-preview-action" @click="previewDocx" :disabled="exporting || saving || dirtyCount > 0"
                :title="t('writer.previewTitle')" :aria-label="t('writer.preview')">
          <span class="material-symbols-outlined" aria-hidden="true">visibility</span>
          <span>{{ t('writer.preview') }}</span>
        </button>
        <button class="primary writer-export-action" @click="exportDocx(false)" :disabled="exporting || saving || dirtyCount > 0"
                :title="t('writer.export')" :aria-label="t('writer.export')">
          <span class="material-symbols-outlined" aria-hidden="true">download</span>
          <span>{{ exporting ? t('writer.exporting') : t('writer.export') }}</span>
        </button>
      </div>
    </header>

    <div class="writer-columns">
    <!-- 左侧章节导航 -->
    <aside class="sidebar">
      <div class="tree-nav-header" style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
        <span style="font-size: 12px; font-weight: 600; color: var(--muted)">{{ t('writer.outline') }}</span>
        <button v-if="canEditStructure" class="tiny" @click="openAddSectionModal" :title="t('writer.addSection')">
          ➕ {{ t('writer.addSection') }}
        </button>
        <span v-else class="tiny" style="font-size: 11px; color: var(--muted); opacity: 0.8;" :title="t('writer.lockedTitle')">
          🔒 {{ t('writer.locked') }}
        </span>
      </div>
      <nav class="tree-nav">
        <div v-for="node in displaySections" :key="node.key"
             class="nav-row" :class="{ active: current === node.key }"
             :style="{ paddingLeft: (8 + node.depth * 14) + 'px' }" @click="select(node)">
          <span v-if="isContentType(node.type) && node.key !== 'cover'">✍️</span>
          <span v-else-if="node.type === 'cover'">📋</span>
          <span class="nav-title">{{ label(node) }}</span>
          <span v-if="isRequired(node) && !isDone(node)" class="dot" :title="t('writer.incomplete')">●</span>
          <span v-if="canEditStructure" class="nav-action-move">
            <button class="nav-move-btn" :disabled="structureBusy || isFirstSection(node)" @click.stop="moveSection(node, -1)" :title="t('writer.moveUp')" :aria-label="`${t('writer.moveUp')}「${label(node)}」`">↑</button>
            <button class="nav-move-btn" :disabled="structureBusy || isLastSection(node)" @click.stop="moveSection(node, 1)" :title="t('writer.moveDown')" :aria-label="`${t('writer.moveDown')}「${label(node)}」`">↓</button>
          </span>
          <span v-if="canEditStructure && isCustomSection(node)" class="nav-action-del" @click.stop="removeSection(node)" :title="t('writer.deleteSection')">×</span>
        </div>
      </nav>

      <!-- 字数与进度 -->
      <div class="stats-panel">
        <div class="progress-row">
          <span class="stats-label">{{ t('writer.completion') }}</span>
          <span class="stats-value">{{ doneCount }}/{{ requiredCount }}</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
        </div>
        <div class="stats-row">
          <span>{{ t('writer.totalCharacters') }} <b>{{ totalChars }}</b></span>
          <span>{{ t('writer.currentSectionCharacters') }} <b>{{ sectionChars }}</b></span>
        </div>
      </div>
    </aside>

    <!-- 右侧编辑区 -->
    <section class="editor-pane">
      <div v-if="error" class="error-banner" role="alert">⚠ {{ error }}<button v-if="exportJob && !exporting" class="small" @click="retryExportJob">{{ t('writer.exportRetry') }}</button></div>
      <div v-if="templateNotice" class="notice-banner">✅ {{ templateNotice }}</div>
      <div v-if="fixNotice" class="notice-banner" role="status">{{ fixNotice }}</div>
      <div v-if="recoveryUnavailable" class="hint recovery-status" role="status">{{ t('writer.recoveryUnavailable') }}</div>
      <div v-if="recoveryDraft" class="recovery-banner" role="status">
        <div>
          <strong>{{ t('writer.recoveryFound') }}</strong>
          <span>{{ t('writer.recoverySavedAt', { time: recoveryDraft.saved_at, revision: recoveryDraft.base_revision }) }}</span>
        </div>
        <div class="form-row">
          <button class="primary" @click="restoreLocalDraft">{{ t('writer.restoreDraft') }}</button>
          <button @click="discardLocalDraft">{{ t('writer.discardDraft') }}</button>
        </div>
      </div>
      <div v-if="failedSave" class="recovery-banner save-failure" role="alert">
        <span>{{ t('writer.saveFailure') }}</span>
        <button @click="retryFailedSaves">{{ t('writer.retrySave') }}</button>
      </div>
      <div v-if="conflictSave" class="recovery-banner conflict-banner" role="alert">
        <span>{{ t('writer.saveConflict') }}</span>
        <button @click="reloadAfterConflict">{{ t('writer.reloadServer') }}</button>
      </div>
      <input ref="docxInput" type="file" accept=".docx" hidden @change="onDocxSelected" />

      <!-- Export gate details -->
      <div v-if="missing.length" class="missing-panel">
        <b>{{ t('writer.exportMissing') }}</b>
        <ul>
          <li v-for="(m, i) in missing" :key="i">{{ m }}</li>
        </ul>
        <div class="form-row" style="margin-top: 8px">
          <button @click="exportDocx(true)" :disabled="exporting">{{ t('writer.exportAnyway') }}</button>
          <button @click="missing = []">{{ t('writer.acknowledged') }}</button>
        </div>
      </div>

      <Teleport to="body">
      <div v-if="waiverTarget" class="modal-mask writer-modal-mask" @click.self="waiverTarget = null">
        <div class="modal card">
          <h3>{{ t('writer.waiverTitle') }}</h3>
          <p class="hint">{{ waiverTarget.message }}</p>
          <label class="field">{{ t('writer.waiverReason') }}<textarea v-model="waiverReason" rows="4" :placeholder="t('writer.waiverPlaceholder')"></textarea></label>
          <div class="form-row"><button class="primary" :disabled="waiverBusy || !waiverReason.trim()" @click="submitWaiver">{{ waiverBusy ? t('common.processing') : t('writer.submitApplication') }}</button><button @click="waiverTarget = null">{{ t('common.cancel') }}</button></div>
        </div>
      </div>

      <div v-if="disputeTarget" class="modal-mask writer-modal-mask" @click.self="disputeTarget = null">
        <div class="modal card">
          <h3>{{ t('writer.disputeTitle') }}</h3>
          <p class="hint">{{ disputeTarget.message }}</p>
          <label class="field">{{ t('writer.disputeReason') }}<textarea v-model="disputeReason" rows="4" :placeholder="t('writer.disputePlaceholder')"></textarea></label>
          <div class="form-row"><button class="primary" :disabled="disputeBusy || !disputeReason.trim()" @click="submitDispute">{{ disputeBusy ? t('common.processing') : t('writer.submitDispute') }}</button><button @click="disputeTarget = null">{{ t('common.cancel') }}</button></div>
        </div>
      </div>

      <div v-if="upgradePreview" class="modal-mask writer-modal-mask" @click.self="upgradePreview = null">
        <div class="modal card upgrade-modal">
          <h3>{{ t('writer.upgradeTitle') }}</h3>
          <p class="hint">{{ t('writer.versionUpgradeDetail', { from: upgradePreview.from_snapshot_hash?.slice(0, 18) + '…', to: upgradePreview.to_snapshot_hash?.slice(0, 18) + '…' }) }}</p>
          <p v-if="!upgradePreview.diff.changed" class="ok-banner">{{ t('writer.latestVersion') }}</p>
          <div v-else class="upgrade-summary"><p>{{ t('writer.addedRuleSets', { count: upgradePreview.diff.added_sets.length, removed: upgradePreview.diff.removed_sets.length, changed: upgradePreview.diff.changed_sets.length }) }}</p><p>{{ t('writer.addedRequirements', { added: upgradePreview.diff.added_items.length, removed: upgradePreview.diff.removed_items.length }) }}</p></div>
          <div class="form-row"><button v-if="upgradePreview.diff.changed" class="primary" :disabled="upgradeBusy" @click="applyUpgrade">{{ t('writer.applyToProject') }}</button><button @click="upgradePreview = null">{{ t('common.close') }}</button></div>
        </div>
      </div>

      <div v-if="upgradeConfirm" class="modal-mask writer-modal-mask" @click.self="upgradeConfirm = false">
        <div class="modal card">
          <h3>{{ t('writer.confirmUpgradeTitle') }}</h3>
          <p class="hint">{{ t('writer.upgradeDetail') }}</p>
          <div class="form-row"><button class="primary" :disabled="upgradeBusy" @click="confirmUpgrade">{{ t('writer.confirmUpgrade') }}</button><button @click="upgradeConfirm = false">{{ t('common.cancel') }}</button></div>
        </div>
      </div>

      <ReasonDialog
        :open="revokeWaiverTarget !== null"
        :title="t('writer.revokeWaiverTitle')"
        :description="t('writer.revokeWaiverDescription')"
        :placeholder="t('writer.revokeWaiverPlaceholder')"
        :confirm-label="t('writer.revokeWaiver')"
        :busy="revokeWaiverBusy"
        @confirm="confirmRevokeWaiver"
        @cancel="revokeWaiverTarget = null"
      />

      <div v-if="assistResult" class="modal-mask writer-modal-mask" @click.self="assistResult = null">
        <div class="modal card">
          <h3>{{ t('writer.aiReviewTitle') }}</h3>
          <p class="hint">{{ t('writer.aiReviewDetail') }}</p>
          <p v-if="assistResult.saved || assistResult.saved_at" class="assist-saved-location">{{ t('writer.savedTo', { location: t('writer.formattingReview') + ' · ' + assistResult.requirement_item_id }) }}</p>
          <div class="assist-result">{{ assistResult.suggestion }}</div>
          <div class="form-row"><button @click="assistResult = null">{{ t('common.close') }}</button></div>
        </div>
      </div>

      <div v-if="importPreview" class="modal-mask writer-modal-mask" @click.self="importPreview = null">
        <div class="modal card import-modal">
          <h3>{{ t('writer.wordImportTitle') }}</h3>
          <p class="hint">{{ importPreview.filename }} · {{ t('writer.importStats', { paragraphs: importPreview.stats.paragraphs, tables: importPreview.stats.tables, images: importPreview.stats.images, equations: importPreview.stats.equations || 0 }) }}</p>
          <div v-if="importPreview.warnings?.length" class="notice-banner">{{ importPreview.warnings.join('；') }}</div>
          <div class="import-section-list">
            <div v-for="section in importPreview.sections" :key="section.key" class="import-section-item">
              <strong>{{ section.title }}</strong><span>{{ t('writer.contentBlocks', { count: section.paragraphs.length }) }}</span>
            </div>
          </div>
          <div class="form-row"><button class="primary" :disabled="importing" @click="applyDocxImport">{{ t('writer.applyToDraft') }}</button><button @click="importPreview = null">{{ t('common.cancel') }}</button></div>
        </div>
      </div>
      <div v-if="exportPreview" class="modal-mask writer-modal-mask" @click.self="exportPreview = null">
        <div class="modal card export-preview-modal">
          <h3>{{ t('writer.exportPreviewTitle') }}</h3>
          <p class="hint">{{ exportPreview.title || t('home.untitledThesis') }} · revision {{ exportPreview.revision }} · {{ exportPreview.content_hash.slice(0, 18) }}…</p>
          <div class="export-preview-stats">{{ t('writer.exportStats', { sections: exportPreview.counts.sections, paragraphs: exportPreview.counts.paragraphs, tables: exportPreview.counts.tables, images: exportPreview.counts.images, equations: exportPreview.counts.equations }) }}</div>
          <div v-if="exportPreview.missing?.length" class="notice-banner">{{ t('writer.exportGate', { count: exportPreview.missing.length }) }}</div>
          <div class="import-section-list"><div v-for="section in exportPreview.sections_preview" :key="section.key" class="import-section-item"><strong>{{ section.title }}</strong><span>{{ t('writer.blockStats', { blocks: section.blocks, characters: section.characters }) }}</span></div></div>
          <div class="form-row"><button class="primary" :disabled="!exportPreview.can_export" @click="exportDocx(false); exportPreview = null">{{ t('writer.downloadDocx') }}</button><button @click="exportPreview = null">{{ t('common.close') }}</button></div>
        </div>
      </div>
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
      </Teleport>

      <!-- Cover -->
      <div v-if="currentSection && currentSection.type === 'cover'">
        <h2>📋 {{ t('writer.coverInfo') }}</h2>
        <p style="color: var(--muted); font-size: 14px; margin-bottom: 16px">{{ t('writer.coverInfoDetail') }}</p>
        <div v-for="f in metaFields" :key="f.key" class="meta-row">
          <label>{{ f.label }}</label>
          <input :value="project.meta[f.key]" @input="onMetaInput(f.key, $event.target.value)"
                 :placeholder="f.required ? t('writer.required') : t('writer.optional')" />
        </div>
        <button class="primary" @click="saveMeta" :disabled="saving">💾 {{ saving ? t('common.saving') : t('writer.saveCover') }}</button>
      </div>

      <!-- Heading section -->
      <div v-else-if="currentSection && isHeading(currentSection.type)">
        <div class="editor-head">
          <span class="type-tag">{{ typeLabel(currentSection.type) }}</span>
          <span class="hint">{{ t('writer.headingHint') }}</span>
          <button class="small" @click="toggleSectionInputMode">
            {{ sectionInputMode === 'blocks' ? t('writer.sectionModeBlocks') : t('writer.sectionModeParagraphs') }}
          </button>
        </div>
        <input v-model="currentSection.title" class="heading-input"
               :class="'type-' + currentSection.type" :style="editorStyle(currentSection.type, true)" @input="scheduleSave" />
        <section-text-editor v-if="sectionInputMode === 'section'" v-model="sectionText"
                             :reset-key="currentSection.key" :style-vars="editorStyle(currentSection.type)" @apply="applySectionText" />
        <block-list v-else :paragraphs="currentSection.paragraphs" :project-id="projectId"
                    :style-class="'style-' + currentSection.type" :section-title="currentSection.title"
                    :style-vars="editorStyle(currentSection.type)"
                    :chapter="chapterOf(currentSection.key)"
                    :ai-enabled="aiEnabled" :ai-busy="aiBusy" @ai="onBlockAi" @change="scheduleSave" />
      </div>

      <!-- Body section -->
      <div v-else-if="currentSection">
        <div class="editor-head">
          <span class="type-tag">{{ typeLabel(currentSection.type) }}</span>
          <span class="hint">{{ t('writer.bodyHint') }}</span>
          <button class="small" @click="toggleSectionInputMode">
            {{ sectionInputMode === 'blocks' ? t('writer.sectionModeBlocks') : t('writer.sectionModeParagraphs') }}
          </button>
          <button v-if="aiEnabled && isAiSection(currentSection.type)" class="small ai-gen"
                  :disabled="!!aiBusy || aiGenerating" @click="aiGenerate">
            {{ aiGenerating ? t('writer.aiGenerating') : t('writer.aiGenerate') }}
          </button>
        </div>
        <input v-model="currentSection.title" class="heading-input" :class="'type-' + currentSection.type"
               :style="editorStyle(currentSection.type, true)" @input="scheduleSave" />
        <section-text-editor v-if="sectionInputMode === 'section'" v-model="sectionText"
                             :reset-key="currentSection.key" :style-vars="editorStyle(currentSection.type)" @apply="applySectionText" />
        <block-list v-else :paragraphs="currentSection.paragraphs" :project-id="projectId"
                    :style-class="'style-' + currentSection.type" :section-title="currentSection.title"
                    :style-vars="editorStyle(currentSection.type)"
                    :chapter="chapterOf(currentSection.key)"
                    :ai-enabled="aiEnabled" :ai-busy="aiBusy" @ai="onBlockAi" @change="scheduleSave" />
      </div>

    </section>

    <!-- Writing context and live checks -->
    <aside class="writer-context">
      <div class="context-header">
        <div>
          <p class="writing-eyebrow">{{ t('writer.writingContextKicker') }}</p>
          <h2>{{ t('writer.writingAssistant') }}</h2>
        </div>
        <span class="context-header-icon material-symbols-outlined">tune</span>
      </div>

      <div v-if="requirementSnapshot" class="requirements-panel context-panel">
        <div class="requirements-head">
          <div>
            <span class="eyebrow">{{ t('writer.projectBasisKicker') }}</span>
            <h3>{{ t('writer.projectBasis') }}</h3>
          </div>
          <div class="basis-actions">
            <span class="snapshot-tag">v{{ template.version || requirementSnapshot.template?.version || '1' }}</span>
            <button v-if="!hasAcknowledged" class="tiny" @click="acknowledgeBasis">{{ t('writer.acknowledgeBasis') }}</button>
            <button v-if="isManager()" class="tiny" :disabled="upgradeBusy" @click="openUpgradePreview">{{ t('writer.updateBasis') }}</button>
          </div>
        </div>
        <div class="requirements-sources">
          <div class="source-chip template-chip">
            <span class="material-symbols-outlined" aria-hidden="true">description</span>
            <span>{{ t('writer.templateSource', { name: template.name }) }}</span>
          </div>
          <div v-for="source in requirementSets" :key="source.id" class="source-chip">
            <span class="material-symbols-outlined" aria-hidden="true">rule</span>
            <span>{{ source.name }} · v{{ source.version }}</span>
          </div>
          <div v-for="packId in (requirementSnapshot.rule_packs || [])" :key="packId" class="source-chip">
            <span class="material-symbols-outlined" aria-hidden="true">format_align_left</span>
            <span>{{ t('writer.formatRules', { id: packId }) }}</span>
          </div>
        </div>
        <p v-if="requirementSnapshot.format_override_count" class="format-resolution">
          <span class="material-symbols-outlined" aria-hidden="true">verified</span>
          {{ t('writer.rulesApplied', { count: requirementSnapshot.format_override_count }) }}
        </p>
        <p class="requirements-foot">{{ t('writer.autoChecked', { count: autoRequirementCount }) }}<span v-if="checkResult">{{ t('writer.notCheckedInline', { count: checkResult.summary.not_checked || 0 }) }}</span></p>
      </div>

      <div v-if="canReviewRequirements && (pendingWaivers.length || approvedWaivers.length)" class="waiver-review-panel context-panel">
        <div class="requirements-head"><div><span class="eyebrow">{{ t('writer.reviewQueueKicker') }}</span><h3>{{ t('writer.reviewQueue') }}</h3></div><span class="snapshot-tag">{{ t('writer.countItems', { count: pendingWaivers.length + approvedWaivers.length }) }}</span></div>
        <div v-for="waiver in pendingWaivers" :key="waiver.id" class="waiver-review-row"><span>{{ t('writer.waiver', { id: waiver.requirement_item_id, reason: waiver.reason }) }}</span><span><button class="tiny" @click="decideWaiver(waiver, 'approved')">{{ t('writer.agree') }}</button><button class="tiny danger" @click="decideWaiver(waiver, 'rejected')">{{ t('writer.reject') }}</button></span></div>
        <div v-for="waiver in approvedWaivers" :key="`approved-${waiver.id}`" class="waiver-review-row approved-waiver-row"><span>{{ t('writer.approvedWaiver', { id: waiver.requirement_item_id, reason: waiver.reason }) }}</span><button class="tiny danger" @click="revokeWaiver(waiver)">{{ t('writer.revoke') }}</button></div>
      </div>
      <div v-if="canReviewRequirements && pendingDisputes.length" class="waiver-review-panel dispute-review-panel context-panel">
        <div class="requirements-head"><div><span class="eyebrow">{{ t('writer.disputeQueueKicker') }}</span><h3>{{ t('writer.disputeQueue') }}</h3></div><span class="snapshot-tag">{{ t('writer.countItems', { count: pendingDisputes.length }) }}</span></div>
        <div v-for="dispute in pendingDisputes" :key="dispute.id" class="waiver-review-row"><span>{{ t('writer.dispute', { id: dispute.requirement_item_id, reason: dispute.reason }) }}</span><span><button class="tiny" @click="decideDispute(dispute, 'accepted')">{{ t('writer.accept') }}</button><button class="tiny danger" @click="decideDispute(dispute, 'rejected')">{{ t('writer.reject') }}</button></span></div>
      </div>

      <div v-if="checking || checkResult || checkError" class="check-panel context-panel" :class="{ clean: checkResult && checkResult.summary.total === 0 && checkResult.summary.not_checked === 0 && checkResult.summary.waived === 0 && !(requirementSnapshot?.conflicts?.length) }">
        <div class="check-panel-head">
          <div class="check-panel-title">
            <span class="check-panel-icon material-symbols-outlined">fact_check</span>
            <div>
              <span class="check-panel-kicker">{{ t('writer.formattingReviewKicker') }}</span>
              <strong>{{ t('writer.formattingReview') }}</strong>
            </div>
          </div>
          <button class="check-refresh-btn" :disabled="checking" @click="runCheckNow">
            <span class="material-symbols-outlined" aria-hidden="true">refresh</span>
            <span>{{ t('writer.rerunCheck') }}</span>
          </button>
        </div>

        <button class="check-summary" type="button" :aria-expanded="checkOpen" @click="checkOpen = !checkOpen">
          <span class="check-summary-leading">
            <span class="check-summary-icon material-symbols-outlined">{{ checking ? 'progress_activity' : (checkError || checkStale ? 'warning' : 'warning') }}</span>
            <span v-if="checking" class="check-summary-text">{{ t('writer.checking') }}</span>
            <span v-else-if="checkError" class="check-summary-text">{{ t('writer.checkFailed', { message: checkError }) }}</span>
            <span v-else-if="checkStale" class="check-summary-text">{{ t('writer.checkStale') }}</span>
            <span v-else-if="checkResult.context && checkResult.summary.total === 0 && checkResult.summary.not_checked === 0 && checkResult.summary.waived === 0 && !(requirementSnapshot?.conflicts?.length)" class="check-summary-text">{{ t('writer.checkPassed') }}</span>
            <span v-else class="check-summary-text">{{ t('writer.issueSummary', { issues: checkResult.summary.total, notChecked: checkResult.summary.not_checked || 0 }) }}</span>
          </span>
          <span class="material-symbols-outlined context-chevron" aria-hidden="true">{{ checkOpen ? 'expand_less' : 'expand_more' }}</span>
        </button>

        <div v-if="checkOpen && checkResult && checkResult.findings.length" class="check-list">
          <div v-for="(f, i) in checkResult.findings" :key="f.rule_id + ':' + (f.section_key || '') + ':' + i" class="check-item"
               :class="'severity-' + f.severity" @click="jumpTo(f)">
            <div class="check-item-body">
              <div class="check-item-message">
                <span class="severity-marker" :class="f.severity">
                  <span class="material-symbols-outlined" aria-hidden="true">{{ f.severity === 'error' ? 'error' : f.severity === 'info' ? 'info' : 'warning' }}</span>
                  <span>{{ { error: t('writer.errorShort'), warning: t('writer.warningShort'), info: t('writer.infoShort') }[f.severity] }}</span>
                </span>
                <span class="check-msg">{{ f.message }}</span>
              </div>
              <div class="check-item-actions">
                <span class="check-sec" v-if="f.section_key || f.location?.section_key">{{ sectionTitle(f.section_key || f.location.section_key) }} →</span>
                <template v-if="f.source_type === 'requirement'">
                  <button class="check-action-btn" @click.stop="openWaiver(f)">
                    <span class="material-symbols-outlined" aria-hidden="true">gavel</span>{{ t('writer.requestWaiver') }}
                  </button>
                  <button class="check-action-btn" @click.stop="openDispute(f)">
                    <span class="material-symbols-outlined" aria-hidden="true">feedback</span>{{ t('writer.submitDispute') }}
                  </button>
                </template>
              </div>
            </div>
          </div>
        </div>

        <div v-if="checkOpen && checkResult && checkResult.waived?.length" class="waived-list">
          <strong>{{ t('writer.waived') }}</strong>
          <span v-for="item in checkResult.waived" :key="item.rule_id">{{ item.message }} · {{ item.waiver?.reason }}</span>
        </div>

        <div v-if="checkOpen && checkResult && checkResult.not_checked?.length" class="not-checked-list">
          <div class="not-checked-heading">
            <span><span class="material-symbols-outlined" aria-hidden="true">fact_check</span>{{ t('writer.notChecked') }}</span>
            <span class="not-checked-count">{{ t('writer.countItems', { count: checkResult.not_checked.length }) }}</span>
          </div>
          <div v-for="item in checkResult.not_checked" :key="item.id" class="not-checked-item">
            <div class="not-checked-copy">
              <span class="manual-dot"></span>
              <span>{{ item.description }}<span class="not-checked-source"> · {{ item.source }}</span></span>
            </div>
            <template v-if="item.type === 'manual'">
              <div class="not-checked-actions">
                <span v-if="assistResultFor(item.id)" class="assist-saved-tag"><span class="material-symbols-outlined" aria-hidden="true">check_circle</span>{{ t('writer.saved') }}</span>
                <button v-if="assistResultFor(item.id)" class="check-action-btn secondary" @click.stop="showAssistResult(assistResultFor(item.id))">
                  <span class="material-symbols-outlined" aria-hidden="true">visibility</span>{{ t('writer.view') }}
                </button>
                <button class="check-action-btn assist-action" :disabled="!!assistBusy" @click.stop="openAssist(item)">
                  <span class="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
                  {{ assistBusy === item.id ? t('writer.aiProcessing') : (assistResultFor(item.id) ? t('writer.aiAssistAgain') : t('writer.aiAssist')) }}
                </button>
              </div>
            </template>
          </div>
        </div>
      </div>
    </aside>

      <Teleport to="body">
      <!-- AI result comparison -->
      <div v-if="aiDiff" class="modal-mask writer-modal-mask" @click.self="cancelAiDiff">
        <div class="modal card diff-modal">
          <h3 style="margin-top:0; display:flex; align-items:center; gap:8px">
            <span>{{ aiDiff.action === 'continue' ? t('writer.aiContinueCompare') : t('writer.aiPolishCompare') }}</span>
            <span class="type-tag" style="font-size:12px; font-weight:normal">
              {{ t('writer.blockNumber', { number: aiDiff.index + 1 }) }}
            </span>
          </h3>
          <p class="hint" style="margin-top:0">
            {{ t('writer.aiReviewPrompt') }}
          </p>

          <div class="diff-grid">
            <div class="diff-pane">
              <div class="diff-label">{{ t('writer.originalParagraph') }}</div>
              <div class="diff-box original">{{ aiDiff.original }}</div>
            </div>
            <div class="diff-pane">
              <div class="diff-label">
                {{ aiDiff.action === 'continue' ? t('writer.newAiContent') : t('writer.polishedAiContent') }}
              </div>
              <div class="diff-box generated">{{ aiDiff.generated }}</div>
            </div>
          </div>

          <div v-if="aiDiff.action === 'continue'" class="diff-preview-pane">
            <div class="diff-label">{{ t('writer.mergePreview') }}</div>
            <div class="diff-box preview">{{ aiDiff.preview }}</div>
          </div>

          <div class="form-row" style="margin-top:16px; justify-content:flex-end">
            <button class="primary" @click="applyAiDiff">
              {{ aiDiff.action === 'continue' ? t('writer.acceptAppend') : t('writer.acceptReplace') }}
            </button>
            <button @click="cancelAiDiff">{{ t('writer.discardKeepOriginal') }}</button>
          </div>
        </div>
      </div>

      <!-- AI settings -->
      <div v-if="showAiSettings" class="modal-mask writer-modal-mask" @click.self="showAiSettings = false">
        <div class="modal card">
          <h3 style="margin-top:0">{{ t('writer.aiSettingsTitle') }}</h3>
          <div v-if="aiDisclosure" class="ai-disclosure" role="note"><p>{{ aiDisclosure.notice }}</p><p>接收方：{{ aiDisclosure.data_recipient_host }}</p><p>{{ aiDisclosure.content_sent }}</p></div>
          <div v-if="isAdmin" class="ai-tab-row">
            <button class="ai-tab-btn" :class="{ active: aiTab === 'user' }" @click="switchAiTab('user')">
              {{ t('writer.personalModel') }}
            </button>
            <button class="ai-tab-btn" :class="{ active: aiTab === 'admin' }" @click="switchAiTab('admin')">
              {{ t('writer.adminModel') }}
            </button>
          </div>
          <p class="hint" style="font-size: 13px; margin-top: 4px">
            <template v-if="aiTab === 'user'">
              {{ t('writer.personalModelDetail') }}
              <br />
              <template v-if="aiEnabled">
                {{ t('writer.currentSource', { source: aiSource === 'user' ? t('writer.ownModel') : t('writer.systemModel'), model: aiModel }) }}
              </template>
              <template v-else>{{ t('writer.aiDisabled') }}</template>
            </template>
            <template v-else>
              {{ t('writer.adminModelDetail') }}
              <br />
              <template v-if="adminAiInfo.configured">
                {{ t('writer.defaultSource', { source: adminAiInfo.source === 'database' ? t('writer.databaseCustom') : t('writer.environmentModel') }) }}
              </template>
              <template v-else>{{ t('writer.noDefaultModel') }}</template>
            </template>
          </p>

          <!-- 个人模型表单 -->
          <template v-if="aiTab === 'user'">
            <label class="field">{{ t('writer.apiBaseUrl') }}
              <input v-model="aiCfgForm.base_url" :placeholder="t('writer.baseUrlPlaceholder')" />
            </label>
            <label class="field">{{ t('writer.apiKey') }}
              <input v-model="aiCfgForm.api_key" type="password" autocomplete="new-password"
                     :placeholder="aiCfgInfo.configured ? t('writer.savedKeyPlaceholder', { key: aiCfgInfo.api_key_masked }) : t('writer.requiredKeyPlaceholder')" />
            </label>
            <label class="field">{{ t('writer.model') }}
              <input v-model="aiCfgForm.model" :placeholder="t('writer.modelPlaceholder')" />
            </label>
            <p v-if="aiCfgInfo.message" :class="aiCfgInfo.ok ? 'ok-banner' : 'error-banner'" style="font-size: 13px">
              {{ aiCfgInfo.ok ? '✅' : '⚠' }} {{ aiCfgInfo.message }}
            </p>
            <div class="form-row">
              <button class="primary" :disabled="aiCfgInfo.saving" @click="saveAiCfg">
                {{ aiCfgInfo.saving ? t('common.saving') : t('writer.saveOwn') }}
              </button>
              <button :disabled="aiCfgInfo.testing" @click="testAiConnection">
                {{ aiCfgInfo.testing ? t('writer.aiProcessing') : t('writer.testConnection') }}
              </button>
              <button v-if="aiCfgInfo.configured" @click="clearAiCfg">{{ t('writer.clearOwn') }}</button>
              <button @click="showAiSettings = false">{{ t('common.close') }}</button>
            </div>
          </template>

          <!-- 管理员全站默认模型表单 -->
          <template v-else>
            <label class="field">{{ t('writer.apiBaseUrl') }}
              <input v-model="adminAiForm.base_url" :placeholder="t('writer.baseUrlPlaceholder')" />
            </label>
            <label class="field">{{ t('writer.apiKey') }}
              <input v-model="adminAiForm.api_key" type="password" autocomplete="new-password"
                     :placeholder="adminAiInfo.configured ? t('writer.savedKeyPlaceholder', { key: adminAiInfo.api_key_masked }) : t('writer.requiredKeyPlaceholder')" />
            </label>
            <label class="field">{{ t('writer.model') }}
              <input v-model="adminAiForm.model" :placeholder="t('writer.modelPlaceholder')" />
            </label>
            <p v-if="adminAiInfo.message" :class="adminAiInfo.ok ? 'ok-banner' : 'error-banner'" style="font-size: 13px">
              {{ adminAiInfo.ok ? '✅' : '⚠' }} {{ adminAiInfo.message }}
            </p>
            <div class="form-row">
              <button class="primary" :disabled="adminAiInfo.saving" @click="saveAdminAiCfg">
                {{ adminAiInfo.saving ? t('common.saving') : t('writer.saveDefault') }}
              </button>
              <button :disabled="adminAiInfo.testing" @click="testAdminAiConnection">
                {{ adminAiInfo.testing ? t('writer.aiProcessing') : t('writer.testConnection') }}
              </button>
              <button v-if="adminAiInfo.source === 'database'" @click="clearAdminAiCfg">{{ t('writer.restoreEnvironment') }}</button>
              <button @click="showAiSettings = false">{{ t('common.close') }}</button>
            </div>
          </template>
        </div>
      </div>

      <!-- Template switcher -->
      <div v-if="showTemplateModal" class="modal-mask writer-modal-mask" @click.self="showTemplateModal = false">
        <div class="modal card">
          <h3 style="margin-top: 0">🔀 {{ t('writer.switchTemplate') }}</h3>
          <p class="hint" style="margin-top: 0">
            {{ t('writer.switchTemplateDetail') }}
          </p>
          <div v-if="templateError" class="error-banner">⚠ {{ templateError }}</div>
          <div v-if="templateLoading" style="color: var(--muted)">⏳ {{ t('writer.loadingTemplates') }}</div>
          <div v-else-if="!templateList.length" style="color: var(--muted)">{{ t('writer.noTemplates') }}</div>
          <div v-for="tpl in templateList" :key="tpl.id" class="template-row"
               :class="{ current: tpl.id === project.template_id }">
            <div class="template-meta">
              <b>{{ tpl.name }}</b>
              <span v-if="tpl.id === project.template_id" class="tag">{{ t('writer.current') }}</span>
              <div class="template-desc">
                {{ tpl.description || t('common.noDescription') }} · {{ t('common.sectionsCount', { count: tpl.section_count }) }}
              </div>
            </div>
            <button class="small primary" :disabled="!!templateBusy"
                    @click="applyTemplate(tpl.id)">
              {{ templateBusy === tpl.id ? t('writer.applyingTemplate') : (tpl.id === project.template_id ? t('writer.reapplyTemplate') : t('writer.applyTemplate')) }}
            </button>
          </div>
          <div class="form-row" style="margin-top: 14px">
            <button @click="showTemplateModal = false">{{ t('common.close') }}</button>
          </div>
        </div>
      </div>

      <!-- Add section -->
      <div v-if="showAddSectionModal" class="modal-mask writer-modal-mask" @click.self="showAddSectionModal = false">
        <div class="modal card">
          <h3 style="margin-top: 0">{{ t('writer.addSectionTitle') }}</h3>
          <p class="hint" style="margin-top: 0">{{ t('writer.addSectionDetail') }}</p>
          <label class="field">{{ t('writer.sectionTitle') }}
            <input v-model="newSectionForm.title" :placeholder="t('writer.sectionTitlePlaceholder')" />
          </label>
          <label class="field">{{ t('writer.sectionType') }}
            <select v-model="newSectionForm.type" style="width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 8px;">
              <option value="heading1">{{ t('writer.heading1') }}</option>
              <option value="heading2">{{ t('writer.heading2') }}</option>
              <option value="heading3">{{ t('writer.heading3') }}</option>
              <option value="body">{{ t('writer.bodyContainer') }}</option>
              <option value="appendix">{{ t('writer.appendix') }}</option>
            </select>
          </label>
          <label class="field">{{ t('writer.insertPosition') }}
            <select v-model="newSectionForm.position" class="structure-select">
              <option value="start">{{ t('writer.positionStart') }}</option>
              <option v-for="node in displaySections" :key="`before-${node.key}`" :value="`before:${node.key}`">
                {{ t('writer.beforeSection', { name: label(node) }) }}
              </option>
              <option v-for="node in displaySections" :key="`after-${node.key}`" :value="`after:${node.key}`">
                {{ t('writer.afterSection', { name: label(node) }) }}
              </option>
              <option value="end">{{ t('writer.positionEnd') }}</option>
            </select>
          </label>
          <div class="form-row" style="margin-top: 16px;">
            <button class="primary" :disabled="!newSectionForm.title.trim() || structureBusy" @click="confirmAddSection">
              {{ structureBusy ? t('writer.savingStructure') : t('writer.confirmAdd') }}
            </button>
            <button @click="showAddSectionModal = false">{{ t('common.cancel') }}</button>
          </div>
        </div>
      </div>
      </Teleport>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { auth, isManager } from '../auth'
import {
  fetchProject, fetchTemplate, fetchTemplates, updateProjectSection, updateProjectMeta, restoreProjectSnapshot, createProjectExportJob, fetchProjectExportJob, downloadProjectExportJob, retryProjectExportJob, previewProjectExport,
  checkProject, fixProject, updateProjectSectionsStructure, refreshProjectTemplate, switchProjectTemplate,
  importProjectDocx,
  acknowledgeRequirements, assistRequirement, listRequirementWaivers, listRequirementDisputes, previewRequirementUpgrade,
  requestRequirementWaiver, requestRequirementDispute, upgradeRequirements, decideRequirementWaiver, revokeRequirementWaiver, decideRequirementDispute,
  getAiDisclosure, aiStatus, getAiConfig, saveAiConfig, clearAiConfig, testAiConfig, aiAssist,
  getAdminAiConfig, saveAdminAiConfig, clearAdminAiConfig, testAdminAiConfig,
} from '../api'
import BlockList from '../components/BlockList.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import ReasonDialog from '../components/ReasonDialog.vue'
import SectionTextEditor from '../components/SectionTextEditor.vue'
import { useI18n } from '../i18n'
import { createTextBlocks, mergeTextBlocksWithMedia, textBlocksToSectionText } from '../utils/paragraphSplitter.js'

const route = useRoute()
const router = useRouter()
const { t, formatDate: formatLocaleDate } = useI18n()
const projectId = route.params.id

const project = ref(null)
const template = ref(null)
const current = ref('cover')
const error = ref('')
const loadError = ref('')
const loading = ref(true)
const saving = ref(false)
const savedAt = ref('')
const exporting = ref(false)
const exportJob = ref(null)
const exportPreview = ref(null)
const importing = ref(false)
const importPreview = ref(null)
const docxInput = ref(null)
const showTemplateModal = ref(false)
const templateList = ref([])
const templateLoading = ref(false)
const templateBusy = ref('')
const templateError = ref('')
const templateNotice = ref('')
const fixNotice = ref('')
const recoveryDraft = ref(null)
const recoveryUnavailable = ref(false)
const failedSave = ref(false)
const conflictSave = ref(null)
const checkResult = ref(null)
const checking = ref(false)
const fixing = ref(false)
const checkError = ref('')
const checkStale = ref(false)
const checkOpen = ref(true)
const missing = ref([])
const dirtyKeys = reactive(new Set())

const requirementSnapshot = computed(() => project.value?.requirement_snapshot || null)
const requirementSets = computed(() => requirementSnapshot.value?.policy_sets || [])
const autoRequirementCount = computed(() => (requirementSnapshot.value?.resolved_items || []).filter((item) => item.type === 'auto').length)
const hasAcknowledged = computed(() => (project.value?.requirement_acknowledgements || []).some((item) => item.user_id === auth.user?.id && item.snapshot_hash === requirementSnapshot.value?.content_hash))
const waiverTarget = ref(null); const waiverReason = ref(''); const waiverBusy = ref(false)
const revokeWaiverTarget = ref(null); const revokeWaiverBusy = ref(false)
const disputeTarget = ref(null); const disputeReason = ref(''); const disputeBusy = ref(false)
const upgradePreview = ref(null); const upgradeBusy = ref(false); const upgradeConfirm = ref(false); const assistResult = ref(null); const assistBusy = ref('')
const waivers = ref([])
const disputes = ref([])
const pendingWaivers = computed(() => waivers.value.filter((item) => item.status === 'pending'))
const approvedWaivers = computed(() => waivers.value.filter((item) => item.status === 'approved'))
const pendingDisputes = computed(() => disputes.value.filter((item) => item.status === 'pending'))
// 指导教师能打开学生论文，因此也能看到该论文的豁免与申诉队列；最终审核权限仍由
// 后端 _can_review_waiver 判定，越权时决定/撤销接口返回 403。
const canReviewRequirements = computed(() => isManager() || auth.user?.role === 'teacher')

const saveTimers = new Map()
const saveVersions = new Map()
const pendingSaves = new Map()
let pendingMeta = null
let metaTimer = null
let saveQueue = Promise.resolve()
const inFlightSaves = new Set()
let checkGeneration = 0
let beforeUnloadHandler = null

const chapterMap = ref({})
const sectionInputMode = ref('blocks')
const sectionText = ref('')
const CONTENT_TYPES = ['body', 'references', 'acknowledgement', 'appendix', 'abstract', 'english_abstract', 'keywords', 'english_keywords']
const HEADING_TYPES = ['heading1', 'heading2', 'heading3']

// 架构与权限：是否允许修改章节框架
const canEditStructure = computed(() => {
  if (isAdmin.value || auth.user?.role === 'teacher') return true
  if (project.value && project.value.allow_structure_edit !== undefined) {
    return !!project.value.allow_structure_edit
  }
  if (template.value && template.value.allow_structure_edit !== undefined) {
    return !!template.value.allow_structure_edit
  }
  return true
})

// 展示的章节列表（以项目实际 sections 为准，若未初始化则以模板为准）
const displaySections = computed(() => {
  const cover = { key: 'cover', type: 'cover', title: t('writer.coverSection'), required: true, depth: 0 }
  if (!project.value || !project.value.sections) return [cover, ...flatTemplateSections.value]
  const tplMap = new Map()
  for (const item of flatTemplateSections.value) {
    tplMap.set(item.key, item)
  }
  const sections = project.value.sections.map((sec) => {
    const tplItem = tplMap.get(sec.key)
    let depth = tplItem?.depth || 0
    if (sec.type === 'heading2') depth = 1
    if (sec.type === 'heading3') depth = 2
    return {
      key: sec.key,
      type: sec.type,
      title: sec.title,
      required: sec.required,
      depth,
    }
  })
  return sections.some((section) => section.key === 'cover') ? sections : [cover, ...sections]
})

const showAddSectionModal = ref(false)
const structureBusy = ref(false)
const newSectionForm = reactive({ title: '', type: 'heading2', position: 'end' })
const confirmBusy = ref(false)
const confirmDialog = reactive({ open: false, title: '', description: '', confirmLabel: '', danger: false, action: null })
const protectedSectionKeys = new Set(['cover', 'abstract', 'english_abstract', 'toc', 'references'])

function openAddSectionModal() {
  const sections = project.value?.sections || []
  const currentIndex = sections.findIndex((section) => section.key === current.value)
  const refIndex = sections.findIndex((section) => section.type === 'references' || section.key === 'references')
  const currentSectionItem = currentIndex >= 0 ? sections[currentIndex] : null
  if (currentIndex >= 0 && currentSectionItem.type !== 'references') {
    newSectionForm.position = `after:${sections[currentIndex].key}`
  } else if (refIndex >= 0) {
    newSectionForm.position = `before:${sections[refIndex].key}`
  } else {
    newSectionForm.position = 'end'
  }
  showAddSectionModal.value = true
}

function isCustomSection(node) {
  // 保护核心段落不被删除
  return !protectedSectionKeys.has(node.key)
}

function structureItem(section) {
  return {
    key: section.key,
    type: section.type,
    title: section.title,
    required: section.required,
  }
}

function sectionIndex(node) {
  return project.value?.sections?.findIndex((section) => section.key === node.key) ?? -1
}

function isFirstSection(node) { return sectionIndex(node) <= 0 }
function isLastSection(node) {
  const index = sectionIndex(node)
  return index < 0 || index >= (project.value?.sections?.length || 0) - 1
}

function insertIndexForPosition(sections, position) {
  if (position === 'start') return 0
  if (position === 'end') return sections.length
  const separator = position.indexOf(':')
  const direction = separator === -1 ? '' : position.slice(0, separator)
  const key = separator === -1 ? '' : position.slice(separator + 1)
  const index = sections.findIndex((section) => section.key === key)
  if (index === -1) return sections.length
  return direction === 'before' ? index : index + 1
}

async function confirmAddSection() {
  if (!newSectionForm.title.trim() || structureBusy.value) return
  structureBusy.value = true
  error.value = ''
  try {
    await flushPendingSaves()
    const currentList = project.value.sections.map(structureItem)
    const newKey = `sec_${Date.now()}-${Math.random().toString(16).slice(2, 8)}`
    const insertIdx = insertIndexForPosition(currentList, newSectionForm.position)
    currentList.splice(insertIdx, 0, {
      key: newKey,
      type: newSectionForm.type,
      title: newSectionForm.title.trim(),
      required: false,
    })

    const updated = await updateProjectSectionsStructure(projectId, currentList, project.value.revision)
    project.value = updated
    current.value = newKey
    computeChapterNumbers()
    showAddSectionModal.value = false
    newSectionForm.title = ''
    void runCheckNow()
  } catch (e) {
    error.value = e.message || t('writer.addSectionFailed')
  } finally {
    structureBusy.value = false
  }
}

async function moveSection(node, delta) {
  if (structureBusy.value) return
  const index = sectionIndex(node)
  const targetIndex = index + delta
  if (index < 0 || targetIndex < 0 || targetIndex >= project.value.sections.length) return
  structureBusy.value = true
  error.value = ''
  try {
    await flushPendingSaves()
    const currentList = project.value.sections.map(structureItem)
    ;[currentList[index], currentList[targetIndex]] = [currentList[targetIndex], currentList[index]]
    const updated = await updateProjectSectionsStructure(projectId, currentList, project.value.revision)
    project.value = updated
    computeChapterNumbers()
    void runCheckNow()
  } catch (e) {
    error.value = e.message || t('writer.moveSectionFailed')
  } finally {
    structureBusy.value = false
  }
}

function removeSection(node) {
  if (structureBusy.value) return
  openConfirmDialog({ title: t('writer.deleteSectionTitle', { name: node.title || node.key }), description: t('writer.deleteSectionDescription'), confirmLabel: t('common.confirm'), danger: true }, () => performRemoveSection(node))
}

async function performRemoveSection(node) {
  structureBusy.value = true
  error.value = ''
  try {
    await flushPendingSaves()
    const deletedIndex = sectionIndex(node)
    const currentList = project.value.sections
      .filter((s) => s.key !== node.key)
      .map(structureItem)
    const updated = await updateProjectSectionsStructure(projectId, currentList, project.value.revision)
    project.value = updated
    if (current.value === node.key) {
      current.value = updated.sections[Math.min(deletedIndex, updated.sections.length - 1)]?.key || 'cover'
    }
    computeChapterNumbers()
    void runCheckNow()
  } catch (e) {
    error.value = e.message || t('writer.removeSectionFailed')
  } finally {
    structureBusy.value = false
  }
}

const currentSection = computed(() => {
  if (!project.value) return null
  if (current.value === 'cover') return { type: 'cover', key: 'cover', title: t('writer.coverSection') }
  return project.value.sections.find((s) => s.key === current.value) || null
})

watch(currentSection, (section) => {
  sectionInputMode.value = 'blocks'
  sectionText.value = section ? textBlocksToSectionText(section.paragraphs) : ''
}, { immediate: true })

function toggleSectionInputMode() {
  if (sectionInputMode.value === 'blocks') {
    sectionText.value = textBlocksToSectionText(currentSection.value?.paragraphs)
    sectionInputMode.value = 'section'
  } else {
    sectionInputMode.value = 'blocks'
  }
}

function applySectionText(text) {
  const sec = currentSection.value
  if (!sec) return
  const textBlocks = createTextBlocks(text, sec.key)
  sec.paragraphs = mergeTextBlocksWithMedia(textBlocks, sec.paragraphs)
  sectionText.value = textBlocksToSectionText(sec.paragraphs)
  scheduleSave()
  sectionInputMode.value = 'blocks'
}

const metaFields = computed(() => template.value?.meta_fields || [])
const flatTemplateSections = computed(() => {
  const result = []
  function walk(nodes, depth = 0) {
    for (const node of nodes || []) {
      result.push({ ...node, depth })
      walk(node.children, depth + 1)
    }
  }
  walk(template.value?.sections)
  return result
})
const dirtyCount = computed(() => dirtyKeys.size)

onMounted(loadProject)

// ---------- AI 写作助手 ----------
const AI_GEN_TYPES = ['abstract', 'english_abstract', 'keywords', 'english_keywords']
const aiEnabled = ref(false)
const aiSource = ref(null)
const aiModel = ref('')
const aiBusy = ref(null)          // { index, action } 块级 AI 处理中
const aiDiff = ref(null)          // { index, action, original, generated, preview } 差异对照选择
const aiGenerating = ref(false)   // 章节级 AI 生成中
const showAiSettings = ref(false)
const aiDisclosure = ref(null)
async function confirmAiDisclosure() {
  aiDisclosure.value = await getAiDisclosure()
  if (!aiDisclosure.value.enabled) throw new Error('AI 未配置，不能发送内容')
  return window.confirm(`${aiDisclosure.value.notice}\n接收方：${aiDisclosure.value.data_recipient_host}\n发送内容：${aiDisclosure.value.content_sent}`)
}
const isAdmin = computed(() => auth.user?.role === 'admin')
const aiTab = ref('user') // 'user' | 'admin'
const aiCfgForm = reactive({ base_url: '', api_key: '', model: '' })
const aiCfgInfo = reactive({
  configured: false, api_key_masked: '', testing: false, saving: false, message: '', ok: null,
})
const adminAiForm = reactive({ base_url: '', api_key: '', model: '' })
const adminAiInfo = reactive({
  configured: false, source: null, api_key_masked: '', has_env_fallback: false,
  testing: false, saving: false, message: '', ok: null,
})

function switchAiTab(tab) {
  aiTab.value = tab
  if (tab === 'admin') {
    void loadAdminAiCfg()
  } else {
    void loadUserAiCfg()
  }
}

onMounted(loadAiStatus)

async function loadAiStatus() {
  try {
    const s = await aiStatus()
    aiEnabled.value = s.enabled
    aiSource.value = s.source
    aiModel.value = s.model || ''
  } catch { aiEnabled.value = false }
}

async function loadUserAiCfg() {
  aiCfgInfo.message = ''
  aiCfgInfo.ok = null
  try {
    const cfg = await getAiConfig()
    aiCfgInfo.configured = cfg.configured
    aiCfgInfo.api_key_masked = cfg.api_key_masked || ''
    aiCfgForm.base_url = cfg.base_url || ''
    aiCfgForm.model = cfg.model || ''
    aiCfgForm.api_key = ''
  } catch (e) {
    aiCfgInfo.message = e.message || t('writer.loadAiConfigFailed')
  }
}

async function loadAdminAiCfg() {
  adminAiInfo.message = ''
  adminAiInfo.ok = null
  try {
    const cfg = await getAdminAiConfig()
    adminAiInfo.configured = cfg.configured
    adminAiInfo.source = cfg.source
    adminAiInfo.api_key_masked = cfg.api_key_masked || ''
    adminAiInfo.has_env_fallback = cfg.has_env_fallback
    adminAiForm.base_url = cfg.base_url || ''
    adminAiForm.model = cfg.model || ''
    adminAiForm.api_key = ''
  } catch (e) {
    adminAiInfo.message = e.message || t('writer.loadAdminAiConfigFailed')
  }
}

async function openAiSettings() {
  showAiSettings.value = true
  aiTab.value = 'user'
  try { aiDisclosure.value = await getAiDisclosure() } catch (e) { error.value = e.message }
  await loadUserAiCfg()
  if (isAdmin.value) {
    await loadAdminAiCfg()
  }
}

function adminAiFilled() {
  return !!(adminAiForm.base_url && adminAiForm.model
    && (adminAiForm.api_key || (adminAiInfo.configured && adminAiInfo.api_key_masked)))
}

async function testAdminAiConnection() {
  if (!adminAiFilled()) {
    adminAiInfo.ok = false
    adminAiInfo.message = adminAiInfo.configured
      ? t('writer.keepSavedKey')
      : t('writer.completeAiFields')
    return
  }
  adminAiInfo.testing = true
  adminAiInfo.message = ''
  try {
    const res = await testAdminAiConfig({ ...adminAiForm })
    adminAiInfo.ok = res.ok
      adminAiInfo.message = res.ok ? t('writer.testSucceeded', { model: res.model }) : (res.error || t('writer.testFailed'))
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || t('writer.testFailed')
  } finally {
    adminAiInfo.testing = false
  }
}

async function saveAdminAiCfg() {
  if (!adminAiFilled()) {
    adminAiInfo.ok = false
    adminAiInfo.message = adminAiInfo.configured
      ? t('writer.adminKeepKey')
      : t('writer.adminCompleteFields')
    return
  }
  adminAiInfo.saving = true
  adminAiInfo.message = ''
  try {
    await saveAdminAiConfig({ ...adminAiForm })
    adminAiInfo.configured = true
    adminAiInfo.source = 'database'
    adminAiInfo.ok = true
    adminAiInfo.message = t('writer.adminSaved')
    await loadAiStatus()
    await loadAdminAiCfg()
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || t('writer.adminSaveFailed')
  } finally {
    adminAiInfo.saving = false
  }
}

async function clearAdminAiCfg() {
  try {
    await clearAdminAiConfig()
    adminAiInfo.ok = true
    adminAiInfo.message = t('writer.systemRestored')
    await loadAiStatus()
    await loadAdminAiCfg()
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || t('writer.clearFailed')
  }
}

function isAiSection(type) { return AI_GEN_TYPES.includes(type) }

/** 块级 AI: 润色/续写当前块, 获得结果后展示对比确认面板 */
async function onBlockAi({ action, index }) {
  const sec = currentSection.value
  if (!sec || aiBusy.value) return
  const par = sec.paragraphs[index]
  if (!par || par.type !== 'paragraph') return
  const text = (par.text || '').trim()
  if (!text) {
    error.value = t('writer.emptyBlock')
    return
  }
  aiBusy.value = { index, action }
  error.value = ''
  try {
    if (!await confirmAiDisclosure()) return
    const result = await aiAssist({
      action, text, section_title: sec.title, section_type: sec.type,
    })
    const generated = result.text || ''
    const original = par.text || ''
    aiDiff.value = {
      index,
      action,
      original,
      generated,
      preview: action === 'continue' ? (original + (original ? '\n' : '') + generated) : generated,
    }
  } catch (e) {
    error.value = e.message || t('writer.aiCallFailed')
  } finally {
    aiBusy.value = null
  }
}

/** 用户在对比面板选择采纳结果 */
function applyAiDiff() {
  if (!aiDiff.value) return
  const { index, action, generated, original } = aiDiff.value
  const sec = currentSection.value
  if (sec && sec.paragraphs[index]) {
    const par = sec.paragraphs[index]
    if (action === 'continue') {
      par.text = original + (original ? '\n' : '') + generated
    } else {
      par.text = generated
    }
    scheduleSave()
  }
  aiDiff.value = null
}

/** 用户放弃本次 AI 生成结果 */
function cancelAiDiff() {
  aiDiff.value = null
}

/** 拼接正文上下文: 除封面/目录/四个 AI 目标章节外的所有章节段落 */
function bodyContext() {
  const exclude = new Set([...AI_GEN_TYPES, 'cover', 'toc'])
  const parts = []
  for (const s of project.value?.sections || []) {
    if (exclude.has(s.type)) continue
    for (const p of s.paragraphs || []) {
      if (p.type === 'paragraph' && (p.text || '').trim()) parts.push(p.text.trim())
    }
  }
  return parts.join('\n')
}

/** 章节级 AI: 按正文生成 摘要/Abstract/关键词 */
async function aiGenerate() {
  const sec = currentSection.value
  if (!sec || aiGenerating.value || aiBusy.value) return
  const body = bodyContext()
  if (body.length < 50) {
    error.value = t('writer.bodyTooShort')
    return
  }
  aiGenerating.value = true
  error.value = ''
  try {
    if (!await confirmAiDisclosure()) return
    const result = await aiAssist({
      action: sec.type === 'english_keywords' ? 'keywords' : sec.type,
      text: body.slice(0, 11000),
      section_title: sec.title, section_type: sec.type,
    })
    let first = sec.paragraphs.find((p) => p.type === 'paragraph')
    if (!first) {
      first = { id: `p${Date.now()}`, type: 'paragraph', text: '', bold: false, italic: false, underline: false }
      sec.paragraphs.unshift(first)
    }
    first.text = result.text
    scheduleSave()
  } catch (e) {
    error.value = e.message || t('writer.aiGenerateFailed')
  } finally {
    aiGenerating.value = false
  }
}

function aiCfgFilled() {
  return !!(aiCfgForm.base_url && aiCfgForm.model
    && (aiCfgForm.api_key || (aiCfgInfo.configured && aiCfgInfo.api_key_masked)))
}

async function testAiConnection() {
  if (!aiCfgFilled()) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = aiCfgInfo.configured
      ? t('writer.keepSavedKey')
      : t('writer.completeAiFields')
    return
  }
  aiCfgInfo.testing = true
  aiCfgInfo.message = ''
  try {
    const r = await testAiConfig({ ...aiCfgForm })
    aiCfgInfo.ok = r.ok
    aiCfgInfo.message = r.ok ? t('writer.connected', { model: r.model }) : (r.error || t('writer.connectionFailed'))
  } catch (e) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = e.message || t('writer.testFailed')
  } finally {
    aiCfgInfo.testing = false
  }
}

async function saveAiCfg() {
  if (!aiCfgFilled()) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = aiCfgInfo.configured
      ? t('writer.keepSavedKey')
      : t('writer.completeAiFields')
    return
  }
  aiCfgInfo.saving = true
  aiCfgInfo.message = ''
  try {
    await saveAiConfig({ ...aiCfgForm })
    aiCfgInfo.configured = true
    aiCfgInfo.ok = true
    aiCfgInfo.message = t('writer.savedOwn')
    await loadAiStatus()
  } catch (e) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = e.message || t('writer.saveFailed')
  } finally {
    aiCfgInfo.saving = false
  }
}

async function clearAiCfg() {
  try {
    await clearAiConfig()
    await loadAiStatus()
    aiCfgInfo.configured = false
    aiCfgForm.base_url = ''
    aiCfgForm.model = ''
    aiCfgForm.api_key = ''
    aiCfgInfo.ok = true
    aiCfgInfo.message = t('writer.clearedOwn')
  } catch (e) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = e.message || t('writer.clearFailed')
  }
}

async function loadProject() {
  loading.value = true
  loadError.value = ''
  error.value = ''
  try {
    const p = await fetchProject(projectId)
    project.value = p
    loadLocalRecovery()
    template.value = p.template_snapshot
      ? p.template_snapshot
      : await fetchTemplate(p.template_id)
    computeChapterNumbers()
    loading.value = false
    beforeUnloadHandler = (event) => {
      if (dirtyCount.value || saving.value) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', beforeUnloadHandler)
    void runCheckNow()
    void loadWaivers()
  } catch (e) {
    loading.value = false
    loadError.value = e.message || t('writer.projectLoadFailed')
  }
}

/** 计算每个章节所属章号(一级标题/参考文献各占一章) */
function computeChapterNumbers() {
  const map = {}
  let chapter = 0
  if (project.value?.sections?.length) {
    for (const node of project.value.sections) {
      if (node.type === 'heading1' || node.type === 'references') chapter += 1
      map[node.key] = chapter
    }
    chapterMap.value = map
    return
  }
  function walk(nodes) {
    for (const node of nodes || []) {
      if (node.type === 'heading1' || node.type === 'references') chapter += 1
      map[node.key] = chapter
      walk(node.children)
    }
  }
  walk(template.value?.sections)
  chapterMap.value = map
}

function chapterOf(key) { return chapterMap.value[key] || 0 }

// ---------- 字数统计与完成度 ----------
const requiredSections = computed(() =>
  (project.value?.sections || []).filter((s) => s.required && s.type !== 'cover' && s.type !== 'toc'),
)
const doneCount = computed(() => requiredSections.value.filter((s) => isDoneForSec(s)).length)
const requiredCount = computed(() => requiredSections.value.length)
const progressPct = computed(() => requiredCount.value ? Math.round((doneCount.value / requiredCount.value) * 100) : 0)
const totalChars = computed(() => {
  if (!project.value) return 0
  return project.value.sections.reduce((n, sec) => n + sectionCharsOf(sec), 0)
})
const sectionChars = computed(() => currentSection.value ? sectionCharsOf(currentSection.value) : 0)

function sectionCharsOf(sec) {
  let n = (sec.title || '').trim().length
  for (const p of sec.paragraphs || []) {
    if (p.type === 'paragraph') n += (p.text || '').trim().length
    else if (p.type === 'table' && p.table) {
      n += p.table.cells.flat().reduce((s, c) => s + String(c || '').trim().length, 0)
    }
  }
  return n
}

function isDoneForSec(sec) {
  if (sec.type === 'cover' || sec.type === 'toc') return true
  if (sec.paragraphs && sec.paragraphs.length) {
    return sec.paragraphs.some((p) => {
      if (p.type === 'image') return !!(p.image && p.image.url)
      if (p.type === 'table') return !!p.table && p.table.cells.flat().some((c) => String(c).trim())
      return (p.text || '').trim()
    })
  }
  return !!sec.title && sec.title.trim() !== ''
}

function label(node) { return node.title || node.key }
function isContentType(type) { return CONTENT_TYPES.includes(type) }
function isHeading(type) { return HEADING_TYPES.includes(type) }
function isRequired(node) {
  const sec = project.value?.sections.find((s) => s.key === node.key)
  return node.required !== false && (sec ? sec.required !== false : true)
}
function isDone(node) {
  const sec = project.value?.sections.find((s) => s.key === node.key)
  return sec ? isDoneForSec(sec) : true
}
function typeLabel(type) {
  return {
    cover: t('writer.coverSection'), abstract: t('section.abstract'), english_abstract: t('section.englishAbstract'), keywords: t('section.keywords'), english_keywords: t('section.englishKeywords'), toc: t('section.toc'), heading1: t('section.heading1'), heading2: t('section.heading2'),
    heading3: t('section.heading3'), body: t('section.body'), references: t('section.references'), acknowledgement: t('section.acknowledgement'), appendix: t('section.appendix'),
  }[type] || type
}

function editorStyle(type, heading = false) {
  const headingTypes = ['heading1', 'heading2', 'heading3']
  let styleKey = type
  if (!heading && headingTypes.includes(type)) styleKey = 'body'
  if (!heading && type === 'abstract') styleKey = 'abstract_body'
  if (!heading && type === 'english_abstract') styleKey = 'english_abstract_body'
  const styles = template.value?.styles || {}
  const style = styles[styleKey] || styles.body || {}
  const fontNames = [style.font_zh, style.font_en, 'sans-serif']
    .filter(Boolean)
    .map((name) => `"${String(name).replace(/"/g, '')}"`)
    .join(', ')
  const align = { left: 'left', center: 'center', right: 'right', justify: 'justify' }[style.align] || 'left'
  return {
    fontFamily: fontNames,
    fontSize: style.size_pt ? `${style.size_pt}pt` : undefined,
    fontWeight: style.bold ? '700' : '400',
    fontStyle: style.italic ? 'italic' : 'normal',
    textDecoration: style.underline ? 'underline' : 'none',
    textAlign: align,
    lineHeight: style.line_spacing_rule === 'auto'
      ? String(style.line_spacing || 1.5)
      : (style.line_spacing ? `${style.line_spacing}pt` : undefined),
    textIndent: style.first_line_indent_chars ? `${style.first_line_indent_chars}em` : '0',
  }
}

async function select(node) {
  if (node.key === current.value) return
  try {
    await flushPendingSaves()
    current.value = node.key
  } catch { /* 保存错误已显示, 保留当前章节 */ }
}

// ---------- 保存队列 ----------
function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function recoveryStorageKey() {
  return `pg_recovery:${auth.user?.id || 'anonymous'}:${projectId}`
}

function hasRecoveryStorage() {
  try {
    const probe = '__pg_recovery_probe__'
    localStorage.setItem(probe, '1')
    localStorage.removeItem(probe)
    return true
  } catch {
    recoveryUnavailable.value = true
    return false
  }
}

function localRecoveryPayload() {
  return {
    project_id: projectId,
    user_id: auth.user?.id || '',
    base_revision: project.value.revision,
    saved_at: new Date().toISOString(),
    snapshot: editableSnapshot(),
  }
}

function editableSnapshot() {
  return clone({
    title: project.value.title,
    meta: project.value.meta,
    sections: project.value.sections,
  })
}

function persistLocalRecovery() {
  if (!project.value || !hasRecoveryStorage()) return
  try {
    localStorage.setItem(recoveryStorageKey(), JSON.stringify(localRecoveryPayload()))
  } catch {
    recoveryUnavailable.value = true
  }
}

function loadLocalRecovery() {
  if (!project.value || !hasRecoveryStorage()) return
  try {
    const saved = JSON.parse(localStorage.getItem(recoveryStorageKey()) || 'null')
    const savedSnapshot = saved?.snapshot ? JSON.stringify(saved.snapshot) : ''
    if (savedSnapshot && saved.saved_at && savedSnapshot !== JSON.stringify(editableSnapshot())) {
      recoveryDraft.value = saved
    } else if (savedSnapshot) {
      clearLocalRecovery()
    }
  } catch {
    recoveryDraft.value = null
  }
}

function clearLocalRecovery() {
  try { localStorage.removeItem(recoveryStorageKey()) } catch { /* ignore storage cleanup errors */ }
  recoveryDraft.value = null
}

function maybeClearRecovery() {
  if (!dirtyKeys.size && !pendingSaves.size && !pendingMeta && !inFlightSaves.size) {
    clearLocalRecovery()
    failedSave.value = false
  }
}

async function restoreLocalDraft() {
  if (!recoveryDraft.value || !project.value) return
  try {
    const updated = await restoreProjectSnapshot(projectId, recoveryDraft.value.snapshot, recoveryDraft.value.base_revision)
    project.value = updated
    template.value = updated.template_snapshot || template.value
    clearLocalRecovery()
    failedSave.value = false
    conflictSave.value = null
    error.value = ''
    computeChapterNumbers()
    void runCheckNow()
  } catch (e) {
    error.value = e.message || t('writer.restoreDraftFailed')
    if (e.status === 409) conflictSave.value = { key: '__recovery' }
  }
}

function discardLocalDraft() {
  clearLocalRecovery()
}

async function reloadAfterConflict() {
  try {
    const updated = await fetchProject(projectId)
    project.value = updated
    template.value = updated.template_snapshot || template.value
    pendingSaves.clear()
    pendingMeta = null
    dirtyKeys.clear()
    conflictSave.value = null
    error.value = ''
    loadLocalRecovery()
    computeChapterNumbers()
  } catch (e) {
    error.value = e.message || t('writer.reloadServerFailed')
  }
}

function markDirty(key) { dirtyKeys.add(key) }
function markClean(key) { dirtyKeys.delete(key) }

function scheduleSave() {
  const sec = currentSection.value
  if (!sec || sec.type === 'cover' || !project.value) return
  const key = sec.key
  const version = (saveVersions.get(key) || 0) + 1
  saveVersions.set(key, version)
  pendingSaves.set(key, { version, payload: clone({ title: sec.title, paragraphs: sec.paragraphs }) })
  markDirty(key)
  persistLocalRecovery()
  clearTimeout(saveTimers.get(key))
  saveTimers.set(key, setTimeout(() => {
    saveTimers.delete(key)
    const pending = pendingSaves.get(key)
    if (!pending) return
    pendingSaves.delete(key)
    enqueueSectionSave(key, pending.payload, pending.version)
  }, 800))
  scheduleCheck()
}

function onMetaInput(key, value) {
  if (!project.value) return
  project.value.meta[key] = value
  pendingMeta = { version: (saveVersions.get('__meta') || 0) + 1, payload: clone(project.value.meta) }
  saveVersions.set('__meta', pendingMeta.version)
  markDirty('__meta')
  persistLocalRecovery()
  clearTimeout(metaTimer)
  metaTimer = setTimeout(() => {
    metaTimer = null
    const pending = pendingMeta
    pendingMeta = null
    enqueueMetaSave(pending.payload, pending.version)
  }, 800)
  scheduleCheck()
}

function scheduleCheck() {
  checkStale.value = true
  fixNotice.value = ''
  clearTimeout(scheduleCheck.timer)
  scheduleCheck.timer = setTimeout(() => void runCheckNow(), 2500)
}

function enqueueSectionSave(key, payload, version) {
  const task = async () => {
    if (!project.value) return
    saving.value = true
    try {
      const result = await updateProjectSection(projectId, key, payload, project.value.revision)
      project.value.revision = result.revision
      if (saveVersions.get(key) === version && !pendingSaves.has(key)) markClean(key)
      savedAt.value = new Date().toLocaleTimeString()
    } catch (e) {
      if (saveVersions.get(key) === version && !pendingSaves.has(key)) {
        pendingSaves.set(key, { version, payload })
      }
      failedSave.value = true
      persistLocalRecovery()
      if (e.status === 409) conflictSave.value = { key, revision: e.revision }
      error.value = e.message || t('writer.saveSectionFailed')
      throw e
    } finally {
      saving.value = false
    }
  }
  return enqueueSaveTask(task)
}

function enqueueMetaSave(payload, version) {
  const task = async () => {
    if (!project.value) return
    saving.value = true
    try {
      const result = await updateProjectMeta(projectId, payload, project.value.revision)
      project.value.revision = result.revision
      if (saveVersions.get('__meta') === version && !pendingMeta) markClean('__meta')
      savedAt.value = new Date().toLocaleTimeString()
    } catch (e) {
      if (saveVersions.get('__meta') === version && !pendingMeta) {
        pendingMeta = { version, payload }
      }
      failedSave.value = true
      persistLocalRecovery()
      if (e.status === 409) conflictSave.value = { key: '__meta', revision: e.revision }
      error.value = e.message || t('writer.saveCoverFailed')
      throw e
    } finally {
      saving.value = false
    }
  }
  return enqueueSaveTask(task)
}

function enqueueSaveTask(task) {
  const promise = saveQueue.catch(() => {}).then(task)
  saveQueue = promise.catch(() => {})
  inFlightSaves.add(promise)
  promise.finally(() => {
    inFlightSaves.delete(promise)
    maybeClearRecovery()
  }).catch(() => {})
  return promise
}

async function flushPendingSaves() {
  for (const [key, pending] of pendingSaves) {
    clearTimeout(saveTimers.get(key))
    saveTimers.delete(key)
    pendingSaves.delete(key)
    enqueueSectionSave(key, pending.payload, pending.version)
  }
  if (metaTimer) {
    clearTimeout(metaTimer)
    metaTimer = null
  }
  if (pendingMeta) {
    const pending = pendingMeta
    pendingMeta = null
    enqueueMetaSave(pending.payload, pending.version)
  }
  const pendingPromises = [...inFlightSaves]
  if (pendingPromises.length) await Promise.all(pendingPromises)
}

async function saveCurrent() { scheduleSave(); await flushPendingSaves() }
async function saveMeta() {
  if (!project.value) return
  clearTimeout(metaTimer)
  metaTimer = null
  pendingMeta = null
  const version = (saveVersions.get('__meta') || 0) + 1
  saveVersions.set('__meta', version)
  markDirty('__meta')
  await enqueueMetaSave(clone(project.value.meta), version)
}

async function retryFailedSaves() {
  failedSave.value = false
  error.value = ''
  try {
    await flushPendingSaves()
  } catch (e) {
    error.value = e.message || t('writer.retrySaveFailed')
  }
}

// ---------- 实时检查 ----------
async function runCheckNow() {
  if (!project.value || loading.value || checking.value || fixing.value) return
  const generation = ++checkGeneration
  try {
    await flushPendingSaves()
  } catch {
    checkError.value = t('writer.checkUnsaved')
    return
  }
  checking.value = true
  checkError.value = ''
  try {
    const result = await checkProject(projectId)
    if (generation !== checkGeneration) return
    checkResult.value = result
    checkStale.value = false
  } catch (e) {
    if (generation === checkGeneration) checkError.value = e.message || t('writer.checkFailedFallback')
  } finally {
    if (generation === checkGeneration) checking.value = false
  }
}

async function runOneClickFix() {
  if (!project.value || fixing.value || checking.value || loading.value) return
  clearTimeout(scheduleCheck.timer)
  checkGeneration += 1
  fixing.value = true
  error.value = ''
  fixNotice.value = ''
  try {
    await flushPendingSaves()
    const result = await fixProject(projectId, project.value.revision)
    project.value = result.project
    if (result.project.template_snapshot) template.value = result.project.template_snapshot
    checkResult.value = result.report
    checkStale.value = false
    checkOpen.value = true
    savedAt.value = new Date().toLocaleTimeString()
    if (result.fixed_count > 0) {
      fixNotice.value = t('writer.fixedNotice', { count: result.fixed_count })
    } else if (result.report?.findings?.length) {
      fixNotice.value = t('writer.fixUnavailable')
    } else {
      fixNotice.value = t('writer.noFixNeeded')
    }
  } catch (e) {
    error.value = e.message || t('writer.oneClickFixFailed')
  } finally {
    fixing.value = false
  }
}

async function acknowledgeBasis() {
  try { const result = await acknowledgeRequirements(projectId, requirementSnapshot.value?.content_hash); project.value.requirement_acknowledgements = [...(project.value.requirement_acknowledgements || []).filter((item) => item.user_id !== auth.user?.id), result] }
  catch (e) { error.value = e.message || t('writer.requirementAckFailed') }
}

function openWaiver(finding) { waiverTarget.value = finding; waiverReason.value = '' }
function openDispute(finding) { disputeTarget.value = finding; disputeReason.value = '' }
async function submitWaiver() {
  waiverBusy.value = true
  try {
    await requestRequirementWaiver(projectId, waiverTarget.value.requirement_item_id, { reason: waiverReason.value })
    waiverTarget.value = null; await runCheckNow()
  } catch (e) { error.value = e.message || t('writer.waiverSubmitFailed') }
  finally { waiverBusy.value = false }
}

async function loadWaivers() {
  try { waivers.value = await listRequirementWaivers(projectId) } catch { waivers.value = [] }
  try { disputes.value = await listRequirementDisputes(projectId) } catch { disputes.value = [] }
}
async function decideWaiver(waiver, status) {
  try {
    await decideRequirementWaiver(projectId, waiver.id, { status, review_note: status === 'approved' ? t('writer.waiverApprovedReview') : t('writer.waiverNeedsInfoReview') })
    await loadWaivers(); await runCheckNow()
  } catch (e) { error.value = e.message || t('writer.waiverReviewFailed') }
}
function revokeWaiver(waiver) {
  revokeWaiverTarget.value = waiver
}
async function confirmRevokeWaiver(reason) {
  const waiver = revokeWaiverTarget.value
  if (!waiver || !reason.trim()) return
  revokeWaiverBusy.value = true
  try {
    await revokeRequirementWaiver(projectId, waiver.id, { reason: reason.trim() })
    revokeWaiverTarget.value = null; await loadWaivers(); await runCheckNow()
  } catch (e) { error.value = e.message || t('writer.revokeFailed') }
  finally { revokeWaiverBusy.value = false }
}
async function submitDispute() {
  disputeBusy.value = true
  try {
    await requestRequirementDispute(projectId, disputeTarget.value.requirement_item_id, { reason: disputeReason.value })
    disputeTarget.value = null; await loadWaivers()
  } catch (e) { error.value = e.message || t('writer.disputeSubmitFailed') }
  finally { disputeBusy.value = false }
}
async function decideDispute(dispute, status) {
  try {
    await decideRequirementDispute(projectId, dispute.id, { status, review_note: status === 'accepted' ? t('writer.disputeAcceptedReview') : t('writer.disputeRejectedReview') })
    await loadWaivers()
  } catch (e) { error.value = e.message || t('writer.disputeReviewFailed') }
}

async function openUpgradePreview() {
  upgradeBusy.value = true
  try { upgradePreview.value = await previewRequirementUpgrade(projectId) }
  catch (e) { error.value = e.message || t('writer.upgradePreviewFailed') }
  finally { upgradeBusy.value = false }
}
async function applyUpgrade() {
  if (!upgradePreview.value?.diff.changed) return
  upgradeConfirm.value = true
}
async function confirmUpgrade() {
  if (!upgradePreview.value?.diff.changed) return
  upgradeConfirm.value = false
  upgradeBusy.value = true
  try {
    const result = await upgradeRequirements(projectId, { expected_snapshot_hash: upgradePreview.value.from_snapshot_hash, reason: t('writer.upgradeReasonDefault'), confirm: true })
    project.value = result.project; upgradePreview.value = null; await runCheckNow()
  } catch (e) { error.value = e.message || t('writer.upgradeApplyFailed') }
  finally { upgradeBusy.value = false }
}
function assistResultFor(itemId) {
  return [...(project.value?.ai_assist_results || [])].reverse().find((item) => item.requirement_item_id === itemId) || null
}

function showAssistResult(result) {
  assistResult.value = result
}

async function openAssist(item) {
  if (assistBusy.value) return
  assistBusy.value = item.id
  error.value = ''
  try {
    await flushPendingSaves()
    if (!await confirmAiDisclosure()) return
    const result = await assistRequirement(projectId, item.id, item.description)
    const previous = project.value.ai_assist_results || []
    project.value.ai_assist_results = previous.filter((saved) => saved.requirement_item_id !== item.id).concat(result)
    if (Number.isInteger(result.project_revision)) project.value.revision = result.project_revision
    assistResult.value = result
  } catch (e) { error.value = e.message || t('writer.aiAssistFailed') }
  finally { assistBusy.value = '' }
}

function jumpTo(f) { if (f.section_key) current.value = f.section_key }
function sectionTitle(key) {
  const sec = project.value?.sections.find((s) => s.key === key)
  return sec ? sec.title || key : key
}

function chooseDocx() {
  docxInput.value?.click()
}

async function onDocxSelected(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  importing.value = true
  error.value = ''
  try {
    await flushPendingSaves()
    importPreview.value = { ...(await importProjectDocx(projectId, file)), file }
  } catch (e) {
    error.value = e.message || t('writer.wordPreviewFailed')
  } finally {
    importing.value = false
  }
}

async function applyDocxImport() {
  if (!importPreview.value?.file || importing.value) return
  importing.value = true
  error.value = ''
  try {
    await flushPendingSaves()
    const result = await importProjectDocx(projectId, importPreview.value.file, true, project.value.revision)
    project.value = result.project
    template.value = result.project.template_snapshot || template.value
    importPreview.value = null
    computeChapterNumbers()
    templateNotice.value = t('writer.importedNotice', { paragraphs: result.stats.paragraphs, tables: result.stats.tables })
    setTimeout(() => { templateNotice.value = '' }, 5000)
    void runCheckNow()
  } catch (e) {
    error.value = e.message || t('writer.applyImportFailed')
  } finally {
    importing.value = false
  }
}

async function pollExportJob(jobId, initial) {
  let current = initial
  for (let attempt = 0; attempt < 120 && current.status !== 'succeeded'; attempt += 1) {
    if (current.status === 'failed') {
      throw new Error(t('writer.exportBackendFailed', { code: current.error_code || 'PROJECT_EXPORT_FAILED', id: current.id }))
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
    current = await fetchProjectExportJob(projectId, jobId)
  }
  if (current.status !== 'succeeded') {
    throw new Error(t('writer.exportStillProcessing', { id: jobId }))
  }
  await downloadProjectExportJob(projectId, jobId, current.filename || 'paper.docx')
}

async function exportDocx(force = false) {
  if (exporting.value) return
  exporting.value = true
  error.value = ''
  missing.value = []
  try {
    await flushPendingSaves()
    const task = await createProjectExportJob(projectId, force)
    exportJob.value = task.id
    await pollExportJob(task.id, task)
    exportJob.value = null
  } catch (e) {
    if (e.missing && e.missing.length) missing.value = e.missing
    else error.value = e.message || t('writer.exportFailed')
  } finally {
    exporting.value = false
  }
}

// 导出失败或超时后原地重试同一任务，而不是引导用户重新排队。
async function retryExportJob() {
  if (exporting.value || !exportJob.value) return
  exporting.value = true
  error.value = ''
  try {
    const task = await retryProjectExportJob(projectId, exportJob.value)
    await pollExportJob(task.id, task)
    exportJob.value = null
  } catch (e) {
    error.value = e.message || t('writer.exportFailed')
  } finally {
    exporting.value = false
  }
}

async function previewDocx() {
  if (exporting.value) return
  exporting.value = true
  error.value = ''
  try {
    await flushPendingSaves()
    exportPreview.value = await previewProjectExport(projectId)
  } catch (e) {
    error.value = e.message || t('writer.exportPreviewFailed')
  } finally {
    exporting.value = false
  }
}

async function refreshTemplate() {
  openConfirmDialog({ title: t('writer.updateStandardsTitle'), description: t('writer.updateStandardsDetail'), confirmLabel: t('writer.updateStandardsConfirm') }, async () => {
    error.value = ''
    const updated = await refreshProjectTemplate(projectId)
    project.value = updated
    template.value = updated.template_snapshot || template.value
  })
}

function openConfirmDialog(config, action) { Object.assign(confirmDialog, { ...config, open: true, action }) }
function closeConfirmDialog() { if (confirmBusy.value) return; confirmDialog.open = false; confirmDialog.action = null }
async function confirmAction() {
  const action = confirmDialog.action
  if (!action || confirmBusy.value) return
  confirmBusy.value = true
  try { await action() } catch (e) { error.value = e.message || t('common.operationFailed') } finally { confirmBusy.value = false; closeConfirmDialog() }
}

async function openTemplateModal() {
  showTemplateModal.value = true
  templateError.value = ''
  templateLoading.value = true
  try {
    templateList.value = await fetchTemplates()
  } catch (e) {
    templateError.value = e.message || t('writer.templatesLoadFailed')
  } finally {
    templateLoading.value = false
  }
}

async function applyTemplate(templateId) {
  if (templateBusy.value) return
  templateBusy.value = templateId
  templateError.value = ''
  try {
    await flushPendingSaves()
    const updated = await switchProjectTemplate(projectId, templateId)
    project.value = updated
    template.value = updated.template_snapshot || template.value
    showTemplateModal.value = false
    templateNotice.value = t('writer.templateAppliedNotice', { name: updated.template_name })
    setTimeout(() => { templateNotice.value = '' }, 5000)
  } catch (e) {
    templateError.value = e.message || t('writer.templateApplyFailed')
  } finally {
    templateBusy.value = ''
  }
}

onBeforeRouteLeave(async () => {
  try {
    await flushPendingSaves()
    return true
  } catch {
    return false
  }
})

onBeforeUnmount(() => {
  for (const timer of saveTimers.values()) clearTimeout(timer)
  clearTimeout(metaTimer)
  clearTimeout(scheduleCheck.timer)
  if (beforeUnloadHandler) window.removeEventListener('beforeunload', beforeUnloadHandler)
})
</script>

<style scoped>
.writer { display: flex; gap: 24px; align-items: flex-start; }
.sidebar {
  width: 280px; flex-shrink: 0; background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; padding: 16px; position: sticky; top: 16px; max-height: calc(100vh - 120px);
  display: flex; flex-direction: column;
}
.sidebar-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.head-actions { display: flex; gap: 6px; margin-left: auto; }
.ai-gen { margin-left: auto; }

/* AI 设置与对比弹窗 */
.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, .45); display: flex; align-items: center; justify-content: center; z-index: 60; }
.writer-modal-mask { z-index: 120; }
.modal { width: min(480px, 92vw); text-align: left; max-height: 88vh; overflow-y: auto; }
.modal.diff-modal { width: min(720px, 94vw); }
.diff-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.diff-pane { display: flex; flex-direction: column; gap: 6px; }
.diff-label { font-size: 12px; font-weight: 600; color: var(--muted); }
.diff-box {
  border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
  font-size: 13px; line-height: 1.6; max-height: 240px; overflow-y: auto;
  white-space: pre-wrap; word-break: break-word; font-family: inherit;
}
.diff-box.original { background: #f8fafc; color: #475569; }
.diff-box.generated { background: #f0fdf4; border-color: #bbf7d0; color: #166534; }
.diff-preview-pane { margin-top: 12px; }
.diff-box.preview { background: #eff6ff; border-color: #bfdbfe; color: #1e40af; max-height: 180px; }
.assist-saved-location { margin: 0 0 10px; color: var(--success, #15803d); font-size: 13px; }
.assist-saved-tag { color: var(--success, #15803d); font-size: 12px; white-space: nowrap; }
.import-modal { width: min(560px, 92vw); }
.import-section-list { display: grid; gap: 6px; margin: 14px 0; max-height: 260px; overflow-y: auto; }
.import-section-item { display: flex; justify-content: space-between; gap: 12px; padding: 9px 10px; background: var(--surface-container-low); border: 1px solid var(--border); font-size: 12px; }
.import-section-item span { color: var(--on-surface-variant); white-space: nowrap; }
.modal .field { display: block; font-size: 13px; color: var(--muted); margin-bottom: 12px; }
.modal .field input { width: 100%; margin-top: 4px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
.structure-select { width: 100%; margin-top: 4px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; background: var(--card); color: var(--text); }
.ok-banner { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.error-banner { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.notice-banner { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.recovery-status { margin: 0 auto 10px; max-width: 840px; }
.recovery-banner { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 auto 10px; max-width: 840px; padding: 10px 12px; border: 1px solid #bfdbfe; border-radius: 8px; background: #eff6ff; color: #1e3a8a; font-size: 12px; }
.recovery-banner > div:first-child { display: grid; gap: 3px; min-width: 0; }
.recovery-banner span { color: inherit; }
.recovery-banner .form-row { flex-shrink: 0; }
.recovery-banner button { white-space: nowrap; }
.save-failure { border-color: #fcd34d; background: #fffbeb; color: #92400e; }
.conflict-banner { border-color: #fecaca; background: #fef2f2; color: #991b1b; }
.requirements-panel {
  border: 1px solid #cbd5e1; background: #ffffff; border-radius: 10px;
  padding: 12px 14px; margin-bottom: 14px; box-shadow: var(--shadow-card);
}
.requirements-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.requirements-head h3 { font-size: 15px; margin-top: 3px; }
.basis-actions { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.eyebrow { color: var(--primary); font-size: 10px; font-weight: 700; letter-spacing: .08em; }
.snapshot-tag { color: #166534; background: #dcfce7; border-radius: 999px; padding: 3px 8px; font-size: 11px; white-space: nowrap; }
.requirements-sources { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
.source-chip { display: inline-flex; align-items: center; gap: 5px; padding: 5px 8px; border: 1px solid #dbeafe; border-radius: 6px; background: #eff6ff; color: #1e40af; font-size: 12px; }
.source-chip.template-chip { border-color: #e2e8f0; background: #f8fafc; color: #475569; }
.source-chip .material-symbols-outlined { font-size: 14px; }
.requirements-foot { color: var(--muted); font-size: 12px; margin-top: 10px; }
.waiver-review-panel { border: 1px solid #fcd34d; background: #fffbeb; border-radius: 10px; padding: 12px 14px; margin-bottom: 14px; }
.dispute-review-panel { border-color: #bfdbfe; background: #eff6ff; }
.waiver-review-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 0; border-top: 1px solid #fde68a; color: #92400e; font-size: 12px; }
.approved-waiver-row { color: #166534; border-top-color: #bbf7d0; }
.template-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; }
.template-row.current { border-color: var(--primary); background: #f8fafc; }
.template-meta { min-width: 0; }
.template-desc { font-size: 12px; color: var(--muted); margin-top: 2px; }
.template-row .tag { display: inline-block; margin-left: 8px; padding: 1px 6px; font-size: 11px; border-radius: 999px; background: var(--primary); color: #fff; }
.ai-tab-row { display: flex; gap: 8px; margin-bottom: 10px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.ai-tab-btn { background: transparent; border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; color: var(--muted); }
.ai-tab-btn.active { background: #eff6ff; border-color: var(--primary); color: var(--primary); font-weight: 600; }
.proj-title { font-weight: 600; font-size: 14px; flex: 1 1 100%; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tree-nav { flex: 1; overflow-y: auto; }
.nav-row { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.nav-row:hover { background: #f1f5f9; }
.nav-row.active { background: #eff6ff; color: var(--primary); font-weight: 600; }
.nav-row.child { padding-left: 24px; }
.nav-row.grand { padding-left: 40px; }
.nav-title { flex: 1; }
.nav-action-move { display: none; gap: 2px; }
.nav-row:hover .nav-action-move, .nav-row.active .nav-action-move { display: inline-flex; }
.nav-move-btn {
  width: 22px; height: 22px; padding: 0; border: 1px solid transparent; border-radius: 4px;
  background: transparent; color: var(--muted); cursor: pointer; line-height: 1;
}
.nav-move-btn:hover:not(:disabled) { border-color: var(--border); background: var(--card); color: var(--primary); }
.nav-move-btn:disabled { opacity: .3; cursor: not-allowed; }
.nav-action-del {
  display: none;
  font-size: 16px;
  line-height: 1;
  color: var(--muted);
  padding: 0 4px;
  border-radius: 4px;
}
.nav-row:hover .nav-action-del {
  display: inline-block;
}
.nav-action-del:hover {
  color: var(--error);
  background: #fee2e2;
}
.dot { color: #f59e0b; font-size: 10px; }
.sidebar-foot { display: flex; justify-content: space-between; align-items: center; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
button.small { font-size: 13px; padding: 6px 12px; white-space: nowrap; }

.editor-pane { flex: 1; min-width: 0; }
.editor-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.type-tag { background: #eff6ff; color: var(--primary); font-size: 12px; padding: 2px 10px; border-radius: 999px; }
.hint { color: var(--muted); font-size: 13px; }
.heading-input { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 16px; margin-bottom: 20px; }

.para-block { border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px; background: var(--card); }
.para-toolbar { display: flex; align-items: center; gap: 6px; padding: 6px 10px; border-bottom: 1px solid var(--border); }
.para-no { font-size: 12px; color: var(--muted); margin-right: auto; }
button.tiny { font-size: 12px; padding: 2px 8px; }
button.tiny.on { background: var(--primary); color: #fff; border-color: var(--primary); }
button.tiny.danger { color: var(--error); }
.para-text { width: 100%; border: none; padding: 10px 12px; font-size: 14px; resize: vertical; line-height: 1.6; font-family: inherit; }
.para-text:focus { outline: none; }

/* 模板锁定样式的编辑预览 */
.para-text.style-abstract, .para-text.style-body,
.para-text.style-references, .para-text.style-acknowledgement {
  text-indent: 2em;
}

.meta-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.meta-row label { width: 120px; font-size: 14px; color: var(--muted); }
.meta-row input { flex: 1; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }

/* 实时检查面板 */
.check-panel {
  border: 1px solid #efc7cb; background: #fff8f8; border-radius: 8px;
  padding: 14px; margin-bottom: 16px; color: #17263d;
}
.check-panel.clean { border-color: #b9dfc8; background: #f4fbf6; }
.check-panel-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.check-panel-title { display: flex; align-items: center; gap: 9px; min-width: 0; }
.check-panel-icon {
  display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto;
  width: 30px; height: 30px; border: 1px solid #b9ccf3; border-radius: 6px;
  background: #edf3ff; color: #155eef; font-size: 18px;
}
.check-panel-kicker { display: block; margin-bottom: 1px; color: #7a8494; font-size: 9px; font-weight: 700; letter-spacing: .1em; line-height: 1.2; }
.check-panel-title strong { display: block; color: #152640; font-size: 15px; line-height: 1.35; }
.check-refresh-btn {
  flex: 0 0 auto; min-height: 31px; padding: 5px 9px; border: 1px solid #d5dce8;
  border-radius: 6px; background: #fff; color: #20324b; font-size: 12px; font-weight: 650;
}
.check-refresh-btn:hover:not(:disabled) { border-color: #8ba8df; background: #f5f8ff; color: #155eef; }
.check-refresh-btn .material-symbols-outlined { font-size: 15px; }
.check-summary {
  display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 8px;
  margin-top: 13px; padding: 11px 0; border: 0; border-top: 1px solid #f0dfe0; border-bottom: 1px solid #f0dfe0;
  border-radius: 0; background: transparent; color: #27374d; text-align: left; cursor: pointer;
}
.check-summary:hover { background: rgba(255, 255, 255, .62); }
.check-panel button:focus-visible { outline: 2px solid #155eef; outline-offset: 2px; }
.check-summary-leading { display: inline-flex; align-items: center; gap: 7px; min-width: 0; }
.check-summary-icon { flex: 0 0 auto; color: #c5525c; font-size: 18px; }
.check-panel.clean .check-summary-icon { color: #20834b; }
.check-summary-text { min-width: 0; font-size: 13px; font-weight: 600; line-height: 1.4; overflow-wrap: anywhere; }
.check-panel .context-chevron { flex: 0 0 auto; color: #7d8796; font-size: 18px; }
.check-summary-icon.material-symbols-outlined { transition: color .15s ease; }
.check-list { margin-top: 0; padding: 0; }
.check-item { padding: 13px 0; border-bottom: 1px solid #f0dfe0; cursor: pointer; }
.check-item:last-child { border-bottom: 0; }
.check-item:hover { margin: 0 -6px; padding-right: 6px; padding-left: 6px; border-radius: 6px; background: rgba(255, 255, 255, .64); }
.check-item-message { display: flex; align-items: flex-start; gap: 8px; min-width: 0; }
.severity-marker { display: inline-flex; align-items: center; gap: 3px; flex: 0 0 31px; padding-top: 2px; color: #b74a55; font-size: 12px; font-weight: 700; }
.severity-marker.error { color: #b42318; }
.severity-marker.info { color: #155eef; }
.severity-marker .material-symbols-outlined { font-size: 15px; }
.check-msg { min-width: 0; flex: 1; color: #17263d; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.check-item-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 8px; margin: 9px 0 0 39px; }
.check-action-btn {
  display: inline-flex; align-items: center; gap: 4px; min-height: 28px; padding: 4px 8px;
  border: 1px solid #d2dbe9; border-radius: 6px; background: #fff; color: #155eef;
  font-size: 12px; font-weight: 650; line-height: 1.2; white-space: nowrap;
}
.check-action-btn:hover:not(:disabled) { border-color: #8ba8df; background: #f3f7ff; color: #0b4cc4; }
.check-action-btn.secondary { color: #44546b; }
.check-action-btn.assist-action { color: #155eef; }
.check-action-btn .material-symbols-outlined { font-size: 14px; }
.check-sec { flex: 1 1 100%; min-width: 0; color: #7a8494; font-size: 11px; line-height: 1.4; overflow-wrap: anywhere; }
.not-checked-list { margin-top: 4px; padding-top: 15px; border-top: 1px solid #e4dfe0; color: #46566c; font-size: 12px; }
.not-checked-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.not-checked-heading > span:first-child { display: inline-flex; align-items: center; gap: 6px; color: #30425b; font-size: 13px; font-weight: 700; }
.not-checked-heading .material-symbols-outlined { color: #155eef; font-size: 16px; }
.not-checked-count { color: #7a8494; font-size: 11px; white-space: nowrap; }
.not-checked-item { padding: 12px 0; border-bottom: 1px solid #ebe6e6; }
.not-checked-item:last-child { border-bottom: 0; padding-bottom: 2px; }
.not-checked-copy { display: flex; align-items: flex-start; gap: 8px; color: #26364c; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
.manual-dot { flex: 0 0 auto; width: 7px; height: 7px; margin-top: 7px; border-radius: 50%; background: #d79a27; }
.not-checked-source { color: #8a93a0; font-size: 11px; }
.not-checked-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 8px 0 0 15px; }
.waived-list { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin-top: 3px; padding: 12px 0 2px 39px; border-top: 1px solid #cde8d6; color: #166534; font-size: 12px; line-height: 1.5; }
.waiver-btn { flex-shrink: 0; }
.dispute-btn { flex-shrink: 0; }
.assist-saved-tag { display: inline-flex; align-items: center; gap: 3px; color: #168344; font-size: 11px; white-space: nowrap; }
.assist-saved-tag .material-symbols-outlined { font-size: 14px; }
.upgrade-summary { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-container-low); color: var(--muted); }
.assist-result { white-space: pre-wrap; border: 1px solid var(--border); border-radius: 8px; padding: 12px; max-height: 420px; overflow: auto; line-height: 1.7; }

/* 导出缺失清单 */
.missing-panel {
  border: 1px solid #fecaca; background: #fef2f2; color: var(--error);
  border-radius: 10px; padding: 12px 16px; margin-bottom: 16px; font-size: 14px;
}
.missing-panel ul { margin: 8px 0 0 20px; }
.missing-panel li { margin: 2px 0; }

/* 字数统计与进度 */
.stats-panel {
  margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border);
  font-size: 13px;
}
.progress-row { display: flex; justify-content: space-between; margin-bottom: 6px; }
.stats-label { color: var(--muted); }
.stats-value { font-weight: 600; color: var(--primary); }
.progress-bar { height: 8px; background: #e2e8f0; border-radius: 999px; overflow: hidden; margin-bottom: 10px; }
.progress-fill { height: 100%; background: var(--primary); border-radius: 999px; transition: width .3s; }
.stats-row { display: flex; justify-content: space-between; color: var(--muted); }
@media (max-width: 768px) {
  .recovery-banner { align-items: stretch; flex-direction: column; }
  .recovery-banner .form-row { justify-content: flex-start; }
  .writer { flex-direction: column; gap: 14px; }
  .sidebar { width: 100%; position: static; max-height: none; }
  .tree-nav { max-height: 220px; }
  .editor-head { align-items: flex-start; flex-wrap: wrap; }
  .meta-row { align-items: stretch; flex-direction: column; gap: 4px; }
  .meta-row label { width: auto; }
}

@media (max-width: 480px) {
  .sidebar-head, .sidebar-foot { gap: 8px; flex-wrap: wrap; }
  .check-panel-head { align-items: flex-start; }
  .check-refresh-btn { padding-right: 7px; padding-left: 7px; }
  .check-item-actions { margin-left: 0; }
  .not-checked-actions { margin-left: 15px; }
  .caption-row { align-items: stretch; flex-direction: column; gap: 4px; }
  .caption-num { width: max-content; }
  .requirements-head { flex-direction: column; }
  .snapshot-tag { align-self: flex-start; }
  .basis-actions { justify-content: flex-start; }
}

/* Stitch writing workspace */
.writer-shell { width: 100%; }
.writer-command-bar {
  position: sticky;
  top: 60px;
  z-index: 40;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 60px;
  margin: 0 calc(-1 * var(--page-gutter));
  padding: 10px var(--page-gutter);
  background: var(--surface-container-lowest);
  border-bottom: 1px solid var(--border);
  box-shadow: 0 1px 5px rgba(15, 23, 42, .04);
}
.writer-command-leading, .writer-command-actions, .writer-command-metrics { display: flex; align-items: center; gap: 10px; }
.writer-command-leading { min-width: 0; flex-shrink: 0; }
.writer-command-title { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.writer-command-title span { color: var(--on-surface-variant); font-size: 10px; }
.writer-command-title strong { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
.writer-command-metrics { color: var(--on-surface-variant); font-size: 11px; white-space: nowrap; }
.writer-command-metrics > span + span { padding-left: 10px; border-left: 1px solid var(--border); }
.writer-command-metrics b { color: var(--on-surface); font-family: "JetBrains Mono", monospace; font-weight: 600; }
.writer-save-status { display: inline-flex; align-items: center; gap: 6px; }
.save-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981; }
.writer-save-status.pending .save-dot { background: var(--tertiary); }
.writer-review-action, .writer-fix-action, .writer-import-action, .writer-preview-action, .writer-export-action { display: inline-flex; align-items: center; gap: 5px; }
.writer-preview-action { border: 1px solid var(--border); }
.export-preview-stats { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0; }
.export-preview-stats span { padding: 8px 10px; background: var(--background); color: var(--on-surface-variant); }
.export-preview-stats b { color: var(--on-surface); margin-left: 4px; }
.writer-review-action { color: var(--primary); border-color: var(--primary-fixed); background: var(--background); }
.writer-fix-action { color: #b45309; border-color: #fcd34d; background: #fffbeb; }
.writer-import-action { color: #0f766e; border-color: #99f6e4; background: #f0fdfa; }
.writer-command-bar .icon-action { flex-shrink: 0; }
.writer-command-actions { flex-shrink: 0; flex-wrap: wrap; }
.writer-command-bar button { white-space: nowrap; }

.writer-columns {
  display: grid;
  grid-template-columns: 225px minmax(0, 1fr) 300px;
  align-items: start;
  gap: 0;
  margin: 0 calc(-1 * var(--page-gutter)) -40px;
  min-height: calc(100vh - 120px);
  background: var(--background);
}
.writer-columns > .sidebar {
  width: auto;
  min-height: calc(100vh - 120px);
  max-height: calc(100vh - 120px);
  position: sticky;
  top: 120px;
  border: 0;
  border-right: 1px solid var(--border);
  border-radius: 0;
  padding: 20px 14px 18px;
  background: var(--surface-container-lowest);
  box-shadow: none;
}
.writer-columns > .sidebar .sidebar-head { padding: 0 6px 12px; border-bottom: 1px solid var(--border); }
.writer-columns > .sidebar .proj-title { font-size: 13px; }
.writer-columns > .sidebar .head-actions { width: 100%; margin-left: 0; }
.writer-columns > .sidebar .head-actions button { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.writer-columns > .sidebar .tree-nav-header { margin: 14px 6px 6px !important; }
.writer-columns > .sidebar .sidebar-foot { padding: 12px 6px 0; }

.editor-pane {
  min-width: 0;
  min-height: calc(100vh - 120px);
  padding: 28px clamp(24px, 5vw, 72px) 64px;
  background: var(--background);
}
.editor-pane > .error-banner, .editor-pane > .notice-banner, .editor-pane > .missing-panel { max-width: 840px; margin-left: auto; margin-right: auto; }
.editor-head { max-width: 840px; margin: 0 auto 12px; flex-wrap: wrap; align-items: flex-start; }
.editor-head .type-tag { flex-shrink: 0; white-space: nowrap; }
.editor-head .hint { flex: 1 1 180px; min-width: 0; }
.editor-head button { flex-shrink: 0; white-space: nowrap; }
.heading-input { display: block; max-width: 840px; margin: 0 auto 20px; background: var(--surface-container-lowest); border-radius: var(--radius-xs); }
.editor-pane > div:not(.error-banner):not(.notice-banner):not(.missing-panel) { max-width: 840px; margin-left: auto; margin-right: auto; }
.editor-pane > div > .para-block, .editor-pane > div > .section-text-editor { max-width: 840px; }
.editor-pane .para-block { border-radius: var(--radius-xs); box-shadow: 0 1px 2px rgba(15, 23, 42, .02); }

.writer-context {
  min-width: 0;
  min-height: calc(100vh - 120px);
  max-height: calc(100vh - 120px);
  overflow-y: auto;
  position: sticky;
  top: 120px;
  padding: 20px 14px 28px;
  border-left: 1px solid var(--border);
  background: var(--surface-container-lowest);
}
.context-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; padding: 0 4px 14px; border-bottom: 1px solid var(--border); }
.context-header h2 { font-size: 16px; font-weight: 650; }
.context-header-icon { color: var(--primary); font-size: 19px; }
.context-panel { margin: 12px 0 0; border-radius: var(--radius-xs); box-shadow: none; }
.context-panel.requirements-panel { padding: 12px; }
.context-panel .requirements-head { gap: 6px; }
.context-panel .requirements-head h3 { font-size: 13px; }
.context-panel .basis-actions { gap: 4px; }
.context-panel .snapshot-tag { padding: 2px 5px; font-size: 10px; }
.context-panel .requirements-sources { gap: 5px; }
.context-panel .source-chip { width: 100%; min-width: 0; padding: 4px 6px; font-size: 11px; }
.context-panel .source-chip span:last-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.context-panel .format-resolution { display: flex; align-items: center; gap: 4px; margin: 8px 0 0; color: var(--primary); font-size: 11px; }
.context-panel .format-resolution .material-symbols-outlined { font-size: 15px; }
.context-panel .requirements-foot { font-size: 11px; }
.context-panel.check-panel { padding: 14px; }
.context-panel .check-panel-kicker { font-size: 8px; }
.context-panel .check-list { max-height: none; overflow: visible; }
.context-panel .check-msg, .context-panel .not-checked-copy { font-size: 12px; }
.context-panel .check-item-actions { gap: 5px 6px; }
.context-panel .check-action-btn { padding-right: 7px; padding-left: 7px; }
.context-panel .waived-list, .context-panel .not-checked-list { font-size: 11px; }

@media (max-width: 1360px) {
  .writer-command-bar { flex-wrap: wrap; gap: 10px 18px; }
  .writer-command-leading { flex: 1; }
  .writer-command-actions { width: 100%; justify-content: flex-end; flex-wrap: wrap; }
  .writer-columns > .sidebar, .writer-context { position: static; }
}

@media (max-width: 1180px) {
  .writer-columns { grid-template-columns: 220px minmax(0, 1fr); }
  .writer-context { grid-column: 2; min-height: auto; max-height: none; position: static; border-left: 0; border-top: 1px solid var(--border); padding: 18px 24px 28px; }
  .writer-context .context-panel { max-width: 680px; }
}

@media (max-width: 768px) {
  .writer-command-bar { top: 60px; margin: 0 calc(-1 * var(--page-gutter)); padding: 9px 14px; gap: 8px; }
  .writer-command-leading { flex: 1; min-width: 0; }
  .writer-command-title strong { max-width: 180px; }
  .writer-command-metrics > span:not(.writer-save-status) { display: none; }
  .writer-command-actions { gap: 8px; justify-content: flex-start; }
  .writer-review-action span:last-child { display: none; }
  .writer-review-action { width: 34px; padding: 6px 8px; justify-content: center; }
  .writer-fix-action span:last-child { display: none; }
  .writer-fix-action { width: 34px; padding: 6px 8px; justify-content: center; }
  .writer-import-action span:last-child { display: none; }
  .writer-import-action { width: 34px; padding: 6px 8px; justify-content: center; }
  .writer-export-action { padding: 6px 10px; }
  .writer-columns { display: flex; flex-direction: column; margin: 0 calc(-1 * var(--page-gutter)) -40px; }
  .writer-columns > .sidebar { width: 100%; min-height: auto; max-height: none; position: static; border-right: 0; border-bottom: 1px solid var(--border); padding: 14px 16px; }
  .writer-columns > .sidebar .tree-nav { max-height: 220px; }
  .editor-pane { width: 100%; min-height: auto; padding: 22px 16px 40px; }
  .writer-context { width: 100%; padding: 16px; }
}

@media (max-width: 480px) {
  .writer-command-title strong { max-width: 150px; }
  .writer-command-actions > .small span:last-child,
  .writer-preview-action span:last-child,
  .writer-export-action span:last-child { display: none; }
  .writer-command-actions > button { min-width: 34px; min-height: 34px; padding: 6px 8px; justify-content: center; }
}
</style>
