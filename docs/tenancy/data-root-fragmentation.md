# 数据根分裂：11 处解析点与备份路径缺陷

> 状态：**design_ready** — 跨切面发现，非单一工作包产出。**核心断言 DR-02 已本地复现。**
> 日期：2026-10-01。基线：HEAD `e166f97`。
> 关联：`BUG-20261001-02`（归档目录硬编码）、U-07（恢复只有隔离环境证据）、总设计第 20.4 节、登记表 `access_surfaces`。
> 授权边界：本文件是**只读代码分析与本地只读复现**。未执行任何 DDL/DML，未连接生产，未触发备份或恢复。

## 0. 结论

项目存在**同一个数据根的 11 处独立解析点**，其中 **2 处已与基准对齐、9 处未对齐**，它们读取不同的环境变量、回退到不同的 `__file__` 推导路径。后果分三层：

| 严重度 | 问题 | 影响 |
|---|---|---|
| **高** | `backup_restore.py` 的**数据根与数据库路径解析规则不一致** | 只设 `PAPERGUARD_DB_PATH` 时，备份清单指向错误目录 → 灾难恢复可能备份不到在线数据库 |
| **高** | 2 处**完全忽略所有环境变量**（`EXPORT_DIR`、`_DOCUMENT_DIR`） | 导出产物与政策文档**无法被重定位**，只能随代码目录走 |
| 中 | 2 处读**独立变量**（`PAPERGUARD_ARTIFACT_DIR`、`PAPERGUARD_TEMPLATE_DIR`） | 只设 `DATA_DIR` 时这两类资产**留在代码目录**，不在数据卷 |
| 中 | 3 处读 `DATA_DIR` 但**不读 `DB_PATH`** | 与 `db.py:data_dir()` 的优先级不一致 |

**这同时是对我上一轮修复的一次更正。** `BUG-20261001-02`（归档目录硬编码）我只修了 `api/workflow.py` 一处，属**点修而非类修**——同一缺陷类在其余 8 处仍然存在。

## 1. 证据边界

| 项 | 状态 | 说明 |
|---|---|---|
| 10 处解析点及其环境变量 | **已验证** | 逐处读源码 + 全仓 `environ.get("PAPERGUARD_*")` 汇总 |
| 生产当前是否踩到 | **未验证** | 未连生产；未读 `/etc/paperguard/paperguard.env` 的键名 |
| 备份是否已漏数据 | **未验证** | 未触发、未检查任何备份 |
| 实际数据分布 | **未验证** | 未列生产目录 |

> **本文件证明的是「代码路径存在且规则不一致」，不是「生产已发生数据丢失」。** 判定生产是否踩到需 T-01.1 基线冻结中的只读核对（读 env 键名，不读值）。

## 2. 十一个解析点全景

基准为 #1（`db.py:data_dir()`）。#2 是上轮已修复、与基准一致。**其余 9 处未对齐。**

| # | 位置 | 解析函数/常量 | 读取的环境变量 | 与 `data_dir()` 一致 |
|---|---|---|---|---|
| 1 | `backend/app/db.py:28,43-53` | `data_dir()` / `_default_db_path()` | `PAPERGUARD_DB_PATH` → `PAPERGUARD_DATA_DIR` → `__file__` 推导 | ✅ **基准** |
| 2 | `backend/app/api/workflow.py` | `_archive_dir()` | 同 #1（上轮已修） | ✅ 已对齐 |
| 3 | `backend/app/services/backup_restore.py:31-35` | `configured_db_path()` | `PAPERGUARD_DB_PATH` → `configured_data_dir()/paperguard.db` | ⚠️ 与 #4 规则不同 |
| 4 | `backend/app/services/backup_restore.py:27-28` | `configured_data_dir()` | **仅** `PAPERGUARD_DATA_DIR` → `__file__` 推导 | ❌ **不读 `DB_PATH`** |
| 5 | `backend/app/main.py:160-165` | `_configured_upload_dir()` | **仅** `PAPERGUARD_DATA_DIR` | ❌ 不读 `DB_PATH` |
| 6 | `backend/app/services/document_assets.py:9,17-18` | `document_upload_dir()` | **仅** `PAPERGUARD_DATA_DIR` | ❌ 不读 `DB_PATH`；且与 #5 **重复实现同一目录** |
| 7 | `backend/app/api/process_records.py:67-75` | `_process_file_dir()` | `PAPERGUARD_PROCESS_FILE_DIR` → `PAPERGUARD_DATA_DIR` | ❌ 不读 `DB_PATH` |
| 8 | `backend/app/services/document_render_jobs.py:35,43-46` | `artifact_dir()` | **仅** `PAPERGUARD_ARTIFACT_DIR` | ❌ 独立变量 |
| 9 | `backend/app/services/template_source.py:21,24-28` | `template_source_dir()` | **仅** `PAPERGUARD_TEMPLATE_DIR` | ❌ 独立变量 |
| 10 | `backend/app/services/export_jobs.py:21,159,163,173` | `EXPORT_DIR` **模块级常量** | **无** | ❌ **完全硬编码** |
| 11 | `backend/app/api/policies.py:41,538` | `_DOCUMENT_DIR` **模块级常量** | **无** | ❌ **完全硬编码** |

## 3. 三类缺陷

### 3.1 高：备份工具的数据根与数据库路径规则不一致

`backend/app/db.py:50-53` 的 `data_dir()` 优先级是 **`PAPERGUARD_DB_PATH` > `PAPERGUARD_DATA_DIR` > 推导值**。
`backend/app/services/backup_restore.py:27-28` 的 `configured_data_dir()` 优先级是 **`PAPERGUARD_DATA_DIR` > 推导值**——**不读 `PAPERGUARD_DB_PATH`**。

而备份根的取值在 `backend/app/services/backup_restore.py:78` 与 `:163`：

```python
root = Path(data_dir) if data_dir else configured_data_dir()
```

**在「只设 `PAPERGUARD_DB_PATH`、未设 `PAPERGUARD_DATA_DIR`」的配置下**：

| 对象 | 实际位置 | 是否正确 |
|---|---|---|
| 在线数据库 | `/custom/paperguard.db` | — |
| `configured_db_path()` | `/custom/paperguard.db` | ✅ |
| `configured_data_dir()`（= 备份根） | `<代码目录>/backend/data/` | ❌ **指向错误目录** |

备份会遍历一个**不含在线数据库**的目录。这直接冲击总设计第 20.4 节「数据库和文件必须同一可解释代数」与 U-07。

> 这正是 `backend/app/db.py:43-49` 那段 docstring 描述的问题在备份侧的**未修版本**——该 docstring 说「数据目录与数据库同源，保证归档/附件等持久化产物与 DB 落在同一备份域」，但备份工具自己遵守了另一套规则。

### 3.1.1 DR-02 本地复现（已验证）

用只读子进程导入 `db.data_dir()` 与 `backup_restore.configured_data_dir()`，比较在线 DB 路径是否落在备份根内。三种环境组合：

| 场景 | `PAPERGUARD_DB_PATH` | `PAPERGUARD_DATA_DIR` | 在线 DB | 备份根 | DB 在备份根内 |
|---|---|---|---|---|---|
| **A（缺陷）** | `/custom/vol/paperguard.db` | 未设 | `/custom/vol/paperguard.db` | `<仓库>/backend/data` | ❌ **否** |
| B | `/custom/vol/paperguard.db` | `/custom/vol` | `/custom/vol/paperguard.db` | `/custom/vol` | ✅ 是 |
| C | 未设 | 未设 | `<仓库>/backend/data/paperguard.db` | `<仓库>/backend/data` | ✅ 是 |

> 复现脚本 `/tmp/dr02b.py`（只导入模块、读环境变量、比较路径字符串，**不创建目录、不写文件、不触碰任何数据库**）。
> 场景 A 的输出即为 §3.1 表格的实测值。**这是本地只读复现，未使用生产配置。**

### 3.2 高：两处完全硬编码，资产无法重定位

- `services/export_jobs.py:21` `EXPORT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "exports"`，在 `:159,163,173` 直接使用（模块级常量，进程生命周期内不变）
- `api/policies.py:41` `_DOCUMENT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "policy-documents"`，在 `:538` 使用

**后果**：论文导出产物与政策文档**永远跟随代码目录**。发布切换代码目录时会被带走，且不在按数据目录做的备份范围内——**与 `BUG-20261001-02` 完全同型**。

### 3.3 中：独立变量与优先级不一致

- `PAPERGUARD_ARTIFACT_DIR`（渲染产物）、`PAPERGUARD_TEMPLATE_DIR`（模板源）**不随 `DATA_DIR` 联动**。只设 `DATA_DIR` 时，这两类客户内容资产**留在代码目录**。
- #5/#6/#7 不读 `PAPERGUARD_DB_PATH`，与 `db.py` 的优先级相反。
- #5 与 #6 是**同一目录的两份独立实现**（都指向 `<data>/uploads`），存在漂移风险。

## 4. 触发条件（已由 DR-02 复现精确界定）

复现结果表明，缺陷**不是**「代码目录与数据目录重合与否」的问题，而是精确的触发条件：

> **当且仅当 `PAPERGUARD_DB_PATH` 被设置、而 `PAPERGUARD_DATA_DIR` 未设置或设为不同值时，`backup_restore` 的备份根与在线数据库分裂。**

- 场景 B（两个都设且同值）→ 一致
- 场景 C（都不设）→ 一致
- 场景 A（只设 `DB_PATH`）→ **分裂**

因此**生产当前是否踩到，取决于它的 env 里 `PAPERGUARD_DATA_DIR` 是否与 `PAPERGUARD_DB_PATH` 同值**。该事实**未验证**——需 T-01.1 基线冻结时只读核对 `/etc/paperguard/paperguard.env` 的**键名与两者是否同值**（不读其他密钥值）。

若生产确实只设了 `DB_PATH`，则**现有备份可能未覆盖在线数据库**——这需要 DR-04 只读检查既有备份清单，**不得触发新备份**。

> 即使生产当前安全，该缺陷仍卡在 W-02 T-02.7（PG 隔离演练）与 M7（切换）的必经之路上：迁移必然要把数据移到独立卷，而那正是场景 A。

## 5. 目标模型：单一数据根 + 子目录解析

```
data_dir()                       ← 唯一权威（db.py:43，已有）
├── uploads/          document_assets.document_upload_dir()   ← main.py 复用它
├── archives/         _archive_dir()                          ← 已修
├── artifacts/        artifact_dir()
├── exports/          export_dir()            ← 需从常量改为函数
├── templates/        template_source_dir()
├── policy-documents/ policy_document_dir()   ← 需从常量改为函数
├── process-files/    process_file_dir()
└── paperguard.db     _default_db_path() + backup_restore.configured_db_path()
```

**约束**：
1. **只保留一个环境变量入口**。`PAPERGUARD_DB_PATH` 保留（兼容现有部署），`PAPERGUARD_DATA_DIR` 保留；`ARTIFACT_DIR` / `TEMPLATE_DIR` / `PROCESS_FILE_DIR` 降级为**可选覆盖**，未设时一律由 `data_dir()` 派生。
2. **所有解析函数接受可选 `root` 参数**，便于测试与多实例注入。
3. **模块级常量全部改为调用时解析**（`EXPORT_DIR`、`_DOCUMENT_DIR`）。
4. `backup_restore.configured_data_dir()` **必须与 `db.py:data_dir()` 同规则**——建议直接复用，不要各写一份。
5. 备份清单**必须记录实际使用的 data_dir 绝对路径**（总设计 20.4 节要求「恢复目录至少记录……包含范围」），使恢复方可核对是否同一代数。

## 6. 验证方案（read-only 优先）

| 编号 | 检查 | 判据 |
|---|---|---|
| DR-01 | 10 个解析点在**同一组环境变量**下返回值是否全等 | 全等；不等即分裂 |
| DR-02 | 只设 `PAPERGUARD_DB_PATH` 时 `configured_data_dir()` 是否等于 `Path(DB_PATH).parent` | **当前会失败**（本文件的核心断言） |
| DR-03 | 生产 env **键名**（不读值）是否同时设置 `DB_PATH` 与 `DATA_DIR` | 需 T-01.1 只读授权 |
| DR-04 | 现有备份清单记录的根路径是否等于在线 `data_dir()` | 需只读检查，不触发备份 |
| DR-05 | 迁移后新旧根路径 diff | 不得出现孤儿产物 |

> **DR-02 可在本地立即复现，不需要生产访问。** 建议作为 W-01 T-01.2 的一个新增负例。

### 6.1 生产只读核对结果（2026-10-01，已获只读授权）

**核对范围**：只读 SSH，仅打印环境变量**键名**与文件系统元数据；未读任何密钥值、未重启服务、未触发备份或恢复。

| 编号 | 判据 | 实测结果 | 结论 |
|---|---|---|---|
| DR-03 | env 是否同时设置 `DB_PATH` 与 `DATA_DIR` | **两个键都不存在**。`/etc/paperguard/paperguard.env` 与运行时 `/proc/<pid>/environ` 中全部 `PAPERGUARD_*` 键只有 3 个：`PAPERGUARD_RESEND_API_KEY`、`PAPERGUARD_RESEND_FROM`、`PAPERGUARD_SECRET_KEY` | 走**默认推导值**，两函数在该配置下恰好一致 |
| DR-04 | 是否存在可用的应用级备份归档 | 全盘未找到 `backup-*.zip`。**机制澄清**：`POST /api/operations/backup` 产出的是**一次性下载**（临时 zip，`background_tasks` 发送后即删），**设计上就不落盘**。因此「盘上无备份文件」是**预期行为**，不能据此推断「从未跑过」 | 真实缺口是**没有定时归档机制**，而非执行异常 |

**判定：DR-02 在当前生产配置下未实际发生。** 理由是可复算的，不是推测：

- 旧逻辑 `backup_restore._DEFAULT_DATA_DIR = <backend>/data`
- 旧逻辑 `db._DEFAULT_DB_PATH = <backend>/data/paperguard.db`
- 两键都未设时，两者同落在 `<backend>/data` → **旧代码也不分裂**

分裂只在「**只设 `DB_PATH` 而不设 `DATA_DIR`**」时出现。生产没有这个配置，所以**不能把 DR-02 表述为生产已丢数据**（本文件第 8 节约束仍然成立）。

**已本地复刻验证的两组配置**（`local_fixed`）：

| 配置 | 旧逻辑：备份是否覆盖在线库 | 新逻辑（DR-FIX-1 后） |
|---|---|---|
| 两键都不设（= 当前生产） | 是 | 是 |
| 只设 `DB_PATH=/srv/pgdata/custom.db` | **否**（备份根 `<backend>/data`，在线库 `/srv/pgdata/custom.db`） | **是**（两侧同为 `/srv/pgdata`） |

**仍然存在的生产风险（与 DR-02 无关，另记）**：

1. `backend.next-454a771/data/` 与 `backend.next-dd035e7/data/` 两个发布暂存目录各自带 `uploads`，**不在**当前数据根内。发布切换时若暂存目录被清理，暂存期内写入的附件会随之丢失。
2. **无定时备份归档机制** → 当前数据库的最近快照来自发布脚本（`pre-deploy-20260929T022145Z` 等），**周期性与完整性均无保证**。`POST /api/operations/backup` 只提供一次性下载，需人工执行并自行留存。归档建立前不应把「可恢复」写入任何交付承诺。

## 7. 交给工作包的动作

| 编号 | 动作 | 归属 | 阻塞 |
|---|---|---|---|
| DR-FIX-1 | `backup_restore.configured_data_dir()` 改为复用 `db.data_dir()` | W-01 T-01.1 / W-02 T-02.1 | 无 |
| DR-FIX-2 | `EXPORT_DIR`、`_DOCUMENT_DIR` 常量改调用时解析函数 | W-02 T-02.1 | 无 |
| DR-FIX-3 | `ARTIFACT_DIR` / `TEMPLATE_DIR` / `PROCESS_FILE_DIR` 降级为可选覆盖 | W-02 T-02.1 | 无 |
| DR-FIX-4 | `main.py` 上传目录改为复用 `document_upload_dir()`，消除重复实现 | W-02 T-02.1 | 无 |
| DR-FIX-5 | 备份清单记录 data_dir 绝对路径 | W-10 | 无 |
| DR-01～05 | 验证 | W-01 T-01.2 | DR-03/04 需生产只读授权 |

> **W-02 T-02.7（PostgreSQL 隔离演练）不得在这些动作完成前执行**——演练本身要移动数据根，届时分裂会直接污染演练结论。

## 8. 明确不做

- 不修改任何解析函数（本文件只做分析与设计）
- 不触发备份、不执行恢复、不连接生产
- 不把「代码规则不一致」表述为「生产已丢数据」
- 不把上一轮的 `BUG-20261001-02` 修复表述为已解决该缺陷类——**它只是 9 个未对齐点中的 1 个**
