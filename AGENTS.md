# AGENTS.md — PaperGuard 仓库工作约定

> 放置位置：PaperGuard 仓库根目录，文件名 `AGENTS.md`
> Codex 每个任务开始前先读本文件；通用工作习惯见 `~/.codex/AGENTS.md`；
> 与 README 冲突时以 README 为准，冲突处上报。

## 0. 项目定位（一句话）

PaperGuard：论文格式检查 / 修复工具，FastAPI + Vue3。
**当前交付口径以单学校试点为边界，不对外宣称完整多租户 SaaS。**
代码已有租户/成员运行时基础与受限隔离切片，不能据此宣称完整 SaaS 已交付。
任何对外文案、接口注释、文档里，禁止把「学校—学院—专业」组织层级
（`backend/app/models/organization.py` 的 `OrganizationUnit`）描述成多租户 SaaS。

## 1. 动手前先看

1. 仓库根目录存在 `.codegraph/` 时，改代码前先用 CodeGraph 定位影响范围；不存在时用仓库搜索和调用关系阅读，不自行索引。
2. 涉及产品语义的改动，先读 `docs/`（含 `docs/product/SaaS产品设计补齐评估.md`）。
3. 数据访问以 `backend/app/db.py` 的 `Store` 类为核心（改前先通读）；
   检查 / 修复引擎在 `backend/app/core/`，不要动它的公共接口。
4. 开工前看 `docs/evidence/未决项_20260913.md` 和 `docs/tenancy/implementation-status-20261004.md`，避免撞车；历史证据不替代当前状态核验。

## 2. 构建与测试（硬门禁）

- 后端：先按 README 为执行环境安装 `backend/requirements-dev.txt` 开发依赖，或激活已具备这些依赖且满足根目录 `pyproject.toml` 的项目 venv（Python >=3.10）。当前机器的 `python3.12` 存在，但该解释器尚未安装 pytest；准备好依赖后，从仓库根目录执行 `cd backend && python3.12 -m pytest -q`。以本轮结果为准，不固定用例数。
- 前端学院管理相关：从仓库根目录执行 `cd frontend && npm run check:college-admin`
- 小程序：以下命令均在 `miniprogram/` 执行：
  - `npm run test:flow`（本地逻辑测试，不替代真机）
  - `npm run check:wechat-uat`（真机前置门禁；`UAT-MP-DEVICE` 固定为 BLOCKED，退出码 2 是预期的，不要"修绿"它；脚本不能代替真机点击）
  - `npm run build:mp-weixin` 自带环境变量校验（仓库根目录路径 `miniprogram/scripts/validate-build-env.mjs`）
- CI：`.github/workflows/release-preflight.yml` 全绿是合入 main 的前提；须绑定最新 head。

## 3. 数据库迁移（只增不改）

- 走 `backend/app/db.py` 的 `SCHEMA_MIGRATIONS` 账本及 `Store._init_schema()` 的迁移逻辑，新迁移以当前账本末尾为基准按序号递增，不复用已有编号。
  当前已有 `0009_ai_usage_quota` 至 `0014_ai_tenant_idempotency`；不得再使用 `0009_tenant`。
- 迁移入口在 `backend/app/cli/migrate.py`；按 README 准备依赖或激活上述项目 venv 后，从仓库根目录可执行 `cd backend && python3.12 -m app.cli migrate`。执行前必须明确授权目标 SQLite 路径（`PAPERGUARD_DB_PATH` 或 `--db-path`），该命令会创建/升级数据库，不是只读检查。
- 本仓库当前没有独立的逐版本迁移文件目录；已发布迁移账本项及对应逻辑不得随意改写，要改结构就新增迁移。

## 4. 部署与线上

- 镜像：`python:3.12-slim`、`node:24-alpine`、`nginx:alpine`；
  Docker Hub 拉取超时曾是已知问题，当前网络需重新核验；必要时配加速器或预拉取，从仓库根目录执行 `docker compose build`，本机构建能过再谈部署。
- 受控部署：授权包（目标地址、账号、变更窗口、回滚负责人、上一个可用镜像 tag）
  → 执行 → 逐项验证：`GET /api/health/live` 200、`GET /api/health/ready` 200、
  OpenAPI 含最新路由、前端品牌显示正确。
- 线上验证 secrets 放 GitHub Environment `paperguard-online-verification`
 （`PAPERGUARD_ONLINE_BASE_URL` / `PAPERGUARD_ONLINE_ADMIN_USERNAME` / `PAPERGUARD_ONLINE_ADMIN_PASSWORD`），
  从仓库根目录用 `bash ops/online-verify.sh` 跑；工作流 `.github/workflows/online-verification.yml` 上传 evidence artifact。这里只核实配置键与工作流，不推断远端 secrets 已配置。
- 当前后端没有 `/api/version` 路由；线上版本按 README 的服务器发布标记与 `ops/sync-release-markers.py` 源码/前端摘要核验确认，不把验证脚本的来源 SHA 当成部署 SHA。
- main 每次发布走「部署 → 验证」闭环，不许跳过。

## 5. 租户模型红线与实施边界（最高安全级别）

- `OrganizationUnit` 是学校内部层级，**不是租户**；当前 `tenants` / `tenant_memberships` 已独立存储。
- 当前 `backend/app/auth.py` 的 `current_user` / `trusted_context` 结合 `backend/app/services/tenant_access.py` 从服务端成员关系解析可信上下文；上下文类型在 `backend/app/core/context.py`。`Store` / repositories 尚未统一要求 `tenant_context` 参数，不得声称全资源隔离已完成。
- **铁律：永远不信任客户端传来的 `tenant_id`。** 选择头只定位服务端成员关系，不自动授予身份或能力。
- 后续全量隔离的验收约束：用户、组织、模板、规范、项目、提交、文件、归档、任务、审计等资源须有经批准的租户归属；文件访问须在生成链接与实际读取时鉴权。全资源强制租户列、RLS、文件/异步隔离目前尚未完成，不把目标约束写成当前实现。
- 后续文件隔离目标：服务端生成 `tenants/{tenant_id}/...` 前缀和不可猜测 ID；该前缀是目标存储命名规则，不是当前已完成的文件隔离实现或仓库目录。
- 存量迁移须先通过显式归属映射与批准的只读预检；当前认证路径不自动 seed 默认租户或回填成员。未通过准入或未授权，不执行真实库迁移；迁移后仍须对账行数/文件数。
- 后续隔离验收须覆盖同实例跨租户互换 ID，以及列表 / 搜索 / 导出等入口；当前受限本地切片和 mock 测试不替代完整矩阵、生产迁移或真实 UAT。

## 6. 密钥与配置（不进仓库）

`PAPERGUARD_WECHAT_APP_ID`、`PAPERGUARD_WECHAT_APP_SECRET`、
`PAPERGUARD_MP_BINDING_SECRET`、线上验证三件套、
`PAPERGUARD_ADMIN_PASSWORD`（bootstrap，≥12 位含字母数字）。
小程序生产参数填 `miniprogram/.env.production`（本地，不提交）。

## 7. 禁止事项

- 不把组织层级宣传成多租户 SaaS。
- 不跳过 §2 测试门禁交活。
- 真机验证通过前不声称小程序"可用"（`UAT-MP-DEVICE` 门禁说了算）。
- AI 计费未启用：不对外宣称"合规 AI 写作"或"学术不端判定"，AI 功能默认可关闭。
- 上传检查的文件仅在处理期间使用，处理完清理；原文件永不被修改。
