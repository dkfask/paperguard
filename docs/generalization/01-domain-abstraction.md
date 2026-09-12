# Phase 1 Domain Abstraction

状态：实施完成，等待独立 PR CI 和审查

分支：`phase/1-domain-abstraction`

基线：Phase 0 绿色提交 `f010e6e316e96a13ee168ef1aced449858508101`

## 目标与边界

本阶段只建立通用文档领域契约和兼容适配器，为后续 Template Engine、Spec Engine 和 DocumentPackage 做稳定输入。没有改变现有 HTTP API、SQLite schema、Writer、Check/Fix Engine 或 DOCX Export 的行为。

## 新增契约

`backend/app/domain/documents/` 提供：

- 模板域：`DocumentType`、`DocumentTemplate`、`TemplateVersion`、`TemplateDefinition`、`TemplateField`、`TemplateSection`、`TemplateAsset`、`StyleDefinition`。
- 规范域：`SpecSet`、`SpecVersion`、`SpecRule`，以及 `RequirementSet -> SpecSet/SpecVersion/SpecRule` 映射。
- 执行契约：`DocumentPackage`、`ProjectSnapshot`，明确 `template_version_id`、`spec_version_id`、`renderer_version` 和 `rule_engine_version`。
- 存储边界：`TemplateRepository`、`SpecRepository`、`ProjectSnapshotRepository` 协议，以及基于当前 `Store` 的只读 legacy adapters。

## 兼容策略

1. `PaperTemplate` 仍是现有 API 和数据库的事实来源；`paper_template_to_domain()` 将它映射成 `thesis` 类型的逻辑模板和不可变 `TemplateVersion`。
2. `domain_to_paper_template()` 只接受 thesis 可表示的 section kind；未知通用 section 不会被静默压缩成论文 section，而是保留在通用 schema 中，直到对应 renderer adapter 出现。
3. `RequirementSet.family_id` 映射为逻辑 `SpecSet.id`，版本映射为 `SpecVersion`；规则的 severity、enforce、checker、params、fix 和 provenance 均保留。
4. `ProjectSnapshot.from_legacy_project()` 完整保存现有项目数据、`template_snapshot`、`template_source_snapshot` 和 `requirement_snapshot`，并补充 renderer/rule engine 版本。历史数据不从当前可变模板重新计算。
5. Phase 1 不创建新表、不迁移旧 JSON、不替换现有 Store。后续持久化迁移必须单独设计 expand/backfill/compatibility/rollback 流程。

## 不变量

- 版本对象使用 frozen Pydantic contract；更新必须产生新对象，不能原地修改已发布版本。
- definition/spec/project hash 使用排序 JSON 和稳定分隔符计算，不依赖字典插入顺序。
- Package 必须指向精确的模板版本、规范版本、renderer 和 rule engine 版本。
- AI 结果仅通过结构化候选和 provenance 进入领域层，确定性检查和渲染仍由现有引擎执行。
- 领域层不读取文件内容、不发起网络请求、不承担 HTTP 鉴权；资产授权和扫描继续由应用层负责。

## 验证

- `backend/tests/test_document_domain.py`：7 passed。
- 后端在移除 `PAPERGUARD_AI_BASE_URL`、`PAPERGUARD_AI_API_KEY`、`PAPERGUARD_AI_MODEL` 后全量：561 passed，2 warnings。
- 已覆盖 thesis 模板 round-trip、不可变版本、未知 generic section/field、规范 provenance/hash、项目快照冻结、Store adapter 和 package execution version。
- 前端和 GitHub Actions 结果会在本阶段 PR 完成后回填；不得用 Phase 0 的旧 head 作为本阶段证据。

## 下一阶段门禁

Phase 2 只能在本分支 PR 的 Python 3.11/3.12、前端 build、相关 contract tests 全绿并完成审查后开始。Phase 2 的实现范围应限定为模板解析候选 schema、人工确认和发布前校验，不提前引入数据库迁移或通用编辑器重写。
