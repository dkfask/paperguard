# 计划/额度/定价规则与账务算法评审（27.3 文档包第 4 项）

> 状态：**design_ready**。本文是现状取证 + 算法设计 + 评审结论，**不是已完成的实现**。
> 日期：2026-10-01。基线：HEAD `e166f97`（含本轮未提交的 F-1/F-5 修复）。
> 上游：总设计第 12 节（租户服务生命周期与权益）、第 11 节（租户与隔离）、第 13.1 节；`ai-metering-runbook.md`（本文的关键前置依赖）、`resource-registry.yaml`。
> 授权边界：**只读取证**。未执行任何 DDL/DML，未修改业务代码，未连接生产。

---

## 0. 结论摘要

总设计第 12.3 节写明：

> 用户、租户、平台供应商池**三个维度**都可设置资源准入；机构配额应在数据库内**原子消费/预留**。

实测：**三个维度中只有一个存在。**

| 维度 | 要求 | 现状 | 缺口 |
|---|---|---|---|
| 用户 | ✅ 原子消费 | ✅ `ai_quota(user_id, period, used)`，`WHERE used < limit` upsert | 无 |
| **租户** | ✅ 原子消费/预留 | ❌ **表不存在** | **全部** |
| **平台供应商池** | ✅ 原子消费/预留 | ❌ **表不存在** | **全部** |
| **预留（reserve）** | ✅ 硬预算可用值含未决预留 | ❌ 只有 consume/release，**无预留态** | **全部** |

**其余三项关键结论：**

1. **定价与支付完全不存在**——全仓无 `billing`/`payment`/`subscription`/`plan`/`price` 任何表或实现。这**符合**总设计「价格、支付渠道和真实收费可留到 C1/C2」的安排，不是缺陷。
2. **当前每用户配额的定位必须写死**：按 §12.3 末句，它**只能**作为「旧接口兼容和风控」，**不得**迁成金钱扣款、不得对外称「已用额度」。
3. **在 `ai-metering-runbook.md` 的 M-1/M-2/M-3 修复前，账务算法只能做「次数账」，不能做「成本账」。** 这不是保守估计，是总设计 §12.3「未拿到实际供应商计量时不能宣称已精确限制费用」的直接要求。

---

## 1. 证据边界与方法

| 项 | 状态 | 方法 |
|---|---|---|
| 无计费/支付实现 | **已验证（全仓）** | 搜 `CREATE TABLE` 命中 `bill\|pay\|subscri\|plan\|price\|invoice` → 25 张表中仅 2 张**运营类**配额表；搜 `alipay\|wechat_pay\|stripe\|checkout\|pricing` → 唯一命中是 `db.py:27` 注释里的 `source checkout`（**Git 术语**，`checkout` 一词的假阳性），非任何支付概念 |
| `ai_quota` 结构与原子性 | **已验证（代码级）** | `db.py:210-216` 表定义；`db.py:881-895` `consume_ai_quota` |
| 无预留机制 | **已验证（全仓）** | `ai_quota` 相关方法仅 `get_ai_quota_used` / `consume_ai_quota` / `release_ai_quota`，无 `reserve` / `commit` |
| 周期基于 UTC | **已验证（代码级）** | `ai_governance._utc_periods` → `strftime("%Y-%m-%d")` / `"%Y-%m-%dT%H:%M"` |
| **生产真实用量分布** | **未验证** | 生产无任何查询入口（`ai-metering-runbook.md` M-4） |
| **供应商实际计费口径** | **未验证** | 见 `ai-metering-runbook.md` V-1，需采购确认 |

---

## 2. 现状盘点

### 2.1 全仓仅有的两张配额表

| 表 | 主键 | 语义 | 维度 |
|---|---|---|---|
| `ai_quota` | `(user_id, period)` | AI 调用次数风控 | **仅用户** |
| `mini_program_push_quotas` | `(user_id, template_id)` | 微信推送剩余次数 | **仅用户** |

两张表都是**用户级风控**，不是**权益/额度**。差别在于：

| | 用户级风控 | 租户级额度 |
|---|---|---|
| 主体 | 单个用户 | 租户（可下推到用户） |
| 超限后果 | 该用户被拦 | 该租户被拦 |
| 可否转成钱 | **否**（§12.3 明令） | 是（未来） |
| 可否预占 | 不需要 | **需要**（批量导出/渲染会长时间占用） |

### 2.2 `consume_ai_quota` 的原子性（这部分是对的）

`db.py:881-895`：

```sql
INSERT INTO ai_quota (user_id, period, used, updated_at) VALUES (?, ?, 1, ?)
ON CONFLICT(user_id, period) DO UPDATE SET
    used = used + 1, updated_at = excluded.updated_at
WHERE used < ?
```

**判超限 + 计数在一条语句内完成**，避免了「先查后写」的并发竞态。两个请求同时读到 `used=limit-1` 会双双通过——这个坑已经避开了。

`release_ai_quota` 用 `MAX(0, used - 1)` 兜底，避免负数。

> 这段实现质量高于本项目平均水平，应当作为新增维度配额表的实现模板。

### 2.3 缺失的预留（reserve）语义

`ai_quota` 只有三态转换：

```text
        consume_ai_quota
   0 ────────────────────→ 1 ────→ 2 ────→ limit
   ▲                       │
   └───────────────────────┘
        release_ai_quota（仅用于日配额扣减失败时回滚突发计数）
```

但总设计 §12.3 要求「硬预算的可用值包括**未决预留**」。场景上，批量导出、批量渲染、文档包生成这类操作：

1. 提交时预占 N 份存储 / M 次 AI
2. 异步执行，可能跑几分钟
3. 成功 → 提交（预占转实扣）
4. 失败 → 释放预占

**没有预留态**，就无法防止「多个并发任务各自看到余量充足，一起把额度用穿」。

### 2.4 周期边界的现状与风险

`ai_governance._utc_periods()` 的实现（`ai_governance.py:59-60`）：

```python
def _utc_periods(now: datetime) -> tuple[str, str]:
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M")
```

> **注意（一个潜在脆弱点）**：这个函数**名字承诺 UTC，但自身不强制**——它只是把传入的 `datetime` 格式化。UTC 性质完全来自调用方 `begin_call` 里的 `datetime.now(timezone.utc)`（`:83-84`）。
> 后果：将来若有人写 `_utc_periods(datetime.now())`（本地时间），周期边界会**静默**平移到本地时区，不会有任何报错。而这恰好会破坏「配额按 UTC 日 0 点重置」这条要写进用户可见契约的规则。
> **处置**：在 BQ-FIX-5 中把函数改为内部 `astimezone(timezone.utc)`，或改名去掉 `utc` 字样以免误导。**属于小修，但要在扩到租户维度之前做**——三维额度一旦上线，周期错误的影响面就是全租户。

| 维度 | 评价 |
|---|---|
| **夏令时** | ✅ UTC 无夏令时，天然规避 |
| **跨时区用户** | ⚠️ 中国用户与 UTC 日界相差 8 小时，「今天」在用户看来不一致 |
| **周期切换** | ⚠️ **无测试**。§12.3 明确要求「周期切换有测试」 |

> UTC 日界对风控是合理的（避免时区导致的计数绕过），但**必须在契约中明示**「配额按 UTC 日 0 点重置」，否则用户会认为规则有 bug。

---

## 3. 目标：三维额度模型

### 3.1 为什么必须是三维

| 场景 | 只有用户维度时 | 三维模型下 |
|---|---|---|
| 学校买 5000 次 AI，分配给 200 名学生 | 每人才 25 次，不够用且无法按需调剂 | 租户池 5000，任意成员共享，可下推 |
| 某教师脚本失控烧光配额 | 只能拦该教师 | 租户池设上限，熔断保护全机构 |
| 平台自营 AI 池成本控制 | 无从下手 | 供应商池独立计量，跨租户汇总 |
| 按成本向客户分摊 | **不可能**（无成本数据） | 供应商池有 token/成本后可分摊 |

### 3.2 数据模型

```sql
-- 租户额度池：主体是租户，不是用户
CREATE TABLE tenant_quota_pool (
    tenant_id        TEXT NOT NULL,
    resource         TEXT NOT NULL,   -- 'ai_calls' | 'ai_tokens' | 'storage_bytes' | 'exports'
    period           TEXT NOT NULL,    -- '2026-10' | '2026-10-01' | 'perpetual'
    granted          INTEGER NOT NULL DEFAULT 0,  -- 授予总量
    consumed         INTEGER NOT NULL DEFAULT 0,  -- 已实扣
    reserved         INTEGER NOT NULL DEFAULT 0,  -- 未决预留
    granted_at       TEXT NOT NULL,
    period_start     TEXT NOT NULL,
    period_end       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, resource, period)
);

-- 用户在租户内的下推额度（可空 = 不下推，直接吃租户池）
CREATE TABLE tenant_member_quota (
    tenant_id        TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    resource         TEXT NOT NULL,
    period           TEXT NOT NULL,
    granted          INTEGER NOT NULL DEFAULT 0,
    consumed         INTEGER NOT NULL DEFAULT 0,
    reserved         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tenant_id, user_id, resource, period)
);

-- 平台供应商池：跨租户的成本边界
CREATE TABLE provider_pool_quota (
    pool_id          TEXT NOT NULL,
    base_url_host    TEXT NOT NULL,
    model            TEXT NOT NULL,
    period           TEXT NOT NULL,
    granted          INTEGER NOT NULL DEFAULT 0,  -- 平台采购的总量
    consumed         INTEGER NOT NULL DEFAULT 0,
    reserved         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pool_id, base_url_host, model, period)
);

-- 预留凭证：让「预占」可审计、可超时回收
CREATE TABLE quota_reservation (
    id               TEXT PRIMARY KEY,
    scope            TEXT NOT NULL,  -- 'tenant' | 'member' | 'provider_pool'
    scope_id         TEXT NOT NULL,
    resource         TEXT NOT NULL,
    amount           INTEGER NOT NULL,
    state            TEXT NOT NULL,  -- 'held' | 'committed' | 'released' | 'expired'
    reason           TEXT NOT NULL,  -- 'batch_export' | 'document_render' | ...
    held_at          TEXT NOT NULL,
    expires_at       TEXT NOT NULL,  -- 超时自动释放，防止泄漏
    committed_at     TEXT,
    released_at      TEXT
);
CREATE INDEX idx_quota_reservation_scope
    ON quota_reservation (scope, scope_id, resource, state);
```

**`quota_reservation.state` 的四态是核心**：

```text
held ──commit──→ committed   （预占转实扣，consumed += amount, reserved -= amount）
  │
  └──release──→ released     （失败回滚，reserved -= amount）
  │
  └──超时─────→ expired      （进程崩溃也不会永久泄漏）
```

### 3.3 消费算法

```text
消费(amount, actor, resource):
  1. 解析三维额度，找到所有生效的限额：
       member_grant  = tenant_member_quota(actor)     # 可为 NULL
       tenant_pool   = tenant_quota_pool(actor.tenant)
       provider_pool = provider_pool_quota(...)      # 仅平台 AI
  2. 逐层校验，可用值 = granted - consumed - reserved
  3. 任一层可用值 < amount → 返回 REASON_EXHAUSTED + 缺口明细
  4. 三层在**同一个事务**内原子扣减（consumed += amount）
  5. 提交
```

**判据：必须单事务。** 分层逐步扣减会在「第一层扣成功、第二层失败」时留下半扣状态。

**与现有 `consume_ai_quota` 的关系**：`ai_quota` 保留不动，作为**用户级风控闸**（§12.3 要求），放在三维消费**之后**：

```text
三维额度通过 → ai_quota 用户风控 → 真实派发
```

理由：`ai_quota` 的历史计数不能迁成钱（§12.3 明令），但它作为防脚本失控的最后一道闸仍有价值，且改动它会破坏既有语义。

---

## 4. 完整账务算法评审

### 4.1 权益生效值的合并规则

§12.2 要求「生效值来自计划版本、合同批准覆盖和限期赠予，必须有确定的合并规则」。

**评审结论：不能靠多行相加。** 采纳 §12.2 的「**单一有效权益版本**」：

```sql
CREATE TABLE tenant_entitlement_snapshot (
    tenant_id        TEXT NOT NULL,
    effective_from   TEXT NOT NULL,
    effective_to     TEXT,             -- NULL = 当前生效
    plan_version_id  TEXT NOT NULL,
    overrides        TEXT NOT NULL,   -- JSON：合同批准的逐项覆盖
    grants           TEXT NOT NULL,   -- JSON：限期赠予（带 expires_at）
    approved_by      TEXT NOT NULL,
    approved_at      TEXT NOT NULL,
    reason           TEXT NOT NULL,   -- C0 试点必须填
    snapshot_hash    TEXT NOT NULL,   -- 内容哈希，防止事后篡改
    PRIMARY KEY (tenant_id, effective_from)
);
```

**合并顺序**（后者覆盖前者）：

```text
1. 计划版本默认值
2. → 合同批准覆盖（overrides）
3. → 限期赠予（grants，且未过期）
= 唯一生效权益版本（snapshot）
```

调用方只引用 snapshot，**不在请求时做多行归约**。理由：多行相加在有「降级 + 赠予 + 合同覆盖」时会算出无限额度，且无法解释某个数字的来源。

`snapshot_hash` 让「这个月为什么能用 5000 次」可回溯——这是对账的前提。

### 4.2 降级不删数据（§12.3 硬要求）

> 降级前检查实际用量和成员数；超出新上限时**限制新增并提示整改，不删除多出的学生或文件**。

**评审：这条必须写成可测试的判据，不能只写在文档里。**

| 降级后超出新上限 | 允许 | 禁止 |
|---|---|---|
| 已存在的学生席位 | ✅ 保留，标记 `over_quota` | ❌ 删除账号 |
| 已上传的文件 | ✅ 保留，可读 | ❌ 删除文件 |
| 新的 AI 消耗 | ❌ 拒绝并提示 | — |
| 新的导出 | ❌ 拒绝并提示 | — |

`over_quota` 状态是**可见的**（管理端能看到「本租户已超配 12 个席位」），不是静默的。

### 4.3 硬预算的可用值

§12.3：「硬预算的可用值包括未决预留」。

```text
可用值 = granted - consumed - reserved
```

**注意**：`reserved` 必须计入。只算 `granted - consumed` 会让并发任务超发——这正是 §2.3 缺预留态要解决的问题。

### 4.4 周期冻结

| 规则 | 判据 |
|---|---|
| 周期键 | AI 风控用 UTC 日（现状不变）；租户额度用**账期**（月/年），不跟随 UTC 日 |
| 边界 | 账期边界**冻结在账期开始时写入** `period_start`/`period_end`，不运行时计算 |
| 跨时区 | 用户展示的「剩余额度」按租户时区渲染，但**计数不按用户时区切分** |
| 切换测试 | 覆盖：月末 23:59:59 / 月初 00:00:00 / 闰年 2 月 29 日 / 夏令时切换日（即便用 UTC 也要有测试证明无影响） |

> 现状的「最后一次按钮点击日期」式周期划分**不存在**，不要引入。

---

## 5. 定价与计费：可以推迟，但有前置条件

### 5.1 推迟是合理的

总设计 §26.3：「价格、支付渠道和真实收费可留到 C1/C2 裁决，**不需要为它们阻塞租户安全底座**。」

全仓无任何计费实现，**这不是缺陷**。

### 5.2 但 C1 计费有硬前置

按依赖关系，C1 启动计费**必须**先完成：

| 顺序 | 前置 | 来源 | 缺失后果 |
|---|---|---|---|
| **1** | `ai_usage` 记录精确 token | `ai-metering-runbook.md` MR-FIX-1 | 无法核算成本 |
| **2** | 派发次数可计 | MR-FIX-2 | fallback 场景系统性低估 |
| **3** | `unknown` 状态可查 | MR-FIX-3 | 无法解释账单差异 |
| **4** | 用量有查询入口 | MR-FIX-4 | 无法对账 |
| **5** | 供应商计费口径确认 | V-1（需采购） | 分摊口径错误 |
| **6** | 三维额度 + 预留 | 本文 §3 | 并发超发 |

**在前 4 项完成前，任何「已按成本计费」的表述都是不成立的。**

### 5.3 建议的计费口径演进

| 阶段 | 口径 | 可对外承诺 |
|---|---|---|
| **C0** | 次数（`ai_quota`） | ✅ 「本月可用 N 次」 |
| **C0+** | token（`ai_usage` 扩展后） | ✅ 「本月已用约 M token」 |
| **C1** | 成本（token × 供应商单价） | ⚠️ 仅在 V-1 确认后，且注明「按上游账单最终为准」 |
| **C2** | 预付与自动续费 | — |

---

## 6. C0 试点的人工批准流程

§12.3：「C0 允许平台人工批准非付费试点权益，记录原因、期限、批准人和版本；**不生成虚假现金充值**」。

**评审：这意味着 C0 的额度来源必须可区分**：

| 来源 | 标记 | 是否可转钱 |
|---|---|---|
| 计划版本 | `source = 'plan'` | C1 后可 |
| 合同批准覆盖 | `source = 'contract'` | C1 后可 |
| 限期赠予 | `source = 'grant'`，带 `expires_at` | ❌ **永不** |
| **C0 试点人工批准** | `source = 'pilot'`，强制填 `reason`/`approved_by` | ❌ **永不** |

**不允许**用「充值 100 元 = 1000 次」的形式记账——那会在 C1 真实收费时产生对不上的历史数据。

---

## 7. 交给工作包的动作

> ⚠️ **2026-10-01 已对齐归属。** 本文写于工作包规格产出之前，
> 当时按「schema 都归 W-02」分配。规格定稿后**权益/配额/周期的行为规则归 W-06**，
> W-02 只负责 DDL 形态。下表已按规格归属更正。

| 编号 | 动作 | 归属（已更正） | 阻塞 |
|---|---|---|---|
| **BQ-FIX-1** | 建 `tenant_quota_pool` / `tenant_member_quota` 表 + 单事务消费方法 | W-02 T-02.4（DDL）· **W-06 §2（席位 SL-1～3）** | ADR-02 |
| **BQ-FIX-2** | 建 `provider_pool_quota` 表 + 平台 AI 池计量 | W-02 T-02.4（DDL）· **W-06 QT-4** | BQ-FIX-1 + MR-FIX-1 |
| **BQ-FIX-3** | 建 `quota_reservation` 四态表 + 预留/提交/释放/超时回收 | W-02 T-02.4（DDL）· **W-06 QT-1～3 / W-11 RSV-1～5** | BQ-FIX-1 |
| **BQ-FIX-4** | 建 `tenant_entitlement_snapshot` + 单一生效版本合并 | W-02 T-02.4（DDL）· **W-06 §1.2（PV-4/PV-5）** | ADR-02 |
| **BQ-FIX-5** | 周期边界冻结 + 账期切换测试（含闰年/夏令时） | **W-06 §3（PB-1～PB-5）** | BQ-FIX-1 |
| **BQ-FIX-6** | `over_quota` 可见状态（降级不删数据） | W-05 · **W-06 SL-4** | BQ-FIX-4 |
| **BQ-FIX-7** | 文档化配额周期重置边界并在前端展示 | **W-06 PB-1～PB-2** | 无 |
| BQ-FIX-1～7 | **全部不阻塞** C0 试点 | — | — |

> **BQ-FIX-1～7 都不阻塞 C0。** C0 继续用现有 `ai_quota` 用户级风控即可；
> 三维额度是 C1 计费的底座，不是 C0 教学试点的阻塞项。

### 7.1 两处已被规格推翻的原建议（保留原文以备追溯）

| 原建议 | 现状 | 推翻理由 |
|---|---|---|
| BQ-FIX-5「**修正 `_utc_periods` 强制 UTC**」 | ❌ **部分推翻** | W-06 **PB-2**：时区来自**租户设置**，缺省 UTC。原建议若照做会把「租户时区」永久固化成 UTC |
| BQ-FIX-7「文档化「**配额按 UTC 日 0 点重置**」」 | ❌ **表述已过期** | 同上——UTC 日 0 点只是**缺省**行为，不是全局事实。对外文案必须按租户周期渲染，否则会显示错误的重置时间 |
| BQ-02「AI 同步调用**不预留**」 | ❌ **建议删除该项** | W-11 **RSV-4/RSV-5**：不预留则 `available` 不含未决消耗，并发下会**超发**，与账务不变量 JV-4 冲突 |

---

## 8. 待裁决项

| 编号 | 问题 | 推荐默认 | 阻塞 |
|---|---|---|---|
| **BQ-01** | 租户额度是否可下推到用户？下推后成员间能否调剂？ | 允许下推，但**成员间不可调剂**（调剂需管理员显式操作并留痕） | BQ-FIX-1 |
| **BQ-02** | `reserved` 超时回收时长 | 导出 2 小时、渲染 30 分钟、AI 同步调用不预留 | BQ-FIX-3 |
| **BQ-03** | 降级超配时是否允许付费扩容 | 允许，走合同变更；不自动降级已有数据 | BQ-FIX-6 |
| **BQ-04** | 试点额度是否设总量上限 | **设**。防止试点期间失控，且让成本可预估 | C0 启动前 |
| **BQ-05** | 账期对齐自然月还是合同周年 | 合同周年（与 §4.4 的 `period_start` 冻结一致） | BQ-FIX-5 |

> **BQ-04 建议在 C0 启动前裁决**——试点期没有总量上限，等于在没有计量能力（M-1/M-2 未修）的情况下开放无限消耗。

---

## 9. 明确不做

- **不把 `ai_quota` 的历史计数迁成金钱扣款**（§12.3 明令）
- **不把当前每用户配额对外称作「额度」或「已用金额」**——它是风控计数
- **不做多行权益相加**——采纳「单一有效权益版本」（§4.1）
- **不设计具体价格**——留到 C1/C2 裁决（§5.1）
- **不修改任何业务代码或执行 DDL**——本文只做评审
- **不把「无计费实现」表述为缺陷**——它符合总设计安排
- **不在 MR-FIX-1～4 之前启动计费**（§5.2）

---

## 附：与本文配套的文档

| 文档 | 关系 |
|---|---|
| `ai-metering-runbook.md` | **本文的硬前置**。M-1/M-2/M-3 决定账务能否做「成本账」 |
| `api-contract-delta.md` | 权益/额度端点需要统一的错误信封与租户只读字段 |
| `resource-registry.yaml` | 租户级资源清单，是 `resource` 字段取值的依据 |
| 总设计第 12 节 | 本文的规范性来源 |
