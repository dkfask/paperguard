# `conditional` 8 张表的租户归属裁决包（Q-13）

> 状态：**decision_ready** — 目的是把「8 张表都卡在业务裁决上」这句话，
> 换成**逐张表、逐个具体问题**的清单。
> 日期：2026-10-02。基线：HEAD `e166f97`，本地库 `backend/data/paperguard.db`（mtime 2026-09-30 10:36，23 张表）。
> 授权边界：本文件是**只读代码分析 + 本地库只读查询**产物。
> 未执行任何 DDL/DML，未查询生产库，未重启生产，未提交。

## 0. 为什么要出这份文件

W-04 §1 与 `resource-registry.yaml` 把 8 张表判为 `conditional`，
处置一栏写的是「**取决于业务裁决**，须逐条定稿」。

**这句话被高估了。** 逐张表核对后：**8 张里只有 3 张真正需要产品/业务裁决，
且每张只需回答 1～2 个具体问题**；其余 5 张要么表是空的、要么早已完成、
要么只剩一个根节点映射。**把它们一并挂在「等裁决」上，
会让 5 张本来可以直接干的表无限期停摆。**

| 真实行数 | 表 | 是否需要业务裁决 | 实际卡点 |
|---|---|---|---|
| 0 | `document_packages` | **否** | UNIQUE 四元组不含租户——**改 schema 即可，且冲突从未真的发生** |
| 0 | `system_settings` | **否** | 拆 `scope_type` + `scope_id` 三态——纯 schema，无存量数据 |
| 0 | `audit_events` | **否** | 无 legacy 回填问题；只需保证事务内写入路径不变 |
| 0 | `workflow_records` | **是，但只 2/17** | Q-04 早已处置完毕（17 kind 全部枚举），**只剩 D 类 2 个 kind** |
| 6 | `organization_units` | **是，但只 1 个映射** | 单根、无环、无断链；只差「根 → 租户」（Q-01） |
| 3 | `templates` | **是，一条规则** | 什么样才算「已发布公共资产」 |
| 8 | `policy_audit_events` | **部分** | `scope_type` 模型可自行定；当前 8/8 实为平台作用域，无迁移数据 |
| 4 | `requirement_sets` | **是，`scope` 语义** | `scope` 三值但 `scope_id` **4/4 为空串**，空串承载多语义 |

## 1. 五张**不需要业务裁决**的表（可直接推进）

### 1.1 `document_packages` —— 0 行，登记表说的「硬冲突」从未发生

`resource-registry.yaml` 写「两个租户用同一版本四元组会撞唯一键，
**这是共享库下的硬冲突，不是理论风险**」。

**实测：`document_packages` 共 0 行。** 冲突的前提（两个租户各建一份）
在本地数据里**一次都没出现过**。判定的**方向没错**（UNIQUE 确实不含租户，
`db.py:230`），但**紧急性被写高了**。

处置：**不需要裁决，只需要一条 schema 变更**——把 UNIQUE 四元组扩为含 `tenant_id`
（归 `unique_extend` 迁移规则，与 W-04 §2 的 L3 复合外键同一套机制）。
**在建到第二个租户之前改，成本几乎为零；之后改要搬数据。**

### 1.2 `system_settings` —— 0 行，三态拆分是纯 schema 工作

0010 迁移（`db.py:428-452`）把全局键值表改为 `(key, organization_id)` 复合主键，
存量行归到 `organization_id=''`。登记表要求拆成 `scope_type` + `scope_id` 显式三态
（平台 / 租户 / 成员）。

**实测 0 行。** 所以这是**一次干净的三态 schema 重建**，没有数据搬迁，
也不需要为「旧空串行归到哪一态」做裁决。

实际只有 2 个键（`ai_default_config` 组织级、`retention_policy` 恒平台级），
其中 `retention_policy` 的平台级语义**已经确定**，不构成待决项。

### 1.3 `audit_events` —— 0 行，「老事件标 legacy」当前无对象

§10 要求「老事件标 legacy，**不伪造 request_id 或操作者**」。

**实测 0 行。** 该要求在当前数据上**没有作用对象**，但**约束必须保留**——
它是对**未来迁移**的约束，不是对存量数据的描述。

唯一真实的工程约束：`save_audit_event_conn` 是事务内写入路径
（§21.2 要求关键审计与状态变更同事务），**加列时不得把它改成异步**。
这一条是实现约束，不需要裁决。

### 1.4 `workflow_records` —— Q-04 早已处置，只剩 2/17 个 kind

`docs/tenancy/workflow-records-kind-inventory.md` 已是 `design_ready`：
17 种 kind 全部枚举并分了 A/B/C/D 四类，**结论是可以加单一 `tenant_id` 列**，
不需拆表（但必须同时加 `owner_ref` 固化父资源引用）。

**真正待裁决的只有 D 类 2 种**：

| kind | 位置 | 问题 |
|---|---|---|
| `document_template_candidate` | `services/document_candidates.py:20` | 记录**不携带 `template_id`**，无法推导租户；API `api/templates.py:257` `require_manager` **无任何租户参数** |
| `document_spec_candidate` | `services/document_candidates.py:21` | 同上，`api/specs.py:113` `require_manager` |

它们是 `manager` 级**全局解析队列**，**既可能是平台公共资产的候选，
也可能是某个租户上传的模板/规范——现有代码无法区分**（已归 Q-07）。

> **即使这两个 kind 不裁决，其余 15 种（2 行数据）也能推进。**
> 把整张表挂在裁决上，会让 15 种已经推导清楚的 kind 一起停摆。

### 1.5 `organization_units` —— 6 行、单根无环无断链，只差一个映射

实测 6 行结构：

```text
org-uat-scho   type=school    parent=（空，唯一根）
└── org-uat-coll   type=college
    └── org-uat-majo  type=major
        ├── org-uat-cour   type=course
        ├── org-uat-batc   type=batch
        └── org-4de38301   type=batch
```

**无多根、无循环、无断链。** 因此 §10 的「根组织经人工确认映射租户，子树继承」
在本数据上**只产生一个决策**：`org-uat-scho` → 哪个租户（即 **Q-01**）。

登记表列的 `cycle_detection_absent` / `no_fk_integrity` 等风险标志
是**能力缺口**（回填器必须自己做环检测与断链检测），**不是待裁决项**——
那是 W-02 T-02.6 校验器的工作。

## 2. 三张**需要业务裁决**的表（每张 1～2 个具体问题）

### 2.1 `templates`（3 行）—— 只需裁决「什么算已发布公共资产」

实测 3 行：

| id | 名称 | 判读 |
|---|---|---|
| `default-gb7713` | 学位论文模板 (GB/T 7713) | **随产品发布的国标模板** → 天然是公共资产 |
| `uat-browser-te` | 浏览器 UAT 论文模板 | 名字带 `uat-` → 测试期用户产出 |
| `tpl-175a522998` | 毕业设计（论文）毕业论文（报告）-物联网工程-… | `tpl-` 前缀 + 专业定制内容 → 租户产出 |

**要裁决的问题只有一条**：

> **一条模板在什么条件下成为「已发布公共资产」？**
>
> - **A 案**：`published` 是**显式动作**，需要平台管理员操作；创建者创建的模板**一律私有**。
>   代价：没有「一键共享模板」；收益：**§7.3 的禁令天然满足**——不存在「忘了标记」的状态。
> - **B 案**：提供**租户发布**路径，租户管理员可把自己模板发布为公共。
>   代价：需要额外的内容安全审核；收益：试点期复用更快。
> - **C 案**：按名称/前缀规则自动判公共（如 `default-*`）。
>   **本包明确反对 C 案**：用命名约定承载权限语义，正是 §9.1 禁止的
>   「空串 / 约定承载多语义」在模板上的重演，而且 `uat-browser-te`
>   这类测试产出**会靠名字猜错**。

**无论选哪案，两条实现约束已经确定**：
① **禁止用 `tenant_id IS NULL` 表示「任意人可读写」**（§7.3）；
② 复制到租户后产生**租户版本并保留来源引用**（§7.3）。

### 2.2 `requirement_sets`（4 行）—— 裁决 `scope` 的语义

实测 4 行，**`scope_id` 键存在且值为空串的行数是 4/4**：

| id | `scope` | `scope_id` |
|---|---|---|
| `policy-4a8da5d` | `global` | `''` |
| `policy-1a47a28` | `teacher` | `''` |
| `policy-1c3c303` | `college` | `''` |
| `policy-f0b7519` | `college` | `''` |

> **对 INC-04 的更正**：该条记的是「4 行中 **3** 行 `scope_id` 为空串」，
> **实测是 4/4**。漏掉的那行正是 `scope='global'` 的 `policy-4a8da5d`——
> 也正是**问题最关键的一行**：它说明空串**不只表示「还没有指向某个组织」**，
> 它**同时表示「平台全局」**。**一个值承载两种语义，正是 §9.1 明令禁止的。**
> 少算一行，就把「双语义」这个核心问题算成了「部分行缺数据」。

**要裁决的问题**：

> **`scope` 的三值（`global` / `teacher` / `college`）与租户是什么关系？**
>
> - **A 案**：`scope` 描述**租户内**的作用域（`global` = 该租户全局、
>   `teacher` = 该租户的教师要求、`college` = 该租户某学院），
>   因此需要 `tenant_id` + `scope_id`（`college` 行必须填）。
>   代价：`scope='global'` 这个名字**必须改名**（否则与「平台全局」撞名）。
> - **B 案**：`scope` 描述**平台级模板的作用域**，而实际归属由 `tenant_id` 决定，
>   二者正交（与 W-10 的「`rejected_by` 是正交维度」同一处理法）。
>   代价：需要同时读两个列才能判断可见性。
>
> **本包倾向 A 案**：`scope='global'` 与「平台全局」**同名不同义**是当前最大的歧义源，
> 而 §9.1 的原则是「消歧优先于便利」。

**已确定、不需要裁决**：`policy_1c3c303` / `policy_f0b7519` **两行 `scope='college'`
但 `scope_id` 都为空**——它们当前**无法解析到任何组织**，
无论选哪案都必须进入**迁移隔离清单**（已登记）。

### 2.3 `policy_audit_events`（8 行）—— 模型可自定，无迁移数据

实测 8 行，**全部是 §2.2 那 4 个 policy 的审计**（每个 2 条
`created`/`published`，其中 `policy-f0b7519` 多一条 `version_created`）。

因为 §2.2 那 4 个 policy 当前的 `scope_id` 全为空，
**这 8 条审计事件在事实上全部是平台作用域的**。

要做的不是裁决，而是**与 §7.3 对齐的 schema 设计**：
控制面审计与数据面审计需要 `scope_type` 区分，**单列 `tenant_id` 不足以表达平台作用域**。

> **本包的处置建议**：加 `scope_type ∈ ('platform','tenant')`，
> `scope_type='platform'` 时 `tenant_id` 为空。
> **注意这与 `plan_versions` 的 `global` 类别是同一模式**
> （W-02 §3.6.1 已引入），**不要为它发明第二套表达**。

## 2.4 两个**状态取值集合**待采纳（INC-76，与租户归属无关但同样卡实施）

W-02 §3.3.1 补 C0 的 11 张表 DDL 时，两个状态集合是**直接写进 CHECK 的**，
但**全部设计文档里找不到它们的出处**：

| 表 | DDL 里的取值集合 | 出处 | 需要谁采纳 |
|---|---|---|---|
| `ai_requests.state` | §14.2 八态 | **有**（RQ-2 明写「严格取自 §14.2」） | — |
| `usage_events.to_state` | 同上八态 | **有**（US-2/US-3 同源） | — |
| `plan_versions.state` | `draft/published/retired` | **有**（W-06 PV-2 明写「至少含」三者） | — |
| `subscriptions.state` | `trialing/active/grace/expired/cancelled` | **散文有、规则无**（总设计 §12.1 列出） | **已补 W-06 `PB-6`/`PB-7`** |
| `support_tickets.state` | `open/triaged/in_progress/resolved/closed` | **无** | **W-10 `SP` 规则** |
| `exit_requests.state` | `requested/approved/exporting/completed/rejected/cancelled` | **无** | **W-10 `XR` 规则** |

**要裁决的问题**（各 1 问）：

> **Q-14｜—工单的生命周期就是这五态吗？** 具体是：`triaged` 与 `in_progress`
> 是否需要区分（区分则能表达「已受理未处理」，不区分则少一态但流程更简）？
> 撤回（requester 反悔）走 `closed` 还是单独一态？
>
> **Q-15｜—退出的这六态对吗？** 具体是：`exporting` 是否必要
> （若删除申请与执行是两笔事务，就必须有它来表达「已批但正在导出」）；
> `cancelled` 与 `rejected` 是否都要（`rejected` 是审批否决，`cancelled` 是申请人撤回——
> **两者的审计含义完全不同**，合成一态就丢了「谁决定不做的」这个事实）。

> **为什么这两问必须有人答**：状态集合一旦写进 CHECK 就是**数据库层约束**，
> 实施者会照抄、迁移会照搬。**编一个默认值比留空更危险**——
> 而现在这两个集合**已经是编的**，只是没人在看。
> 唯一能接受的状态是：**要么有规则承认它，要么明确标为待采纳**（DDL 里已加 ⚠ 标注）。

### 2.5 第三个**无出处**的取值集合：`budget_policies.metric`（INC-77）

| 表 | DDL 里的取值 | 出处 |
|---|---|---|
| `budget_policies.metric` | `'count'` / `'token'` / `'money'` | **无** —— 三个英文取值在 13 份规格里一次都没出现过 |

管辖本表的规则是 **W-06 `QT-4`**：「`budget_policies` 必须声明度量是
**次数 / token / 货币成本**三者之一，**不混用**」。**它给出了三个概念，但没给英文取值。**

> **Q-16｜这三个英文字面量对吗？** 需要定的是：
> ① `count` 还是 `times` 还是 `quota`？② `token` 与 `count` 是平级两态还是
> token 属于 count 的子类（决定 `metric='token'` 能不能被 `count` 的策略覆盖）？
> ③ 货币态的字面量用 `money` 还是 `currency` 还是 `cost`？
> ④ **`currency` 与 `money` 是否强制绑定**——当前 DDL 有这条约束，
> 但**它是「从『不混用』推出来的，不是任何规则明说的**」。
> 若将来要支持「按次但每次有单价」这种形态，这条约束会挡住它。

## 3. 裁决清单（按「解锁量 ÷ 决策成本」排序）

| 优先 | 问题 | 涉及 | 问一句就能定 | 解锁 |
|---|---|---|---|---|
| **1** | `org-uat-scho` 映射到哪个租户 | Q-01 | 是 | `organization_units` 全部 6 行 + 回填器可收口 |
| **2** | 模板「已发布公共资产」的判定规则（A/B 案） | 本包 §2.1 | A 还是 B | `templates` 3 行 + `list_templates()` 作用域过滤 |
| **3** | `scope` 与租户的关系（A/B 案） | 本包 §2.2 | A 还是 B | `requirement_sets` 4 行 + `policy_audit_events` 8 行 |
| **4** | 两个 `*_candidate` kind 的归属 | Q-07 | 是平台队列还是租户队列 | `workflow_records` 15/17 种 kind |

**四问之外不需要任何裁决**：`document_packages` / `system_settings` /
`audit_events` 三张（0 行）可直接推进，`policy_audit_events` 的 schema 形态
可按 §2.3 直接定。

## 4. 证据边界（这份文件不能证明什么）

| 项 | 状态 | 说明 |
|---|---|---|
| 8 张表的**分类** | **已验证** | 逐条核对 `resource-registry.yaml` 的 `tenant_derivable_reason` 与风险标志 |
| 8 张表的**本地行数与内容形态** | **已验证** | 本地库只读查询，库 mtime 2026-09-30 10:36 |
| `workflow_records` 的 17 种 kind | **已验证（代码级）** | 来自 Q-04 已处置的专项清单 |
| **生产数据形态** | **未查询** | 未连生产库。生产若有本地不存在的行（尤其 `document_packages` / `system_settings`），§1 的「0 行」结论要重新量 |
| `templates` 三行的**归属判读** | **推断，未裁决** | 依据是 `id` 命名与名称内容，**不是产品决定**。`uat-browser-te` 到底是测试残留还是真实租户资产，**只能由业务回答** |
| 裁决后的**实施与验证** | **未开始** | 本文件不授权任何写操作 |

## 2026-10-04 本轮后仍需明确的选择

以下均未替用户作答；只读预检为BLOCKED，不产生批准或生产写入。

| 决策 | 用户需明确 | 未明确前行为 |
|---|---|---|
| Q-01 学校根→tenant | 本地唯一school根对应哪个客户，批准引用与确认人；不用候选替批准 | 映射pending，准入拒绝 |
| Q-09 旧admin→平台 | 哪些旧管理员确需平台权限、批准依据与撤销责任 | 全局admin不是可信平台，运维接口拒绝 |
| Q-08 指导关系 | 旧全局teacher_id按哪个tenant/member范围迁移 | 未批准规则进入待裁决，不自动复制/猜测 |
| 模板/全局要求归属 | 平台公共资产还是客户副本；无scope_id要求逐条归属 | 不默认落某客户，隔离 |
| 共享部署DB | PostgreSQL+应用授权+RLS，还是继续独立SQLite实例 | SQLite本地pilot不是共享生产DB决议 |
| 完整启用/资源隔离 | 组织、模板、成员准入、权益、配额、AI政策的真实事实及文件/任务合同 | draft不启用；未隔离export/job拒绝 |

实现与验证入口：[本轮实施状态](implementation-status-20261004.md)。首位owner本地路径已实现，但完整客户启用/成员批量导入/迁移放行仍未完成。
