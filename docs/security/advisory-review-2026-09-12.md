# Mimosa Advisory 人工复核记录(2026-09-12)

> 复核对象:深度扫描 `scan-2026-09-12T14-22-49.520Z-b59620eae099`(封印 `sha256:dbf3b523…41e9c3e`,phase/13-online-verification @ e245ce7 工作区)
>
> 结论:**12 条"资源标识未校验归属"high 与 4 条"敏感操作未观察角色检查"medium 均为误报**;静态分析未识别项目自定义守卫函数的调用链。逐条证据如下。

## 1. "资源标识来自请求,未观察到所有权或租户绑定"(high ×12)

项目的对象级授权由一组显式守卫函数承担,所有被标记端点都在取到资源 ID 后**先过守卫再使用**:

| 守卫 | 校验内容 | 失败行为 |
|---|---|---|
| `_ensure_cohort_access` → `_cohort_access` | 组织范围/角色对批次的可见性 | 404(资源隐匿) |
| `_participant_access` | 批次访问 + 学生仅本人任务(403)+ 教师须当前有效指导关系(403) | 403/404 |
| `_ensure_project_workflow_access` | admin / 学生 owner / 教师 owner 或指导批次 | 403/404 |
| `_ensure_project_access`(document_projects) | `can_access_document_project`,拒绝时写 `audit_document_access_denied` 审计 | 404(带审计) |
| `_ensure_manager(role, status)` | 管理角色 + 账号 active | 403 |

逐条核验(workflow.py 行号为扫描时点):

- `:583 get_cohort` → `_ensure_cohort_access` ✅
- `:781 list_stages` → `_ensure_cohort_access` ✅
- `:814 create_stage_version` → `_ensure_manager(workflow=True)` + `_ensure_cohort_access(stage.cohort_id)` ✅
- `:868 publish_stage_version`(×2)→ 同上,另校验 config 与 stage 的归属一致 ✅
- `:1010 get_participant` → `_participant_access`(学生/教师双重归属校验)✅
- `:1085 compare_project_versions`(×2)→ `_ensure_project_workflow_access` ✅
- `:1108 get_project_version` → 先取 version 再按 `version.project_id` 过守卫 ✅
- `:1272 close_cohort` → `_ensure_manager` + `_ensure_cohort_access` ✅
- `:1964 mark_notification_read` → `store.mark_workflow_event_read(user_id, event_id)` 用户域内更新,失败 404 ✅
- `document_projects.py:446 list_render_jobs` → `_ensure_project_access`(拒绝即审计+404)✅

## 2. "敏感操作未观察到角色或权限检查"(medium ×4)

- `auth.py:42`:登录/注册为匿名可达端点,本身即认证入口,无需角色检查 ✅
- `policies.py:465`、`projects.py:767/799`:均位于 `Depends(current_user)` 之后并调用各自的 `_ensure_*` 守卫(同 §1 机制)✅

## 3. XXE(medium ×3)— 已加固写法的识别局限

`docx_exporter.py:43`、`xml_utils.py:66`、`docx_anchors.py:136` 均使用 `etree.XMLParser(resolve_entities=False, no_network=True, …)`(docx_exporter 另拒绝含 DOCTYPE/ENTITY 的输入)。lxml 在该配置下不展开外部实体,不构成 XXE;扫描器对 lxml 参数式加固暂不建模,判为误报。若后续要求彻底消灯,可统一迁移到 defusedxml 封装。

## 4. 已修复项(本轮)

- `ops/test-online-verify.sh`:回环 mock 的契约口令由静态字面量改为每次随机生成(`secrets.token_urlsafe`),经参数传入 mock、经 `:="${VAR:=}"` + `export` 注入验证脚本,仓库不再含静态凭据;契约测试本地实测通过(8 checks)。
- 此前 `820a088` 已修复的 9 处测试凭据、导出链路路径穿越(三层断链)在 phase/13 保持有效。

## 遗留建议

- 依赖扫描命中 1 条离线通告(matchedPackages=1),优先级低,建议在依赖例行升级时顺带核对。
- 后续新增 API 请继续沿用 `_ensure_*` 守卫族,并在命名上尽量靠近框架惯例(如 `Depends` 注入式守卫),可降低此类误报噪音。
