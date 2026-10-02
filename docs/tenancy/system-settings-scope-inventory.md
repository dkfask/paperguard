# system_settings 作用域拆分与 AI 配置解析链（Q-05 处置）

> 状态：**design_ready** — 解决登记表隔离项 **Q-05**，并为 W-07（AI 凭据治理）提供前置分析。
> 日期：2026-10-01。基线：HEAD `e166f97`。上游：`docs/tenancy/resource-registry.yaml`（`system_settings` 条目）、总设计第 13 节。
> 授权边界：本文件是**只读代码分析产物**。未执行任何 DDL/DML，未查询生产库，未修改任何配置。

## 0. 结论摘要

`system_settings` 实际只有 **2 个设置键**。作用域问题不是「键太多」，而是三件事：

1. **`organization_id=''` 一列承载两种语义**（平台默认 / 0010 迁移的存量行），不可区分。
2. **AI 配置解析链是 5 级隐式回退**，与总设计 13.1 节要求的「先判租户政策、再判来源」**方向相反**——当前完全没有政策门禁。
3. **个人 BYOK 存在 `users.ai_config` 全局列上，不绑定租户成员** → 同账号在 A 校配置的密钥在 B 校自动生效，直接违反总设计 13.1 节第 551 行。

第 3 项是本次分析中**风险最高**的一条，且不在 W-02 的存储范围内，属 W-07。

## 1. 证据边界

| 项 | 状态 | 说明 |
|---|---|---|
| 设置键集合（2 个） | **已验证** | 全部 `get/set_system_setting` 调用点 |
| 解析链分支 | **已验证** | 逐行读 `api/ai.py:53-100`、`db.py:929-996` |
| 各租户实际配了哪些键 | **未验证** | 未查生产库；本地库 `system_settings` 未取样 |
| BYOK 跨租户实际泄露面 | **推断，未实测** | 代码路径清晰，但未构造请求验证 |

## 2. 设置键清单（2 个）

| 键 | 定义/使用 | 作用域 | 证据 |
|---|---|---|---|
| `ai_default_config` | `db.py:994` `set_system_ai_config` | **组织级**（`organization_id` 可空） | 写入 `api/ai.py:613,630`；读取 `db.py:980`、`api/ai.py:67,562` |
| `retention_policy` | `api/operations.py:23` | **恒为平台级** | 读写 `api/operations.py:37,57` 均**不传** `organization_id` → 永远落在 `''` |
> `retention_policy` 的租户化缺口见 §6。

## 3. `organization_id=''` 的双语义问题

`db.py:436-452`（0010 迁移）把原本全局键值表改为 `PK(key, organization_id)`，并把**所有存量行统一归到 `organization_id=''`**，注释写明「存量行一律归到 `organization_id=''`（平台全局默认），因此升级后行为不回退」。

于是 `''` 同时表示：

| 语义 | 来源 | 是否可区分 |
|---|---|---|
| 平台全局默认（管理员显式设置） | `set_system_setting(key, v)` 不传 org | **否** |
| 0010 迁移前的历史存量行 | `db.py:449-451` `SELECT key, '', value, updated_at FROM system_settings_legacy` | **否** |

两者在表中完全同形。总设计第 9.1 节要求「平台表/公开资产表与租户表明确分开，不能靠空字符串租户承载多种语义」——**当前正是靠空字符串承载了两种语义**。

### 目标模型（三态显式）

```sql
-- expand 阶段：加列，不动现有 PK 与写入路径
ALTER TABLE system_settings ADD COLUMN scope_type text NOT NULL DEFAULT 'platform';
ALTER TABLE system_settings ADD COLUMN scope_id   text;

-- 回填：把 '' 拆成两种来源
--   0010 迁移遗留行 → scope_type='legacy_platform', scope_id=NULL（保留可识别性）
--   管理员显式设置   → scope_type='platform',        scope_id=NULL
-- 判定依据：0010 迁移在 db.py:449-451 有确定执行时点，需以迁移批次记录区分，
--           不可仅凭值猜测（见 §8 动作 W-05-A）
```

> **不要**用「值看起来像默认配置」来猜来源。`db.py:449-451` 的迁移是确定性的，可用 `schema_migrations` 的 `0010_tenant_system_settings` 应用时间做分界。

## 4. AI 配置解析链：现状 vs 目标

### 现状（`api/ai.py:_resolve_cfg` → `_system_cfg`，已验证）

```text
1. users.ai_config                              ← 个人 BYOK，全局列
2. system_settings[ai_default_config][user.school_id]   ← 组织级
3. system_settings[ai_default_config]['']        ← 平台全局（隐式自动回退）
4. 环境变量 PAPERGUARD_AI_BASE_URL/API_KEY/MODEL
5. None
```

### 目标（总设计 13.1 节第 540-546 行）

```text
0. 租户禁止外发            → 不调用外部 AI          ← 现状完全缺失
1. 成员 BYOK（本租户成员）
2. 租户默认
3. 平台回退（需显式允许 + 平台 AI 权益）          ← 现状是隐式自动
4. AI_NOT_CONFIGURED / AI_POLICY_DENIED
```

### 五个具体差距

| # | 差距 | 证据 | 违反 |
|---|---|---|---|
| G-1 | **无租户政策门禁**。设计第 0 步「租户禁止外发」在代码中不存在 | 全仓无外发策略判定；`api/ai.py:53-100` 直接进配置解析 | 13.1 第 541 行 |
| G-2 | **平台回退是隐式的**，非显式允许 | `db.py:944-951` `get_tenant_system_setting` 读到空即回退 `''` | 13.1 第 544、550 行 |
| G-3 | **BYOK 是全局的，不绑定租户成员** | `db.py:124` `users.ai_config TEXT`；`api/ai.py:97,508,522,542` 共 **4 处**均为 `store.get_user_ai_config(user["id"])`，**不传组织/成员** | 13.1 第 551 行「A 校设置的密钥不自动给 B 校或其他成员使用」 |
| G-4 | **未知租户自动借用平台配置** | `api/ai.py:78-87` `_school_of()` 取不到学校返回空串（`:87` 返回 `str(context.get("school_id") or "")`），注释明说「回退到平台全局默认——保持与修复前一致的行为」 | 13.1 第 550 行「未知租户不能自动借用平台配置」 |
| G-5 | **回退链不冻结来源版本** | 返回 `(cfg, source)` 但不记录 `credential_ref/version`、policy_version | 13.1 第 548 行；W-08 计量前提 |

> **G-3 是本次分析的最高风险项。** `users.ai_config` 挂在全局 `users` 行上，`get_user_ai_config(user_id)` 只按 `user_id` 取，**没有任何组织或成员维度**，且该调用在 `api/ai.py` 的 **4 条路径**（解析链 `:97`、配置测试/保存 `:508,522,542`）上全部如此。AT-01 要求的「A 校教师、B 校学生，BYOK 均不串租户」在当前 schema 下**无法通过**——不是逻辑写错，是存储位置不允许。

## 5. BYOK 正确形态（交 W-07）

总设计第 9.3 节已给出目标表，本节只补落地约束：

```sql
CREATE TABLE provider_credentials (
    id            text PRIMARY KEY,
    tenant_id     text NOT NULL REFERENCES tenants(id),
    membership_id text,                        -- NULL = 租户级默认；非空 = 成员 BYOK
    provider_ref  text NOT NULL,
    key_ref       text NOT NULL,               -- 指向 secret_store 的版本化引用，不存明文
    key_version   integer NOT NULL,
    state         text NOT NULL DEFAULT 'active',
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, membership_id, provider_ref),
    -- **2026-10-01 补（INC-60）**：本列原先**没有任何取值约束**。
    -- `retired` 对应 W-07 **SK-8** 的「核对完成 → 才退役旧 `key_id`」——
    -- **退役是轮换流程的终态，必须能被持久化并可查**，
    -- 否则「旧密钥已退役」这件事只存在于某个人的记忆里。
    CONSTRAINT provider_cred_state_chk CHECK (state IN ('active','retired','revoked'))
);
```

**约束**：
- `membership_id` 非空时，该凭据**只能**被该成员使用；不得被同租户其他成员读取
- 解析时按 `(tenant_id, membership_id, provider_ref)` 精确匹配，**不存在「取不到就往上退」到平台的行为**，除非租户显式允许（G-2）
- 退出一个租户不影响该账号在其他租户的凭据；但**不自动复制**

## 6. `retention_policy` 的租户化缺口

`api/operations.py:37,57` 读写均不传 `organization_id`，因此保留策略是**全平台单值**，所有租户共用。

总设计第 20.1 节要求「每类数据的期限、驻留地点、处理者和退出方式列入 D-08」，第 20.3 节的租户退出流程需要按租户判断保留。当前能力不足：

- 无法为不同租户设不同保留期
- 退出流程（20.3）执行时无法判断「本租户数据应保留多久」
- 改策略会**同时影响所有租户**，且无版本化/审计

**目标**：迁入 `tenant_settings`（总设计 9.3 节，`tenant_id + key + schema_version + value + version`），平台默认作为继承基线，逐租户可覆盖。**属 W-06/W-10，本规格只记录缺口。**

## 7. 第四处 JSON 隐藏归属

`users.organization_context` 是 `ALTER TABLE` 加入的 TEXT 列（`db.py:386-391`，`ALTER` 语句在 `:387`），存 JSON，由 `db.py:580-598` `_hydrate_user` 反序列化。AI 配置的隔离维度 `_school_of()` 取的正是它的 `school_id` 键（`api/ai.py:87`）。

**写入路径与校验**（已验证）：

| 路径 | 行为 | 校验强度 |
|---|---|---|
| `api/auth.py:121-145`（自助认证） | 注册/申请时写入 **`requested_organization_context`**，`organization_context` 置空 `{}` | **强**：键名白名单 `{school_id, college_id}`（`:144`）、`school_id` 必填且类型为 `school`、`college_id` 类型为 `college` 且 `parent_id == school.id` |
| `api/organizations.py:115-133`（管理员设置） | `PUT /users/{user_id}/context` 写入 **`organization_context`** | **弱**：需 `require_manager` + `admin`/`college_admin`；每个 value 必须是存在的 `organization_unit`（`:124-125`）；`college_admin` 限本人可见范围（`:129`）。**无键名白名单、无类型校验、无父子关系校验** |

**残余缺口（INC-01）**：**同一字段存在两条写入路径，校验强度不一致。** 管理员路径可以把 `{"school_id": "<某个 college 节点 id>"}` 写入 `organization_context`，也可以写入完全不含 `school_id` 的键集；后一种情况会让 `_school_of()` 返回 `""`（`api/ai.py:87`），从而触发 G-4 的平台回退。

> **⚠ 本节表述已于 2026-10-01 更正。** 原文写作「校验只覆盖 value，不校验 key」，**该表述不准确**——自助路径有完整键名与类型校验。准确表述为「**管理员路径**不校验 key 与类型，**自助路径校验**」。缺陷本身仍成立（两条路径可写入不同强度的数据），但责任范围从「全系统」收窄为「管理员路径」。同步更正见 `account-migration-inventory.md` §6 INC-01 与登记表 `inc_findings`。

> 严格说这不是跨租户泄露（`get_system_ai_config` 仍按原始 id 精确查），但是**语义错标**且会让 G-4 的回退被无意触发。迁移到 `tenant_memberships` + `membership_org_grants`（W-03）后该问题自然消失。

## 8. 交给 W-02 / W-07 的动作

| 编号 | 动作 | 归属 | 阻塞 |
|---|---|---|---|
| W-05-A | 用 `schema_migrations` 中 `0010_tenant_system_settings` 的 `applied_at` 作分界，把 `organization_id=''` 的行拆为 `legacy_platform` 与 `platform` | W-02 T-02.5/T-02.6 | 需生产只读快照 |
| W-05-B | expand 阶段加 `scope_type` / `scope_id` 两列，**不动现有 PK 与写入路径** | W-02 T-02.5 | 无 |
| W-05-C | 加列前后逐行 diff 只读结果集，diff 必须为 0 | W-02 AC-02-6 | 无 |
| W-07-A | BYOK 从 `users.ai_config` 迁到 `provider_credentials`，绑定 `(tenant_id, membership_id)` | W-07 | 需 W-03 的 membership 就绪 |
| W-07-B | 解析链加租户政策门禁（第 0 步），平台回退改为显式许可 | W-07 | 需 W-06 权益模型 |
| W-07-C | 解析结果冻结 `credential_ref/version` + `policy_version` | W-07 + W-08 | — |
| W-07-D | `retention_policy` 迁 `tenant_settings`，平台默认作继承基线 | W-06/W-10 | 需 D-08 |
| W-03-A | `organization_context` **管理员写入路径**补齐键名白名单与类型校验（或直接由 `membership_org_grants` 取代） | W-03 | 无 |

## 9. 对 AT 的影响

| AT | 当前能否通过 | 原因 |
|---|---|---|
| AT-01（同账号双租户 BYOK 不串） | **不能** | G-3：`users.ai_config` 无组织维度 |
| AT-16（禁外发/禁 BYOK/禁平台回退） | **不能** | G-1/G-2：无政策门禁、无显式回退许可 |
| AT-19（BYOK 密钥错误不熔断其他租户） | 部分 | 熔断按凭据作用域（13.1 第 555 行）需与 G-3 一并解决 |
| AT-15（周期边界/时区/计划版本） | 不涉及 | — |

## 10. 明确不做

- 不修改任何配置、代码或 schema（本文件只做分析与设计）
- 不查询生产库取各租户实际配置
- 不构造请求实测 BYOK 跨租户泄露（需授权，且应作为 W-07 的 AT-01 验证）
- 不把「代码路径存在」表述为「已发生泄露」——G-3 是**已证实的结构性缺陷**，实际泄露面**未实测**
