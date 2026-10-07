# 权限矩阵：现状执行模型与目标能力矩阵

> 状态：**design_ready** — 27.3 文档包第 1 项的剩余部分；**U-02 的设计侧交付物**。
> 日期：2026-10-01。基线：HEAD `e166f97`。
> 上游：总设计第 6 章（角色与能力）、第 8.3 节（保护面）、第 24.2 节（验收矩阵）；`docs/tenancy/resource-registry.yaml`。
> 授权边界：本文件是**只读代码分析 + 设计产物**。未修改任何代码，未执行任何请求，未创建任何账号。

## 0. 结论摘要

当前授权模型是**「边缘粗粒度守卫 + 处理器内资源判定」**两段式。守卫层只回答「是否登录/是否某种角色」，**真正的租户与组织边界由处理器内调用 `visible_organization_ids` / `can_access_organization` 决定**。

这带来三个结论：

1. **`admin` 的作用域是全平台**，不是任何租户。`access_service.py:27-28` 对 admin 直接返回**全部组织**。当前数据模型里不存在租户，所以这是必然的——但它意味着 **AT-02（伪造 tenant Header 不能授予权限）、AT-07（旧 admin 迁移与平台/客户管理分离）在当前 schema 下无法通过**。
2. **「查看机构使用聚合」完全没有实现**。`Store.ai_usage_summary()` 是全库聚合且**无任何 API 端点或前端入口**（仅测试调用）——总设计 6.2 节该行当前是空的。
3. **矩阵不能靠读守卫得出**。214 条路由中 160 条只用 `current_user`，判定散落在处理器内。本文件因此**只登记已验证的执行点**，未验证的一律标 `NOT VERIFIED`，不做推测填充。

## 1. 证据边界

| 项 | 状态 | 方法 |
|---|---|---|
| 路由清单与守卫依赖 | **已验证** | AST 解析全部 `@router.*` 装饰器 + 参数默认值中的 `Depends()` |
| 守卫语义 | **已验证** | 逐个读 `auth.py` / `services/audit_service.py` / `services/access_service.py` |
| 处理器内资源判定 | **部分已验证** | 仅对审计、模板、学生绑定等关键路径取证；**未逐条覆盖 214 个处理器** |
| 运行时实际越权行为 | **未验证** | 本轮未发起任何请求；需浏览器/接口层 UAT |

> **本文件不是完整授权审计。** 它给出的是「已验证的执行点 + 明确的目标矩阵」，缺口部分如实标 `NOT VERIFIED`。

## 2. 现状：守卫与判定模型（已验证）

### 2.1 路由与守卫分布

全量 **214 条** API 路由：

| 认证守卫 | 路由数 | 语义 |
|---|---|---|
| `current_user` | 160 | 仅要求已登录；**授权在处理器内完成** |
| `require_manager` | 25 | `MANAGE_ROLES = ("admin","college_admin","teacher")`（`auth.py:43,640-646`）；teacher/college_admin 额外要求 `status == "active"` |
| `require_admin` | 7 | 仅 `admin`（`auth.py:658-662`） |
| `require_template_manager` | 6 | 学校管理员 + 已认证指导教师（`auth.py:649`） |
| `require_general_document_platform` + `require_manager` | 4 | 通用文档平台能力 |
| **无认证守卫** | **12** | 全部为合理公开面，逐条列明如下 |

**12 条公开路由（完整清单，无认证守卫）**：

| # | 方法 | 路径 | 处理函数 | 文件:行 |
|---|---|---|---|---|
| 1 | POST | `/api/auth/register` | `register` | `api/auth.py:69` |
| 2 | POST | `/api/auth/email/code` | `request_email_code` | `api/auth.py:156` |
| 3 | POST | `/api/auth/login` | `login` | `api/auth.py:162` |
| 4 | POST | `/api/auth/mp/login` | `mini_program_login_endpoint` | `api/auth.py:168` |
| 5 | POST | `/api/auth/mp/bind` | `mini_program_bind_endpoint` | `api/auth.py:177` |
| 6 | POST | `/api/auth/mp/register` | `mini_program_register_endpoint` | `api/auth.py:189` |
| 7 | POST | `/api/auth/logout` | `logout` | `api/auth.py:226` |
| 8 | GET | `/api/health` | `health` | `api/routes.py:78` |
| 9 | GET | `/api/health/live` | `health_live` | `api/routes.py:83` |
| 10 | GET | `/api/health/ready` | `health_ready` | `api/routes.py:89` |
| 11 | GET | `/api/packs` | `packs` | `api/routes.py:100` |
| 12 | GET | `/api/packs/{pack_id}` | `pack_detail` | `api/routes.py:106` |

> `/api/auth/mp/bind-email`、`/api/auth/mp/unbind`、`/api/auth/mp/subscribe` **不在此列**——它们使用 `current_user`，属需登录面。

> **注意 `require_manager` 的实际授予面远大于其 docstring。** `auth.py:641` 写「模板管理权限」，但该守卫被 25 条路由使用，涵盖组织、批次、成员、过程材料、策略等。**任何 active 教师都能通过。** 这是命名与语义不符，不是缺陷本身，但会误导审计——本文件按实际语义而非 docstring 登记。

### 2.2 组织可见性（`access_service.py:25-39`，已验证）

```python
def visible_organization_ids(store, user) -> set[str]:
    if user.get("role") == "admin":
        return {unit.id for unit in store.list_organization_units()}   # ← 全平台
    values = _scope_roots(user)
    if user.get("role") != "college_admin":
        return values                                                  # teacher/student：仅自身根
    ...  # college_admin：根 + 递归后代
```

| 角色 | 可见组织范围 | 证据 |
|---|---|---|
| `admin` | **全部组织** | `access_service.py:27-28` |
| `college_admin` | 自身作用域根 + 全部后代 | `access_service.py:30-38` |
| `teacher` / `student` | 仅自身作用域根 | `access_service.py:29-31` |

### 2.3 关键执行点（已验证）

| 操作 | 端点 | 实际判定 | 与总设计 6.2 的差异 |
|---|---|---|---|
| 查看审计 | `GET /api/audit` | `_ensure_audit_manager`（`audit.py:24-27`）限定 `admin`/`college_admin` + active；再由 `audit_service.py:72-80` 按 `visible_organization_ids` 过滤，**admin 仍看全量** | 设计要求「平台默认否，安全流程另授权」；当前 admin 无需额外授权即可看全量 |
| 导出审计 | `GET /api/audit/export` | 同上，输出 CSV（`audit.py:54-73`） | 同上；且**CSV 外流无二次确认、无水印、无审批** |
| 策略审计 | `GET /api/policies/{id}/audit` | 经 `policies.py:490` | — |
| 设置用户组织作用域 | `PUT /api/organizations/users/{id}/context` | 需 `admin`/`college_admin`（`organizations.py:118-119`）；每个 value 必须是存在的组织（`:124-125`）；college_admin 限自身可见（`:129`） | **key 无白名单**（见 Q-05 §7） |
| 模板管理 | `POST/PUT/DELETE /api/templates/*` | `require_template_manager` | — |
| 学生绑定/解绑 | `/api/teacher/students/*` | `require_manager` + 处理器内教师范围 | 教师可管理**自己指导范围内**学生，边界需逐条核（未全覆盖） |

### 2.4 确认缺失的执行点

| 设计 6.2 操作 | 当前状态 | 证据 |
|---|---|---|
| 新建客户租户 | **不存在** | 全仓无 tenant 概念 |
| 修改租户 AI 外发策略 | **不存在** | 见 `system-settings-scope-inventory.md` G-1 |
| 查看机构使用聚合 | **不存在对外端点** | `Store.ai_usage_summary()`（`db.py:906-920`）为全库聚合，**仅被 `tests/test_ai_governance.py`、`tests/test_ai_breaker.py` 调用**；`api/` 与 `frontend/` 零引用 |
| 修改计划/价格版本 | **不存在** | C1 范围 |
| 手工调账/退款 | **不存在** | C1/C2 范围 |
| 恢复/删除租户数据 | **不存在** | W-10 |

## 3. 目标能力矩阵

> 命名沿用总设计第 3 节（能力 = 服务端授权使用的具体操作，如 `members.invite`）。
> **作用域**：`platform` 平台控制面 / `tenant` 整个租户 / `org_subtree` 授权组织子树 / `guide` 指导关系内 / `self` 本人 / `grant` 受控支持授权内。
> **数据面**：`metadata` 仅元数据 / `content` 客户正文 / `secret` 密钥与凭据 / `financial` 账务事实。

### 3.1 能力 × 角色

| 能力 | 平台运营 | 平台安全运维 | 租户所有者 | 租户管理员 | 账务管理员 | 学院管理员 | 教师 | 学生 | 支持人员 |
|---|---|---|---|---|---|---|---|---|---|
| `tenant.create` / `tenant.enable` | ✓ platform | — | 申请 | — | — | — | — | — | — |
| `tenant.suspend` / `tenant.close` | ✓ 双重控制 | — | 申请 | — | — | — | — | — | — |
| `tenant.snapshot_read` | ✓ metadata | — | ✓ tenant | ✓ metadata | ✓ financial | — | — | — | — |
| `members.invite` / `members.remove` | 受控支持 | — | ✓ tenant | ✓ tenant | — | 明确授权 org_subtree | 指导成员兼容入口（需授权） | — | — |
| `members.transfer_owner` | — | — | ✓ 二次确认 | — | — | — | — | — | — |
| `org.manage` | — | — | ✓ tenant | ✓ tenant | — | ✓ org_subtree | — | — | — |
| `ai.policy.write` | 平台上限 platform | — | ✓ tenant | ✓ tenant | — | — | — | — | — |
| `credential.manage` | platform 池 | — | — | ✓ tenant | — | — | ✓ self (BYOK) | ✓ self (BYOK) | — |
| `usage.read` | ✓ 脱敏聚合 | — | ✓ tenant | ✓ tenant | ✓ tenant | ✓ org_subtree | ✓ guide | ✓ self | — |
| `content.read` | **默认否** | **默认否** | 依教学治理 | 依教学治理 | **默认否** | ✓ org_subtree | ✓ guide | ✓ self | 需 grant |
| `content.write` | — | — | ✓ tenant | ✓ tenant | — | ✓ org_subtree | ✓ guide | ✓ self | — |
| `review.decide` | — | — | — | — | — | 范围内 | ✓ guide | — | — |
| `submission.create` | — | — | — | — | — | — | — | ✓ self | — |
| `plan.select` | — | — | ✓ 申请 | — | — | — | — | — | — |
| `price.version.write` | ✓ C1/C2 | — | — | — | — | — | — | — | — |
| `billing.read` | ✓ financial | — | ✓ tenant | — | ✓ tenant | — | — | — | — |
| `ledger.adjust` / `refund.request` | ✓ 特定能力+审批 | — | 申请 | — | ✓ 申请 | — | — | — | — |
| `audit.read` / `audit.export` | 默认否，安全流程另授权 | — | ✓ tenant 脱敏 | ✓ tenant 脱敏 | ✓ financial 范围 | ✓ org_subtree | ✓ self 相关 | ✓ self 相关 | — |
| `job.retry` | 受控运维授权 | ✓ 受控 | ✓ tenant | ✓ tenant | — | ✓ org_subtree | 原有授权范围 | ✓ self 导出 | — |
| `support.grant.issue` | ✓ 需审批 | ✓ 需审批 | — | — | — | — | — | — | — |
| `data.export` / `data.delete` | 双重控制 + 正式流程 | — | 申请/确认 | — | — | — | — | 提交本人申请 | — |
| `backup.restore` | ✓ 双重控制 | ✓ 双重控制 | — | — | — | — | — | — | — |

### 3.2 四条不可跨越的边界（总设计 4.4 / 4.5 行）

1. **角色、权益、账务分别判断。** 买套餐不产生管理员角色；停 AI 不取消论文读取权。
2. **平台运营默认无正文读取权。** 代客排障用 `support_grant`（租户/工单/能力/期限/审批），访问与结束均审计。
3. **支持人员不得借「帮忙导出」取得其他客户正文。**
4. **运营动作、账务动作、恢复动作、删除动作分别授权**，不得由通用 admin 一把钥匙无限执行。

## 4. 差距清单

| 编号 | 差距 | 严重度 | 证据 | 归属 |
|---|---|---|---|---|
| GAP-P-01 | `admin` 作用域 = 全部组织，无租户边界 | **高** | `access_service.py:27-28` | W-03（`platform_role_bindings` + 租户上下文） |
| GAP-P-02 | BYOK 不绑定租户成员，A 校密钥 B 校可用 | **高** | Q-05 G-3 | W-07 |
| GAP-P-03 | 无租户外发策略门禁，平台回退隐式 | **高** | Q-05 G-1/G-2 | W-07 |
| GAP-P-04 | 「查看机构使用聚合」无对外实现 | 中 | `db.py:906` 零 API 调用 | W-06/W-08 |
| GAP-P-05 | 审计导出（CSV）无二次确认、无水印、无审批 | 中 | `api/audit.py:54-73` | W-10 |
| GAP-P-06 | `require_manager` 命名与实际授予面不符（25 条路由含全部 active 教师） | 中 | `auth.py:43,640-646` | W-03（能力化改造时拆分） |
| GAP-P-07 | `organization_context` 键名无白名单 | 中 | Q-05 §7 | W-03 |
| GAP-P-08 | 学生/教师/过程材料等 160 条 `current_user` 路由的处理器内判定**未逐条核验** | 中 | 本文件 §1 | W-03 补全 + AT-02/AT-03 矩阵 |
| GAP-P-09 | 无支持授权（`support_grant`）实体与流程 | 中 | 全仓无此表 | W-10 |
| GAP-P-10 | 租户退出/恢复/删除授权路径不存在 | 中 | 无端点 | W-10 |

## 5. 与验收用例的对应

| AT | 场景 | 当前可否通过 | 阻塞项 |
|---|---|---|---|
| AT-01 | 同账号双租户角色/BYOK 不串 | **否** | GAP-P-01、GAP-P-02 |
| AT-02 | 伪造 tenant Header/URL 不授予权限 | **否** | 无租户上下文；GAP-P-01 |
| AT-03 | 列表/详情/批量/报表/导出跨租户 | **否** | GAP-P-01、GAP-P-08 |
| AT-07 | 旧 admin 迁移、平台与客户管理分离 | **否** | GAP-P-01 |
| AT-14 | 买权益但无角色 / 有角色但 AI 到期 | 部分 | 权益模型未建（W-06） |
| AT-16 | 禁外发/禁 BYOK/禁平台回退 | **否** | GAP-P-03 |
| AT-29 | 审计与日志敏感字段扫描 | 待核 | 需 W-10 实测 |
| AT-30 | 支持授权过期/撤销/超范围 | **否** | GAP-P-09 |

> **U-02 在设计侧已闭合（本文件），在实现侧仍完全未闭合。** 总设计 2.3 节要求它是 C0 共享试点的必需门禁——当前 5 条 AT 明确无法通过。

## 6. 交给 W-03 的动作

| 编号 | 动作 |
|---|---|
| P-ACT-1 | 引入 `RequestContext`（总设计 8.1 行），把 `visible_organization_ids` 升级为「租户 + 成员 + 组织授权」三段判定 |
| P-ACT-2 | 拆分 `require_manager`：按能力拆为 `require_org_admin` / `require_coach` / `require_platform`，消除命名与授予面不符 |
| P-ACT-3 | 建立 `platform_role_bindings`，使 `admin` 不再隐式等于全租户访问（对应 AT-07） |
| P-ACT-4 | 补齐 GAP-P-08：对 160 条 `current_user` 路由逐条登记处理器内判定，纳入资源登记表 |
| P-ACT-5 | 建 `support_grants` 与审批流程（GAP-P-09） |
| P-ACT-6 | 审计导出增加二次确认与审计事件（GAP-P-05） |

## 7. 明确不做

- 不修改任何守卫或权限判定代码
- 不发起任何请求验证越权行为（需授权，且应作为 W-03 的 AT-02/AT-03 验证）
- 不把「设计侧矩阵已产出」表述为「授权已闭合」
- 不为 GAP-P-08 未核验的 154 条路由编造判定说明——只登记为 `NOT VERIFIED`
