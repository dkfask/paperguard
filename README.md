# PaperGuard — 论文格式全流程约束系统

基于 **FastAPI + Vue 3** 的 Web 应用,把论文格式从"事后检查"变为"**写作时约束**":

- **模板约束写作**:教师在线配置论文模板(章节结构 + 每类元素的字体/字号/行距/缩进/分页规则 + 封面字段),学生从零开始写作时,结构由模板生成、排版样式自动应用且不可修改,只需填写内容
- **一键导出 docx**:写作内容按模板生成符合规范的 `.docx`,可直接提交
- **上传检查兜底**:对已有 `.docx` 按 YAML 规则包(GB/T 7713、GB/T 7714、校规)检查并自动修复,原文件永不被修改,上传文件仅在处理期间使用

## 架构

```
┌──────────────────────────┐   HTTP    ┌──────────────────────────────┐
│  Vue 3 前端               │ ───────▶ │  FastAPI 后端                 │
│  · 模板管理 (章节+样式)    │  /api/*  │  · 模板 / 项目 / 工作流 / 归档  │
│  · 结构化写作 (锁定样式)   │          │  · 核心引擎 (检查/修复引擎)     │
│  · 上传检查 / 报告         │          └──────────────────────────────┘
└──────────────────────────┘
```

- **backend/app/models/** — 模板(章节树 + TextStyle)与写作项目(段落块)数据模型
- **backend/app/core/export/** — 按模板生成 docx 的导出引擎
- **backend/app/core/** — 检查引擎(不可变 IR → YAML 规则包 → JMESPath/JSONLogic → 修复回写)
- **backend/app/db.py** — SQLite 存储(可用 `PAPERGUARD_DB_PATH` 覆盖)
- **frontend/** — Vue 3 + Vite:模板编辑页、写作页、上传检查页
- **工作流闭环** — 批次/阶段/成员导入、阶段配置版本、任务、提交快照、教师审核、过程材料、消息、报表和归档包
- **小程序 MP0** — uni-app 微信小程序骨架、微信登录/校内账号绑定、任务/消息/账号安全 Tab；长篇写作继续使用桌面端

## 快速开始(本地开发)

```bash
# 后端管理员 bootstrap 密码(至少 12 位, 含字母和数字)
export PAPERGUARD_ADMIN_PASSWORD='请替换为强密码'
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # 仅运行服务
pip install -r requirements-dev.txt      # 需要跑测试时用这个(含 pytest/Pillow)
uvicorn app.main:app --reload          # http://localhost:8000

# 前端 (Node 18+)
cd frontend
npm install
npm run dev                            # http://localhost:5173 (自动代理 /api)
```

浏览器打开 http://localhost:5173:

1. **模板管理** — 编辑内置模板或新建(章节结构、每类元素样式、封面字段)
2. **我的论文** — 选择模板新建论文,进入结构化写作(左侧章节树,右侧锁定样式编辑器)
3. **导出 docx** — 写作页右上角一键导出
4. **上传检查** — 对已有 docx 做格式检查/修复
5. **AI 写作助手** — 写作页内润色/续写段落,按正文一键生成 摘要/Abstract/关键词

## AI 写作助手

配置优先级为：**用户自带配置 → 所属学校配置 → 平台全局默认 → 环境变量默认**。
管理员接口可用 `organization_id` 指定学校；不传时操作平台全局默认。当前管理页面
仍配置全局默认，学校覆盖通过受控管理接口配置。OpenAI 兼容端点使用
`/chat/completions`，以 `/anthropic` 结尾的端点使用 Anthropic Messages 协议。

- **环境变量默认模型**：

  ```bash
  export PAPERGUARD_AI_BASE_URL='https://api.openai.com/v1'
  export PAPERGUARD_AI_API_KEY='<由受控环境注入，不写入仓库>'
  export PAPERGUARD_AI_MODEL='<供应商实际支持的模型>'
  # PAPERGUARD_AI_TIMEOUT 默认 60 秒
  ```

  本地 `.env` 已被 gitignore；显式环境变量优先。生产凭据由受控环境注入，
  不在报告、日志或页面中回传明文。

- **离线幂等切片（SQLite/local mock）**：AI 请求按 `tenant/member + action + Idempotency-Key` 隔离；同域同载荷只派发一次，unknown 不自动重发。历史 `(user_id,key)` 记录不自动回填租户；这不是收费、生产迁移或最终对账能力。
- **用户自带模型（BYOK）**：仅本账号生效。保存、清除、管理员修改均记录审计元数据；
  密钥和论文正文不进入审计。系统已有密钥时，管理员保存配置可留空 API Key 以保留原值。
- **邮件验证**：配置 Resend 的 `PAPERGUARD_RESEND_API_KEY` / `PAPERGUARD_RESEND_FROM`，
  或 SMTP 的 `PAPERGUARD_SMTP_HOST` / `PORT` / `USER` / `PASSWORD` / `FROM`。
  **未配置邮件服务时，邮箱注册、验证码发送和邮箱绑定明确返回 `EMAIL_NOT_CONFIGURED`，
  不降级为免验证成功**。无邮箱的兼容账号注册与学校统一注册是独立路径。
- **静态密钥保护**：配置 `PAPERGUARD_SECRET_KEY` 后，新写入供应商密钥以 AES-GCM
  `pgsec1$...` 格式加密并校验完整性，页面仅显示脱敏摘要。主密钥使用至少 32 字节随机值，
  通过受控环境保存，和数据库备份分开保管，恢复时使用原主密钥，**不得直接换新值**。
  缺少主密钥、发现明文存量凭据或解密失败均 fail-closed，不会静默回退到其他供应商；
  存量明文迁移必须在受控维护窗口完成。
- **运行治理**：SSRF 防护、数据接收方披露、每日调用次数配额、突发限流、用量记录和
  上游熔断已实现。默认每用户每 UTC 日 200 次、每分钟 10 次，可用
  `PAPERGUARD_AI_DAILY_QUOTA` / `PAPERGUARD_AI_BURST_PER_MINUTE` 调整；
  熔断默认连续 5 次失败后打开，30 秒后允许恢复探针，可用
  `PAPERGUARD_AI_BREAKER_THRESHOLD` / `PAPERGUARD_AI_BREAKER_RESET_SEC` 调整。
  新的 W-08/C1 前置切片把请求登记、分钟/日非付费 quota 预留、审计和 reservation 状态放进同一 SQLite 事务；`unknown` 持有不自动释放或重发，仍不代表余额、收费或财务账务。

### 商业化边界：已有运行用量，尚无收费账务

`ai_usage` 与次数配额用于运行控制，**不是余额、token 成本或财务账本**。
当前熔断拒绝也计入请求次数；这不代表供应商已收费，不能直接据此对用户扣款。
当前没有充值、订阅、套餐、支付、发票、余额扣减和资金对账能力。

商业化按 [商业化范围与放行条件](docs/product/商业化范围与放行条件_20260930.md)
推进：先完成非付费试点运营与权益设计，再在计费主体、BYOK、免费额度、失败结算、
价格和数据退出规则明确后实现账务与支付。该范围文档是设计与验收边界，不是上线声明。

### 发布身份与验收证据

代码提交、PR/CI、部署版本、运行探针与真实业务验收分别记录，不能互相替代。
生产文本标记 `current-release` 与 JSON 标记 `current-release.json` 通过
`ops/sync-release-markers.py` 核验后同步；脚本默认只读，后端源码及前端产物
任一摘要不匹配即失败关闭，只有显式 `--write` 才备份旧 JSON 并原子更新元数据。
它**不部署代码、不重启服务，也不会把较新的测试提交伪装成生产版本**。

```bash
python3 ops/sync-release-markers.py source-manifest \
  --repo /absolute/path/to/paperguard --revision <实际部署的完整提交> \
  > /secure/operator/path/source-checksums.json

# 在目标服务器上，先只读核验；确认后才加 --write
python3 ops/sync-release-markers.py sync \
  --root /home/ubuntu/paperguard --webroot /var/www/paperguard \
  --expected-release <current-release 中的实际版本> \
  --source-manifest /secure/operator/path/source-checksums.json
```

前端发布时可传入 `PAPERGUARD_RELEASE_SOURCE_MANIFEST` 接入同一核验；未提供源码清单
时不声称 JSON 标记已同步。元数据校正的时间记录为 `marker_updated_at`，不捏造部署时间。
最新收口记录见 [CI、审计与异常恢复报告](docs/reports/CI审计与异常恢复收口_20260930.md)，
尚未放行项见 [未决项](docs/evidence/未决项_20260913.md)。
2026-09-30 已复核追加验收提交 `0740f8f` 的四组 workflow、七项检查全部通过；
后续提交仍须重新读取 PR 最新 head 的 CI。生产管理员审计已通过生产浏览器核验；生产恢复演练、
失败归档任务处置、其他角色完整 UAT 仍是未决项，商业化仅完成范围设计。

## Docker 部署(服务器)

```bash
# 1. 服务器安装 Docker + Docker Compose 后:
git clone https://github.com/dkfask/paperguard.git
cd paperguard
docker compose up -d --build

# 2. 访问
#    前端:  http://服务器IP:8080
#    后端不直接暴露, 由前端 Nginx 代理 /api
```

- 数据(论文项目 SQLite + 上传图片)持久化在 Docker volume `paperguard_data`,重建/升级容器不丢数据
- `worker` 容器负责站内通知 outbox 消费和截止提醒调度，与后端共享数据库卷；可用 `PAPERGUARD_WORKER_INTERVAL_SECONDS` 调整周期
- 首次部署前必须设置强密码环境变量 `PAPERGUARD_ADMIN_PASSWORD`(至少 12 位, 含字母和数字), 不再提供固定默认密码
- 小程序服务端还需在受控 secrets 中设置 `PAPERGUARD_WECHAT_APP_ID`、`PAPERGUARD_WECHAT_APP_SECRET` 和独立的 `PAPERGUARD_MP_BINDING_SECRET`; 未配置时 MP 登录保持关闭并返回明确错误码
- 订阅消息模板映射通过环境变量 `PAPERGUARD_WECHAT_SUBSCRIBE_TEMPLATES` 下发 JSON（事件类型 → 模板 ID），源码与 compose 不写入可用凭据或模板字面量；未配置时只走站内消息
- 微信真机 / 合法域名 UAT 前置：在 `miniprogram/` 执行 `npm run check:wechat-uat`。该门禁只打印阻塞项（合法域名、AppID、服务端凭据是否已从环境注入、订阅模板、真机点击），**不能替代开发者工具/真机验收**。`localhost`、裸 IP、`example.com` / `example.edu` 等占位域名一律失败关闭；构建微信小程序时 `VITE_API_BASE_URL` 必须是备案 HTTPS 域名。凭据只从受控环境读取，源码与报告不写入可用字面量。
- 学院管理员桌面端产品化门禁：在 `frontend/` 执行 `npm run check:college-admin`，并跑 `backend/tests/test_policy_lifecycle.py`。覆盖报表 `rate()`、组织树可见根、登录/错误边界落地 `/workspace`、规范不得绑定兄弟学院。这是本地代码门禁，不是 Chrome 真机复测。
- 小程序隐私/分享/角标：首启需同意隐私说明（登录凭证、账号绑定、推送标识；不采集手机号/位置）；「我的」含关于与隐私政策；提交/审阅/正文页禁用分享，仅任务首页可分享；消息 Tab 用未读数设置角标。由 `miniprogram/` 的 `check:structure` 与 `test:flow` 覆盖，不是真机 UAT。
- 小程序提交详情展示教师退回理由与修订截止；预检/确认提交对逾期、已有待审、正文过期返回稳定错误码，并给出「联系教师 / 查看待审核提交 / 重新预检」下一步。仍不是真机 UAT。
- 小程序学生可撤回待审核提交（教师不能代撤）；提交历史展示修改说明与上轮未解决阻断；我的任务把已通过/归档阶段折叠为历史。由 `check:structure`、`test:flow` 与工作流 happy path 覆盖，不是真机 UAT。
- 小程序登录页用户可见名称为「论文卫士Pro」；任务、提交详情与提交确认页展示截止倒计时/逾期时长；提交确认页、提交详情与教师审阅检查页签逐条展示必交材料绑定结果，检查未完成/失败给出重新预检；提交详情时间线按提交、撤回、退回、通过事件展示；教师「其他任务」打开已有决定的只读审阅记录，审阅页在已退回/已通过后不再显示退回、通过、新增批注或人工确认。`GET /api/my-tasks` 在撤回/重开后仍用 `last_submission_id` 回填只读 `current_submission`，不把撤回提交重新排进待审队列。仍不是真机 UAT。
- 当前 PR 已落地租户/成员运行时基础、可信上下文与类型化 SaaS API 切片、限定租户 AI 元数据查询/待复核 CAS 和密钥 fail-closed；这**不等于完整多租户 SaaS**。现有业务资源全量 tenant_id/RLS、租户级恢复、对账、支付和商业放行仍未完成。不要把本地测试或 CI 绿灯宣传成 SaaS 已上线。详见 `docs/tenancy/implementation-status-20261004.md`。
- 上传检查和自动修复需要登录; 项目与图片按用户权限隔离
- 上传检查文件只在处理期间使用; 写作项目和图片会持久化到 Docker volume, 删除项目后按引用回收图片
- 导出和自动修复生成的临时文件在响应完成后清理

### 反向代理(可选,HTTPS/域名)

用 Nginx/Caddy 把 80/443 转发到 `http://127.0.0.1:8080` 即可,无需改应用配置。

### 升级

```bash
git pull
docker compose up -d --build
```

### 备份数据

```bash
docker run --rm -v paperguard_data:/data -v $(pwd):/backup alpine tar czf /backup/paperguard-backup.tar.gz -C /data .
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/templates` | 模板列表 |
| GET/POST/PUT/DELETE | `/api/templates/{id}` | 模板 CRUD(结构+样式+封面字段) |
| GET | `/api/projects` | 项目列表 |
| POST | `/api/projects` | 基于模板创建写作项目 |
| GET/PUT/DELETE | `/api/projects/{id}` | 项目读写 |
| PUT | `/api/projects/{id}/section/{key}` | 保存章节内容(标题+段落) |
| PUT | `/api/projects/{id}/meta` | 保存封面字段 |
| POST | `/api/projects/{id}/export` | 按模板同步导出 docx（历史兼容接口） |
| POST/GET | `/api/projects/{id}/export-jobs` | 创建或查询持久化 DOCX 导出任务 |
| GET | `/api/projects/{id}/export-jobs/{export_id}/download` | 下载已完成的项目导出制品 |
| POST | `/api/projects/{id}/import-docx` | 预览或将已有 DOCX 正文、表格和图片导入当前草稿 |
| GET | `/api/packs` | 检查规则包列表 |
| GET | `/api/policies` | 当前用户可见的学校规范/教师要求 |
| GET | `/api/policies/{id}` | 规范集合详情 |
| POST | `/api/policies` | 创建规范集合 |
| PUT | `/api/policies/{id}` | 更新未发布草稿 |
| POST | `/api/policies/{id}/publish` | 发布规范版本（发布后不可原地修改） |
| POST | `/api/policies/{id}/archive` | 停用规范版本 |
| POST | `/api/policies/{id}/versions` | 从已发布规范创建新版本草稿 |
| POST | `/api/policies/{id}/documents` | 上传规范参考文档 |
| POST | `/api/policies/import` | 上传规范文档，AI 解析并创建可编辑草稿 |
| GET | `/api/policies/{id}/audit` | 查看规范审计记录 |
| GET | `/api/audit` | 按资源、动作、操作者和组织范围查询统一审计投影 |
| GET | `/api/audit/export` | 按同一权限与筛选条件导出脱敏审计 CSV |
| GET | `/api/organizations` | 组织层级与作用域 |
| GET | `/api/projects/{id}/check` | 按模板快照和项目规范生成统一检查报告 |
| GET | `/api/projects/{id}/check-runs` | 查看项目检查运行记录 |
| GET | `/api/projects/{id}/compliance-report` | 生成送审/归档合规报告 |
| POST | `/api/projects/{id}/requirements/{item}/waiver` | 对单条要求申请豁免 |
| POST | `/api/projects/{id}/requirements/waivers/{waiver_id}/revoke` | 撤销已批准豁免并触发候选版本复核 |
| POST | `/api/projects/{id}/requirements/{item}/dispute` | 对单条要求提交申诉 |
| GET | `/api/projects/{id}/requirements/upgrade-preview` | 预览规范快照升级差异 |
| POST | `/api/projects/{id}/requirements/upgrade` | 显式升级项目规范快照 |
| POST | `/api/check` | 上传 docx 检查 |
| POST | `/api/fix` | 上传 docx 自动修复并下载 |
| GET/POST | `/api/cohorts` | 批次列表与创建 |
| POST | `/api/cohorts/{id}/members/import` | 成员批量导入预检与部分成功提交 |
| POST | `/api/cohorts/{id}/stages` | 创建阶段草稿 |
| POST | `/api/stages/{id}/preview` | 阶段发布预览 |
| POST | `/api/stages/{id}/publish` | 发布阶段并冻结成员快照 |
| POST | `/api/stages/{id}/versions` | 创建阶段配置升级版本与影响预览 |
| POST | `/api/stages/{id}/versions/{config_id}/publish` | 发布升级版本并显式迁移任务 |
| GET/POST | `/api/submission-candidates`、`/api/submissions` | 提交预检、幂等正式提交与版本快照 |
| POST | `/api/submissions/{id}/reviews` | 教师审核、退回或通过 |
| GET/POST | `/api/archives` | 异步归档任务与归档包查询 |
| GET | `/api/jobs` | 查询归档和项目导出等持久后台任务 |
| GET/POST | `/api/outbox`、`/api/outbox/claim` | 查看范围内待发送事件、原子领取租约 |
| POST | `/api/outbox/{id}/ack`、`/fail`、`/retry` | 消费确认、失败回退/封顶和人工重试 |
| GET/PUT/POST | `/api/operations/retention`、`/api/operations/backup` | 保留期限配置与管理员一致性备份下载 |
| GET | `/api/saas/v1/context` | 返回服务端解析的租户、成员、能力和授权版本上下文 |
| GET/POST | `/api/saas/v1/tenants` | 平台管理员创建租户；成员可读取被授权租户列表 |
| GET/POST | `/api/saas/v1/tenants/{tenant_id}/memberships` | 成员范围读取与平台管理员成员绑定 |
| POST | `/api/projects/{id}/export-preview` | 正式下载前只读预览版本、规范门禁、缺失项、章节/表格/图片/公式摘要 |
| POST | `/api/auth/mp/login` | 微信 code 换取已绑定会话或一次性绑定票据 |
| POST | `/api/auth/mp/bind` | 用校内账号密码绑定当前微信身份 |
| POST | `/api/auth/mp/unbind` | 小程序会话下经口令确认解绑微信 |

## 命令行

核心引擎也保留了 Click CLI:

```bash
paperguard check paper.docx --pack GB-T-7713-1-2006
paperguard fix paper.docx -o fixed.docx --pack GB-T-7713-1-2006
paperguard backup /backup/paperguard-backup.zip
paperguard restore /backup/paperguard-backup.zip --yes  # 停止服务后执行
paperguard migrate  # 幂等创建/升级 SQLite 并输出迁移账本

# 也可单次执行 worker（排障/演练）
PAPERGUARD_WORKER_ONCE=1 python -m app.worker
```

## 测试

```bash
cd backend
pip install -r requirements-dev.txt   # 含 pytest/Pillow；生产镜像只装 requirements.txt
pytest -q        # 当前 657 passed, 2 skipped: 引擎 / docx 读写 / 模板/项目 API / 规范生命周期 / 工作流与安全回归 / 小程序认证契约
```

## 模板与规则

- **模板**(`/api/templates`):章节树 + 每类元素样式 + 封面字段,在线配置
- **检查规则包**(`backend/app/core/rules/packs/*.yaml`):GB/T 7713、GB/T 7714、SIM-UNIV 通用规则,每条含 `scope`(JMESPath)、`when`(JSONLogic)、`message` 和可选 `fix`; 规范项可按规则 ID 冻结受控覆盖 `when/scope/severity/message/fix`
- **写作规范**:学校/学院/专业/课程/批次/教师要求独立建模,支持 Markdown frontmatter、自动/人工/文档要求、组织绑定、版本生效时间、项目快照、统一报告、豁免和申诉留痕

### 2026-10-04 SaaS 安全基础续作（受限本地切片）

PR #34 已有只读历史归属准入、显式平台批准下的draft/首位owner与安全成员创建，以及tenant/member限定的空白手写项目create/list/get/CAS update。导出、文件、异步及完整启用尚未隔离的入口拒绝，不代表共享SaaS上线。

- [实施状态与证据边界](docs/tenancy/implementation-status-20261004.md)
- [待裁决的客户映射/平台身份/指导关系/DB选择](docs/tenancy/conditional-tenancy-decision-packet.md)
- [本轮交付记录](docs/releases/2026-10-04-pr34-ownership-pilot.md)

本地真实历史归属预检仍BLOCKED；无生产迁移、部署、合并或真实UAT授权。
