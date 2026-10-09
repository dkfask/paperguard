# 学生工作台折叠抽屉（2026-10-09）

> 本页为抽屉实施阶段的历史记录；同步主线后的提交门禁见 [合入验证](./ui-refactor-merge-validation-20261009.md)。

用户圈选“下一步处理、阶段任务、最近提交”，随后明确选择三个独立的可展开/收起抽屉，默认收起。

## 实现

- 仅调整 StudentWorkspace 的三块内容，使用原生 details/summary，点击标题或按 Enter/Space 即可切换。
- 收起时保留标题、真实任务/提交数量与箭头；展开时显示原有任务、空态、历史记录和业务入口。
- 三个抽屉可分别展开，关闭一个不影响其他抽屉。保持原 API、权限与路由。
- 窄屏适配、焦点样式和减少动画偏好已覆盖，箭头使用本地 SVG 图标。
- 用户提供的圈选 PNG 保持原样，新的预览另存。

## 验证

- npm run typecheck：通过。
- npm run test：23 文件、96 测试通过。
- npm run check:college-admin：结构检查及 5 测试通过。
- npm run build：通过。
- npm run test:e2e -- ui-refactor.spec.ts：桌面与手机共 6 项通过，退出码 0。覆盖默认关闭、三个独立开关、Enter/Space、关闭后内容隐藏、业务链接、溢出和原有写作回归。
- git diff --check：通过。
- 后端未修改；同一工作区本轮既有后端 1066 项基线仍适用。没有提交、推送或部署，也没有独立 UAT/线上验证。

验证绑定本地未提交工作区：fix/ui-review-findings-20260929，HEAD a64e8cdc56e3db76b691c4d4c054452d9f759173。

前端源码/测试/脚本/config 快照 SHA-256：`c4ee4ef00cc32b2c362454e10fd037e6c674d0d7d68b66050dc0679a1a6c2ec9`。快照计算方法与 [原 UI 交付记录](./ui-refactor-delivery-20261009.md) 一致；本记录补充其后续调整，不改写历史结果。

## 预览

- [桌面默认收起](./ui-refactor-20261009/student-drawers-desktop-collapsed.png)
- [桌面展开](./ui-refactor-20261009/student-drawers-desktop-expanded.png)
- [手机默认收起](./ui-refactor-20261009/student-drawers-mobile-collapsed.png)
- [手机展开](./ui-refactor-20261009/student-drawers-mobile-expanded.png)
