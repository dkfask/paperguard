# Phase 2 Template Engine

状态：本地实现完成，等待独立 PR CI 和审查

分支：`phase/2-template-engine`

## 已实现

- `backend/app/services/template_engine.py` 将现有 DOCX OOXML 样式、标题树和封面字段解析为通用 `TemplateParseCandidate`。
- `backend/app/services/docx_anchors.py` 识别显式 `{{field}}`、`{{section:key}}`、Content Control、Bookmark 和 MERGEFIELD，并将部件、选择器和出现次序写入 `TemplateDefinition.anchors`。
- 候选保留源 DOCX 的 `TemplateAsset` 引用、size、checksum 和扫描状态；解析本身不调用 AI、不发起网络请求。
- 未映射 Word 样式、未识别章节、未识别字段和未识别样式以结构化 issue 返回，管理员可以在确认前处理。
- `POST /api/templates/parse` 只允许管理员/已认证教师，返回 `needs_confirmation` 候选并将成功操作写入现有 audit ledger。
- `POST /api/templates/parse/confirm` 对章节 key、字段 key、父子引用、源 asset 和最低章节结构做确定性校验；失败会返回 issue 并审计，成功只记录 confirmed 状态。
- 确认不等于发布，也不写入模板表；DocumentTemplate/TemplateVersion 持久化和 DocumentPackage 发布留在后续阶段。

## 兼容边界

- 旧的 `POST /api/templates/import` 保持原返回结构和论文 UI 兼容，不替换现有 `PaperTemplate` 流程。
- 通用 section kind 不强制转换为 thesis `SectionType`；只有 thesis adapter 能表示的结构才能在后续转回旧模型。
- 源文件仍通过当前受控 `template_source` 存储和上传安全检查处理，Phase 2 不改变路径授权规则。
- audit 只记录 actor、candidate id、action、resource 和结果，不记录模板正文、文件内容或 checksum。
- 没有显式锚点的历史模板仍按原有标题/封面启发式识别，并在候选中给出高保真风险提示；新通用模板应在人工确认阶段采用稳定锚点。

## 验证

- `tests/test_template_engine.py`：5 passed。
- 后端移除 AI 环境变量后全量：566 passed，2 warnings。
- 已覆盖管理员鉴权、DOCX 解析、通用候选、人工确认、无章节/无源文件拒绝、重复 section key 拒绝和审计投影。
- 前端现有 unit、paragraph、typecheck、build 在本分支未改动前已通过；本阶段不引入前端行为变更。
- Docker、Playwright E2E、staging、production：`NOT RUN`，不属于本阶段发布范围。

## 下一阶段门禁

Phase 3 只能在本分支的 Python 3.11/3.12 CI、前端 build 和相关 review 完成后开始。Phase 3 负责规范文档解析与结构化候选规则，不把 AI 输出直接接入检查器，也不跳过人工确认和审计。
