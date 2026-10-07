# PR #34 历史归属准入与安全建户/写作切片交付记录

日期：2026-10-04。分支：`fix/ui-review-findings-20260929`。源码提交：`1154b943ed2801c82d649e4b365fafb259f85e3e`。前阶段 `d876798` / `a754b818` 已交付；本轮不重复推送修复、不修改永久凭据/代理，也不再次注入token文件。

## 实现与禁止边界

[A/B/C实现、未决项与接口范围](../tenancy/implementation-status-20261004.md)。本轮只是安全底座与一条实际写作链；不是完整W-01～W-13验收、生产共享数据库或付费产品放行。

本轮无合并、部署、生产DDL/DML/恢复/业务写入、真实UAT、真实供应商/支付调用；平台批准只在本地临时测试夹具执行。数据库与前端测试均使用合成/临时输入。源库真实预检只读。

## 本地离线证据

本地证据目录：`/tmp/paperguard-pr34-ownership/`；其中部分中间失败日志仅用于定位，不替代最终结果。

| 项目 | 已取得证据 |
|---|---|
| 后端全量 | `backend-final-fourth.log`：987 passed、2依赖弃用警告；exit0 |
| 聚焦基础切片与备份 | `focused-final-third.log`：97 passed；预检24+pilot63+既有备份10 |
| 前端 | `frontend-final.log`：20 files / 84 passed；exit0 |
| typecheck | `typecheck-final.log`：vue-tsc通过；exit0 |
| 源码/测试/工具绑定 | `source-test-manifest-final.json`记录实际测试输入摘要；交付前复查不漂移 |
| 本地真实源只读准入 | `local-ownership-final.json`绑定源码1154b94；BLOCKED、production_authorized false；DB及sidecar与阶段前相同 |
| 真实差距分类 | 资源缺失/冲突30、映射结构1/批准1、账号归属12、旧admin批准2、指导规则批准1；不是旧回填quarantine22 |
| 资源/错误码/规格登记 | 对应最终日志；Store公开127/总136/构造18，AST Depends219、230 routes/138 writes/trusted19；错误码HTTP67，字典已实现并集76/占位17；445规则、13规格引用通过 |
| 严格门禁 | 43次调用的最终JSON以 `strict-final.json` 为准；最终默认深度实跑 **38 PASS / 5 GAP / 0 BROKEN**、input_drift=[]、exit1；24项自检与19次plain均执行；不是strict放行 |
| 空白/敏感审查 | `git diff --check`及仓内secret_scan通过；不会把token文件或外部账号内容提交 |

## 保留的真实五道GAP

- contract_readiness：旧回填隔离22未清零、模板/全局要求归属未裁决、workflow_events无样本。
- context_resolution：121处存量角色读取；完整成员组织授权/全资源替换未完成。
- api_contract：5/9未达标；203操作无security/匿名声明、215无业务错误、9实体无tenant_id、9宽松organization_context、129/138写操作无command。
- synthetic_data：全套AT前置夹具仍缺5项；不能用本轮87个新局部测试冒充全套AT。
- account_path：3条旧学校建号路径仍欠组织合同；全局账号建号不等于自动授予SaaS成员关系。

## 推送、CI、review与运行身份

本记录绑定源码1154b94（包含072f398安全底座与b7199fc手写块收紧），随后文档提交不改源码/测试/工具/门禁事实输入。交付阶段用普通HTTPS+单命令HTTP/1.1推送；实际结果保存于本地证据目录的 `push-final.log` / `remote-after.txt`，仅远端SHA等于交付HEAD时认定已推送，不拿认证成功替代交付证据。用户已重新认证；本轮原生CI诊断仍返回“Sign in to Codex with ChatGPT to connect GitHub”，因此CI未验证。仅使用原生CI诊断，不以CLI绕过，不反复登录。自审通过仅覆盖本轮diff，不声称独立review或合并放行。运行与生产身份未变更。
