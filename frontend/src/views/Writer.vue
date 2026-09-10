<template>
  <div v-if="loading" class="card loading-state">正在加载论文…</div>
  <div v-else-if="loadError" class="card">
    <div class="error-banner" role="alert">⚠ {{ loadError }}</div>
    <button @click="loadProject">重新加载</button>
    <button @click="$router.push('/')">返回论文列表</button>
  </div>
  <div v-else-if="project && template" class="writer-shell">
    <header class="writer-command-bar">
      <div class="writer-command-leading">
        <button class="icon-action" title="返回我的论文" aria-label="返回我的论文" @click="router.push('/')">
          <span class="material-symbols-outlined">arrow_back</span>
        </button>
        <div class="writer-command-title">
          <span>论文写作</span>
          <strong :title="project.title || '未命名论文'">{{ project.title || '未命名论文' }}</strong>
        </div>
      </div>
      <div class="writer-command-metrics">
        <span class="writer-save-status" :class="{ pending: saving || dirtyCount }">
          <span class="save-dot"></span>
          <span v-if="saving">保存中…</span>
          <span v-else-if="dirtyCount">有 {{ dirtyCount }} 项待保存</span>
          <span v-else>已保存 {{ savedAt || '刚刚' }}</span>
        </span>
        <span><b>{{ totalChars }}</b> 字</span>
        <span><b>{{ doneCount }}/{{ requiredCount }}</b> 节完成</span>
      </div>
      <div class="writer-command-actions">
        <button class="writer-review-action" @click="checkOpen = !checkOpen">
          <span class="material-symbols-outlined">rule</span>
          <span>检查</span>
        </button>
        <button class="writer-fix-action" :disabled="fixing || checking || exporting || loading" @click="runOneClickFix"
                title="修复检查结果中可自动处理的警告和错误">
          <span class="material-symbols-outlined">build</span>
          <span>{{ fixing ? '修复中…' : '一键修复' }}</span>
        </button>
        <button class="primary writer-export-action" @click="exportDocx(false)" :disabled="exporting || saving || dirtyCount > 0">
          <span class="material-symbols-outlined">download</span>
          <span>{{ exporting ? '导出中…' : '导出' }}</span>
        </button>
      </div>
    </header>

    <div class="writer-columns">
    <!-- 左侧章节导航 -->
    <aside class="sidebar">
      <div class="sidebar-head">
        <span class="proj-title" :title="project.title || '未命名论文'">{{ project.title || '未命名论文' }}</span>
        <span class="head-actions">
          <button class="small" @click="openAiSettings" title="AI 设置">✨AI</button>
          <button v-if="isManager()" class="small" @click="openTemplateModal"
                  :disabled="saving" title="把本项目换成其它模板的排版规范">🔀 换模板</button>
        </span>
      </div>
      <div class="tree-nav-header" style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px; margin-bottom: 6px;">
        <span style="font-size: 12px; font-weight: 600; color: var(--muted)">章节大纲</span>
        <button v-if="canEditStructure" class="tiny" @click="openAddSectionModal" title="添加新章节">
          ➕ 添加章节
        </button>
        <span v-else class="tiny" style="font-size: 11px; color: var(--muted); opacity: 0.8;" title="导师已锁定论文框架">
          🔒 框架锁定
        </span>
      </div>
      <nav class="tree-nav">
        <div v-for="node in displaySections" :key="node.key"
             class="nav-row" :class="{ active: current === node.key }"
             :style="{ paddingLeft: (8 + node.depth * 14) + 'px' }" @click="select(node)">
          <span v-if="isContentType(node.type) && node.key !== 'cover'">✍️</span>
          <span v-else-if="node.type === 'cover'">📋</span>
          <span class="nav-title">{{ label(node) }}</span>
          <span v-if="isRequired(node) && !isDone(node)" class="dot" title="未完成">●</span>
          <span v-if="canEditStructure" class="nav-action-move">
            <button class="nav-move-btn" :disabled="structureBusy || isFirstSection(node)" @click.stop="moveSection(node, -1)" title="上移章节" :aria-label="`上移章节「${label(node)}」`">↑</button>
            <button class="nav-move-btn" :disabled="structureBusy || isLastSection(node)" @click.stop="moveSection(node, 1)" title="下移章节" :aria-label="`下移章节「${label(node)}」`">↓</button>
          </span>
          <span v-if="canEditStructure && isCustomSection(node)" class="nav-action-del" @click.stop="removeSection(node)" title="删除此章节">×</span>
        </div>
      </nav>
      <div class="sidebar-foot">
        <span v-if="saving">保存中…</span>
        <span v-else-if="dirtyCount" style="color: var(--warning); font-size: 12px">有 {{ dirtyCount }} 项待保存</span>
        <span v-else-if="savedAt" style="color: var(--muted); font-size: 12px">已保存 {{ savedAt }}</span>
        <button class="small" @click="$router.push('/')">← 返回</button>
      </div>

      <!-- 字数与进度 -->
      <div class="stats-panel">
        <div class="progress-row">
          <span class="stats-label">完成度</span>
          <span class="stats-value">{{ doneCount }}/{{ requiredCount }}</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
        </div>
        <div class="stats-row">
          <span>总字数 <b>{{ totalChars }}</b></span>
          <span>本章 <b>{{ sectionChars }}</b></span>
        </div>
      </div>
    </aside>

    <!-- 右侧编辑区 -->
    <section class="editor-pane">
      <div v-if="error" class="error-banner" role="alert">⚠ {{ error }}</div>
      <div v-if="templateNotice" class="notice-banner">✅ {{ templateNotice }}</div>
      <div v-if="fixNotice" class="notice-banner" role="status">{{ fixNotice }}</div>

      <!-- 导出缺失清单 -->
      <div v-if="missing.length" class="missing-panel">
        <b>⚠ 以下必填项未完成, 无法导出:</b>
        <ul>
          <li v-for="(m, i) in missing" :key="i">{{ m }}</li>
        </ul>
        <div class="form-row" style="margin-top: 8px">
          <button @click="exportDocx(true)" :disabled="exporting">仍要导出(跳过检查)</button>
          <button @click="missing = []">知道了</button>
        </div>
      </div>

      <Teleport to="body">
      <div v-if="waiverTarget" class="modal-mask writer-modal-mask" @click.self="waiverTarget = null">
        <div class="modal card">
          <h3>申请规范豁免</h3>
          <p class="hint">{{ waiverTarget.message }}</p>
          <label class="field">申请理由<textarea v-model="waiverReason" rows="4" placeholder="说明特殊情况和希望豁免的原因"></textarea></label>
          <div class="form-row"><button class="primary" :disabled="waiverBusy || !waiverReason.trim()" @click="submitWaiver">{{ waiverBusy ? '提交中…' : '提交申请' }}</button><button @click="waiverTarget = null">取消</button></div>
        </div>
      </div>

      <div v-if="disputeTarget" class="modal-mask writer-modal-mask" @click.self="disputeTarget = null">
        <div class="modal card">
          <h3>提交规范申诉</h3>
          <p class="hint">{{ disputeTarget.message }}</p>
          <label class="field">申诉理由<textarea v-model="disputeReason" rows="4" placeholder="说明规范不适用、事实依据或需要人工复核的原因"></textarea></label>
          <div class="form-row"><button class="primary" :disabled="disputeBusy || !disputeReason.trim()" @click="submitDispute">{{ disputeBusy ? '提交中…' : '提交申诉' }}</button><button @click="disputeTarget = null">取消</button></div>
        </div>
      </div>

      <div v-if="upgradePreview" class="modal-mask writer-modal-mask" @click.self="upgradePreview = null">
        <div class="modal card upgrade-modal">
          <h3>规范版本更新</h3>
          <p class="hint">当前版本 {{ upgradePreview.from_snapshot_hash?.slice(0, 18) }}…，可升级到 {{ upgradePreview.to_snapshot_hash?.slice(0, 18) }}…。</p>
          <p v-if="!upgradePreview.diff.changed" class="ok-banner">当前项目已经是最新规范版本。</p>
          <div v-else class="upgrade-summary"><p>新增规范 {{ upgradePreview.diff.added_sets.length }} 套，移除 {{ upgradePreview.diff.removed_sets.length }} 套，变更 {{ upgradePreview.diff.changed_sets.length }} 套。</p><p>新增要求 {{ upgradePreview.diff.added_items.length }} 条，移除 {{ upgradePreview.diff.removed_items.length }} 条。</p></div>
          <div class="form-row"><button v-if="upgradePreview.diff.changed" class="primary" :disabled="upgradeBusy" @click="applyUpgrade">应用到本项目</button><button @click="upgradePreview = null">关闭</button></div>
        </div>
      </div>

      <div v-if="upgradeConfirm" class="modal-mask writer-modal-mask" @click.self="upgradeConfirm = false">
        <div class="modal card">
          <h3>确认更新规范</h3>
          <p class="hint">应用后，项目后续检查将使用最新规范版本；已有论文内容不会被修改。</p>
          <div class="form-row"><button class="primary" :disabled="upgradeBusy" @click="confirmUpgrade">确认升级</button><button @click="upgradeConfirm = false">取消</button></div>
        </div>
      </div>

      <div v-if="assistResult" class="modal-mask writer-modal-mask" @click.self="assistResult = null">
        <div class="modal card"><h3>AI 人工检查辅助</h3><p class="hint">AI 结果只提供证据线索，不会改变自动检查结果。</p><div class="assist-result">{{ assistResult.suggestion }}</div><div class="form-row"><button @click="assistResult = null">关闭</button></div></div>
      </div>
      </Teleport>

      <!-- 封面 -->
      <div v-if="currentSection && currentSection.type === 'cover'">
        <h2>📋 封面信息</h2>
        <p style="color: var(--muted); font-size: 14px; margin-bottom: 16px">填写封面字段, 导出时自动排版。</p>
        <div v-for="f in metaFields" :key="f.key" class="meta-row">
          <label>{{ f.label }}</label>
          <input :value="project.meta[f.key]" @input="onMetaInput(f.key, $event.target.value)"
                 :placeholder="f.required ? '必填' : '选填'" />
        </div>
        <button class="primary" @click="saveMeta" :disabled="saving">💾 {{ saving ? '保存中…' : '保存封面信息' }}</button>
      </div>

      <!-- 标题节: 标题 + 内容块 -->
      <div v-else-if="currentSection && isHeading(currentSection.type)">
        <div class="editor-head">
          <span class="type-tag">{{ typeLabel(currentSection.type) }}</span>
          <span class="hint">标题排版按规范优先生效, 下方为本章正文(可插图片/表格)</span>
          <button class="small" @click="toggleSectionInputMode">
            {{ sectionInputMode === 'blocks' ? '整节输入' : '逐段编辑' }}
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

      <!-- 正文节: 内容块编辑器 -->
      <div v-else-if="currentSection">
        <div class="editor-head">
          <span class="type-tag">{{ typeLabel(currentSection.type) }}</span>
          <span class="hint">字体/字号/行距/缩进按规范优先生效, 可插图片/表格</span>
          <button class="small" @click="toggleSectionInputMode">
            {{ sectionInputMode === 'blocks' ? '整节输入' : '逐段编辑' }}
          </button>
          <button v-if="aiEnabled && isAiSection(currentSection.type)" class="small ai-gen"
                  :disabled="!!aiBusy || aiGenerating" @click="aiGenerate">
            {{ aiGenerating ? '✨ AI 生成中…' : '✨ AI 生成' }}
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

    <!-- 右侧上下文检查栏：写作依据和格式问题随时可查，但不打断正文 -->
    <aside class="writer-context">
      <div class="context-header">
        <div>
          <p class="writing-eyebrow">WRITING CONTEXT</p>
          <h2>写作辅助</h2>
        </div>
        <span class="context-header-icon material-symbols-outlined">tune</span>
      </div>

      <div v-if="requirementSnapshot" class="requirements-panel context-panel">
        <div class="requirements-head">
          <div>
            <span class="eyebrow">PROJECT BASIS</span>
            <h3>当前写作依据</h3>
          </div>
          <div class="basis-actions">
            <span class="snapshot-tag">v{{ template.version || requirementSnapshot.template?.version || '1' }}</span>
            <button v-if="!hasAcknowledged" class="tiny" @click="acknowledgeBasis">确认已读</button>
            <button v-if="isManager()" class="tiny" :disabled="upgradeBusy" @click="openUpgradePreview">更新</button>
          </div>
        </div>
        <div class="requirements-sources">
          <div class="source-chip template-chip">
            <span class="material-symbols-outlined">description</span>
            <span>模板 · {{ template.name }}</span>
          </div>
          <div v-for="source in requirementSets" :key="source.id" class="source-chip">
            <span class="material-symbols-outlined">rule</span>
            <span>{{ source.name }} · v{{ source.version }}</span>
          </div>
          <div v-for="packId in (requirementSnapshot.rule_packs || [])" :key="packId" class="source-chip">
            <span class="material-symbols-outlined">format_align_left</span>
            <span>格式规则 · {{ packId }}</span>
          </div>
        </div>
        <p v-if="requirementSnapshot.format_override_count" class="format-resolution">
          <span class="material-symbols-outlined">verified</span>
          规范优先 · 已应用 {{ requirementSnapshot.format_override_count }} 项排版约束
        </p>
        <p class="requirements-foot">自动检查 {{ autoRequirementCount }} 项<span v-if="checkResult"> · 未自动判定 {{ checkResult.summary.not_checked || 0 }} 项</span></p>
      </div>

      <div v-if="isManager() && pendingWaivers.length" class="waiver-review-panel context-panel">
        <div class="requirements-head"><div><span class="eyebrow">REVIEW QUEUE</span><h3>待审核豁免</h3></div><span class="snapshot-tag">{{ pendingWaivers.length }} 条</span></div>
        <div v-for="waiver in pendingWaivers" :key="waiver.id" class="waiver-review-row"><span>豁免 · {{ waiver.requirement_item_id }} · {{ waiver.reason }}</span><span><button class="tiny" @click="decideWaiver(waiver, 'approved')">同意</button><button class="tiny danger" @click="decideWaiver(waiver, 'rejected')">驳回</button></span></div>
      </div>
      <div v-if="isManager() && pendingDisputes.length" class="waiver-review-panel dispute-review-panel context-panel">
        <div class="requirements-head"><div><span class="eyebrow">DISPUTE QUEUE</span><h3>待处理申诉</h3></div><span class="snapshot-tag">{{ pendingDisputes.length }} 条</span></div>
        <div v-for="dispute in pendingDisputes" :key="dispute.id" class="waiver-review-row"><span>申诉 · {{ dispute.requirement_item_id }} · {{ dispute.reason }}</span><span><button class="tiny" @click="decideDispute(dispute, 'accepted')">接受</button><button class="tiny danger" @click="decideDispute(dispute, 'rejected')">驳回</button></span></div>
      </div>

      <div v-if="checking || checkResult || checkError" class="check-panel context-panel" :class="{ clean: checkResult && checkResult.summary.total === 0 && checkResult.summary.not_checked === 0 && checkResult.summary.waived === 0 && !(requirementSnapshot?.conflicts?.length) }">
        <div class="context-section-title">
          <span><span class="material-symbols-outlined">fact_check</span>规范检查</span>
          <button class="tiny" :disabled="checking" @click="runCheckNow">重新检查</button>
        </div>
        <div class="check-head" @click="checkOpen = !checkOpen">
          <span v-if="checking">⏳ 检查中…</span>
          <span v-else-if="checkError">⚠ 检查失败: {{ checkError }}</span>
          <span v-else-if="checkStale">⚠ 内容已变化, 检查结果可能过期</span>
          <span v-else-if="checkResult.context && checkResult.summary.total === 0 && checkResult.summary.not_checked === 0 && checkResult.summary.waived === 0 && !(requirementSnapshot?.conflicts?.length)">✅ 当前检查通过</span>
          <span v-else>⚠ {{ checkResult.summary.total }} 个问题 · {{ checkResult.summary.not_checked || 0 }} 项未检查</span>
          <span class="material-symbols-outlined context-chevron">{{ checkOpen ? 'expand_less' : 'expand_more' }}</span>
        </div>
        <div v-if="checkOpen && checkResult && checkResult.findings.length" class="check-list">
          <div v-for="(f, i) in checkResult.findings" :key="f.rule_id + ':' + (f.section_key || '') + ':' + i" class="check-item"
               :class="'severity-' + f.severity" @click="jumpTo(f)">
            <span class="badge" :class="f.severity">{{ { error: '错', warning: '警', info: '提' }[f.severity] }}</span>
            <span class="check-msg">{{ f.message }}</span>
            <span class="check-sec" v-if="f.section_key || f.location?.section_key">{{ sectionTitle(f.section_key || f.location.section_key) }} →</span>
            <template v-if="f.source_type === 'requirement'"><button class="tiny waiver-btn" @click.stop="openWaiver(f)">申请豁免</button><button class="tiny dispute-btn" @click.stop="openDispute(f)">申诉</button></template>
          </div>
        </div>
        <div v-if="checkOpen && checkResult && checkResult.waived?.length" class="waived-list">
          <strong>已豁免</strong>
          <span v-for="item in checkResult.waived" :key="item.rule_id">{{ item.message }} · {{ item.waiver?.reason }}</span>
        </div>
        <div v-if="checkOpen && checkResult && checkResult.not_checked?.length" class="not-checked-list">
          <strong>未自动检查项</strong>
          <span v-for="item in checkResult.not_checked" :key="item.id" class="not-checked-item">
            <span>{{ item.description }} · {{ item.source }}</span>
            <button v-if="item.type === 'manual'" class="tiny" @click.stop="openAssist(item)">AI 辅助</button>
          </span>
        </div>
      </div>
    </aside>

      <Teleport to="body">
      <!-- AI 对照选择弹窗 -->
      <div v-if="aiDiff" class="modal-mask writer-modal-mask" @click.self="cancelAiDiff">
        <div class="modal card diff-modal">
          <h3 style="margin-top:0; display:flex; align-items:center; gap:8px">
            <span>{{ aiDiff.action === 'continue' ? '▶ AI 续写对照' : '✨ AI 润色对照' }}</span>
            <span class="type-tag" style="font-size:12px; font-weight:normal">
              第 {{ aiDiff.index + 1 }} 块
            </span>
          </h3>
          <p class="hint" style="margin-top:0">
            请审阅 AI 生成的结果。确认满意后可点击替换或追加，不满意可直接放弃保持原状。
          </p>

          <div class="diff-grid">
            <div class="diff-pane">
              <div class="diff-label">【原段落内容】</div>
              <div class="diff-box original">{{ aiDiff.original }}</div>
            </div>
            <div class="diff-pane">
              <div class="diff-label">
                {{ aiDiff.action === 'continue' ? '【AI 续写新内容】' : '【AI 润色后内容】' }}
              </div>
              <div class="diff-box generated">{{ aiDiff.generated }}</div>
            </div>
          </div>

          <div v-if="aiDiff.action === 'continue'" class="diff-preview-pane">
            <div class="diff-label">【合并预览效果】</div>
            <div class="diff-box preview">{{ aiDiff.preview }}</div>
          </div>

          <div class="form-row" style="margin-top:16px; justify-content:flex-end">
            <button class="primary" @click="applyAiDiff">
              {{ aiDiff.action === 'continue' ? '✔ 采纳并追加' : '✔ 采纳并替换' }}
            </button>
            <button @click="cancelAiDiff">✖ 放弃 (保留原样)</button>
          </div>
        </div>
      </div>

      <!-- AI 设置弹窗 -->
      <div v-if="showAiSettings" class="modal-mask writer-modal-mask" @click.self="showAiSettings = false">
        <div class="modal card">
          <h3 style="margin-top:0">✨ AI 设置</h3>
          <div v-if="isAdmin" class="ai-tab-row">
            <button class="ai-tab-btn" :class="{ active: aiTab === 'user' }" @click="switchAiTab('user')">
              个人模型 (仅本人生效)
            </button>
            <button class="ai-tab-btn" :class="{ active: aiTab === 'admin' }" @click="switchAiTab('admin')">
              ⚙️ 全站默认模型 (管理员)
            </button>
          </div>
          <p class="hint" style="font-size: 13px; margin-top: 4px">
            <template v-if="aiTab === 'user'">
              配置自己的模型, 支持 OpenAI 兼容接口和 MiniMax Anthropic 接口, 仅对本账号生效, 优先于系统默认。
              <br />
              <template v-if="aiEnabled">
                当前: {{ aiSource === 'user' ? '自有模型' : '系统默认模型' }} ({{ aiModel }})
              </template>
              <template v-else>当前 AI 功能未启用</template>
            </template>
            <template v-else>
              配置全站默认模型, 支持 OpenAI 兼容接口和 MiniMax Anthropic 接口, 全体未自带 Key 的用户将默认使用该配置。
              <br />
              <template v-if="adminAiInfo.configured">
                当前默认来源: {{ adminAiInfo.source === 'database' ? '数据库 (管理员自定义)' : '环境变量 (PAPERGUARD_AI_*)' }}
              </template>
              <template v-else>当前全站未配置默认模型</template>
            </template>
          </p>

          <!-- 个人模型表单 -->
          <template v-if="aiTab === 'user'">
            <label class="field">Base URL
              <input v-model="aiCfgForm.base_url" placeholder="如 https://api.openai.com/v1 或 https://api.minimax.cn/anthropic" />
            </label>
            <label class="field">API Key
              <input v-model="aiCfgForm.api_key" type="password" autocomplete="new-password"
                     :placeholder="aiCfgInfo.configured ? '已保存 ' + aiCfgInfo.api_key_masked + ' (留空则沿用)' : '必填, 如 sk-xxx'" />
            </label>
            <label class="field">模型
              <input v-model="aiCfgForm.model" placeholder="如 gpt-4o-mini / MiniMax-M2.7" />
            </label>
            <p v-if="aiCfgInfo.message" :class="aiCfgInfo.ok ? 'ok-banner' : 'error-banner'" style="font-size: 13px">
              {{ aiCfgInfo.ok ? '✅' : '⚠' }} {{ aiCfgInfo.message }}
            </p>
            <div class="form-row">
              <button class="primary" :disabled="aiCfgInfo.saving" @click="saveAiCfg">
                {{ aiCfgInfo.saving ? '保存中…' : '保存自有配置' }}
              </button>
              <button :disabled="aiCfgInfo.testing" @click="testAiConnection">
                {{ aiCfgInfo.testing ? '测试中…' : '测试连接' }}
              </button>
              <button v-if="aiCfgInfo.configured" @click="clearAiCfg">清除自有配置</button>
              <button @click="showAiSettings = false">关闭</button>
            </div>
          </template>

          <!-- 管理员全站默认模型表单 -->
          <template v-else>
            <label class="field">Base URL
              <input v-model="adminAiForm.base_url" placeholder="如 https://api.openai.com/v1 或 https://api.minimax.cn/anthropic" />
            </label>
            <label class="field">API Key
              <input v-model="adminAiForm.api_key" type="password" autocomplete="new-password"
                     :placeholder="adminAiInfo.configured ? '已配置 ' + adminAiInfo.api_key_masked + ' (留空则沿用)' : '必填, 如 sk-xxx'" />
            </label>
            <label class="field">模型
              <input v-model="adminAiForm.model" placeholder="如 gpt-4o-mini / MiniMax-M2.7" />
            </label>
            <p v-if="adminAiInfo.message" :class="adminAiInfo.ok ? 'ok-banner' : 'error-banner'" style="font-size: 13px">
              {{ adminAiInfo.ok ? '✅' : '⚠' }} {{ adminAiInfo.message }}
            </p>
            <div class="form-row">
              <button class="primary" :disabled="adminAiInfo.saving" @click="saveAdminAiCfg">
                {{ adminAiInfo.saving ? '保存中…' : '保存全站默认' }}
              </button>
              <button :disabled="adminAiInfo.testing" @click="testAdminAiConnection">
                {{ adminAiInfo.testing ? '测试中…' : '测试连接' }}
              </button>
              <button v-if="adminAiInfo.source === 'database'" @click="clearAdminAiCfg">恢复环境变量默认值</button>
              <button @click="showAiSettings = false">关闭</button>
            </div>
          </template>
        </div>
      </div>

      <!-- 更换模板弹窗 -->
      <div v-if="showTemplateModal" class="modal-mask writer-modal-mask" @click.self="showTemplateModal = false">
        <div class="modal card">
          <h3 style="margin-top: 0">🔀 更换模板</h3>
          <p class="hint" style="margin-top: 0">
            换模板只改字体/行距/封面等排版规范，已写内容按章节保留。
          </p>
          <div v-if="templateError" class="error-banner">⚠ {{ templateError }}</div>
          <div v-if="templateLoading" style="color: var(--muted)">⏳ 正在加载模板…</div>
          <div v-else-if="!templateList.length" style="color: var(--muted)">暂无可用模板</div>
          <div v-for="t in templateList" :key="t.id" class="template-row"
               :class="{ current: t.id === project.template_id }">
            <div class="template-meta">
              <b>{{ t.name }}</b>
              <span v-if="t.id === project.template_id" class="tag">当前</span>
              <div class="template-desc">
                {{ t.description || '无描述' }} · {{ t.section_count }} 个章节
              </div>
            </div>
            <button class="small primary" :disabled="!!templateBusy"
                    @click="applyTemplate(t.id)">
              {{ templateBusy === t.id ? '应用中…' : (t.id === project.template_id ? '重新套用' : '套用') }}
            </button>
          </div>
          <div class="form-row" style="margin-top: 14px">
            <button @click="showTemplateModal = false">关闭</button>
          </div>
        </div>
      </div>

      <!-- 添加章节弹窗 -->
      <div v-if="showAddSectionModal" class="modal-mask writer-modal-mask" @click.self="showAddSectionModal = false">
        <div class="modal card">
          <h3 style="margin-top: 0">➕ 添加新章节</h3>
          <p class="hint" style="margin-top: 0">导师已允许自定义增删章节框架。请选择章节名称、类型和插入位置：</p>
          <label class="field">章节标题
            <input v-model="newSectionForm.title" placeholder="如 第六章 结论与建议 或 1.4 本章小结" />
          </label>
          <label class="field">章节类型
            <select v-model="newSectionForm.type" style="width: 100%; padding: 8px; border: 1px solid var(--border); border-radius: 8px;">
              <option value="heading1">一级标题 (章)</option>
              <option value="heading2">二级标题 (节)</option>
              <option value="heading3">三级标题 (小节)</option>
              <option value="body">正文容器</option>
              <option value="appendix">附录</option>
            </select>
          </label>
          <label class="field">插入位置
            <select v-model="newSectionForm.position" class="structure-select">
              <option value="start">目录开头</option>
              <option v-for="node in displaySections" :key="`before-${node.key}`" :value="`before:${node.key}`">
                在「{{ label(node) }}」之前
              </option>
              <option v-for="node in displaySections" :key="`after-${node.key}`" :value="`after:${node.key}`">
                在「{{ label(node) }}」之后
              </option>
              <option value="end">目录末尾</option>
            </select>
          </label>
          <div class="form-row" style="margin-top: 16px;">
            <button class="primary" :disabled="!newSectionForm.title.trim() || structureBusy" @click="confirmAddSection">
              {{ structureBusy ? '保存中…' : '确认添加' }}
            </button>
            <button @click="showAddSectionModal = false">取消</button>
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
  fetchProject, fetchTemplate, fetchTemplates, updateProjectSection, updateProjectMeta, exportProject,
  checkProject, fixProject, updateProjectSectionsStructure, refreshProjectTemplate, switchProjectTemplate,
  acknowledgeRequirements, assistRequirement, listRequirementWaivers, listRequirementDisputes, previewRequirementUpgrade,
  requestRequirementWaiver, requestRequirementDispute, upgradeRequirements, decideRequirementWaiver, decideRequirementDispute,
  aiStatus, getAiConfig, saveAiConfig, clearAiConfig, testAiConfig, aiAssist,
  getAdminAiConfig, saveAdminAiConfig, clearAdminAiConfig, testAdminAiConfig,
} from '../api'
import BlockList from '../components/BlockList.vue'
import SectionTextEditor from '../components/SectionTextEditor.vue'
import { createTextBlocks, mergeTextBlocksWithMedia, textBlocksToSectionText } from '../utils/paragraphSplitter.js'

const route = useRoute()
const router = useRouter()
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
const showTemplateModal = ref(false)
const templateList = ref([])
const templateLoading = ref(false)
const templateBusy = ref('')
const templateError = ref('')
const templateNotice = ref('')
const fixNotice = ref('')
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
const disputeTarget = ref(null); const disputeReason = ref(''); const disputeBusy = ref(false)
const upgradePreview = ref(null); const upgradeBusy = ref(false); const upgradeConfirm = ref(false); const assistResult = ref(null)
const waivers = ref([])
const disputes = ref([])
const pendingWaivers = computed(() => waivers.value.filter((item) => item.status === 'pending'))
const pendingDisputes = computed(() => disputes.value.filter((item) => item.status === 'pending'))

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
  if (!project.value || !project.value.sections) return flatTemplateSections.value
  const tplMap = new Map()
  for (const item of flatTemplateSections.value) {
    tplMap.set(item.key, item)
  }
  return project.value.sections.map((sec) => {
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
})

const showAddSectionModal = ref(false)
const structureBusy = ref(false)
const newSectionForm = reactive({ title: '', type: 'heading2', position: 'end' })
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
    error.value = e.message || '添加章节失败'
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
    error.value = e.message || '调整章节位置失败'
  } finally {
    structureBusy.value = false
  }
}

async function removeSection(node) {
  if (!confirm(`确定要删除章节「${node.title || node.key}」及其正文内容吗？`)) return
  if (structureBusy.value) return
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
    error.value = e.message || '删除章节失败'
  } finally {
    structureBusy.value = false
  }
}

const currentSection = computed(() => {
  if (!project.value) return null
  if (current.value === 'cover') return { type: 'cover', key: 'cover', title: '封面' }
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
    aiCfgInfo.message = e.message || '读取个人 AI 配置失败'
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
    adminAiInfo.message = e.message || '读取全站 AI 配置失败'
  }
}

async function openAiSettings() {
  showAiSettings.value = true
  aiTab.value = 'user'
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
      ? '请填写 Base URL / 模型；API Key 留空即可沿用已保存密钥'
      : '请先完整填写 Base URL / API Key / 模型'
    return
  }
  adminAiInfo.testing = true
  adminAiInfo.message = ''
  try {
    const res = await testAdminAiConfig({ ...adminAiForm })
    adminAiInfo.ok = res.ok
    adminAiInfo.message = res.ok ? `测试成功 (模型: ${res.model})` : (res.error || '测试失败')
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || '测试失败'
  } finally {
    adminAiInfo.testing = false
  }
}

async function saveAdminAiCfg() {
  if (!adminAiFilled()) {
    adminAiInfo.ok = false
    adminAiInfo.message = adminAiInfo.configured
      ? '请填写 Base URL / 模型；API Key 留空即可沿用已保存密钥'
      : '请先完整填写 Base URL / API Key / 模型'
    return
  }
  adminAiInfo.saving = true
  adminAiInfo.message = ''
  try {
    await saveAdminAiConfig({ ...adminAiForm })
    adminAiInfo.configured = true
    adminAiInfo.source = 'database'
    adminAiInfo.ok = true
    adminAiInfo.message = '已保存为全站默认模型！'
    await loadAiStatus()
    await loadAdminAiCfg()
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || '保存全站配置失败'
  } finally {
    adminAiInfo.saving = false
  }
}

async function clearAdminAiCfg() {
  try {
    await clearAdminAiConfig()
    adminAiInfo.ok = true
    adminAiInfo.message = '已恢复系统默认设置'
    await loadAiStatus()
    await loadAdminAiCfg()
  } catch (e) {
    adminAiInfo.ok = false
    adminAiInfo.message = e.message || '清除失败'
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
    error.value = '当前块为空, 请先输入一些内容再使用 AI'
    return
  }
  aiBusy.value = { index, action }
  error.value = ''
  try {
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
    error.value = e.message || 'AI 调用失败'
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
    error.value = '正文内容太少, 请先撰写正文章节, 再回到本页生成'
    return
  }
  aiGenerating.value = true
  error.value = ''
  try {
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
    error.value = e.message || 'AI 生成失败'
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
      ? '请填写 Base URL / 模型；API Key 留空即可沿用已保存密钥'
      : '请先填写完整 Base URL / API Key / 模型'
    return
  }
  aiCfgInfo.testing = true
  aiCfgInfo.message = ''
  try {
    const r = await testAiConfig({ ...aiCfgForm })
    aiCfgInfo.ok = r.ok
    aiCfgInfo.message = r.ok ? `连接成功 (模型: ${r.model})` : (r.error || '连接失败')
  } catch (e) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = e.message || '测试失败'
  } finally {
    aiCfgInfo.testing = false
  }
}

async function saveAiCfg() {
  if (!aiCfgFilled()) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = aiCfgInfo.configured
      ? '请填写 Base URL / 模型；API Key 留空即可沿用已保存密钥'
      : '请填写完整 Base URL / API Key / 模型'
    return
  }
  aiCfgInfo.saving = true
  aiCfgInfo.message = ''
  try {
    await saveAiConfig({ ...aiCfgForm })
    aiCfgInfo.configured = true
    aiCfgInfo.ok = true
    aiCfgInfo.message = '已保存, 现在使用你自己的模型'
    await loadAiStatus()
  } catch (e) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = e.message || '保存失败'
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
    aiCfgInfo.message = '已清除自有配置, 恢复使用系统默认模型(若已配置)'
  } catch (e) {
    aiCfgInfo.ok = false
    aiCfgInfo.message = e.message || '清除失败'
  }
}

async function loadProject() {
  loading.value = true
  loadError.value = ''
  error.value = ''
  try {
    const p = await fetchProject(projectId)
    project.value = p
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
    loadError.value = e.message || '论文加载失败'
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
    cover: '封面', abstract: '中文摘要', english_abstract: '英文摘要', keywords: '中文关键词', english_keywords: '英文关键词', toc: '目录', heading1: '一级标题', heading2: '二级标题',
    heading3: '三级标题', body: '正文', references: '参考文献', acknowledgement: '致谢', appendix: '附录',
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
      error.value = e.message || '保存章节失败'
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
      error.value = e.message || '保存封面信息失败'
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
  promise.finally(() => inFlightSaves.delete(promise))
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

// ---------- 实时检查 ----------
async function runCheckNow() {
  if (!project.value || loading.value || checking.value || fixing.value) return
  const generation = ++checkGeneration
  try {
    await flushPendingSaves()
  } catch {
    checkError.value = '内容尚未保存, 请先解决保存冲突'
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
    if (generation === checkGeneration) checkError.value = e.message || '检查失败'
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
      fixNotice.value = `已一键修复 ${result.fixed_count} 项警告/错误，并完成复检。`
    } else if (result.report?.findings?.length) {
      fixNotice.value = '当前警告/错误无法自动确定修复内容，请按右侧检查结果处理。'
    } else {
      fixNotice.value = '当前没有需要修复的警告或错误。'
    }
  } catch (e) {
    error.value = e.message || '一键修复失败'
  } finally {
    fixing.value = false
  }
}

async function acknowledgeBasis() {
  try { const result = await acknowledgeRequirements(projectId, requirementSnapshot.value?.content_hash); project.value.requirement_acknowledgements = [...(project.value.requirement_acknowledgements || []).filter((item) => item.user_id !== auth.user?.id), result] }
  catch (e) { error.value = e.message || '确认规范失败' }
}

function openWaiver(finding) { waiverTarget.value = finding; waiverReason.value = '' }
function openDispute(finding) { disputeTarget.value = finding; disputeReason.value = '' }
async function submitWaiver() {
  waiverBusy.value = true
  try {
    await requestRequirementWaiver(projectId, waiverTarget.value.requirement_item_id, { reason: waiverReason.value })
    waiverTarget.value = null; await runCheckNow()
  } catch (e) { error.value = e.message || '提交豁免申请失败' }
  finally { waiverBusy.value = false }
}

async function loadWaivers() {
  try { waivers.value = await listRequirementWaivers(projectId) } catch { waivers.value = [] }
  try { disputes.value = await listRequirementDisputes(projectId) } catch { disputes.value = [] }
}
async function decideWaiver(waiver, status) {
  try {
    await decideRequirementWaiver(projectId, waiver.id, { status, review_note: status === 'approved' ? '审核通过' : '请补充说明后重新申请' })
    await loadWaivers(); await runCheckNow()
  } catch (e) { error.value = e.message || '审核豁免失败' }
}
async function submitDispute() {
  disputeBusy.value = true
  try {
    await requestRequirementDispute(projectId, disputeTarget.value.requirement_item_id, { reason: disputeReason.value })
    disputeTarget.value = null; await loadWaivers()
  } catch (e) { error.value = e.message || '提交申诉失败' }
  finally { disputeBusy.value = false }
}
async function decideDispute(dispute, status) {
  try {
    await decideRequirementDispute(projectId, dispute.id, { status, review_note: status === 'accepted' ? '申诉已接受，请按申诉结论人工处理' : '申诉驳回' })
    await loadWaivers()
  } catch (e) { error.value = e.message || '审核申诉失败' }
}

async function openUpgradePreview() {
  upgradeBusy.value = true
  try { upgradePreview.value = await previewRequirementUpgrade(projectId) }
  catch (e) { error.value = e.message || '获取规范升级预览失败' }
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
    const result = await upgradeRequirements(projectId, { expected_snapshot_hash: upgradePreview.value.from_snapshot_hash, reason: '项目主动升级规范', confirm: true })
    project.value = result.project; upgradePreview.value = null; await runCheckNow()
  } catch (e) { error.value = e.message || '应用规范升级失败' }
  finally { upgradeBusy.value = false }
}
async function openAssist(item) {
  try { assistResult.value = await assistRequirement(projectId, item.id, item.description) }
  catch (e) { error.value = e.message || 'AI 辅助检查失败' }
}

function jumpTo(f) { if (f.section_key) current.value = f.section_key }
function sectionTitle(key) {
  const sec = project.value?.sections.find((s) => s.key === key)
  return sec ? sec.title || key : key
}

async function exportDocx(force = false) {
  if (exporting.value) return
  exporting.value = true
  error.value = ''
  missing.value = []
  try {
    await flushPendingSaves()
    await exportProject(projectId, force)
  } catch (e) {
    if (e.missing && e.missing.length) missing.value = e.missing
    else error.value = e.message || '导出失败'
  } finally {
    exporting.value = false
  }
}

async function refreshTemplate() {
  if (!confirm('把本项目的排版规范换成当前模板的最新版本? 已写内容不受影响。')) return
  error.value = ''
  try {
    const updated = await refreshProjectTemplate(projectId)
    project.value = updated
    template.value = updated.template_snapshot || template.value
  } catch (e) {
    error.value = e.message || '重新套用模板失败'
  }
}

async function openTemplateModal() {
  showTemplateModal.value = true
  templateError.value = ''
  templateLoading.value = true
  try {
    templateList.value = await fetchTemplates()
  } catch (e) {
    templateError.value = e.message || '加载模板列表失败'
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
    templateNotice.value = `已套用模板「${updated.template_name}」，导出即按该模板排版`
    setTimeout(() => { templateNotice.value = '' }, 5000)
  } catch (e) {
    templateError.value = e.message || '更换模板失败'
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
.modal .field { display: block; font-size: 13px; color: var(--muted); margin-bottom: 12px; }
.modal .field input { width: 100%; margin-top: 4px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; }
.structure-select { width: 100%; margin-top: 4px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; background: var(--card); color: var(--text); }
.ok-banner { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.error-banner { background: #fef2f2; border: 1px solid #fecaca; color: #b91c1c; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
.notice-banner { background: #f0fdf4; border: 1px solid #bbf7d0; color: #15803d; border-radius: 8px; padding: 8px 12px; margin-bottom: 10px; }
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
  border: 1px solid #fecaca; background: #fff7f7; border-radius: 10px;
  padding: 10px 14px; margin-bottom: 16px;
}
.check-panel.clean { border-color: #bbf7d0; background: #f0fdf4; }
.check-head { display: flex; align-items: center; gap: 10px; font-size: 14px; cursor: pointer; }
.check-head .tiny { margin-left: auto; }
.check-list { margin-top: 8px; border-top: 1px solid #fee2e2; padding-top: 8px; max-height: 220px; overflow-y: auto; }
.check-item { display: flex; align-items: center; gap: 8px; padding: 5px 4px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.check-item:hover { background: #fff; }
.check-item .badge { font-size: 11px; padding: 1px 8px; }
.check-msg { flex: 1; }
.check-source { color: #475569; font-size: 11px; white-space: nowrap; }
.check-sec { color: var(--primary); white-space: nowrap; }
.not-checked-list { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; border-top: 1px solid #e2e8f0; margin-top: 8px; padding-top: 8px; color: var(--muted); font-size: 12px; }
.not-checked-list strong { color: #475569; }
.not-checked-item { display: inline-flex; align-items: center; gap: 5px; }
.waived-list { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; border-top: 1px solid #bbf7d0; margin-top: 8px; padding-top: 8px; color: #166534; font-size: 12px; }
.waiver-btn { flex-shrink: 0; }
.dispute-btn { flex-shrink: 0; }
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
  .writer { flex-direction: column; gap: 14px; }
  .sidebar { width: 100%; position: static; max-height: none; }
  .tree-nav { max-height: 220px; }
  .editor-head { align-items: flex-start; flex-wrap: wrap; }
  .meta-row { align-items: stretch; flex-direction: column; gap: 4px; }
  .meta-row label { width: auto; }
}

@media (max-width: 480px) {
  .sidebar-head, .sidebar-foot { gap: 8px; flex-wrap: wrap; }
  .check-head { align-items: flex-start; flex-wrap: wrap; }
  .check-head .tiny { margin-left: 0; }
  .check-sec { display: none; }
  .check-source { display: none; }
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
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 60px;
  margin: 0 -24px;
  padding: 10px 24px;
  background: rgba(248, 249, 255, .94);
  border-bottom: 1px solid var(--border);
  box-shadow: 0 1px 5px rgba(15, 23, 42, .04);
}
.writer-command-leading, .writer-command-actions, .writer-command-metrics { display: flex; align-items: center; gap: 10px; }
.writer-command-leading { min-width: 0; }
.writer-command-title { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.writer-command-title span { color: var(--on-surface-variant); font-size: 10px; }
.writer-command-title strong { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
.writer-command-metrics { color: var(--on-surface-variant); font-size: 11px; white-space: nowrap; }
.writer-command-metrics > span + span { padding-left: 10px; border-left: 1px solid var(--border); }
.writer-command-metrics b { color: var(--on-surface); font-family: "JetBrains Mono", monospace; font-weight: 600; }
.writer-save-status { display: inline-flex; align-items: center; gap: 6px; }
.save-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981; }
.writer-save-status.pending .save-dot { background: var(--tertiary); }
.writer-review-action, .writer-fix-action, .writer-export-action { display: inline-flex; align-items: center; gap: 5px; }
.writer-review-action { color: var(--primary); border-color: var(--primary-fixed); background: var(--surface-container-low); }
.writer-fix-action { color: #b45309; border-color: #fcd34d; background: #fffbeb; }
.writer-command-bar .icon-action { flex-shrink: 0; }

.writer-columns {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr) 320px;
  align-items: start;
  gap: 0;
  margin: 0 -24px -40px;
  min-height: calc(100vh - 120px);
  background: var(--surface-container-low);
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
  background: var(--surface-container-low);
}
.editor-pane > .error-banner, .editor-pane > .notice-banner, .editor-pane > .missing-panel { max-width: 840px; margin-left: auto; margin-right: auto; }
.editor-head { max-width: 840px; margin: 0 auto 12px; }
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
.context-section-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-bottom: 8px; border-bottom: 1px solid rgba(195, 198, 215, .4); color: var(--on-surface); font-size: 12px; font-weight: 600; }
.context-section-title > span { display: inline-flex; align-items: center; gap: 5px; }
.context-section-title .material-symbols-outlined { color: var(--primary); font-size: 16px; }
.context-panel .check-head { gap: 5px; margin-top: 8px; font-size: 11px; line-height: 1.4; }
.context-chevron { flex-shrink: 0; color: var(--outline); font-size: 16px; }
.context-panel .check-list { max-height: 320px; overflow-y: auto; }
.context-panel .check-item { align-items: flex-start; flex-wrap: wrap; padding: 7px 2px; font-size: 11px; }
.context-panel .check-msg { min-width: 0; }
.context-panel .check-sec { width: 100%; padding-left: 20px; font-size: 10px; }
.context-panel .waived-list, .context-panel .not-checked-list { font-size: 11px; }

@media (max-width: 1180px) {
  .writer-columns { grid-template-columns: 220px minmax(0, 1fr); }
  .writer-context { grid-column: 2; min-height: auto; max-height: none; position: static; border-left: 0; border-top: 1px solid var(--border); padding: 18px 24px 28px; }
  .writer-context .context-panel { max-width: 680px; }
}

@media (max-width: 768px) {
  .writer-command-bar { top: 60px; margin: 0 -24px; padding: 9px 14px; gap: 8px; }
  .writer-command-title strong { max-width: 180px; }
  .writer-command-metrics { display: none; }
  .writer-review-action span:last-child { display: none; }
  .writer-review-action { width: 34px; padding: 6px 8px; justify-content: center; }
  .writer-fix-action span:last-child { display: none; }
  .writer-fix-action { width: 34px; padding: 6px 8px; justify-content: center; }
  .writer-export-action { padding: 6px 10px; }
  .writer-columns { display: flex; flex-direction: column; margin: 0 -24px -40px; }
  .writer-columns > .sidebar { width: 100%; min-height: auto; max-height: none; position: static; border-right: 0; border-bottom: 1px solid var(--border); padding: 14px 16px; }
  .writer-columns > .sidebar .tree-nav { max-height: 220px; }
  .editor-pane { width: 100%; min-height: auto; padding: 22px 16px 40px; }
  .writer-context { width: 100%; padding: 16px; }
}
</style>
