# PaperGuard 通用文档平台基线审计

审计阶段：Phase 0 Baseline / CI

审计日期：2026-09-12

审计分支：`phase/0-baseline-ci`

审计基线：`788e312b7623597dbfde545a9eecf4e8a064867a`，对应当前远端 `main` 的提交 `feat: complete PaperGuard refactor and lifecycle flow`

## 1. 审计结论

PaperGuard 当前已经是一个具备完整论文业务闭环的 Web 应用：模板导入与管理、规范导入与解析、结构化写作、项目快照、规则检查、自动修复、DOCX 导出、权限、组织隔离、审计、工作流、后台任务和 Docker 运行链路均已存在。它适合作为通用文档平台的演进基线，不适合在 Phase 0 进行推倒重写。

Phase 0 的 CI 阻塞已定位为测试依赖注入不完整：requirement AI assist 测试只替换了 `_chat_completion`，但请求处理仍先执行 `_resolve_cfg()`；CI 没有 AI 配置，因此路由返回 `503 AI 功能未启用`。修复方式是在测试中同时注入一个不会发起网络请求的测试配置，并继续 mock completion。没有删除测试、跳过检查或依赖真实在线 AI。

本阶段判断：**GO（Phase 0）/ CONDITIONAL GO（Phase 1）**。

- Phase 0 修复提交已经通过远端 CI，可以进入审查和收口。
- 不得因为本地矩阵通过就直接进入 Phase 1；必须先确认本 PR 的 Python 3.11/3.12 和前端构建均为绿色，并完成代码审查。
- 本阶段没有部署到 staging 或 production；生产发布结论为 **NO-GO / NOT IN SCOPE**。

## 2. 当前架构图

```text
                         Browser
                            |
                     Vue 3 / Vite frontend
                Templates / Writer / Admin / Workflow
                            |
                     FastAPI application
                            |
        +-------------------+-------------------+
        |                   |                   |
   Auth / scope        Template API        Project API
        |                   |                   |
   Users/sessions      PaperTemplate       WritingProject
                            |                   |
                            +---------+---------+
                                      |
        +-----------------------------+-----------------------------+
        |                             |                             |
  DOCX import                    Policy/Requirement               AI assist
  OOXML style/section            resolver/import                    candidate
        |                             |                             |
        +-----------------------------+-----------------------------+
                                      |
                    Project validation / Check Engine
                   OOXML IR + YAML rule packs + fixes
                                      |
                         DOCX Export / Fix Export
                                      |
                      synchronous export and export jobs
                                      |
                         SQLite JSON store + files
                                      |
                 worker / outbox / workflow / audit records
                                      |
                           Docker Compose volume
```

当前代码边界大致如下：

- `frontend/`：Vue 3 + Vite 的模板、写作、管理和工作流界面。
- `backend/app/api/`：FastAPI 路由，覆盖健康检查、模板、项目、AI、规范、组织、工作流、操作和审计。
- `backend/app/models/`：Pydantic/领域数据结构，核心是 `PaperTemplate`、`WritingProject`、`RequirementSet` 和工作流模型。
- `backend/app/services/`：模板导入、项目创建、规范解析与解析、项目检查、导出任务、文件存储和备份恢复。
- `backend/app/core/`：OOXML 检查/修复引擎、规则包、DOCX 导出基础能力。
- `backend/app/db.py`：SQLite 表结构、JSON 数据存储、版本兼容和当前工作流迁移。
- `backend/app/worker.py`：持久化导出/工作任务的后台执行进程。

## 3. 可复用能力矩阵

| 能力 | 当前实现 | 对通用平台的复用判断 | Phase 0 决策 |
|---|---|---|---|
| 登录、会话、角色 | `backend/app/auth.py`，PBKDF2 密码哈希、哈希 session token、过期时间、角色检查 | 可直接复用；后续补齐通用资源授权和密钥治理 | 保留 |
| 组织与作用域 | `OrganizationUnit`、组织上下文、路由层 scope 检查 | 可复用为 package/template/project 的统一租户边界 | 保留 |
| 模板 CRUD | `PaperTemplate` 与模板 API | 可作为 thesis adapter 的兼容存储；不可作为最终通用模型 | 保留并包一层 |
| DOCX 模板导入 | `import_template.py` 读取 OOXML 样式、封面字段、章节树和元字段 | DOCX 引擎基础可复用；论文语义启发式需要可插拔 | 保留 |
| 规范导入 | `policy_import.py` 支持 DOCX/PDF/MD/TXT 文本提取并生成结构化草稿 | 文本抽取和候选生成可复用；必须明确 AI 非权威 | 保留 |
| 规范/规则模型 | `RequirementSet`、`RequirementItem`、binding、版本和状态 | 可映射为 `SpecSet/SpecVersion/SpecRule` | 保留并建立 mapper |
| 项目创建 | `create_project_from_template()` 展开章节树和段落 | 可复用快照语义；章节展开器需要 generic schema | Phase 1 仅包适配器 |
| 项目快照 | `template_snapshot`、`template_source_snapshot`、`requirement_snapshot` | 是通用平台的关键基础，但当前缺 renderer/rule-engine 快照 | 保留并补契约 |
| 检查/修复引擎 | OOXML IR、规则包、JMESPath/JSONLogic、finding/fix | 确定性执行层可复用；输入 IR 和论文类型分支需解耦 | 保留 |
| DOCX 导出 | 以源模板为基底、保留 styles/layout/header/footer/theme，再写入内容 | 高保真底层可复用；当前 section 渲染器论文化 | 保留底层，后续抽 renderer |
| 自动保存与并发更新 | 项目 revision 条件更新、前端自动保存 | 可复用为 generic project persistence | 保留 |
| 导出后台任务 | `export_jobs.py`、worker、artifact 文件和审计 | 可复用为 `RenderJob/Artifact` 的过渡基础 | 保留并统一接口 |
| 工作流、outbox、幂等 | workflow repository、jobs、workflow events | 可复用为后台任务和审计基础；与文档域分开 | 保留 |
| 审计 | workflow/audit API、policy audit、request id | 可复用；需覆盖 template/package/project 版本状态变更 | 保留并统一事件语义 |
| 文件安全 | DOCX/ZIP 大小、条目、压缩比、OOXML 必要部件和安全文件名限制 | 可复用为 asset ingestion 基线；宏/OLE/外链策略仍需补充 | 保留并补强 |
| Docker | backend、worker、frontend、共享 `paperguard_data` volume | 可复用为本地和 staging 运行基线 | 保留 |
| CI | Python 3.11/3.12 后端、前端 Node 20 build | 可复用，但还没有 frontend unit/typecheck/e2e 门禁 | Phase 0 修复，后续补门禁 |

## 4. thesis 强耦合点

以下耦合点决定了通用化必须通过适配器和兼容层逐步完成：

### 后端模型与服务

- `backend/app/models/template.py` 的 `SectionType` 直接包含 `cover`、`abstract`、`english_abstract`、`keywords`、`toc`、`references`、`acknowledgement`、`appendix` 等论文语义。
- `PaperTemplate` 的 `rule_packs`、`requirement_set_ids`、`organization_context` 是现有论文模板的组合方式，不等同于不可变的通用 `DocumentPackage`。
- `backend/app/services/projects.py` 的 `default_template()` 硬编码 GB/T 7713 论文章节、封面字段和章节结构。`create_project_from_template()` 还对封面和目录采用论文专用的编辑器处理。
- `backend/app/services/import_template.py` 使用特殊标题集合、heading 正则和摘要/目录/参考文献/附录启发式。它能提高论文模板识别率，但不应成为所有 DOCX 的通用真值。
- `backend/app/services/project_check.py` 直接导入 `SectionType`，并对摘要、关键词、参考文献等块进行特殊映射；`project_validation.py`、`template_compare.py`、`policy_format.py` 也存在类似论文字段假设。
- `backend/app/core/export/docx_exporter.py` 已具备从源模板保留 OOXML 样式和页面布局的能力，但正文、封面、目录、参考文献和页码逻辑围绕论文 section 类型组织。
- `backend/app/api/ai.py` 的部分提示词直接使用中文学术论文语境。AI 输出目前只能作为候选语义结果，不能绕过结构化 schema 和确定性检查。

### 前端交互

- `frontend/src/views/Writer.vue` 维护 `cover`、`abstract`、`english_abstract`、`toc`、`references` 等受保护类型，并按论文标题层级处理章节编号。
- `frontend/src/views/Templates.vue` 导入合并逻辑会补齐默认论文章节，并按摘要、参考文献、附录等标题归一化。
- 写作页的 AI 生成类型、检查侧栏和部分文案默认面向论文/学术规范；路由和组件名也主要使用 writer/thesis 语义。

这些耦合点当前不是 Phase 0 的重构目标。Phase 1 只建立通用领域契约和 thesis mapper，确保现有 API、快照、检查和导出行为不变。

## 5. 建议抽象点

建议按以下边界逐步抽象，而不是先改表、先改所有 API 或先替换编辑器：

1. **Document schema**：定义文档类型、模板 section、field、asset、style 的稳定结构；保留 thesis 的旧 enum 和 JSON 兼容读取。
2. **Template version**：把当前可变 `PaperTemplate` 视为逻辑模板，把发布内容提升为不可变 `TemplateVersion`，保存 definition hash、source asset、schema version 和 renderer version。
3. **Spec schema**：用 mapper 将现有 `RequirementSet` 转为 `SpecSet/SpecVersion/SpecRule`，保持原有 binding、severity、enforce、fix 和审计信息。
4. **Package contract**：由精确的 template version + spec version + renderer version + rule engine version 组成发布包，发布后不能原地修改。
5. **Project snapshot contract**：项目创建时固定 package 和完整执行版本；现有 snapshots 必须继续作为历史真值。
6. **Ports and adapters**：为 template/spec/project/audit 建立 repository/service port，先由当前 SQLite `Store` 实现，避免 Phase 1 引入新的存储系统。
7. **Generic content IR**：把 paragraph/image/table 等 `ContentBlock` 作为通用内容单元，论文章节只作为一种 section kind 和 thesis adapter。
8. **Renderer and validator ports**：确定性 validator/renderer 接受冻结 snapshot，AI 只提供 candidate schema 或候选规则。
9. **Asset boundary**：模板源文件、规范源文件、图片和导出文件统一进入授权、校验、checksum、扫描状态和保留策略。

## 6. 新领域模型草案

以下是 Phase 1 需要先固化的契约草案。字段名是方向性设计，Phase 0 不创建数据库表，也不改变现有 API。

| 模型 | 责任与最小字段 | 与现有模型的关系 |
|---|---|---|
| `DocumentType` | `id/key/name/status/capabilities`，表示 thesis、申报书、实验报告等类型 | 现有论文业务固定映射为 `key=thesis` |
| `DocumentTemplate` | 逻辑模板 `id/type_id/org_id/owner_id/status/current_version_id` | 由 `PaperTemplate` 兼容映射 |
| `TemplateVersion` | 不可变 `id/template_id/version/definition_hash/source_asset_id/schema_version/renderer_version/published_at` | 当前 `PaperTemplate.version` 不能单独承担此责任 |
| `TemplateField` | `key/label/type/required/default/constraints/section_key`，支持 text、multiline、date、number、select 等 | 映射 `MetaField/CoverField`，保留未知字段 |
| `TemplateSection` | `key/title/kind/order/parent_key/required/content_schema/style_ref` | 映射现有 `TemplateSection` 树和 thesis `SectionType` |
| `TemplateAsset` | `id/storage_key/content_type/size/checksum/purpose/scan_status` | 包装 `source_docx`、规范源文件、图片和导出文件 |
| `StyleDefinition` | `style_key/font/run/paragraph/page/layout/schema_version` | 映射现有 `TextStyle`、页面布局和 OOXML 样式 |
| `SpecSet` | 逻辑规范集 `id/document_type/scope/owner/status/current_version_id` | 由 `RequirementSet.family_id` 兼容映射 |
| `SpecVersion` | 不可变 `id/spec_set_id/version/content_hash/source_asset_ids/effective_at` | 映射 RequirementSet 的 version/status/documents |
| `SpecRule` | `id/kind/target/checker/params/severity/enforce/fix/provenance` | 映射 `RequirementItem`，保留 manual/doc/AI provenance |
| `DocumentPackage` | `id/document_type/template_version_id/spec_version_id/renderer_version/rule_engine_version/status/published_at` | 由模板和规范的精确版本组合形成 |
| `DocumentProject` | `id/package_id/owner_id/org_id/state/revision/current_snapshot_id` | 兼容现有 `WritingProject` |
| `ProjectSnapshot` | `id/project_id/package_id/template_hash/spec_hash/renderer_version/rule_engine_version/content_hash/created_by/created_at` | 兼容并扩展现有三类 snapshot |
| `ContentBlock` | `id/snapshot_id/section_key/field_key/type/order/text/data/asset_ids` | 由 paragraph/image/table 等现有块映射 |
| `ValidationRun` | `id/project_id/snapshot_id/rule_engine_version/state/findings/summary/timing/actor` | 映射现有 `CheckRun` 和项目检查记录 |
| `RenderJob` | `id/project_id/snapshot_id/renderer_version/status/attempt/idempotency/error/queued_at/completed_at` | 由 `export_jobs.py` 的持久化任务过渡映射 |
| `Artifact` | `id/job_id/project_id/storage_key/media_type/size/checksum/retention/download_status` | 统一导出文件和结构化流程产物 |
| `AuditLog` | append-only `id/actor/org/action/resource/before/after/version/request_id/result/reason/created_at` | 汇合 workflow、policy 和模板/项目关键事件 |

领域不变量：

- 逻辑对象可以有多个版本，发布版本不可原地修改。
- `DocumentPackage` 必须引用精确的不可变模板版本、规范版本、renderer 和 rule engine 版本。
- 项目创建必须生成 `ProjectSnapshot`；检查和导出只读取该 snapshot，不从当前可变模板回算历史项目。
- AI 只能产生候选字段、候选 section 或候选规则，并且必须先写入结构化 schema；最终检查、修复和渲染由确定性代码执行。
- 资产必须有 checksum、授权范围和扫描状态；下载和使用必须验证项目/组织 scope。
- 发布、升级、检查、修复、导出、下载和失败均应写入可追踪审计事件。

## 7. 当前 CI 状态与 Phase 0 修复

### 7.1 Workflow 配置

`.github/workflows/ci.yml` 当前包含：

- 后端 Python `3.11` 和 `3.12` matrix。
- 后端安装 `backend/requirements.txt`、`pytest`、`httpx`，执行 `python -m pytest -q`。
- 前端 Node `20`，执行 `npm ci` 和 `npm run build`。
- CI 没有注入 AI base URL、API key 或 model；这是正确的默认安全边界，测试不应依赖真实 AI。
- CI 当前没有执行前端 unit test、typecheck 或 Playwright E2E。

### 7.2 远端红灯证据

基线提交 `610b90e` 的 GitHub Actions run `34439095616`：

- Frontend build：成功。
- Backend Python 3.11：失败，`1 failed, 499 passed, 2 warnings`。
- Backend Python 3.12：失败，`1 failed, 499 passed, 2 warnings`。
- 失败测试：`tests/test_policy_lifecycle.py::test_requirement_ai_assist_is_saved_to_matching_item`。
- 失败响应：`503 AI 功能未启用`。

当前远端 `main` 提交 `788e312` 的 GitHub Actions run `34671278922` 在本次修复前仍然是：

- Frontend build：成功。
- Backend Python 3.11：失败，`1 failed, 553 passed, 2 warnings`。
- Backend Python 3.12：失败，`1 failed, 553 passed, 2 warnings`。
- 失败原因仍是同一条 AI assist 测试，说明后续 UI/生命周期提交没有自然修复这个测试依赖。

### 7.3 修复内容

在 `backend/tests/test_policy_lifecycle.py` 中，测试现在同时 mock：

- `_resolve_cfg()`：返回 `http://test.invalid/v1`、测试 API key 和测试 model，仅用于满足配置解析，不会访问网络。
- `_chat_completion()`：返回固定的人工确认建议。

这样测试覆盖的仍是“AI assist 结果写回匹配 requirement item”的行为，且不要求 CI 配置真实 AI。没有修改生产路径来掩盖缺失配置，也没有降低断言强度。

提交并推送后，PR #7 的 GitHub Actions run `34671687276` 已完成：

- Backend tests (Python 3.11)：**passed**。
- Backend tests (Python 3.12)：**passed**。
- Frontend build：**passed**。

GitHub Actions 仍报告 runner 将 Node.js 20 action 强制切换到 Node.js 24 的 deprecation annotation；这不是本次测试失败，但应作为后续 CI 维护项处理。

## 8. 测试数量、失败原因与修复结果

### 8.1 已执行并通过

执行环境：仓库自带 `.venv`；本地可用 Python 版本为 3.12。为了模拟 CI 缺少 AI 配置，后端全量测试额外移除了 `PAPERGUARD_AI_BASE_URL`、`PAPERGUARD_AI_API_KEY`、`PAPERGUARD_AI_MODEL`。

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端测试文件盘点 | `rg --files backend/tests -g 'test_*.py'` | 56 个测试文件；另有 7 个 fixture 文件 |
| 后端全量，AI 配置移除 | 在 `backend/` 执行 `env -u PAPERGUARD_AI_BASE_URL -u PAPERGUARD_AI_API_KEY -u PAPERGUARD_AI_MODEL ../.venv/bin/python -m pytest -q` | **554 passed, 2 warnings**，20.14s |
| 前端 unit | 在 `frontend/` 执行 `npm test` | **7 test files passed, 22 tests passed** |
| 前端 paragraph 专项 | `npm run test:paragraphs` | **passed** |
| 前端类型检查 | `npm run typecheck` | **passed**，无输出 |
| 前端生产构建 | `npm run build` | **passed**，Vite 8.2.1，227 modules transformed |
| 差异空白检查 | `git diff --check` | **passed** |

README 当前仍写着 `552 passed`，与本次实际 `554 passed` 不一致，这是需要后续修正的文档债务；本阶段报告以实际命令结果为准。

### 8.2 已修复的问题

修复前：后端 3.11/3.12 的同一个测试均因 `_resolve_cfg()` 找不到 AI 配置返回 503。

修复后：本地 CI 等价环境在不提供 AI 配置时，后端 **554 passed**；该测试不再依赖真实在线 AI。

### 8.3 NOT RUN 项

以下项目在 Phase 0 报告生成时尚未执行，不能标记为通过：

- 本地 Python 3.11 后端矩阵：**NOT RUN**，当前机器没有可用的 Python 3.11 可执行文件。
- 本修复提交对应的 GitHub Actions post-fix run：已执行，run `34671687276`，Python 3.11/3.12 和前端 build 全部 **passed**。
- Docker image build / compose 启动验证：**NOT RUN**。
- Playwright E2E 与完整浏览器回归：**NOT RUN**；已有用户工作区中的历史报告和截图不作为本次验证证据。
- staging 部署与线上测试：**NOT RUN**。
- production 部署与线上验证：**NOT RUN**，且本阶段没有生产部署授权。

## 9. 技术债务

1. README 测试数量过时，且 CI 门禁只验证前端 build，不验证前端 unit/typecheck/e2e。
2. 后端依赖版本范围较宽，CI 直接解析最新兼容依赖，缺少统一 lockfile 门禁；现有 pytest/Starlette/httpx 组合已经产生 warning。
3. `main.py` 会自动加载环境配置，容易让本地 `.env` 掩盖测试没有显式注入依赖的问题；AI 测试已修复，但同类依赖仍需盘点。
4. SQLite 以 JSON blob 存放主要领域对象，项目/模板/规范的查询、索引、外键和并发能力有限；`INSERT OR REPLACE` 也不适合作为未来版本对象的唯一写入语义。
5. `PaperTemplate` 有 `version` 字段，但模板 PUT 仍可原地覆盖，没有真正的不可变 `TemplateVersion` 和发布状态约束。
6. 同步 legacy export、durable export job 和 workflow artifact 并存，尚未统一为通用 `RenderJob/Artifact` 契约。
7. API 路由、服务和 Store 之间存在直接依赖与动态构造，替换存储和 renderer 前需要先建立 ports/adapters。
8. 模板导入和编辑器已有通用外观，但 section 语义、章节编号、AI prompt、检查映射仍以论文为中心。
9. 文件安全已做 ZIP/OOXML 边界限制，但宏、OLE、外部关系、病毒扫描、保留和配额策略还没有形成统一 asset policy。
10. 关键 workflow 事件有较完整审计，但模板、项目、导出和部分规范操作仍需要统一的 append-only audit contract。

## 10. 数据迁移风险

- **身份与兼容性**：现有 `template_id`、`project_id`、`RequirementSet.family_id` 已被 API、快照、审计和文件路径引用；不能通过重建或复用 ID 来迁移。
- **历史真值**：老项目的 `template_snapshot`、`template_source_snapshot`、`requirement_snapshot`、waiver、dispute、acknowledgement 和 check run 必须原样保留。迁移不能按当前可变模板重新计算历史导出。
- **版本映射**：现有 `PaperTemplate.version` 和 `RequirementSet.version/status` 信息不足以自动证明发布不可变，需要生成兼容的 version records 和 content hash，并保留原始 JSON。
- **旧数据形态**：部分历史项目可能缺少 owner、revision 或新加字段；迁移必须支持幂等 backfill、双读兼容和明确默认值，不能把缺字段当成空业务数据。
- **文件资产**：`backend/data/templates`、`backend/data/policy-documents`、上传目录和 Docker volume 中的文件要按 checksum、相对安全路径和引用关系核对，防止迁移造成孤儿文件或错误归属。
- **数据库机制**：当前 schema 同时包含 JSON blob、ad hoc `ALTER TABLE`、`schema_migrations`、workflow/jobs/outbox 表；SQLite 锁和长事务会增加在线迁移风险。
- **行为回归**：DOCX 输出不能只比较 JSON；需保留模板布局、页眉页脚、样式、分节、目录和页码的 golden/结构化回归证据，并做备份恢复演练。
- **回滚**：任何正式 schema 迁移前必须做数据库与文件备份、恢复演练和版本化迁移脚本；Phase 0 不执行迁移。

## 11. 安全风险

### 高优先级

- 用户 AI API 配置当前以明文 JSON 存在本地 SQLite；数据库备份、开发导出和诊断包可能泄露密钥。后续必须改为外部 secret reference 或加密存储，并保证日志、审计和导出脱敏。
- 规范导入和 AI assist 可能把用户文档内容发送给外部模型；需要明确 provider allowlist、数据驻留、保留期限、用户同意和敏感内容策略。AI 解析结果必须视为不可信候选。

### 中优先级

- DOCX 本质是 ZIP/XML，当前已有大小、条目、压缩比和必要 OOXML 部件限制，但宏、OLE、外部关系和恶意内容扫描策略尚未统一；后续应采用 allowlist、清理和可选杀毒扫描，禁止服务端追取外部资源。
- Bearer session token 已做 hash 存储并有 24 小时过期，但通用平台仍需明确撤销、设备管理、CSRF/跨站策略和安全审计。
- CORS 依赖环境 allowlist，AI、导入、检查和导出接口尚缺统一 rate limit、配额和成本控制。
- 通用 package 必须在 repository/service 层强制 organization/resource scope，不能只依赖路由前置检查。
- 模板源文件、规范附件、项目图片和导出 Artifact 必须统一执行授权、路径约束、checksum、扫描状态和下载审计。
- 所有版本发布、升级、检查、修复、渲染、下载及失败必须进入 append-only `AuditLog`，并禁止把用户全文写进审计和普通日志。

### 已有正向控制

- 密码使用 PBKDF2-HMAC-SHA256；session 数据保存 token hash。
- 上传有 DOCX/ZIP 尺寸、条目数、解压大小和压缩比边界；输出文件名使用安全生成策略。
- `/media/uploads` 和规范文档路径已有当前用户/资源 scope 及路径穿越保护。
- AI assist 的结果在现有代码中被标记为非权威建议；Phase 1 必须继续保持这个边界。

## 12. Phase 1 任务拆分与实施计划

Phase 1 目标是 **Domain abstraction**，只建立领域契约和兼容适配器，不替换编辑器、检查引擎、DOCX renderer 或数据库。

### 实施任务

1. 从 Phase 0 合并后的绿色提交创建 `phase/1-domain-abstraction` 分支。
2. 新建 `backend/app/domain/documents/` 的 schema/types，先覆盖 `DocumentType`、`DocumentTemplate`、`TemplateVersion`、`TemplateField`、`TemplateSection`、`TemplateAsset` 和 `StyleDefinition`。
3. 实现 thesis mapper：`PaperTemplate` 与 generic template definition 双向映射，未知 section/field 必须保留，现有模板 ID 和 API 输出保持兼容。
4. 实现 policy mapper：把 `RequirementSet`、binding、item、document 和版本状态映射为 `SpecSet/SpecVersion/SpecRule`，生成稳定 hash，不在本阶段写新表。
5. 定义 `DocumentPackage`、`DocumentProject`、`ProjectSnapshot` 契约，并补齐 renderer/rule-engine version 字段的兼容读写；历史 snapshot 不重新计算。
6. 定义 `TemplateRepository`、`SpecRepository`、`ProjectRepository`、`AuditPort`，由现有 SQLite Store 提供 adapter。
7. 添加 contract tests：论文模板 round-trip、快照 hash 稳定、发布后不可变、未知通用 section/field 保留、RequirementSet 映射一致、现有 API 回归。
8. 仅在契约稳定且测试绿色后，更新少量 README/API 说明；不提前改写前端 Writer 或导出器。

### Phase 1 验收门

- 后端全量测试、domain contract tests、DOCX golden/结构化回归全部通过。
- 前端 unit、typecheck、build 通过；与 Phase 1 变更相关的 E2E 通过。
- `git diff --check` 通过。
- GitHub Actions Python 3.11/3.12 和前端 build 全绿。
- 完成 scoped review，确认没有 thesis API 行为回归、权限越界或 snapshot 真值变化。
- Phase 1 PR 未合并前，不开始 Phase 2 Template Engine。

## 13. GO / NO-GO

| 决策项 | 结论 | 依据 |
|---|---|---|
| 继续保留现有 PaperGuard 作为演进基线 | GO | 论文闭环和通用化所需的模板、规范、快照、检查、导出、权限、审计和任务能力都已存在 |
| 合并 Phase 0 修复 | GO for review/merge | 修复最小、测试不依赖真实 AI；PR #7 的远端 Python 3.11/3.12 和前端 build 已绿 |
| 直接进入 Phase 1 | NO-GO，直到 CI/review 完成 | 本报告只完成 Phase 0；必须绑定绿色提交后再开新分支 |
| 现在进行数据库/全量模型重构 | NO-GO | JSON blob、历史 snapshot、文件资产和 workflow schema 的迁移风险尚未在 Phase 0 解决 |
| 现在进行 staging/production 部署 | NO-GO / NOT IN SCOPE | 本阶段未执行线上验证，也没有生产部署授权 |

Phase 0 的完成标准已达到：本报告和最小 CI 测试修复位于独立分支/PR，远端 CI 全绿。PR #7 仍等待审查和用户明确决定；本任务在 Phase 0 停止，不自动开始后续重构，也不自动合并 PR。
