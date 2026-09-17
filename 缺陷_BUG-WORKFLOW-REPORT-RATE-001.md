# BUG-WORKFLOW-REPORT-RATE-001

- 状态：已修复（本地未提交，2026-09-16）
- 日期：2026-09-16
- 仓库：`/Users/a1234/Documents/perpetual/paperguard`
- 分支：`phase/1-miniprogram`
- HEAD：`688c136`
- 环境：本地隔离 Vite `http://127.0.0.1:15173` + API `http://127.0.0.1:18000`，学院管理员浏览器点击

## 现象

学院管理员进入 `/workflow?panel=reports`（工作台「统计报表」或业务闭环页内「统计报表」）后，页面被 `AppErrorBoundary` 捕获，显示「页面暂时无法显示」。同一账号的 `/workspace` 实时报表区块和 `/api/reports/cohorts/{id}` 均正常。

## 期望

统计报表面板应展示批次成员数、待审核、需修改、已归档和及时率，而不是整页崩溃。

## 根因

`frontend/src/views/Workflow.vue` 第 141 行模板调用 `rate(report.metrics?.on_time_first_submission_rate)` 和 `rate(report.metrics?.archive_completion_rate)`，但该文件未定义 `rate`。`AdminWorkspace.vue` 有同名函数：

```js
function rate(value) { return value === null || value === undefined ? '-' : `${Math.round(Number(value) * 100)}%` }
```

报表 API 在有批次数据时会返回 `report`，模板渲染到 `rate()` 即抛 `ReferenceError`，被错误边界吞掉。

## 复现

1. 以 `college_admin` 登录隔离前端。
2. 创建并发布至少一个批次阶段。
3. 打开 `/workflow?panel=reports`，或在业务闭环点击「统计报表」。

## 影响

学院管理员无法从业务闭环查看详细报表；工作台摘要不受影响。不是作用域越权，是前端渲染缺陷。

## 修复

`frontend/src/views/Workflow.vue` 增加与 `AdminWorkspace.vue` 相同的 `rate()`。回归：`frontend/tests/Workflow.reports.test.js`（vitest 渲染 80%/50%，无错误边界）。证据：[测试报告_测试修复闭环_20260916.md](./测试报告_测试修复闭环_20260916.md)。
