# AI 可信计量能力清单与未知结果 Runbook（27.3 文档包第 5 项）

> 状态：**design_ready**。本文是取证 + 处置方案，**不是已完成的实现**。
> 日期：2026-10-01。基线：HEAD `e166f97`（含本轮未提交的 F-1/F-5 修复）。
> 上游：总设计第 13.1 节（AI 边界）、第 12 节（计量与账务）、第 5 项文档包；`command-contracts.md` §4；`api-contract-delta.md`。
> 授权边界：**只读取证**。未修改业务代码，未调用任何上游，未连接生产。

---

## 0. 结论摘要

F-1 修复让**每一次真实派发都被记账**了。但「记账」与「可信计量」是两件事——**当前记账结果不足以支撑成本核算或按租户分摊**。

| 编号 | 发现 | 证据 |
|---|---|---|
| **M-1** | **上游返回的精确 token 数被丢弃**。两条协议路径都只取文本，丢弃 `usage` | `api/ai.py:471`（OpenAI）、`:262`（Anthropic）；全仓**无** `prompt_tokens`/`input_tokens`/`cost` 字段 |
| **M-2** | **一次逻辑请求可能产生两次真实计费派发**，但只记 1 条账 | `api/ai.py:444-457` 的 `/v1` fallback 循环，最多 2 次 `client.post` |
| **M-3** | **超时是典型的「未知结果」，但当前按「失败」处理** | `api/ai.py:448-449` / `:254-255` → 504，`upstream_error` 且 `output_chars=0` |
| **M-4** | **记账数据无任何查询入口**。`ai_usage_summary` 只有测试在调 | 全仓调用点 9 处，**全部在 `tests/`**，生产 0 处 |
| **M-5** | **`refund_call` 是生产死代码** | 仅 `ai_governance.py:113` 定义 + `test_ai_governance.py:173` 调用 |
| **M-6** | **「风控尝试」与「真实消耗」没有分开记录** | 全仓无 `ai_attempt` / `quota_denied` 概念（grep 0 命中）；`ai_usage` 只在**真实派发后**写入，被限流/熔断/出站拦下的尝试**不留任何痕** |

**M-1 + M-2 + M-3 合起来的含义**：当前 `ai_usage` 记录的是**「我们自己发起并收到响应的请求数」**，而不是**「供应商实际计费的量」**。两者在 fallback 和超时场景下会系统性偏离，且**偏离方向是低估**。

> 因此本文档的立场是：**在 M-1/M-2/M-4 修复前，不得把 `ai_usage` 的任何数字写进对客户的计费承诺、对账报告或成本分摊。**

---

## 1. 证据边界与方法

| 项 | 状态 | 方法 |
|---|---|---|
| M-1 token 丢弃 | **已验证（代码级）** | 读 `api/ai.py` 两条协议的响应解析；全仓搜 `prompt_tokens`/`completion_tokens`/`input_tokens`/`output_tokens`/`cost` 均 0 命中 |
| M-2 多次派发 | **已验证（代码级）** | `api/ai.py:444` `for idx, url in enumerate(candidate_urls)`，`:447` `client.post`；`:456` 在 404/502/503 时 `continue` 进入下一候选 |
| M-3 超时语义 | **已验证（代码级）** | `:448-449` OpenAI 路径、`:254-255` Anthropic 路径，均直接 `raise 504` |
| M-4 无查询入口 | **已验证（全仓）** | `grep -rn ai_usage_summary backend/` → 9 处，全在 `tests/` |
| M-5 死代码 | **已验证（全仓）** | `grep -rn refund_call` → 定义 1 处 + 测试 1 处 |
| **各供应商实际计费规则** | **未验证** | 未调用任何上游；需查各供应商合同/账单，见 §6 待核实清单 |
| **未知结果的真实发生频率** | **未验证** | 生产无遥测（M-4），无法统计 |

---

## 2. 当前计量链路

```text
业务入口（7 个派发点）
   └─ _metered_chat                        ← 唯一出口（F-1 修复后）
        ├─ assert_safe_outbound_url        SSRF 守卫（F-5 修复后）
        ├─ ai_governance.begin_call        扣突发 + 日配额
        ├─ _chat_completion                熔断判断
        │    └─ _dispatch_completion
        │         ├─ Anthropic 路径  → 1 次 POST
        │         └─ OpenAI 路径     → 1~2 次 POST（/v1 fallback）
        └─ ai_governance.record_usage      写 ai_usage
```

`ai_usage` 表结构（`db.py:192-208`）：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | — |
| `user_id` | TEXT | 调用者 |
| `organization_id` | TEXT | **调用时**快照，非查询时关联 |
| `action` | TEXT | 如 `polish` / `config_test` / `policy_import` |
| `source` | TEXT | `user` / `system` / `system_env` |
| `model` | TEXT | 模型名 |
| `base_url_host` | TEXT | 主机名（`urlsplit().hostname`） |
| `input_chars` | INTEGER | **字符数，不是 token 数** |
| `output_chars` | INTEGER | **字符数，不是 token 数** |
| `result` | TEXT | `success` / `upstream_error` |
| `created_at` | TEXT | UTC，秒精度 |

> `organization_id` 采用**调用时快照**而非查询时关联用户表，这个设计是对的：用户换学校后历史用量的归属不应跟着漂移，计费口径才成立。见 `ai_governance.py:_user_organization_id` 注释。

---

## 3. M-1：精确 token 数被丢弃

### 3.1 现状

OpenAI 兼容路径（`api/ai.py:469-471`）：

```python
data = resp.json()
return data["choices"][0]["message"]["content"].strip()
```

响应体里的 `usage: {prompt_tokens, completion_tokens, total_tokens}` **被整段丢弃**。

Anthropic 路径（`api/ai.py:262`）：

```python
return _anthropic_response_text(resp.json())
```

同样只取 `content` 文本块，丢弃 `usage: {input_tokens, output_tokens}`。

> **复算时的坑**：`grep -c usage app/api/ai.py` 会返回 **2**，但那两处是**我们自己**的
> `ai_governance.record_usage(...)` 调用（`:405`、`:411`），**不是**供应商返回的
> `usage` 字段。判断标准应当是 token 字段名——`prompt_tokens` / `completion_tokens` /
> `input_tokens` / `output_tokens` / `total_tokens` 在全仓均为 **0 命中**。
> 只看 `usage` 这个词会得出「读了」的相反结论。

### 3.2 后果

| 影响 | 说明 |
|---|---|
| **成本不可核算** | 不同模型的 token 单价差异达 10 倍以上。按字符估算误差不可控 |
| **中英混排误差放大** | 1 个汉字 ≈ 1~2 token，1 个英文单词 ≈ 1~2 token。同一段文字在两种内容下字符数相同而 token 数可差 2 倍 |
| **无法做精细配额** | 配额是「次数」而非「成本」，重度用户与轻度用户消耗相同额度但成本可能差一个量级 |

### 3.3 目标

`ai_usage` 增加三列，并保留字符数作为兜底：

| 新列 | 类型 | 来源 |
|---|---|---|
| `input_tokens` | INTEGER NULL | OpenAI: `usage.prompt_tokens`；Anthropic: `usage.input_tokens` |
| `output_tokens` | INTEGER NULL | OpenAI: `usage.completion_tokens`；Anthropic: `usage.output_tokens` |
| `usage_source` | TEXT | `provider`（上游给了）/ `estimated`（上游没给，本地估算）/ `unknown` |

**兼容策略**：上游可能不返回 `usage`（部分自建推理服务、代理网关会剥掉）。因此 `usage_source` 必须显式区分三种，**不允许把 `estimated` 当 `provider` 用**。

**本地估算兜底**（仅当上游不给）：中英混排的粗略公式

```text
tokens ≈ ceil(cjk_chars * 1.0 + non_cjk_chars / 3.5) + max_tokens_reserve
```

该公式**误差可达 ±30%**，只能用于趋势监控，**不能用于计费**。这一限制必须写进字段注释，避免下游误用。

---

## 4. M-2：一次逻辑请求、两次计费派发

### 4.1 现状

`api/ai.py:444-457`：

```python
for idx, url in enumerate(candidate_urls):
    resp = client.post(url, json=payload, headers=headers)
    ...
    if resp.status_code in (404, 502, 503) and not is_last:
        continue          # ← 已经真的发过一次了
```

触发 fallback 的三种情形，**每一种都已经产生了一次真实 HTTP 请求**：

| 触发条件 | 第一次请求发生了什么 | 供应商是否可能计费 |
|---|---|---|
| 404 | 请求到达服务器，路由未命中 | 通常不计费（未进入推理） |
| 502 / 503 | 网关错误，**上游是否已转发不确定** | **可能计费** |
| 200 但返回 HTML | 请求被完整处理，返回的不是 JSON | **可能计费**（推理已发生） |

而 `_metered_chat` 在 `_dispatch_completion` **外面**记账，因此无论内部发了几次，只写 **1 条** `ai_usage`。

### 4.2 后果

fallback 场景下**系统性低估**。低估只在「第一次真被计费」时发生，所以：

- 平时看不出问题（fallback 罕见）
- 一旦某供应商网关不稳定、fallback 频繁，该供应商的账单会显著高于 `ai_usage` 之和
- **且无法定位**——`ai_usage` 里没有「这次派发了几次」的信息

### 4.3 目标

`_dispatch_completion` 返回 `(text, dispatch_count, usage_dict)`，让记账能反映真实派发次数：

| 新增字段 | 含义 |
|---|---|
| `dispatch_count` | 本次逻辑请求实际发出的 HTTP 次数（1 或 2） |
| `fallback_used` | 是否走了 `/v1` fallback |

成本核算口径：**按 `dispatch_count` 计**，而不是按 `ai_usage` 行数计。

> **不做「拆成两行」的方案**：一次逻辑请求拆成两条 `ai_usage` 会让「调用次数」这个运营指标失真（用户觉得自己调了 1 次，系统记了 2 次）。正确做法是**一行记录 + 一个计数字段**。

---

## 5. M-3：未知结果（Unknown Outcome）

### 5.1 什么是未知结果

> 请求**已发出**，但**无法判定上游是否已处理、是否已计费**。

这与「失败」有本质区别：

| 结果类型 | 判定 | 上游是否可能计费 | 额度处理 |
|---|---|---|---|
| **未发出** | 出站守卫拦截、参数校验失败 | 否 | 应退还 |
| **明确失败** | 401/403 鉴权失败、400 参数错误 | 否 | 应退还 |
| **明确成功** | 200 且内容可解析 | 是（已计费） | 不退还 |
| **未知** | **超时、连接重置、502/503** | **可能** | **不退还，但必须单独标记** |

### 5.2 现状：超时被当作普通失败

`api/ai.py:448-449`：

```python
except httpx.TimeoutException as exc:
    raise HTTPException(status_code=504, detail="AI 服务响应超时, 请稍后重试") from exc
```

经 `_metered_chat` 后：记 `result="upstream_error"`、`output_chars=0`，**不退还额度**（这部分是对的），但：

- ❌ **没有区分「超时未知」与「明确失败」**
- ❌ `output_chars=0` 会让下游以为「没有产生输出」，而上游可能已经生成了 2000 token
- ❌ 触发熔断 `record_failure`，把未知结果计入熔断失败率

### 5.3 目标：三级结果分类

`ai_usage.result` 从当前的 2 值扩展为 4 值：

| result | 触发条件 | 计费假设 | 熔断是否计数 |
|---|---|---|---|
| `success` | 200 且解析成功 | 已计费 | 否（记成功） |
| `failed` | 4xx 等明确失败 | 未计费 | 是 |
| `upstream_error` | 5xx 明确错误、熔断打开 | **可能计费** | 是 |
| **`unknown`** | **超时、连接重置、连接被拒** | **可能计费** | **单独统计，不直接计入熔断** |

**`unknown` 是新增的关键状态**，它让「我们不知道」成为可查询的事实，而不是被抹平成「失败」。

配套改动：

1. `_chat_completion` 的熔断逻辑对 `unknown` **不调用 `record_failure`**，改为单独累计；`unknown` 占比超过阈值（如 50%）才触发熔断。
2. HTTP 响应体改为 `{"code": "AI_UPSTREAM_UNKNOWN", "message": "...", "request_id": "..."}`，与 `api-contract-delta.md` §3.3 的错误信封对齐。
3. 前端**不得**把 `unknown` 显示为「失败，请重试」——自动重试会在上游已计费的情况下**再计一次费**。正确文案：「上游响应超时，结果未知。系统已记录，本次不自动重试；如需重试请手动操作。」

### 5.4 未知结果的处置 Runbook

| 步骤 | 动作 | 判据 |
|---|---|---|
| **R-1 检测** | `unknown` 占比 > 5%（按供应商 `base_url_host` 分组） | 15 分钟滚动窗口 |
| **R-2 定界** | 按 `base_url_host` 定位是哪家供应商；核对同期上游状态页 | 单供应商突增 → 供应商问题；全部突增 → 本地网络或代理 |
| **R-3 核对** | 取该时段 `unknown` 的 `model` 分布，向供应商**索取该时段账单** | 比对 `Σ input_tokens`（若 M-1 已修）与账单 |
| **R-4 临时处置** | 若某供应商 `unknown` 持续 > 30%：**暂停该供应商的自动重试**，改为提示用户手动重试 | 避免「自动重试 × 未知结果」的成本放大 |
| **R-5 熔断校准** | 连续 3 天 `unknown` > 30% → 调高 `PAPERGUARD_AI_TIMEOUT`（60 → 180s）或换供应商 | 需人工判断，不自动执行 |
| **R-6 复盘** | 每周产出 `unknown` 报表：按 `base_url_host` × `model` × `action` | 识别是特定模型慢还是特定动作输入过大 |
| **R-7 对账** | 月度：账单 token 数 vs `ai_usage` token 数，差异 > 10% 必须归因 | 差异原因写入 `unknown` 报表 |

> **R-4 是最重要的一条**。在 `unknown` 状态下自动重试，会把「可能已计费」变成「确定已计费两次」。这是 AI 产品最常见的成本失控来源。

---

## 6. M-4：记账数据无查询入口

### 6.1 现状

`Store.ai_usage_summary()`（`db.py:906`）实现了按 `user_id` + `organization_id` 的汇总，但**全仓 9 个调用点全部在 `tests/`**，生产 API 零调用。

即：**数据在记，但运营看不到。** 想知道「谁花了多少」只能直连生产数据库执行 SQL——这在多租户 SaaS 里不可接受（无审计、无权限隔离）。

### 6.2 目标端点

```text
GET /api/admin/ai/usage?since=&until=&group_by=&tenant=&limit=
```

| 维度 | 说明 |
|---|---|
| `group_by` | `user` / `organization` / `model` / `action` / `base_url_host` / `result` |
| 返回 | 调用次数、input/output **字符与 token**、按 `result` 的分布（含 `unknown`） |

配套：

```text
GET /api/admin/ai/usage/unknown?since=      # R-1/R-6 的数据源
GET /api/admin/ai/quota                      # 当前配额占用（burst / daily）
```

### 6.3 权限与审计

- 必须 `require_admin`，且**按 `organization_id` 过滤**——平台管理员看全局，机构管理员只看本机构。跨租户越权是本项最需要防的风险。
- 每次查询写 `audit_events`（现有 `AuditEventResponse` 已有 `organization_id`，见 `api-contract-delta.md` §3.1）。
- 响应必须带 `request_id`（AC-04）。

---

## 7. M-5：`refund_call` 死代码

`ai_governance.refund_call()`（`ai_governance.py:113`）在生产**从未被调用**。

**当前不构成缺陷**：F-1 修复后 `_metered_chat` 的顺序是「出站守卫 → `begin_call`」，守卫拦下时额度**还没扣**，所以不需要退还。这比「先扣后退」更干净。

**但它是一枚地雷**：`OutboundBlocked` 的文档注释明确写着「前者应归还调用配额」，而 `refund_call` 的 docstring 又写着「请求尚未发往上游就失败时归还」。两处注释都在引导后来者**在 `begin_call` 之后调用它**——而那个位置的失败可能已经产生真实成本。

### 处置

| 选项 | 判定 |
|---|---|
| 保留但标注废弃 | 保留函数，加 `@deprecated` 说明「当前顺序下不应使用」 |
| **删除** | **采纳**。`git log -S refund_call` 确认无生产调用后直接删，连带删除其单测 |

> 删除前必须跑 `git log -S"refund_call" -- backend/` 确认历史，而不是只看当前 grep。

---

## 8. M-6：风控尝试与真实消耗必须分开记录

### 7.1 为什么这是独立问题

`ai-metering-runbook.md` 前面所有内容都在解决「**真实消耗**记不记得准」。
但 AT-21 的判据是两句话：

> 熔断/配额/出站拒绝 → **没有上游派发，风控尝试与消耗分开记录**。

前半句（「没有上游派发」）已由本轮 F-1/F-5 修复后的 6 条参数化用例守住。
**后半句当前完全没有实现。**

### 7.2 现状：被拒的尝试不留痕

```text
请求进入 _metered_chat
  ├─ 出站守卫拦截   → 抛 400，**不写 ai_usage**，不写任何表
  ├─ begin_call 拒绝 → 抛 429，**不写 ai_usage**
  ├─ 熔断打开       → 抛 503，**不写 ai_usage**
  └─ 真实派发       → 写 ai_usage（success / upstream_error）
```

结果：**运维无法区分以下三种情况**

| 现象 | 可能的解释 |
|---|---|
| 某用户 0 条用量记录 | 他没用 AI？还是他的请求全被限流了？还是出站配置错了？ |
| 用量突然下降 | 用户流失？还是配额被调低？还是熔断一直开着？ |

第二种尤其危险：熔断打开时所有请求被拒，`ai_usage` 一行不增，
看起来像"用户不活跃"，实际是**系统完全不可用**。

### 7.3 目标：两张表，两个语义

> ⚠️ **2026-10-01 已裁定：本节的 `ai_attempt` DDL 被取代，不要照此建表。**
>
> 裁定依据与结论见
> [`W-08-请求派发结果事实与用量实施规格_20261001.md`](../product/W-08-请求派发结果事实与用量实施规格_20261001.md)
> §1.3 与 §7.1：
>
> | 本节原方案 | 裁定后 | 理由 |
> |---|---|---|
> | `ai_attempt`（尝试流水，仅被拒与已派发） | **合并进 `ai_requests`**，用 `state` 表达生命周期 | 总设计 §9.4 命名为 `ai_requests`；`ai_attempt` 是它的**子集**（`received` / `rejected_pre_dispatch`），单建一张表会与 `ai_requests` 重复记同一批请求 |
> | `outcome` 枚举把拒绝拆成 4 个值（`denied_rate_limit` 等） | **保留为正交维度 `rejected_by`**，但**不得**用它分裂 `state` | 本节的核心洞察是对的：熔断/限流造成的「用量归零」必须与「用户流失」可区分。但若塞进状态值，看板按状态求和会重复计数（`DASH-3`） |
> | `ai_usage` 收窄为「真实计费量」 | **不变**（`usage_events` 承担该职责） | — |
>
> **实施时**：按 W-08 §1.1 建 `ai_requests`，**必须**带 `rejected_by` 列
> （`rate_limit`/`quota`/`entitlement`/`circuit`/`outbound_guard`/`authz`/`validation`）。
> 本节保留下文的「为什么要这张表」论证——**它没有被否定，只是被合并了**。

```sql
-- ⚠️ 以下 DDL 已被 W-08 §1.1 取代，保留供对照，不要照此建表。
-- 尝试流水：每一次进入 _metered_chat 都写，无论是否放行
CREATE TABLE ai_attempt (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    tenant_id     TEXT,                    -- 迁移后填充；当前为 NULL
    organization_id TEXT,
    action        TEXT NOT NULL,
    source        TEXT NOT NULL,
    model         TEXT NOT NULL,
    base_url_host TEXT,
    outcome       TEXT NOT NULL,           -- 见下表
    denied_by     TEXT,                    -- rate_limit|circuit|outbound_guard|quota|null
    request_id    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    CONSTRAINT ai_attempt_outcome_chk
        CHECK (outcome IN ('dispatched','denied_rate_limit','denied_quota',
                           'denied_circuit','denied_outbound','upstream_error'))
);
CREATE INDEX idx_ai_attempt_created ON ai_attempt (created_at DESC);
CREATE INDEX idx_ai_attempt_denied   ON ai_attempt (denied_by, created_at DESC)
    WHERE outcome <> 'dispatched';        -- 部分索引：只索引被拒的

-- ai_usage 保持不变，但语义收窄为「真实计费量」
-- result 取值扩展见 §5.3
```

| 字段 | 语义 |
|---|---|
| `outcome` | 这次尝试的**唯一结局**，六值封闭 |
| `denied_by` | 被谁拦下的；`dispatched` 时为 NULL |
| `request_id` | 与结构化日志、`ErrorBody.request_id` **同一串**，可三方关联 |

### 7.4 与 `ai_usage` 的关系

```text
ai_attempt                     ai_usage
─────────────────────────────────────────────
每一次进入出口           ⊃     仅 dispatched / upstream_error 时写
含被拒的                         不含被拒的
= 需求量信号                    = 成本信号
```

**关键：两者行数不相等是正常的。** 不要试图用 `ai_usage` 反推需求量。

| 问的问题 | 用哪张表 |
|---|---|
| 这个用户被限流了几次？ | `ai_attempt` where `denied_by='rate_limit'` |
| 这个月真实花了多少 token？ | `ai_usage` |
| 熔断打开期间影响多少人？ | `ai_attempt` where `denied_by='circuit'` |
| 用量下降是用户流失还是系统故障？ | **必须两张表对照**——只看 `ai_usage` 会误判 |

### 7.5 写入时机与失败处理

| 规则 | 理由 |
|---|---|
| **在出站守卫之前写** | 否则被 SSRF 拦下的尝试仍然不留痕 |
| 写失败**不得**影响主流程 | 计量是旁路；`record_usage` 现有的 try 语义应保持 |
| 每次请求**恰好一行** | 不因重试而多写；重试是新的 `request_id`，是新的尝试 |

### 7.6 与本 runbook 其他发现的关系

| 编号 | 关系 |
|---|---|
| M-1（token 丢弃） | 只影响 `ai_usage`；`ai_attempt` 不记 token，**不受影响** |
| M-2（多次派发） | 一次 `ai_attempt` 可能对应多条 `ai_usage`（fallback），反之不成立 |
| M-3（unknown） | `outcome='upstream_error'` + `ai_usage.result='unknown'` |
| M-4（无查询入口） | **两张表都需要查询端点**，见 §7.2 补充 |

### 7.7 落地动作

| 编号 | 动作 | 归属 |
|---|---|---|
| **MR-FIX-8** | 建 `ai_attempt` 表 + 在 `_metered_chat` 出口前写入 | W-08 |
| **MR-FIX-9** | 扩展用量查询端点，支持按 `outcome` / `denied_by` 过滤 | W-08 |
| **MR-FIX-10** | 运维告警：熔断打开时**看 `ai_attempt` 而非 `ai_usage`** | 运维 |

> **MR-FIX-8 不阻塞 M-1/M-2/M-3**，可以并行推进。
> 但若不先做它，**熔断/限流造成的"用量归零"永远无法与"用户流失"区分**——
> 这正是 AT-21 后半句要防的误判。

---

## 9. 提供方可信计量能力清单

「可信」的定义：**该供应商的响应中携带可用于核算的 `usage` 数据，且该数据经过交叉验证**。

| 能力项 | OpenAI 兼容 | Anthropic 兼容 | 自建 vLLM/Ollama | 代理网关 |
|---|---|---|---|---|
| 响应含 `usage` | ✅ 通常有 | ✅ 通常有 | ⚠️ 部分版本有 | ❌ 常常被剥掉 |
| 字段名 | `prompt_tokens` / `completion_tokens` | `input_tokens` / `output_tokens` | 不统一 | — |
| 是否可交叉验证 | 需与账单比对 | 需与账单比对 | 需与本地计数比对 | **不可** |
| 超时后能否查该次调用 | 部分支持（request id 查询） | 部分支持 | 取决于是否暴露 | **不可** |
| 当前代码是否读取 | ❌ **丢弃** | ❌ **丢弃** | ❌ | ❌ |

### 8.1 按可信度分级

| 级别 | 判定 | 允许的用途 |
|---|---|---|
| **A 级 · 可信** | 响应含 `usage` + 已与账单交叉验证差异 < 5% | 可用于计费与成本分摊 |
| **B 级 · 趋势** | 响应含 `usage`，未与账单验证 | 可用于配额预警与容量规划，**不可计费** |
| **C 级 · 估算** | 上游不给 `usage`，用本地字符估算 | 仅用于趋势监控，误差 ±30%，**不可计费** |
| **D 级 · 不可信** | 代理网关剥掉 `usage` 且无法交叉验证 | **只能记调用次数**，不得用于任何成本口径 |

### 8.2 落地动作

| 编号 | 动作 | 归属 | 阻塞 |
|---|---|---|---|
| MR-FIX-1 | 两条协议路径解析并保存 `usage`；`ai_usage` 加 `input_tokens`/`output_tokens`/`usage_source` | W-08 | 无 |
| MR-FIX-2 | `_dispatch_completion` 返回派发次数；`ai_usage` 加 `dispatch_count`/`fallback_used` | W-08 | MR-FIX-1 |
| MR-FIX-3 | `result` 扩展为 4 值，新增 `unknown`；熔断对 `unknown` 单独统计 | W-08 | 无 |
| MR-FIX-4 | 新增 `GET /api/admin/ai/usage` 等 3 个端点（按 `organization_id` 过滤 + 审计） | W-08 | MR-FIX-1 |
| MR-FIX-5 | 删除 `refund_call` 及其单测（先做 `git log -S` 确认） | W-08 | 无 |
| MR-FIX-6 | 建立 `docs/tenancy/ai-provider-capability.md`，逐供应商登记 A/B/C/D 级 | W-08 | 需采购/技术对接人参与 |
| MR-FIX-7 | 执行 R-7 月度对账，把差异写入报表 | 运维 | MR-FIX-1 + MR-FIX-4 |
| **MR-FIX-8** | 建 `ai_attempt` 表 + 在 `_metered_chat` 出口前写入（见 §8） | W-08 | 无 |
| **MR-FIX-9** | 用量端点支持按 `outcome` / `denied_by` 过滤（见 §8） | W-08 | MR-FIX-8 |
| **MR-FIX-10** | 熔断告警改看 `ai_attempt` 而非 `ai_usage`（见 §8） | 运维 | MR-FIX-8 |

---

## 10. 待核实清单（需要外部信息，不能靠推断）

| 编号 | 待核实 | 为什么不能推断 | 需要谁 |
|---|---|---|---|
| **V-1** | 试点供应商的**实际计费口径**（按 token 还是按次） | 不同供应商差异极大，部分按请求数计费 | 采购 / 供应商合同 |
| **V-2** | 超时后供应商是否**已计费** | 取决于供应商是否在收到请求后即开始计费 | 供应商文档 + 实测账单 |
| **V-3** | 是否使用**代理网关**（会剥掉 `usage`） | 本仓库只看到 `base_url`，看不到链路拓扑 | 运维 |
| **V-4** | 现有生产 `ai_usage` 表**实际有多少数据** | 生产从未查询过（M-4），数据量与分布未知 | 需只读授权 |
| **V-5** | 熔断阈值 5 次 / 30 秒**是否适配真实上游** | 无遥测，无法判断是否误熔或漏熔 | 依赖 MR-FIX-4 |

> **V-1 与 V-2 未确认前，不启动任何 C1 计费功能。** 这是总设计「价格、支付渠道和真实收费可留到 C1/C2 裁决」的具体含义。

---

## 11. 明确不做

- **不在 M-1/M-2/M-4 修复前把 `ai_usage` 数字用于计费**（§0）
- **不把本地字符估算的 token 数当作精确值使用**——只标 `estimated`，误差 ±30%
- **不把 `unknown` 自动重试**——会放大成本（R-4）
- **不删除 `refund_call` 之前不做 `git log -S` 确认**（§7）
- **不修改任何业务代码**——本文只做取证与设计
- **不把「记录了字符数」表述为「已实现计量」**——M-1/M-2/M-3 未解决前，`ai_usage` 记录的是**请求数**，不是**计费量**

---

## 附：可复现命令

```bash
cd backend

# M-1：两条协议都只取文本，丢弃 usage
sed -n '469,471p' app/api/ai.py     # OpenAI
sed -n '262p' app/api/ai.py          # Anthropic
grep -rn "prompt_tokens\|completion_tokens\|input_tokens\|output_tokens\|cost" app/  # 应 0 命中

# M-2：fallback 循环最多 2 次真实派发
sed -n '444,457p' app/api/ai.py

# M-3：超时直接 504，未标记为 unknown
sed -n '448,449p' app/api/ai.py
sed -n '254,255p' app/api/ai.py

# M-4：汇总方法无生产调用方
grep -rn "ai_usage_summary" . --include=*.py | grep -v "def ai_usage_summary"
# 输出应全部落在 tests/

# M-5：refund_call 死代码
grep -rn "refund_call" app/ tests/
```
