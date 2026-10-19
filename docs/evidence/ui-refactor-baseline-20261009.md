# Web UI 改造基线（2026-10-09）

本记录对应 [UI 重构方案](../product/ui-refactor-design-20261009.md) 的 Phase 0，记录修改前的实际结果。它不是 UI 改造完成、合入、上线或真实 UAT 证据。

## 身份与范围

- 仓库：`/Users/a1234/Documents/perpetual/paperguard`。
- 分支：`fix/ui-review-findings-20260929`。
- 基线 HEAD：`a64e8cdc56e3db76b691c4d4c054452d9f759173`。
- 开始时已有用户修改：`AGENTS.md`；已有未跟踪设计文档：`docs/product/ui-refactor-design-20261009.md`。两者保持原样。
- 用户已要求实施上述方案，随后明确选择按原型还原，并提供 [Muse 原型](https://muse.ai/s/paperguard-xuv6svxns63xsi)。
- 纯 UI 范围为 Web 外壳、导航、视觉变量、基础交互与现有场景；教师私有模板、租户改造、API 整体迁移和小程序不纳入。
- 角色权限、历史 URL、`panel` 参数、正文、保存、编辑器生命周期、检查与 DOCX 导出契约需要保留。

## 原生门禁基线

前端命令在 `frontend/` 执行；后端使用项目已有 Python 3.12.14 venv，已确认含 pytest/Pillow。

| 命令 | 修改前结果 |
|---|---|
| `npm run typecheck` | PASS |
| `npm run test` | PASS：21 文件，89 测试 |
| `npm run check:college-admin` | PASS：结构检查及 5 测试 |
| `npm run build` | PASS |
| `source ../.venv/bin/activate && python -m pytest -q`，在 `backend/` | PASS：1066 测试；2 项依赖弃用警告 |
| `npm run test:e2e -- smoke.spec.ts component-walkthrough.spec.ts --project=chromium` | FAIL：6 通过、2 失败，详见下节 |

浏览器运行使用 Playwright 仓库配置自动创建的一次性 SQLite 数据库、模板及产物目录，loopback 端口为后端 18090、前端 15190、worker readiness 18091。管理员密码仅为运行时随机值；没有调用生产或付费 AI。

## 浏览器基线失败

1. 管理员视角六个管理页面走查，在 `/operations` 失败。网络证据为 `GET /api/operations/retention` 返回 403，错误代码 `TENANT_MEMBERSHIP_REQUIRED`，消息为“请选择一个有效成员租户”。这是当前身份与运维入口的集成差距，不授权更改租户或平台身份契约。
2. 学院管理员用例要求 `/operations` 直达成功，实际被重定向到 `/workspace`。当前 [路由](../../frontend/src/router.js) 将该页限定为 `admin`；[运维 API](../../backend/app/api/operations.py) 进一步要求显式平台身份及 `operations:manage`。旧用例的允许预期与现有权限规则不一致，不以放宽权限解决。

已通过的六项覆盖教师认证后进入指导端、教师指导页及管理员设置拒绝、匿名公开/受保护入口，以及三条登录 smoke。该选择没有执行完整论文生命周期、写作不变量或真实 UAT。

本地浏览器失败详情在 `frontend/playwright-report/` 与 `frontend/test-results/`，这些是临时测试产物，不纳入提交。测试完成后由 Playwright 关闭隔离服务。

## 静态核验与后续实现映射

| 场景 | 现有入口 | 实现约束 |
|---|---|---|
| 公共外壳与角色导航 | `AppShell.vue`、`RoleSidebar.vue`、`router.js` | 沿用角色守卫与抽屉焦点；页面标题、菜单高亮和 `panel` 对齐 |
| 三类角色工作台 | `StudentWorkspace.vue`、`TeacherWorkspace.vue`、`AdminWorkspace.vue` | 使用真实数据；突出下一步、审核队列与已有配置入口 |
| 论文列表与创建 | `Home.vue` | 生产入口为该视图；不能以仅修改旧 `ProjectList`/`CreateProjectModal` 宣称列表已对齐 |
| 论文写作 | `Writer.vue`、`SectionTextEditor.vue`、`TiptapSectionEditor.vue` | 先改工具栏、侧栏与状态；保持正文、选区、保存及导出机制 |
| 审核、材料、消息、归档 | `Workflow.vue` 与现有组件 | 保留同一状态机、面板定位和各角色权限 |
| 模板、规范与治理 | `Templates.vue`、`Policies.vue`、`Cohorts.vue`、`Organizations.vue`、`AdminUsers.vue` | 保留资产生命周期、学院/学校范围与操作约束 |

根目录没有 `.codegraph/`，本轮按规则使用仓库搜索和调用关系核对影响范围。

Tailwind 虽在依赖及 PostCSS 配置中登记，实际 CSS 入口 `src/style.css` 未见 `@import`、`@tailwind` 或 `@config`；生产构建产物未包含 `.fixed`、`.inline-flex`、`.bg-primary`、`.max-w-2xl` 等旧基础组件使用的工具类。生产页面以现有语义 CSS 为主。后续须按原型映射现有变量，不能假定旧 Tailwind 基础组件已有完整样式，也不在视觉任务中升级 reset 或依赖。

以上记录截至应用代码修改前。随后已取得原型画板并实施 UI；改造结果与最新验证见 [UI 改造交付记录](./ui-refactor-delivery-20261009.md)。本轮没有执行提交、推送、合并、部署、生产迁移或真实 UAT。
