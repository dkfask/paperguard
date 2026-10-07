# 账号迁移清单：全局身份 → 租户成员身份

> 状态：**design_ready** — 27.3 文档包第 1 项的**最后一块**（资源登记表 ✅、权限矩阵 ✅、账号迁移清单 ✅）。
> 日期：2026-10-01。基线：HEAD `e166f97`。
> 上游：总设计第 9.2 节（租户与身份对象）、第 11 章（客户启用与身份生命周期）、ADR-03；`docs/tenancy/permission-matrix.md`。
> 授权边界：本文件是**只读代码分析 + 设计产物**。未修改任何数据，未执行任何迁移，未创建任何账号，未连接生产。

## 0. 结论摘要

旧身份模型是**单行全局状态**：`users` 表上 5 个正交字段描述一个账号的全部身份。目标模型要求把它拆成**租户内的成员身份**。核心难点有三个：

1. **一个 `users.role` 全局单值 → 每个租户一份 `membership_roles`。** 同一账号在 A 校是教师、在 B 校是学生，旧模型**无法表达**，必须由迁移时人工指定。
2. **指导关系有两套异构来源，判定是 OR。** `users.teacher_id`（全局、单值、无批次）与 `workflow_records(kind='supervision')`（批次内、带状态）并存，`api/teacher.py:24-33` 用 **任一命中即成立**；`teacher.py:143` 确认**解绑只解其中一套**。两者可能互相矛盾，**迁移不得静默择一**。
3. **同一字段有两条写入路径，校验强度不一致。** 自助认证路径（`api/auth.py`）有键名白名单与类型校验；管理员路径（`api/organizations.py`）**两者皆无**。

## 1. 旧身份模型的完整形态（已验证）

### 1.1 `users` 表的身份相关列

| 列 | 取值 | 证据 |
|---|---|---|
| `role` | `user` / `student` / `teacher` / `college_admin` / `admin`（**5 值**） | `db.py:118` 默认 `student`；`api/auth.py:126` 注册时置 `user`；`auth.py:43` `MANAGE_ROLES` |
| `status` | `active`（+ 认证中状态） | `db.py:122` 默认 `active`；`auth.py:644` 判 `!= "active"` |
| `role_application_status` | `unselected` / `pending` / `approved` / `rejected` | `db.py:392-393` 默认 `unselected`；`api/auth.py:123,145,335,337` |
| `requested_role` | `None` / `student` / `teacher` | `auth.py:166`；`api/auth.py:330,338` |
| `requested_organization_context` | JSON dict | `db.py:390-391`；`api/auth.py:143` |
| `organization_context` | JSON dict（`school_id` / `college_id`） | `db.py:386-387`；`api/organizations.py:131` |
| `teacher_id` | 全局单值（`TEXT`，无外键） | `db.py:123`；`api/teacher.py:177-178,206` |

> **`role` 有 5 个值而非 4 个**：`user` 是「已注册但未选定学校角色」的中间态，由 `api/auth.py:126` 在注册时写入，且 `_require_unselected_user`（`api/auth.py:80-93`）专门保证该流程只对 `role == "user"` 开放。迁移时 `user` 不是一种租户角色，而是**尚未建立成员身份**。

### 1.2 申请—审批状态机（已验证）

```text
注册 ──> role=user, role_application_status=unselected
          │
          ├─ POST /role-certification  role=user  ──> status=unselected→（清空 context），application=approved，role 仍为 user
          │
          └─ POST /role-certification  role=student|teacher
                 ├─ 校验 school_id 必填且类型为 school
                 ├─ 可选 college_id：类型为 college 且 parent_id == school.id
                 ├─ 键名白名单 {school_id, college_id}
                 └─ application=pending, requested_role/context 落库
                        │
                        └─ POST /admin/users/{id}/role-certification（require_admin）
                               ├─ approve: role=requested_role, status=active,
                               │            organization_context=requested_organization_context,
                               │            application=approved
                               └─ reject : application=rejected
                               （两者都清空 requested_role / requested_organization_context）
```

**该流程已有一道前置守卫** `_require_unselected_user`（`api/auth.py:80-93`），其 docstring 明确记录了若无此守卫的三条越权路径：自助覆写正式角色、已获批账号可申请更高角色、GET 泄漏全量组织树。**这是已落地的安全修复，迁移必须保留该语义。**

### 1.3 指导关系的双来源（核心难点）

指导关系存在**两套互相独立的绑定机制**。`api/teacher.py:79-86` 的 docstring 原文：

> 学生集合来自两个互相独立的绑定机制，这里合并返回并标注来源：
>   - account：users.teacher_id，教师在「指导学生」页批量导入时写入，这是账号级绑定，也是重置密码/解绑所校验的依据；
>   - cohort：批次级 supervision 记录，建批配置成员时写入，教师工作台的「学生进度」按它取数。
>
> 两者不一致时教师会在同一系统里看到两套学生集合，页面还会静默显示「暂无关联学生」。

| 维度 | 来源 A：`users.teacher_id` | 来源 B：`supervision`（`workflow_records`） |
|---|---|---|
| 作用域 | **全局**，无批次、无组织上下文 | `cohort_id` 批次内 |
| 基数 | 每学生**至多 1 个**教师 | 每 (cohort, student) 一条，可跨批次多个 |
| 状态 | 无 | 有 `state` / `scope` |
| 写入者 | 教师在「指导学生」页批量导入（`api/teacher.py:177-178,206`） | 批次成员管理（`api/workflow.py` `upsert_member`） |
| 解绑 | `api/teacher.py:143`：**只解账号绑定** | 需另行走成员流程 |
| 判定 | `api/teacher.py:25`（`_is_supervised_student` docstring「账号绑定或批次指导任一成立即可」）：**任一命中即成立（OR）** | 同左 |

**风险**：两套来源**可以互相矛盾**（学生 `users.teacher_id=T1`，但某批次 `supervision` 指 `T2`）。当前 OR 语义下**两位教师都能访问该学生**。迁移到 `teacher_student_bindings` 时：

- 若照搬 OR → 把跨租户的指导边带进租户模型，直接违反总设计 4.1「客户资源必须具有确定的 `tenant_id`」
- 若改成 AND 或择一 → **改变现有行为**，需明确授权

**本文件不替用户裁决**，登记为 **Q-08**（见 §5）。

## 2. 旧 → 新映射表

| 旧（`users`） | 新（目标） | 规则 |
|---|---|---|
| `users` 行 | `users`（保留为**全局认证身份**） | **不加 `tenant_id`**。总设计 9.2 行、ADR-03 |
| `role` | `membership_roles.role_code` | **按租户拆分**，同一账号可在不同租户持有不同角色 |
| `status` | `tenant_memberships.status` | 成员状态与账号安全状态**分离** |
| —（无对应） | `platform_role_bindings` | 旧 `admin` **不得自动升为平台超管**（总设计 22.1 行明令） |
| `organization_context.school_id` | `tenant_memberships` + 根组织映射 | 经 W-01 Q-01 人工确认的根组织 → `tenants` 映射 |
| `organization_context.college_id` | `membership_org_grants` | 转为组织授权，**不扩张到整租户** |
| `requested_role` / `requested_organization_context` | `tenant_invitations` | 迁移时状态为 `pending` 的申请转为邀请记录 |
| `role_application_status` | 邀请/申请状态 | `approved`→成员已建立；`pending`→待处理邀请；`rejected`/`unselected`→不生成成员 |
| `teacher_id` | `teacher_student_bindings` | **与 `supervision` 合并，见 §1.3 与 Q-08** |
| `ai_config` | `provider_credentials` | **必须绑定 `(tenant_id, membership_id)`**（Q-05 G-3） |
| `email` / `email_verified` | 保留在 `users` | 邮箱是全局身份；**不按邮箱合并账号**（总设计 9.2 行） |
| `username` | 保留 | **不重编号、不改登录规则** |
| `display_name` | 保留 + `member_external_ids` | 学号等进机构命名空间唯一（总设计 9.2 行） |

## 3. 迁移规则（可执行、幂等）

### 3.1 前置条件

1. Q-01（组织树根 → 租户映射）已由人工确认并产出映射表
2. Q-08（指导关系双来源冲突策略）已裁决
3. 平台角色映射表已由人工确认（哪些旧 `admin` 成为 `platform_role_bindings`，哪些成为租户所有者）

### 3.2 逐账号规则

```
对每个 users 行 u：
  1. 若 u.role_application_status ∈ {rejected, unselected} 且 role == "user"
       → 不生成任何 tenant_memberships。记入「未建立身份」清单。
  2. 若 u.role == "admin"
       → **不自动授予 platform_role_bindings**。
         生成「待裁决：平台角色」条目，含 u.id、u.role、可见组织数。
  3. 由 organization_context.school_id 解析根组织 → tenant（查 Q-01 映射表）
       ├─ 解析不到 → 记入隔离清单，**不猜测归属**
       └─ 解析到   → 生成 tenant_memberships(status 由 u.status 映射)
  4. 生成 membership_roles：
       role='student'    → role_code='student'
       role='teacher'    → role_code='teacher'
       role='college_admin' → role_code='college_admin' + membership_org_grants(该学院子树)
       role='admin'      → role_code='tenant_admin'（**不映射为平台角色**）
  5. organization_context.college_id → membership_org_grants(该组织)
       ⚠ 若 college_id 缺失而 role='college_admin' → 记入隔离清单（无组织范围的学院管理员）
  6. requested_role 非空 → 生成 tenant_invitations(state='pending')
  7. ai_config 非空 → 生成 provider_credentials，**tenant_id + membership_id 均必填**
  8. teacher_id 非空 + supervision 记录 → 按 Q-08 裁决合并为 teacher_student_bindings
```

### 3.3 幂等与断点

- 全部写入经 `migration_batches`（W-02 §3.3）记录 phase/checkpoint
- **每条规则以 `(user_id, tenant_id)` 为幂等键**，重复执行不产生重复成员
- 回填器与校验器**分离**（W-02 T-02.6），校验器独立进程、禁止复用回填代码路径

## 4. 校验项（交 W-02 T-02.6 校验器）

| 编号 | 检查 | 判据 |
|---|---|---|
| MIG-01 | 每个 `users` 行都被分类 | 无「未分类」账号 |
| MIG-02 | 旧 `admin` **无一**被自动映射为 `platform_role_bindings` | 计数为 0 |
| MIG-03 | 每个 `teacher_student_bindings` 双方同租户 | 数据库复合外键拒绝（AT-04） |
| MIG-04 | 无成员缺 `tenant_id` | 计数为 0 |
| MIG-05 | `provider_credentials` 无缺 `membership_id` 的租户级误用 | 逐条核对。**2026-10-02 更正（INC-82）**：本条此前指向**一张在任何规格里都不存在的表**——它的 DDL 寄居在本仓 `system-settings-scope-inventory.md` §5（标题写着「交 W-07」，即**盘点素材**），既不在 W-02 §3.3.1 也不在 §3.6 RLS 矩阵。已迁入 **W-02 §3.3.0b**。**「租户级误用」的判定口径也随之明确**：`membership_id IS NULL` 的行是**合法的租户级默认**（W-07 `BK-2`），**不是误用**；真正的误用是**同一个 `(tenant_id, provider_ref)` 存在多条 `membership_id IS NULL` 的行**——原 `UNIQUE` 因可空列静默失效，现已由两个部分唯一索引拦住 |
| MIG-06 | 指导关系合并数 = 来源 A ∪ 来源 B（按 Q-08 裁决） | 与裁决规则一致 |
| MIG-07 | 隔离清单人数与总人数之和 + 已迁移数 = 总数 | 账实平衡 |
| MIG-08 | 迁移后旧登录凭据仍可用（不改 `username`/密码哈希） | 抽样登录验证 |

> **MIG-08 是硬门禁**：总设计 9.2 行「不得未经确认修改已有用户名规则或按邮箱把未验证账号静默合并」。

## 5. 需人工裁决（隔离清单新增）

| 编号 | 问题 | 需要谁 |
|---|---|---|
| **Q-08** | 指导关系双来源冲突策略：`users.teacher_id` 与 `supervision` 不一致时，迁移取哪个？是否保留 OR 语义？ | 产品 + 教师教学负责人（**改变现有访问行为**） |
| **Q-09** | 旧 `admin` 账号的平台角色映射：哪些成为 `platform_role_bindings`、哪些成为租户所有者？ | 产品 + D-01 客户形态 |
| **Q-10** | `role='user'`（未选定学校角色）的 12/15 本地样本：是否需要「个人租户」承载？ | 产品（总设计 1.2 行「个人商业租户不在机构试点中自动启用」） |
| **Q-11** | `college_admin` 无 `college_id` 时的组织范围如何界定？ | 产品 |

## 6. 观察到的两条代码不一致（非本清单阻塞项）

| 编号 | 不一致 | 证据 |
|---|---|---|
| INC-01 | **`organization_context` 两条写入路径校验强度不一致**：自助路径（`api/auth.py:121-145`）有键名白名单 `{school_id, college_id}` + 学校/学院类型校验 + 父子关系校验；管理员路径（`api/organizations.py:115-133`）**只校验 value 存在性**，无键名白名单、无类型、无父子关系 | 见两处代码 |
| INC-02 | 指导关系 OR 语义下，`unbind_student` 只解账号绑定，`supervision` 侧的指导仍生效 | `api/teacher.py:143` 注释明载 |

> **INC-01 是对 `system-settings-scope-inventory.md` §7 的精度更正**：该文原写「校验只覆盖 value，不校验 key」，**准确表述应为「管理员路径不校验 key，自助路径校验」**。已同步更正该文与登记表。

## 7. 本地开发库观测（**非生产数据**）

对 `backend/data/paperguard.db` 以 `mode=ro` 只读查询：

| 观测 | 值 |
|---|---|
| 用户总数 | 15 |
| role 分布 | `student` 9 / `teacher` 3 / `admin` 2 / `college_admin` 1（**无 `user`**） |
| status | `active` 15 |
| `role_application_status` | `unselected` 12 / `approved` 3 |
| `teacher_id` 绑定 | 1 条 |
| `organization_context` 非空 | 3 条 |
| `requested_role` 待审 | 0 |
| `email_verified = 1` | **0** |
| `supervision` 记录 | **0** 条 |

> **这些数字只说明形态，不代表生产规模，不得用于容量或迁移工时估算。** 特别地：本地库 `supervision` 为 0，因此 **Q-08 的冲突场景在本地无法复现**，必须在生产只读快照上实测。

## 8. 交给 W-03 的动作

| 编号 | 动作 |
|---|---|
| MIG-A-1 | 按 §3.2 编写迁移器，幂等键 `(user_id, tenant_id)` |
| MIG-A-2 | 实现 MIG-01～08 校验器，与迁移器**分离进程** |
| MIG-A-3 | 落地 INC-01：统一两条写入路径的校验强度 |
| MIG-A-4 | 保留 `_require_unselected_user` 语义，迁移后 `role='user'` 的处置与 Q-10 一致 |
| MIG-A-5 | 指导关系按 Q-08 裁决实现，冲突项进隔离清单而非静默择一 |

## 9. 明确不做

- 不执行任何迁移、不写入任何数据
- 不为旧 `admin` 预设平台角色（Q-09 未裁决）
- 不为 Q-08 的冲突择一
- 不把本地开发库的 15 个用户当作生产规模
- 不修改 `_require_unselected_user` 等既有安全守卫
