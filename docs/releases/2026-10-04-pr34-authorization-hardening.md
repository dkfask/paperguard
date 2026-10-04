# PR #34 授权基础加固与证据（2026-10-04）

> 结论：**局部代码本地验证通过；共享 SaaS 严格放行未通过；最新远端 CI 未验证。**
> 分支：`fix/ui-review-findings-20260929`。代码提交：`c6d3916111fc8d7075d0cf456c05ade42f5bc24a`。
> 前置 HEAD：`4bcedb7952ec97fe56b697f8321449d5aa6d8b9e`；本地 base：`1cbc8f5f2e528230fd9cded9d766ac6ff37acd21`。
> 本文件是验证记录，不是合并、部署、生产迁移或商业收费授权。

## 本轮变更

1. 认证只读取已有租户成员，不再在登录请求中依据 `school_id` 自动建租户/成员；多成员不按最近更新时间猜选。
2. 空能力集合明确拒绝。租户管理员不继承平台运维能力；成员 API 拒绝未知角色与平台能力；租户列表尊重活动账号与租户读取能力。
3. 租户停用、成员撤权和显式无效上下文 fail-closed。全局管理员依赖同时要求 `status=active`。
4. 成员更新保留 ID；角色、能力或状态变化在同一 UPSERT 中递增 `auth_version`；相同授权重复保存不递增。
5. 认证依赖改为异步，数据库读取在线程池执行；请求任务中设置 ContextVar，同步和异步端点均可读取同一身份，后续匿名请求不继承它。
6. AI 租户/成员校验先于 BYOK 读取；配置读写、配置测试、状态与 assist 入口均前置拒绝已登记但撤权/停用/未选定的身份。未知 `tenant-legacy-*` 不能绕过；仅通过真实租户与成员验证后保留精确匹配的存量学校配置别名，避免意外改道平台供应商。
7. 新增 49 个授权回归用例；SaaS 基础测试显式准备已批准成员关系，不再固化认证时自动映射。补齐管理员计量测试对合法 `system_db` 来源的识别，消除文件执行顺序导致的假失败。
8. 登记 11 个新 HTTP 错误码，迁出已实现占位码；资源清单补齐 13 个 Store 方法和 3 张基础表，更新为 124 个公开方法、219 条路由、131 个写操作。基础表标记 `registered_gap`，不冒充全资源隔离完成。
9. 重绑仍存代码的规格引用，更新 W-03/W-07/W-08 的局部实施事实、设计统计与收口简报。没有改动门禁判据，也没有把 strict 换成 ci。

## 本地验证

环境：仓库 `.venv`，Python 3.12.14。测试使用临时数据库和测试凭据/上游替身，不代表生产供应商或 UI 验收。

| 入口 | 结果 | 证据边界 |
|---|---|---|
| `.venv/bin/pytest -q backend/tests` | **858 passed** | 完整本地后端回归；存在 2 项测试依赖弃用提示 |
| `.venv/bin/pytest -q backend/tests/test_tenant_authorization.py` | **49 passed** | 本轮授权、上下文、AI 前置拒绝及兼容别名用例 |
| `npm test`（frontend） | **84 passed / 20 files** | 前端单元/组件测试，非浏览器 UAT |
| `npm run typecheck`（frontend） | **通过** | 类型检查 |
| `error_codes_check.py` / `resource_registry_check.py` / `spec_citation_check.py` | **通过** | 登记一致性与引用核对；引用通过不等于全部规格语义已实现 |
| `secret_scan_check.py` | **通过，547 个文件（含本记录）** | 未检出疑似秘密或冲突标记，非完整渗透审计 |
| `git diff --check` | **通过** | 差异空白检查 |

## 严格门禁：未通过，5 项真实缺口

实际执行：

```bash
env -u DESIGN_DOC_CHECK_DEPTH .venv/bin/python docs/tenancy/tools/run_all_gates.py --mode strict --json
```

使用真实默认设计文档与 `docs/tenancy/设计阶段收口简报_20261001.md`，未传错误的实施状态简报；未人为抬高递归深度，未用 ci 模式替代 strict。

- **42 项调用：37 通过、5 GAP、0 BROKEN，退出码 1。**
- 套件报告 `input_drift=[]`；另对 181 个源码/测试/设计输入前后 SHA-256 复核，无漂移。提交前输入仍与该冻结快照一致。
- 设计文档、自检、逐表归属、错误码、资源登记、规格引用、秘密与空白检查均通过。

| 未通过项 | 真实缺口 |
|---|---|
| `contract_readiness_check.py::plain` | 4 项 contract 准入前提未满足，含全局资源归属未裁决与空库不能证明 NOT NULL 处置；不得执行 contract |
| `context_resolution_check.py::plain` | 完整不变量/状态机未完成，126 处全局角色引用仍在；CI-5 没有机器检查 |
| `api_contract_check.py::plain` | 统一契约 7/9 项未达标；131 个写操作尚无目标 command 元信息 |
| `synthetic_data.py::plain` | 角色/支持授权等对象缺失，完整多租户 AT 前置数据无法准备 |
| `account_path_check.py::plain` | 教师导入/微信建号等路径缺可确认组织归属，切换 RLS 会产生静默不可见风险 |

这些缺口保留原有失败声明，**没有改成通过**。局部本地回归通过不是共享 SaaS 可上线的证据。

## 外部证据与发布标记

- 原生 PR checks 工具返回要求登录 ChatGPT 连接 GitHub；最新代码提交的 CI/review 结论 **未读取**，没有 CLI 诊断绕过。
- 本轮只做源代码/测试/文档与 Git 交付；**未合并、未部署、未执行生产迁移或生产数据写入、未调用真实供应商、未收费、未做严格 UAT**。
- 最终文档提交的 SHA 与推送状态由终端交付记录绑定；其 source/tests 与上述代码提交保持一致。

## 下一阶段，不属于本轮已完成范围

先补完整租户选择与请求上下文协议、建号归属裁决和迁移前置条件，再推进业务资源强制 tenant 隔离与 PostgreSQL RLS。随后处理租户外发政策/成员凭据域、可信供应商 usage、原子结算与 unknown 对账、目标恢复。权益账务、支付和 C1/C2 放行继续阻塞。
