# 管理员仪表盘一屏布局验证（2026-10-10）

## 来源与范围

用户根据学校运行仪表盘截图要求“页面只铺满这个屏幕就行，不要用下拉”。参考文件为 `/Users/a1234/Desktop/截屏2026-10-10 10.24.00.png`，原始像素尺寸为 2768×1634；本轮包含 1384×817 的浏览器验证，作为常见两倍像素缩放下的对应尺寸，未现场读取用户浏览器的实际缩放比例。

本轮在 `/Users/a1234/.codex/worktrees/dashboard-one-screen/paperguard`、分支 `codex/dashboard-one-screen-20261010` 修改。基线为此前已部署的 `a00dbef9e74a7f3b4f886524cacc511a07ee97ca`；本报告记录的是验证采样时的本地状态：当时修改尚未提交、推送、合并或部署。原项目目录的已有 UI 草稿和上次发布证据保留；本次交付、主线合入和生产状态以同一提交绑定的后续交付记录为准。

## 实现

- 仅学校和学院管理员的 `/workspace` 在宽度 ≥1101px 时按可视高度分配外层空间；顶栏与侧栏保留，整页无纵向滚动。
- 统计卡和快捷操作收紧间距；批次概览与实时进度并排，归档任务位于下方，消息及治理入口保留在右侧。
- 较矮桌面屏幕采用紧凑间距，正常数据下四项进度指标、及时率/完成率和首条归档记录都能在首屏完整显示。
- 批次、归档、进度和消息内容多时可在卡片内滚动；批次、归档、进度区域支持键盘聚焦，侧栏独立滚动。保留原有列表摘要数量和查看完整业务列表的入口。
- 窄屏沿用纵向布局和移动导航；学生、教师、写作与其他长页面没有套用一屏高度限制。无 API、权限、业务状态、数据库或部署配置变更。

源码变化：`frontend/src/layouts/AppShell.vue`、`frontend/src/views/AdminWorkspace.vue`；浏览器回归：`frontend/e2e/admin-workspace-viewport.spec.ts`。

## 本地验证

| 检查 | 结果 |
| --- | --- |
| `npm run typecheck` | PASS |
| `npm run build` | PASS |
| `npm run test` | 23 文件、99 测试通过 |
| `npm run check:college-admin` | 结构检查通过、4 文件 5 测试通过 |
| `npm run test:paragraphs` | PASS |
| 项目 Python 3.12 venv 中执行 `python -m pytest -q` | 1098 通过，2 条依赖弃用警告 |
| `npm run test:e2e -- e2e/admin-workspace-viewport.spec.ts --project=chromium` | 5 通过，0 失败、0 flaky |
| `npm run test:e2e -- e2e/ui-refactor.spec.ts --project=chromium` | 3 通过；使用临时真实后端、数据库和本地测试账号 |
| `git diff --check` | PASS |

仪表盘浏览器测试使用明确的本地 mock 数据，不访问生产接口。覆盖正常、加载、空态、失败重试、消息标记失败、长名称、多批次/多归档/多消息、中英文、键盘访问及 390×844 移动布局。普通数据下逐项验证所有卡片标题、四个实时进度指标、比率说明及第一条归档记录完整位于可视区，滚轮不会推动整页。

| 浏览器可视尺寸 | 页面实际尺寸 | 页面滚动位置 |
| --- | --- | --- |
| 1384×817 | 1384×817 | 0 |
| 1280×720 | 1280×720 | 0 |
| 1440×900 | 1440×900 | 0 |
| 1920×1080 | 1920×1080 | 0 |

## 证据与边界

证据目录：`docs/evidence/admin-workspace-viewport-20261010/`。

- `viewport-geometry.json`：四种桌面尺寸的实际页面尺寸与滚动位置。
- `verification-summary.json`：项目命令、本地结果和浏览器用例摘要。
- `source-identity.json`：基线 SHA、分支、源码文件摘要与未提交补丁摘要。
- `admin-workspace-1384x817.png`、`admin-workspace-1280x720.png`、`admin-workspace-1440x900.png`：一屏截图。
- `crowded-dashboard-english.png`：长列表、中英文切换与错误提示状态。
- `admin-workspace-mobile.png`：移动端全部内容仍可访问。

本轮是本地实现与浏览器验证，浏览器夹具不是生产业务人工 UAT。上一生产发布的授权与验证不自动覆盖本次新增布局；本次发布需以新的具体提交、CI 和部署记录核验。原有学生回归仍出现 DeadlineLabel/StatusBadge 组件解析警告，本次三个既有浏览器用例通过；该警告未扩展为本轮修复任务。单学校试点、租户隔离和小程序真机边界保持原有口径。
