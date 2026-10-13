# 模板预览与紧凑弹窗调整（2026-10-11）

用户确认方案后，在当前本地预览工作区 `/Users/a1234/.codex/worktrees/all-pages-one-screen-v3/paperguard` 实施。分支为 `codex/all-pages-one-screen-20261011`，基线 HEAD 为 `5971f506d889caa4bcaa26f751ddd66cf067460d`。本轮改动未提交，保留该工作区已有界面调整；本报告只覆盖下列增量。

- 通用模板导入：章节名称独占可换行的编辑区域，用中文显示一级、二级、三级标题及摘要等类别；移除章节类型、顺序、样式映射、内部编号、重复和增删权限控件，以及导出模式和底层锚点信息。填写字段默认折叠。解析得到的结构、顺序、格式映射和权限继续原样提交，未修改后端默认值。
- 新建论文：改用居中、随内容高度变化的小弹窗，保留模板选择、论文题目和取消/创建操作；支持表单提交，避免重复创建，失败时保留输入并在弹窗内显示错误，成功后关闭并进入写作页。
- 审计筛选、用户操作和组织编辑：改为紧凑弹窗，筛选项按两列组织并在手机上单列显示；消除组织编辑重复标题。
- 模板管理的样式导入预览：以标题级别等元素的条目替代十列表格，字体、字号、行距等格式信息按需展开。
- 共用弹窗：支持标题栏，并将内容滚动限制在弹窗内部，保留焦点管理和 Escape 关闭行为。

## 本轮验证

| 检查 | 结果 |
| --- | --- |
| `frontend/`：`npm run test` | 24 个文件，103 项通过 |
| `frontend/`：`npm run typecheck` | 通过 |
| `frontend/`：`npm run build` | 通过 |
| `frontend/`：`npm run check:college-admin` | 结构检查及 5 项测试通过 |
| `backend/`：项目 Python 3.12 venv 执行 `python -m pytest -q` | 1098 项通过，2 项依赖弃用警告 |
| `npm run test:e2e -- e2e/document-platform.spec.ts e2e/ui-refactor.spec.ts --project=chromium` | 9 项通过；独立测试服务器与临时数据库，覆盖真实导入、发布、创建、保存、校验和 DOCX 导出，以及创建后的写作与失败重试 |
| `TEST_BASE_URL=http://127.0.0.1:5173 npm run test:e2e -- e2e/compact-previews.spec.ts --project=chromium` | 6 项通过；1280×800、900×600、390×844，检查长名称无裁切、弹窗无横向溢出、创建失败保留输入、焦点返回、三个短弹窗和折叠格式详情 |
| `git diff --check` | 通过 |

视觉测试使用明确的合成数据和浏览器请求拦截，不向本地预览数据库写数据；完整业务回归使用独立临时数据库。最初视觉测试的请求拦截误匹配了源码模块路径，另有按钮名称和动画结束前测量的问题，已修正测试工具后完整重跑，最终 6 项通过；没有放宽溢出或裁切断言。

本轮路径的实际内容摘要见 [source-snapshot.json](template-preview-dialogs-20261011/source-snapshot.json)。增量差异、测试日志保存在 `/Users/a1234/.codex/tmp/paperguard-ui-simplification-20261011`，其中 `task-only.patch` 区分本轮改动与原有未提交调整。截图已人工查看桌面和手机的章节名称及格式预览。

- [新建论文弹窗](template-preview-dialogs-20261011/new-thesis-1280.png)
- [章节预览](template-preview-dialogs-20261011/section-preview-1280.png)
- [手机章节预览](template-preview-dialogs-20261011/section-preview-390.png)
- [手机格式详情](template-preview-dialogs-20261011/style-preview-390.png)

范围为本地代码、Chromium 浏览器与临时数据库验证；未提交、推送、合并、运行远端 CI 或部署，不作为生产、真实 UAT 或微信真机验收证据。本地预览仍由 `http://127.0.0.1:5173` 提供。
