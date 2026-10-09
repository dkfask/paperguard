# Web UI 合入验证（2026-10-09）

用户已明确授权提交、推送、合并及更新项目记忆。本记录补充此前的 [UI 实施记录](./ui-refactor-delivery-20261009.md) 和 [学生折叠抽屉记录](./student-workspace-drawers-20261009.md)，不改写其历史验证结果。

## 身份与范围

- 独立工作区：`/Users/a1234/.codex/worktrees/ui-refactor-delivery/paperguard`。
- 分支：`codex/ui-refactor-20261009`；接入基线：`4659c5cfb3bdbf4fd798d72a669a2f32b68a440d`（当时最新 `origin/main`）。
- 初次提交 `3a3296a2c07215d33c517bd3cc8860c2a4072a0e` 的前端 Git tree：`aec987b1996079d5fb85b6fa657b6dfd92a9ecd2`，涵盖源码、配置、测试和视觉基线，可通过 `git rev-parse 3a3296a:frontend` 对照；后续审查修正另见下节。
- 最终范围为 Muse 视觉改造、导航/标题/状态/弹窗及学生工作台三个独立折叠抽屉，默认收起。设计文档、测试与脱敏截图随代码交付。
- 未修改后端、权限守卫、迁移、小程序、依赖、CI 或生产配置；主线新增版本接口与原型存档原样接入。原工作区的 `AGENTS.md` 修改保留且未纳入提交。

## 同步主线后的本地门禁

| 命令及目录 | 结果 |
|---|---|
| `backend/`：激活既有项目 Python 3.12 venv 后 `python -m pytest -q` | 1098 通过，2 项既有依赖弃用警告 |
| `frontend/`：`npm run typecheck` | PASS |
| `frontend/`：`npm run test` | 23 文件、96 测试通过 |
| `frontend/`：`npm run check:college-admin` | 结构检查与 5 测试通过 |
| `frontend/`：`npm run test:paragraphs` | PASS |
| `frontend/`：`npm run build` | PASS |
| `frontend/`：`npm run test:e2e` | 36 通过、2 按配置跳过，退出码 0；未更新快照 |
| `git diff --cached --check` 与凭据字面量审计 | PASS |

浏览器回归覆盖桌面/手机、角色权限、通用文档、完整论文生命周期、折叠抽屉的独立展开与键盘操作、保存失败重试、草稿/选区和弹窗焦点。两项跳过仍为既有桌面/手机项目分工。使用一次性 SQLite 与运行时随机凭据；原始日志、trace 和凭据不提交。

## PR #36 审查修正与重新绑定

- 写作工具栏在 1360px 以下换行时，章节与上下文栏取消固定偏移的吸顶行为，随页面滚动；保留更宽屏幕的布局。新增 1280px/840px 长编辑框滚动几何回归，草稿与编辑框仍保持。
- 创建论文组件向 BaseModal 传入与可见标题一致的本地化名称；覆盖中文及切换英文后的无障碍名称。
- ReasonDialog 在 busy 时阻止 Ctrl/Cmd+Enter 再次确认。新增快捷键断言，处理中不发 confirm，恢复后仍可提交原草稿。
- 弹窗名称与 busy 快捷键两项回归在修正前明确失败，修正后通过。滚动几何回归在原布局也通过，不能将其描述为已复现遮挡；取消中等宽度的固定吸顶是对审查指出的布局风险的预防性修正。
- 修正后的前端 Git tree：`2cb6545c1c4986c0b38daa59bb044f52225b6dfd`，可用最终提交的 `HEAD:frontend` 对照。
- 重新执行前端 typecheck、23 文件/97 单元测试、学院管理员结构/5 测试、段落检查和 build，全部通过。
- `npm run test:e2e -- ui-refactor.spec.ts full-lifecycle.spec.ts`：8 通过、2 按既有配置跳过，退出码 0；覆盖桌面/手机、受影响写作布局、完整论文流程与视觉基线，未更新快照。
- 后端源码及基线未变化，同一后端树的 1098 项结果仍适用；最新远端 head 重新执行全部 PR CI 与 release-preflight 后方可合并。

本地验证结论为本轮范围内 `PASS`。远端 CI、PR 合并与主线身份另以最新 head 的 GitHub 检查和项目记忆交接页为准。本轮自检不是独立人工审查，也不替代真实系统输入法、AI 或其他历史 UAT；未执行生产部署或线上验收。
