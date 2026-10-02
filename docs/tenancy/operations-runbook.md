# 运维操作手册：worker、恢复、退出、发布与支持（27.3 文档包第 6 项）

> 状态：**design_ready**。本文是取证 + 操作手册，**其中命令未在生产执行过**。
> 日期：2026-10-01。基线：HEAD `e166f97`（含本轮未提交的 F-1/F-5 修复）。
> 生产拓扑：**systemd + venv + nginx**（非 Docker）。服务名 `paperguard` / `paperguard-worker`。
> 关联：`data-root-fragmentation.md`（恢复）、`ai-metering-runbook.md`（支持/成本）、`quota-and-billing-review.md`（额度）。

> ⚠️ **本文所有生产命令均为只读或低风险**。标注 🔴 的步骤会改变生产状态，**执行前必须另行授权**。

---

## 0. 结论摘要

| 编号 | 发现 | 影响 |
|---|---|---|
| **W-1** | **`deploy.sh` 不是生产部署路径。** 仓库并存两套互斥拓扑，`deploy.sh` 走 Docker Compose，生产跑 systemd + venv | 🔴 **误用会在生产旁边拉起第二套分叉部署** |
| **W-2** | **微信推送结果未写入 worker 迭代日志** | 推送失败在正常运行时**完全不可见** |
| **W-3** | **回滚只有前端的、且是手工的**。后端无回滚脚本 | 后端版本出错只能手工处置 |
| **W-4** | **无租户级完整数据导出/清除能力** | 阻塞「退出」流程与数据返还义务 |
| **W-5** | **无定时备份归档机制**。`POST /api/operations/backup` 只做一次性下载，产物发完即删 | 无任何机制保证「需要时可恢复」 |

**总体判断**：worker 的**内部设计质量高**（租约、指数退避、终态park、人工重试入口俱全），真正的风险在**运维面**——部署入口误导、回滚缺失、备份未启用、数据导出缺失。

---

## 1. 部署拓扑：两套并存，生产用第二套

### 1.1 事实

| 项 | 拓扑 A（Docker） | 拓扑 B（systemd）**← 生产实际** |
|---|---|---|
| 入口脚本 | `deploy.sh` | `ops/deploy-frontend.sh` + 手工 systemctl |
| 编排 | `docker-compose.yml`、`docker-compose.staging.yml` | — |
| 镜像 | `backend/Dockerfile` | — |
| 服务管理 | `docker compose` | `paperguard.service`、`paperguard-worker.service` |
| 运行环境 | 容器 | `/home/ubuntu/paperguard/venv/bin/python` |
| 数据 | docker volume `paperguard_data` | `/home/ubuntu/paperguard/backend/data` |
| 反向代理 | compose 内置 | `ops/nginx/paperguard.site.conf` |
| 验证脚本 | — | `ops/online-verify.sh`、`ops/uat-verify.sh`、`ops/test-*.sh` |

生产实测（本轮只读核对）：进程 CWD `/home/ubuntu/paperguard/backend`，`MainPID` 来自 `systemctl show paperguard`，环境文件 `/etc/paperguard/paperguard.env`——**确认为拓扑 B**。

### 1.2 风险

`deploy.sh` 是仓库根目录唯一的「一键部署」入口，文件名和位置都让人以为它是正式路径。实际执行后果：

1. **第 1 步就失败**——生产机若无 Docker，`command -v docker` 失败并退出（相对安全）
2. **若装了 Docker**，`docker compose up -d --build` 会拉起**第二套服务**，与 systemd 那套**争抢同一个 SQLite 文件** → 数据库锁竞争，且两套的静态资源、数据目录、端口都可能不一致

> **结论**：`deploy.sh` 在生产环境是**陷阱**。这不是「未使用的旧脚本」，是「看起来该用但会出事」。

### 1.3 处置

| 编号 | 动作 |
|---|---|
| **OPS-FIX-1** | 在 `deploy.sh` 顶部加硬性拒绝：检测到 `systemctl is-enabled paperguard` 为 enabled 时直接退出并提示改用拓扑 B |
| **OPS-FIX-2** | 在 `docs/` 明确标注「生产部署 = 拓扑 B」，并在仓库根 README 指向 `ops/` |
| **OPS-FIX-3** | 把 `deploy.sh` 重命名为 `deploy-docker-dev.sh`，避免继续被误认为生产路径 |

---

## 2. worker 手册

### 2.1 组成

`app/worker.py` 每轮顺序执行 6 个消费者（默认间隔 **30 秒**，最小 5 秒）：

| 消费者 | 处理对象 | 租约 | 最大尝试 |
|---|---|---|---|
| `create_system_due_reminders` | 到期提醒生成 | — | — |
| `consume_archive_jobs` | 归档任务 | 300s | 5 |
| `consume_project_export_jobs` | 项目导出 | 300s | 5 |
| `consume_document_render_jobs` | 文档渲染 | 300s | 5 |
| `consume_in_app_outbox` | 应用内通知 | 120s | 5 |
| `deliver_wechat_push` | 微信推送 | — | 5 |

**关键设计（已验证，质量高，不必改）**：

- **租约令牌**：`claim_jobs` 发 `lease_token`，完成/失败时校验，**防止陈旧 worker 覆盖新 worker 的结果**
- **崩溃恢复**：`running` 且 `lease_until <= now` 的任务会被重新抢占（`db.py:1650`）
- **原子抢占**：`BEGIN IMMEDIATE` + `UPDATE ... WHERE id = ?`，并发安全
- **指数退避**：`5 * 2^(attempt-1)`，上限 **300 秒**
- **终态 park**：`attempt_count >= max_attempts` 后停在 `failed`，不再自动重试
- **消费者 ID 防注入**：`PAPERGUARD_WORKER_ID` 经 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` 过滤，阻断经任务链路进入文件路径

### 2.2 常用只读命令

```bash
# worker 是否在跑
systemctl status paperguard-worker
systemctl show paperguard-worker --property=MainPID,ActiveEnterTimestamp

# 最近迭代日志
journalctl -u paperguard-worker -n 100 --no-pager
journalctl -u paperguard-worker -f | grep -E "job.failed|iteration.failed"

# 单轮调试（不常驻）
sudo systemctl stop paperguard-worker
cd /home/ubuntu/paperguard/backend
sudo env $(grep -v '^#' /etc/paperguard/paperguard.env | xargs -d '\n') \
  PAPERGUARD_WORKER_ONCE=1 /home/ubuntu/paperguard/venv/bin/python -m app.worker
sudo systemctl start paperguard-worker
```

> ⚠️ 上面的 `env $(...)` 写法是**必需的**——直接跑 `python -m app.worker` 读不到 systemd 的 EnvironmentFile，会缺 `PAPERGUARD_SECRET_KEY`。

### 2.3 积压与终态排查

```sql
-- 各状态任务数
SELECT job_type, state, COUNT(*) AS n, MAX(updated_at) AS latest
FROM jobs GROUP BY job_type, state ORDER BY n DESC;

-- 已 park（耗尽重试）的任务——这些**不会再自动重试**
SELECT id, job_type, resource_id, attempt_count, last_error, updated_at
FROM jobs WHERE state = 'failed' AND attempt_count >= max_attempts
ORDER BY updated_at DESC;

-- 长时间 running（疑似 worker 卡死或租约未回收）
SELECT id, job_type, leased_by, lease_until, updated_at
FROM jobs WHERE state = 'running' AND lease_until < datetime('now')
ORDER BY updated_at DESC;
```

### 2.4 人工重试

四个终态任务都有重试入口（需按对应权限）：

| 任务 | 端点 |
|---|---|
| 归档 | `POST /api/archives/{archive_id}/retry` |
| 项目导出 | `POST /api/projects/{proj_id}/export-jobs/{export_id}/retry` |
| 文档渲染 | `POST /api/document-projects/{project_id}/render-jobs/{render_job_id}/retry` |
| 站内通知 | `POST /api/outbox/{event_id}/retry` |

SQL 直改（🔴 **仅在确认任务确属误判失败时**，且需留痕）：

```sql
UPDATE jobs
SET state = 'queued', attempt_count = 0, available_at = datetime('now'),
    lease_token = NULL, lease_until = NULL, leased_by = NULL
WHERE id = ? AND job_type = ?;
```

> 三个条件缺一不可：`job_type` 防串表、`attempt_count = 0` 防止立刻再次 park、必须先确认 `last_error` 是可重试的（如临时网络故障）而非确定性失败（如文件已损坏）。

### 2.5 W-2：微信推送在迭代日志中不可见

`worker.py:61-74` 的 `worker.iteration.completed` 记录了 `reminders_created`、`archives_*`、`exports_*`、`document_renders_*`、`outbox_*`——**唯独没有 `wechat_pushes`**。

后果：微信推送的 claimed/delivered/failed 在正常迭代里**完全看不到**。只有 `job.failed` 这类逐任务日志能间接反映，但 `deliver_wechat_push` 走的是独立的 `mini_program_push_log` 链路，不一定产生同类日志。

**处置（OPS-FIX-4）**：在 `log_event` 中补上 `wechat_pushes_claimed` / `wechat_pushes_delivered` / `wechat_pushes_failed`。

---

## 3. 恢复手册

### 3.1 现状：能力存在，但是「一次性下载」而非「定时归档」

| 项 | 状态 |
|---|---|
| `app/services/backup_restore.py` | ✅ 实现完整（快照、清单、校验、恢复） |
| 数据根一致性 | ✅ 本轮 DR-FIX-1 已修（复用 `db.data_dir()`） |
| 备份端点 | ✅ `POST /api/operations/backup`（需 manager 权限） |
| **归档机制** | ❌ **不存在定时归档**。备份是一次性下载，产物发完即删 |
| 生产现有快照 | 仅发布脚本产物（`/home/ubuntu/paperguard/backups/`） |

> ⚠️ **一处必须澄清的机制误判（本文撰写过程中自我更正）**：
> 端点实现（`api/operations.py:61-74`）创建**临时 zip** → 作为下载返回 →
> `background_tasks` 在发送后 `unlink`。
> 因此**「服务器上找不到 `backup-*.zip`」是设计使然，不是异常**——即便有人执行过备份，
> 文件也不会留在盘上。
> **正确的结论是**：不是「备份从未跑过」，而是「**没有定时归档机制**」。
> 二者的补救动作完全不同（前者要排查执行，后者要新建归档任务）。
>
> 另注：发布脚本的 `/home/ubuntu/paperguard/backups/` 是**另一套**产物（`tar czf` 整目录），
> 与 `backup_restore` 无关，不能互相替代。

> 详见 `data-root-fragmentation.md` §6.1。**当前不具备「随时可恢复」能力**——
> 因为可恢复的前提是「有一份还在的备份」，而现在没有任何机制保证这一点。

### 3.2 两个已知的数据根风险

| 风险 | 说明 |
|---|---|
| **发布暂存目录** | `backend.next-454a771/data/`、`backend.next-dd035e7/data/` 各带 `uploads`，**不在数据根内**。发布切换时若暂存目录被清理，暂存期写入的附件丢失 |
| **数据目录无版本标记** | 现网目录若不是 Git 仓库，版本判定只能靠 `current-release` 标记文件 |

### 3.3 备份与恢复流程（🔴 会改变生产状态）

**前置**：本节所有步骤执行前**必须**有独立授权。

```bash
# 0. 记录当前版本（用于恢复后核对）
cat /home/ubuntu/paperguard/current-release 2>/dev/null || echo "(无标记文件)"

# 1. 触发备份并**落盘保存**（端点本身只回传下载，文件发完即删）
#    必须加 -o 存到本地，否则拿到的只是一个即将消失的临时文件
curl -fsS -X POST http://127.0.0.1:8000/api/operations/backup \
     -H "Authorization: Bearer $MANAGER_TOKEN" \
     -o /home/ubuntu/backups/manual-$(date +%Y%m%dT%H%M%S).zip

# 2. 核对产物（大小 + 清单可读）
ls -la /home/ubuntu/backups/manual-*.zip
unzip -l /home/ubuntu/backups/manual-*.zip | head -20

# 3. 校验 sha256（端点返回体含 digest，应与文件实际摘要一致）
sha256sum /home/ubuntu/backups/manual-*.zip

# 4. 副本异地
rsync -av /home/ubuntu/backups/manual-*.zip <异地目标>:
```

> **不需要停 worker**：端点用 `create_backup` 走 SQLite 备份 API 取一致快照，
> 与运行中的 worker 并发安全。停 worker 只在**恢复**时需要。
>
> 权限：`_ensure_manager(user)`，需 manager 角色，**不是** admin 专属。
> 🔴 上述命令会写生产磁盘，执行前需独立授权。

### 3.4 恢复流程

```bash
# 1. 停两个服务
sudo systemctl stop paperguard paperguard-worker

# 2. 恢复前**必须**留存当前数据（回退保险）
sudo cp -a /home/ubuntu/paperguard/backend/data \
           /home/ubuntu/paperguard/backend/data.pre-restore-$(date +%Y%m%dT%H%M%S)

# 3. 执行恢复（走 API 或 restore_restore 模块）
# 4. 启动并验证
sudo systemctl start paperguard paperguard-worker
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/health/ready
```

> 备份目录里已有 `restored.pre-restore-20260919T135325Z/paperguard.db` 的先例——说明历史上做过一次恢复演练，且**保留了恢复前的原始数据**。这个做法应当固定为流程。

### 3.5 待补（OPS-FIX-5）

| 编号 | 动作 |
|---|---|
| OPS-FIX-5a | **新建定时归档任务**（当前备份是一次性下载，无人值守时盘上不会有任何副本） |
| OPS-FIX-5b | 备份产出**异地**副本（当前全在同一台机器的同一磁盘，机器级故障即全失） |
| OPS-FIX-5c | 把两个 `backend.next-*` 暂存目录的 `uploads` 纳入数据根或在清理前显式迁移 |
| OPS-FIX-5d | 恢复演练留痕：每次恢复记录版本、时间、执行人、验证结果 |
| OPS-FIX-5e | `retention` 从全局单条设置改为**按租户**存储（退出阶段 5 需要） |

---

## 4. 退出（Offboarding）手册

### 4.1 W-4：能力缺口

总设计 §12.1 规定 `closing` 状态允许「经确认的退出导出、账务核对和关闭流程」。实测：

| 能力 | 现状 |
|---|---|
| 租户级完整数据导出 | ❌ **不存在**。全仓只有 `export_process_record`（单条过程记录） |
| 租户级数据清除 | ❌ **不存在**。所有 `DELETE` 都是窄操作（配置、组织单元、绑定、项目、单个学生、模板） |
| 归档 | ⚠️ 有 `archives` 机制，但用途是项目归档，非租户整体 |
| 账务核对 | ❌ 依赖 `ai-metering-runbook.md` M-4（用量无查询入口） |
| **全局保留策略** | ⚠️ **已存在但只是全局的**：`GET/PUT /api/operations/retention`（`_ensure_manager`），存于 `system_settings`。**不是按租户**，也不覆盖退出场景 |

**结论：退出流程当前无法端到端执行。** 这不是可以边做边补的缺口——数据返还和删除是**合同义务**，必须在 C0 试点前具备。

> `retention` 端点的存在是个**好消息**：说明「保留策略」这个概念已经进了数据模型。
> 但它是**全局单条设置**（一个 key），多租户下每个租户的合同保留期不同，
> 必须改为按租户存储。

### 4.2 退出的五个阶段（目标设计）

```text
阶段 1  申请与确认      客户书面提出 → 平台确认范围与时间
阶段 2  冻结            租户置 closing：禁止新增消费、禁止新批次
阶段 3  导出            全量导出（数据 + 附件 + 用量 + 审计）← 能力缺失
阶段 4  账务核对        期间用量与费用核对 ← 依赖 M-4
阶段 5  清除与关闭      数据清除（按保留义务延后）→ closed
```

### 4.3 各阶段的硬约束（来自总设计 §12.1）

| 状态 | 允许 | 禁止 |
|---|---|---|
| `restricted` | 阅读本人/授权数据、申请续期、约定范围退出导出 | 新增 AI/席位/资源消费 |
| `suspended_security` | 仅明确批准的安全恢复/申诉入口 | 客户业务访问；**不能为「可导出」绕过安全封禁** |
| `closing` | 退出导出、账务核对、关闭流程 | 新的自动续费、新教学批次、普通消费 |
| `closed` | 保留必要服务/审计索引 | 正常业务写入和自动恢复权益 |

> **`suspended_security` 的那条禁止值得单独强调**：安全封禁的租户不能借「退出导出」绕过封禁。这两条需求（导出义务 vs 安全封禁）必须在阶段 1 就由人工裁决，不能自动判定。

### 4.4 待补（OPS-FIX-6）

| 编号 | 动作 |
|---|---|
| OPS-FIX-6a | `GET /api/admin/tenants/{tenant_id}/export` —— 全量导出（元数据 + 附件打包 + 用量 + 审计） |
| OPS-FIX-6b | 导出任务的**保留期与访问控制**（导出包本身是敏感数据） |
| OPS-FIX-6c | `closing` / `closed` 状态机与现有 `users.status` 的映射 |
| OPS-FIX-6d | 清除的**延迟期**（按合同保留义务），软删除 → 物理删除的两阶段 |
| OPS-FIX-6e | `suspended_security` 与导出义务的冲突裁决流程（人工，不自动） |

---

## 5. 发布手册

### 5.1 现有能力

| 资产 | 位置 | 作用 |
|---|---|---|
| 前端发布 | `ops/deploy-frontend.sh` | 5 步流程，含**先备份现网再原子换名** |
| 发布标记 | `ops/sync-release-markers.py` | 校验已部署字节后才写 `current-release.json`；**从不部署代码** |
| 上线验证 | `ops/online-verify.sh` | 线上健康与功能抽查 |
| UAT 验证 | `ops/uat-verify.sh` | UAT 场景 |
| 脚本自测 | `ops/test-online-verify.sh`、`ops/test-uat-verify.sh` | 验证脚本自身 |
| 报告版本检查 | `ops/check-report-version.sh` | 防止用旧报告冒充新发布 |

`ops/deploy-frontend.sh` 的设计值得肯定：第 79 步「原子换名（先备份现网）」，第 124 行给出回滚指引。

### 5.2 W-3：回滚只覆盖前端，且是手工

`ops/deploy-frontend.sh:124`：

```
回滚：mv ${BACKUP} ${WEBROOT} 并把 ${MARKER} 改回旧版本号
```

| 维度 | 现状 |
|---|---|
| 前端回滚 | ⚠️ 有指引，**纯手工**（两条命令） |
| **后端回滚** | ❌ **无脚本、无指引** |
| 数据迁移回滚 | ❌ 无 |
| 回滚判据 | ❌ 未定义「什么情况下该回滚」 |

**后端回滚的特殊风险**：本轮 W-02 的 DDL 草案若落地，迁移可能是**不可逆**的（加列可以，删列/改类型麻烦）。因此后端回滚必须区分：

| 迁移类型 | 可回滚性 |
|---|---|
| 加表 / 加列（向后兼容） | ✅ 可回滚代码，数据列留着无害 |
| 改类型 / 删列 | ⚠️ 需**双写期**或**停机窗口** |
| 数据回填 | ⚠️ 需记录回填水位，回滚时按水位反向清理 |

### 5.3 发布前检查清单

```text
□ CI 全绿，且用的是项目自身解释器（仓库 .venv，Python 3.12）
□ 视觉快照若过期，先人工核对 diff 再决定是否重生成
□ 迁移脚本已在本机复刻 CI 步骤验证过
□ 有明确回滚方案（尤其涉及迁移时）
□ 备份已产出且验证过可读
□ current-release 旧值已记录
□ 暂存目录 backend.next-* 的 uploads 已处理
□ 发布后：online-verify.sh + uat-verify.sh 均通过
```

### 5.4 待补（OPS-FIX-7）

| 编号 | 动作 |
|---|---|
| OPS-FIX-7a | 后端发布脚本（与前端对称，含 systemd 重启与健康等待） |
| OPS-FIX-7b | 后端回滚脚本 |
| OPS-FIX-7c | 回滚判据文档（什么信号触发回滚） |
| OPS-FIX-7d | 迁移回滚指引（按 §5.2 三类分别写） |

---

## 6. 支持手册

### 6.1 已有内容

| 场景 | 文档 |
|---|---|
| AI 成本异常 / 账单对不上 | `ai-metering-runbook.md` §5.4 未知结果 Runbook（R-1～R-7） |
| AI 限流 / 熔断误判 | `ai-metering-runbook.md` + `worker.py` 熔断常量说明 |
| 额度相关咨询 | `quota-and-billing-review.md`（注意：当前只能解释「次数」，不能解释「费用」） |
| 数据恢复 | 本文 §3 |

### 6.2 缺失

| 缺口 | 说明 |
|---|---|
| **无用户可见的状态页** | 用户遇到「任务一直转圈」时，只能等或问客服，没有自查入口 |
| **无错误码字典对外说明** | 367 处裸字符串错误，用户看到的提示无法自助对照（见 `api-contract-delta.md` G-3） |
| **无 incident 记录机制** | 事故过程、影响面、处置动作无结构化留痕 |
| **告警未定义** | 无任何告警配置；worker 停了、积压了、熔断打开了，都不会主动通知 |

### 6.3 待补（OPS-FIX-8）

| 编号 | 动作 | 优先级 |
|---|---|---|
| OPS-FIX-8a | 最小告警集：worker 心跳丢失、任务积压超阈值、熔断打开、`unknown` 占比突增 | 高 |
| OPS-FIX-8b | 运维健康页（只读）：worker 状态、各类积压、最近失败、熔断状态 | 中 |
| OPS-FIX-8d | 错误码字典 `docs/tenancy/error-codes.md` 并在用户界面可查 | 中 |

---

## 7. 汇总：交给工作包的动作

| 编号 | 动作 | 优先级 | 阻塞 C0？ |
|---|---|---|---|
| **OPS-FIX-1** | `deploy.sh` 检测到 systemd 生产实例时硬拒绝 | 🔴 **高** | **是** |
| **OPS-FIX-3** | `deploy.sh` 重命名为 `deploy-docker-dev.sh` | 高 | 是 |
| **OPS-FIX-2** | 文档标注生产拓扑 | 高 | 是 |
| **OPS-FIX-4** | worker 日志补 `wechat_pushes_*` | 中 | 否 |
| **OPS-FIX-5a** | 启用定期应用级备份 | 🔴 **高** | **是** |
| **OPS-FIX-5b** | 备份异地副本 | 高 | 是 |
| **OPS-FIX-5c** | 暂存目录 `uploads` 处置 | 高 | 是 |
| **OPS-FIX-6a** | 租户级全量导出 | 🔴 **高** | **是**（合同义务） |
| **OPS-FIX-6c/d/e** | closing 状态机、延迟清除、封禁冲突裁决 | 高 | 是 |
| **OPS-FIX-7a/b** | 后端发布与回滚脚本 | 中 | 否 |
| **OPS-FIX-8a** | 最小告警集 | 高 | 否 |

> **标记「阻塞 C0」的 6 项**理由：`OPS-FIX-1/3` 防误操作导致生产事故；`OPS-FIX-5a/b` 无备份即无恢复能力；`OPS-FIX-6a` 是合同义务。其余可与试点并行。

---

## 8. 明确不做

- **不执行本文任何 🔴 命令**——本文只提供手册，执行需独立授权
- **不在生产运行 `deploy.sh`**（§1.2）
- **不把「备份功能已实现」表述为「已具备恢复能力」**——没有归档机制，盘上不留副本（M-5 / §3.1）
- **不自动裁决 `suspended_security` 与导出义务的冲突**（§4.3）——必须人工
- **不把运维缺口的优先级排序当作已获授权的排期**
- **不修改任何脚本或服务配置**——本文只做取证与手册

---

## 附：只读核对命令

```bash
# 部署拓扑
systemctl is-enabled paperguard paperguard-worker
systemctl show paperguard --property=MainPID,ExecStart,WorkingDirectory

# worker 状态
systemctl status paperguard-worker --no-pager
journalctl -u paperguard-worker -n 50 --no-pager

# 任务积压（只读 SELECT）
sqlite3 /home/ubuntu/paperguard/backend/data/paperguard.db \
  "SELECT job_type, state, COUNT(*) FROM jobs GROUP BY job_type, state;"

# 备份产物是否存在
find /home/ubuntu/paperguard -name "backup-*.zip" 2>/dev/null | head

# 租户级导出能力是否存在
grep -rn "tenants/.*export" /home/ubuntu/paperguard/backend/app/api/ 2>/dev/null || echo "不存在"
```
