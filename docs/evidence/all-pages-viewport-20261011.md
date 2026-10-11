# 全页面一屏布局验证（2026-10-11）

## 来源与范围

用户要求“所有页面都要设计成首页的样子，只铺满这个屏幕就行，不要用下拉”，并确认了全站改造方案。桌面宽度 ≥1101px 使用固定可视高度，保留首页式侧栏、顶栏、背景、卡片和间距；长列表及长表单在各自工作面板内部查看。窄屏保留纵向布局和移动导航。

本轮工作区为 `/Users/a1234/.codex/worktrees/all-pages-one-screen-v3/paperguard`，分支为 `codex/all-pages-one-screen-20261011`。主线基线 `2246e594a888358f3a797cf69efe355986c6c971`；最终源码提交 `909abf5a857c315f1f77c9195b30b51ff1bd3eeb`，完整源码 tree 为 `a0fd2cc964a7cbc080ba4ede6f8295bb4045d965`。分支此前基于上一轮 squash 前提交，已移到实际 main 基线；变基前后完整 tree 一致，验证的源码没有变化。

上一轮管理员专用证据 `admin-workspace-viewport-20261010.md` 保留为历史。本报告及同名目录才是本轮全页面布局的源码、截图与验证依据。

## 实现与关键状态

- 认证后的所有页面共用一屏应用外壳，顶栏、侧栏和工作区按可视高度分配空间。
- 首页、各角色工作台、模板与规范、组织、批次及工作流、用户管理、AI 设置、审计、运行治理、文档列表、发布器与编辑器在内部工作面板滚动。
- 写作页保留命令栏与三栏布局，目录、正文和辅助面板分别查看长内容；草稿、编辑器实例、选择位置和失败重试行为保持通过。
- 学生“下一步处理、阶段任务、最近提交”三个折叠抽屉仍默认收起；同时展开时内容可达。
- 首次登录表单采用内部滚动，长错误提示不会让提交按钮不可达；导航失败横幅与页面共用可视高度。
- 滚动区留出聚焦边距，首页内容底部保留空间，避免底部入口被边缘裁切。

修改文件：`frontend/src/layouts/AppShell.vue`、`frontend/src/style.css`、`frontend/src/views/Writer.vue`；对应回归为 `frontend/e2e/all-pages-viewport.spec.ts` 与 `frontend/e2e/ui-refactor.spec.ts`。API、权限、数据库及部署配置未变。

## 本地验证

| 检查 | 最终结果 |
| --- | --- |
| `frontend: npm run typecheck` | PASS |
| `frontend: npm run build` | PASS |
| `frontend: npm test -- --run` | 23 文件、99 测试通过 |
| `frontend: npm run check:college-admin` | 结构检查通过、4 文件 5 测试通过 |
| `frontend: npm run test:paragraphs` | PASS |
| `backend: python -m pytest -q`（项目 Python 3.12 venv） | 1098 通过、2 条依赖弃用警告 |
| 三套浏览器回归：all-pages-viewport、admin-workspace-viewport、ui-refactor | 21 通过、0 失败；5 个桌面专用矩阵用例在移动 project 按范围跳过 |
| `git diff --check` | PASS |

浏览器命令在 `frontend/` 执行：`npm run test:e2e -- e2e/all-pages-viewport.spec.ts e2e/admin-workspace-viewport.spec.ts e2e/ui-refactor.spec.ts --reporter=line`。使用临时本地后端、worker、SQLite 和测试账号，未访问生产。桌面专用矩阵的跳过不代表移动端全路由验收；原管理员与 UI 套件在两个浏览器 project 均执行。

矩阵覆盖当前路由注册表全部 22 个路由模式（含认证页面、教师专属页面与未匹配页面），以及工作流的归档、报告、消息子面板；管理员 20 个路由及子面板在 1280×720 与 1384×817 执行，另验证首页上传检查页签。42 个管理员/首页几何采样的实际页面尺寸均等于可视尺寸，页面与隐藏外壳各层的滚动位置均为 0。逐个聚焦所有可见可用控件，并在其工作面板内将完整控件滚动到视口内，断言完整可见，同时禁止隐藏外壳发生滚动。多行输入框的原生聚焦会先显示光标，完整输入区的可达性通过面板内滚动核验。

另外覆盖教师工作台及指导学生页、学生抽屉展开、长首次登录错误、真实页面资源加载失败横幅、公开登录/注册和身份认证。管理员既有回归仍覆盖四种桌面尺寸、长列表、中英文、加载、空态、失败重试与移动导航。

## 证据与交付边界

证据目录 `docs/evidence/all-pages-viewport-20261011/` 保存 1280×720 各页面截图、特殊状态截图、1384×817 管理员截图与移动端代表截图。`viewport-geometry.json` 记录 42 个采样；`verification-summary.json` 记录最终命令与结果；`source-identity.json` 记录完整源码提交、逐文件摘要、源码补丁摘要及截图/JSON 摘要。

本轮源码已形成 PR #38 的可审查改动，尚未合入 main，也未部署到生产。旧一轮“提交推送部署”的授权属于 PR #37，不能作为本轮生产授权；本次确认对应全页面实施方案。CI 结论必须另行绑定 PR 当前 head，不能复用旧 head 的结果。

这些结果属于本地自动化与截图验证，不代替生产浏览器点击和真实业务 UAT。既有 `DeadlineLabel` / `StatusBadge` 组件解析警告未在本轮修复；单学校试点、租户隔离、AI 与小程序真机边界保持原有口径。此目录不保存认证材料、生产数据或完整测试日志。
