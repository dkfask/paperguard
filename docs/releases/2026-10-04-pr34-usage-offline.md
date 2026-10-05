# PR #34 离线可信 usage/mock 计量切片（2026-10-04）

基线 `dfb91fd5c05d93601f0adc2f20c309d17ce86794`；源码 `6cf37f902b6ff453611c505c17d1a3b929e3cc29`。分支 `fix/ui-review-findings-20260929`，PR #34。本轮不重做前阶段归属/建号/写作隔离。

## 推送与外部证据

只执行一次普通HTTPS+单命令HTTP/1.1非强制push，将前4提交同步的尝试 **exit128 / Empty reply from server**。随后立即停止网络重试；未改永久代理/凭据、未重注入token、未force push。最新远端分支/refs/pull/34/head未核验，推送外部结果未确认，不能把本地dfb91fd写成远端交付。

因此未满足“push成功后读取原生CI”的条件，本轮没有新CI证据；此前原生工具要求ChatGPT登录只是历史事实，不推断当前状态。不CLI绕过、不反复登录。下一次联网须先reconcile远端再决定push，不盲目重发。

## 实现及有限结论

- OpenAI/Anthropic非流式成功响应保留供应商usage；冻结usage数据与str兼容返回。缺失/部分/异常整数等为unavailable/null，不造0、token总数、费用。供应商请求id只保留不可逆引用；缓存计数独立保存，不聚合成收费事实。
- 当前请求持久化身份绑定tenant/member/user，`ai_usage_fact`旁表严格INSERT，与completed/旧估算列/审计同事务。当前账号/成员/租户/版本失效或记录失败则unknown，不能自动重发/退款。
- 旧估算保留`input_tokens_estimate`字段；新provider token与quality显式投影。计量缺失复核标记不是完整队列或供应商对账；billable恒false。
- 旁表复用SQLite workflow_records，无新增生产DDL/迁移/真实业务写入。通用Store的upsert仍存在，服务严格INSERT不等于数据库级不可变/RLS；后续PG usage/财务ledger另建合同。供应商回报也不等于经账单或签名独立验证。

## 本地证据

证据目录 `/tmp/paperguard-pr34-ownership/`，不含密钥或供应商原始payload。

| 检查 | 结果/文件 |
|---|---|
| 前阶段身份与无漂移复验 | dfb91fd干净、tracking领先4；旧source/strict manifest无漂移；前阶段987/84/38 PASS+5 GAP有效但不冒充本轮结果 |
| 本轮后端全量 | `usage-backend-final-second.log`：**1023 passed**、2依赖弃用警告、exit0 |
| 本轮mock/相关回归 | `usage-focused-final-third.log`：**96 passed**，其中新mock36；真实协议→唯一计量→HTTP/事实/投影、缺测量、坏字段、撤权、审计回滚、unknown不重派 |
| 前端/typecheck | `usage-frontend-final.log`：**84 passed /20 files**；`usage-typecheck-final.log`通过，均exit0 |
| API/出站/引用/资源 | 独立工具日志；全站契约仍5/9未达标；无新增出站/派发点，13规格引用核对；资源新增sidecar入口，无新增表/Store方法/路由 |
| Strict | 最终默认深度43次实跑 **38 PASS / 5 GAP / 0 BROKEN**，input_drift=[]，strict exit1；JSON为 `usage-strict-final.json`，不是严格放行 |
| 审查/卫生 | 本轮仅自审；git diff --check、仓内secret扫描与source/strict输入manifest分别核验；不声称独立PR批准 |

## 尚未放行

原五GAP仍保留：contract_readiness、context_resolution、api_contract、synthetic_data、account_path。后端mock通过不能代替供应商账单核对、PG/RLS或整套AT。

租户域幂等仍未实现（user+key唯一键，跨tenant同key拒绝），原子额度预留/财务结算、unknown最终供应商对账/校正、流式协议/账单来源、全资源/文件/async/RLS隔离及真实UAT仍未完成。本轮未合并、部署、生产迁移/恢复/业务写入或任何真实支付/付费供应商调用。源码与文档只做本地提交；网络已停止，不声称新增远端交付。
