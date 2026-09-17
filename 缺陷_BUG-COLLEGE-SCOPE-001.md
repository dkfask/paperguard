# BUG-COLLEGE-SCOPE-001 学院管理员可给兄弟学院创建并发布规范

- 状态：已修复（本地未提交，2026-09-16）
- 发现日期：2026-09-16
- 严重级别：P1（组织范围越权，但不破坏本院批次闭环）
- 复现脚本：[college_admin_lifecycle_20260916.py](./college_admin_lifecycle_20260916.py)
- 关联：BUG-COLLEGE-SCOPE-002（同一根因的发布步骤）

## 复现

1. 学校管理员创建学校、学院 A、学院 B。
2. 将用户提升为 `college_admin`，`organization_context` 设为 `{school_id, college_id: A}`。
3. 学院管理员 `POST /api/policies`，`level=college`，binding 指向学院 B。
4. 实际：201 创建成功。
5. 继续 `POST /api/policies/{id}/publish`。
6. 实际：200，`status=published`。

同账号：

- `GET /api/cohorts/{B的批次}` 返回 404
- `GET /api/audit?resource_type=cohort` 不含学院 B

## 期望

规范绑定超出本人最具体组织根时返回 403，与批次/审计隔离一致。

## 根因

`backend/app/services/access_service.py` 的 `_scope_roots` 只取最具体组织（`college_id` 优先于 `school_id`），再向下展开。

`backend/app/api/policies.py` 的 `_user_scope_ids` 把 context 中所有值（含 `school_id`）都当作可见根，再展开全部下级。学院管理员绑定学校+学院后，兄弟学院 `scope_id` 会被 `_binding_visible` 判为可见。

## 建议修复

规范授权复用 `visible_organization_ids`，不要单独维护一套会把 `school_id` 整棵树算进去的可见集合。现有 `test_college_admin_can_manage_scoped_policy_but_not_template` 只覆盖了不存在的 `college-b`，补一条真实兄弟学院用例。

## 修复

`backend/app/api/policies.py`：`college_admin` 的 `_user_scope_ids` 改为 `visible_organization_ids`；绑定可见性只认该集合，不再用 `school_id` 精确匹配扩权。`test_college_admin_can_manage_scoped_policy_but_not_template` 现覆盖真实兄弟学院创建/发布 403 以及学校级 binding 403。隔离运行时复核：兄弟学院与学校 binding 均为 403，本院创建 201。证据：[测试报告_测试修复闭环_20260916.md](./测试报告_测试修复闭环_20260916.md)。
