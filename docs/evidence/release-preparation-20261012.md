# 2026-10-12 发布准备与本地回归

本记录对应用户明确选择的 `all-pages-one-screen-v3` 工作区和 `codex/all-pages-one-screen-20261011` 分支。交付基线为 `5971f506d889caa4bcaa26f751ddd66cf067460d`，目标 PR 为 #38；最终交付提交和远端 CI 以 PR 最新 head 为准。

## 发布范围

- 桌面一屏布局、按可用高度分页、保留草稿的内容切换、详情抽屉及嵌套弹窗焦点。
- 管理员、教师和学生的认证、阶段发布、历史提交、退回批注与回复、通知直达和归档名称等流程修复。
- 教师个人写作与学生论文模板发布分开；教师新通用模板、规范候选及文档包由服务端记录真实所有者。其他角色不能列出、直接读取、确认其私有候选，或用该包创建文档；已有共享包保留原组织范围。
- 模板导入预览的中英文摘要、关键词名称与标题分组；写作章节大纲常驻页面。

数据库迁移账本、后端依赖及生产配置未变化。相对现网后端 `46dc35071a21c33fa094760344ece895ed712fb0`，应用变化仅为两处通用文档 API 及两处候选/访问服务。仍以单学校试点为边界。

## 本轮验证

后端使用项目 Python 3.12 venv；测试数据库、上传目录和浏览器服务均为隔离夹具。

| 原生检查 | 本轮结果 |
| --- | --- |
| `backend/`：`python -m pytest -q` | 1100 通过，2 条既有依赖弃用警告 |
| `frontend/`：`npm run test` | 30 文件、128 项通过 |
| `npm run typecheck` | 通过 |
| `npm run check:college-admin` | 结构检查及 5 项通过 |
| `npm run test:paragraphs` | 通过 |
| `npm run build` | 通过 |
| 仓库根：`docker compose build` | backend、worker、frontend 构建通过；此阶段尚未生成发布身份 |
| 全量浏览器首轮：`npm run test:e2e -- --update-snapshots=none` | 75 通过、14 按配置跳过、3 失败，原失败保留 |
| 受影响全页矩阵：`npm run test:e2e -- e2e/all-pages-viewport.spec.ts --update-snapshots=none` | 9 通过、9 按配置跳过，退出码 0 |
| 受影响组件走查：`npm run test:e2e -- e2e/component-walkthrough.spec.ts --update-snapshots=none` | 10 通过，退出码 0 |
| `git diff --check` | 通过 |

初次失败经独立隔离服务定向复现，均为 TEST-DEFECT：两项全页检查沿用大纲在抽屉中的假设，将常驻大纲的内部滚动误判为主页面滚动；另一项对比度检查只寻找页面顶栏按钮，未覆盖规范空状态已获确认的唯一操作组。分别只修正对应区域的选择器。整页/主面板的溢出阈值、控件完整可见断言、按钮必须存在及文字对比度 ≥4.5 的要求保留。

全量执行后的应用、后端和其他测试文件摘要未变化，只有上述两个浏览器测试文件调整；两文件全部用例均已重跑。按文件、浏览器项目和用例名绑定最近有效结果，最终 **92 项唯一用例为 78 通过、14 既定跳过、0 失败、0 flaky**。这不是声称首轮全量命令退出码为 0。逐项结果见 [browser-summary.json](release-preparation-20261012/browser-summary.json)，源文件摘要见 [source-checksums.json](release-preparation-20261012/source-checksums.json)。跳过不计为通过，移动模拟不代表微信真机或完整 UAT。

本轮未自动更新视觉基线。现有截图及报告检查未发现可用凭据；新增报告仅记录结果与摘要，不包含 trace、数据库、下载文档、密码、token 或 Cookie。

## 线上只读核验与发布边界

- 目标为现有 SSH 别名 `perpetual`：`ubuntu`，`43.167.211.145:8080`；受控无密码认证及 sudo 检查成功。
- 对外首页、live、ready、version 均返回 200；后台 API、worker、nginx 为 active。OpenAPI 已包含通用文档包路由。
- 当前发布标记：`46dc350-ui-2246e59-b20261010T155646Z`；后端版本接口声明 `46dc35071a21c33fa094760344ece895ed712fb0`。前端清单摘要与 JSON 标记一致，为 `bd093fcef715aa08adfde30bea7ca399b1a8a0f2afd8840c3b1e6f3fb6685388`。
- 运行数据在 `/home/ubuntu/paperguard/backend/data`，约 7.1 MB；服务从 `/etc/paperguard/paperguard.env` 加载受控配置，未读取秘密值。
- GitHub Environment `paperguard-online-verification` 的三项配置键存在；这只证明配置键可见，不代表本轮线上验证已通过。

发布前仍须以最终提交生成不可变后端身份、制品摘要，并核实该完整 SHA 的最新 CI。具体部署待用户确认目标、窗口与回滚负责人；授权后按 README 完成当前源码/前端/依赖及数据库/资产的一致备份、副本演练，保留当前版本作为回滚目标，再部署和对账。此记录创建时未推送本轮新提交、未合并、未部署、未修改生产数据，历史本地证据不替代这些状态。
