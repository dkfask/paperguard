# Sprint 4 — HTML/SARIF Reporter + 3 CLI 辅助 + Sprint 4.5 闭环 + pyproject PLAN

> **状态**：已完成归档（2026-06-11，commit `5f36692`）
> **前置**：Sprint 3 完成（commit `33c712e`，GB-T-7714 + SIM-UNIV-generic + Click CLI 5 子命令 + JSON reporter + fix 骨架）
> **后续**：Sprint 5 mutator 成熟已开始，首个 commit `e60a05a`
> **目标**：补齐 reporter 层（HTML + SARIF）让 CI/CD 可消费 + 3 个 CLI 辅助命令 + Sprint 4.5 完整 fix→check 闭环 + pyproject.toml 装包入口。性能：HTML/SARIF 渲染 < 100ms（100 findings）；smoke < 10s；`app/core/` 零 PyQt 硬约束保持。

---

## 0. Completion Summary

Sprint 4 已按推荐 ADR 全量实施并提交：

- commit：`5f36692 feat(sprint-4): add reporters cli helpers and fix verification`
- 新增 `HTMLReporter`（Jinja2 + autoescape）和 `SARIFReporter`（SARIF 2.1.0 子集）。
- `paperguard check --format` 支持 `text/json/html/sarif`。
- 新增 `list-packs`、`doctor`、`version` 三个 CLI 辅助命令。
- `fix` 默认二次 check 并写完整 `changes.md`；新增 `--skip-verify`，保留隐藏兼容 `--no-verify`。
- 新增 `pyproject.toml` + `paperguard = "app.cli.main:cli"` console script。
- 新增 `scripts/sprint4_smoke.py`。
- 完成时验证：`python -m pytest -q` -> `381 passed, 1 skipped`；`python scripts/sprint4_smoke.py` 通过；`pip install -e . --no-deps` 通过；`paperguard --help` 可用。

---

## 1. Goal

Sprint 4 在 4 处落地：

1. **`app/core/reporters/`** — `html_reporter.py`（~120 LOC Jinja2 嵌入式模板 + 渲染器）+ `sarif_reporter.py`（~80 LOC SARIF 2.1.0 子集）；`paperguard check --format html|sarif` 全通；SARIF 让 GitHub Code Scanning / VS Code Problems 面板直接消费
2. **`app/cli/`** — 3 个辅助命令：`--version` 增强（本地默认 + 可选 auto-update）+ `list-packs`（text/JSON 双格式）+ `doctor`（5 项检查：Python/依赖/packs/PyQt/可选网络）
3. **`app/cli/_fix_then_check.py`** — Sprint 4.5 完整闭环：`changes.md` 完整 schema（含 summary + Auto-fixed + Remaining 列表 + skipped/pass 计数）；`paperguard fix` 默认做二次 check（加 `--skip-verify` opt-out）
4. **`pyproject.toml`** — PEP 621 entry_points + `pip install -e .` 让 `paperguard` 全局可用

---

## 2. Architecture Decision Records

### ADR-1：HTML 模板引擎（Jinja2 第三方 vs stdlib vs 内联字符串）

- **状态**：**已拍板并实施：A**
- **Context**：Sprint 4 需要 HTML 报告；设计文档 §4.1 line 341 规划
- **Options**：
  - **A（推荐）**：**Jinja2 第三方库**（`pip install jinja2>=3.1,<4`）— 嵌入式模板字符串（不读外部 .html 文件）；`Environment(autoescape=True)` 强制 HTML 转义防 XSS
  - **B**：stdlib `string.Template` — 无依赖但循环/条件难维护
  - **C**：内联字符串拼接 — 120+ LOC 难维护
- **推荐**：**A**，理由：① Jinja2 是 Python 生态最成熟模板引擎；② `{% for f in findings %}` / `{% if f.fix %}` 语法清晰；③ 嵌入式（不读文件）满足分发约束；④ **autoescape=True 强制 HTML 转义**（Context7 调研关键安全考量，XSS 防护）
- **风险**：requirements.txt 加 `jinja2>=3.1,<4` 一行

### ADR-2：SARIF 输出范围（完整 spec vs 子集）

- **状态**：**已拍板并实施：A**
- **Context**：SARIF 2.1.0 规范 ~200 页；GitHub Code Scanning 只要求 ruleId + message + location
- **Options**：
  - **A（推荐）**：**SARIF 2.1.0 子集 11 字段**（Context7 调研定义）— `version` / `$schema` / `runs[].tool.driver.{name,version,informationUri,rules[]}` / `runs[].results[].{ruleId,level,message.text,locations[],fixes[]}`
  - **B**：完整 2.1.0（200+ 页规范全实现）— 过度工程
- **推荐**：**A**，理由：① GitHub Code Scanning 只要求 ruleId+message+location；② 子集 ~80 LOC 可测可维护；③ 字段映射清晰（PaperGuard `severity` → SARIF `level`，severity.info → SARIF `note`）

**字段映射**：

| PaperGuard | SARIF 2.1.0 |
|---|---|
| `rule_id` | `results[].ruleId` |
| `severity` | `results[].level`（error→error / warning→warning / info→**note**）|
| `message` | `results[].message.text` |
| `location.paragraph_index` | `results[].locations[].region.startLine` |
| `fix` | `results[].fixes[].changes[]` |
| `_pack.meta` | `runs[].tool.driver.rules[].id`（去重 + 帮助文本） |

### ADR-3：list-packs 输出格式（text / JSON）

- **状态**：**已拍板并实施：A**
- **Options**：
  - **A（推荐）**：**双格式 `--format text|json`（默认 text）** — CI 用 JSON；终端默认 text 表格
  - **B**：仅 JSON
  - **C**：仅 text
- **推荐**：**A**

### ADR-4：doctor 检查范围（Python/依赖/packs/PyQt/网络）

- **状态**：**已拍板并实施：A**
- **Options**：
  - **A（推荐）**：**5 项检查**（Python >= 3.10 / 核心依赖可 import / packs 目录可读 / PyQt5 可用 / 网络默认关闭 opt-in）— 覆盖 80% 安装问题
  - **B**：3 项（Python/依赖/packs）
  - **C**：7+ 项
- **推荐**：**A**

### ADR-5：--version 增强（auto-update 网络请求 vs 仅本地）

- **状态**：**已拍板并实施：A**
- **Options**：
  - **A（推荐）**：**本地版本默认 + 可选 `--check-update`（网络请求）** — `urllib.request` stdlib + 3s 超时
  - **B**：联网默认开启 — 性能/隐私问题
  - **C**：不实现 auto-update
- **推荐**：**A**

### ADR-6：Sprint 4.5 闭环范围（完整 vs 简化 vs 恢复 --no-verify）

- **状态**：**已拍板并实施：A**
- **Options**：
  - **A（推荐）**：**完整闭环**（fix 后跑二次 check + `changes.md` 完整 schema：summary + Auto-fixed 列表 + Remaining 列表 + skipped/pass 计数）
  - **B**：简化（仅 check，不写 changes.md）
  - **C**：恢复 `--no-verify`
- **推荐**：**A**，设计文档 §7 line 697 明确要求 "fix 后剩余 N 个问题清单 + 前后对比指标"

`changes.md` schema 示例：
```markdown
# PaperGuard Fix Report

- Source: `paper.docx`
- Pack: `GB-T-7713-1-2006`
- Pre-fix issues: 12
- Auto-fixed: 8
- Remaining: 4
- Skipped: 0

## Summary
| Metric | Count |
|--------|-------|
| Pre-fix | 12 |
| Auto-fixed | 8 |
| Remaining | 4 |
| Skipped | 0 |

## Auto-fixed (8)
- [PG-BODY-LINE-SPACING] 段落 3: 行距 1.0 → 1.5 ✓
- ...

## Remaining (4)
- [PG-BODY-FONT-SIZE] 段落 7: ...
```

### ADR-7：pyproject.toml 时机（Sprint 4 引入 vs 延后 vs 不引入）

- **状态**：**已拍板并实施：A**
- **Options**：
  - **A（推荐）**：**Sprint 4 引入**（PEP 621 + `[project.scripts]` `paperguard = "app.cli.main:cli"` + `pip install -e .` 让 CLI 全局可用）
  - **B**：延后
  - **C**：不引入
- **推荐**：**A**，设计文档 §4 line 305 明确规划；requirements.txt 保留兼容

---

## 3. File-Level Deliverables

### Phase 0：Reporter TDD（commit #1-4）

#### `app/core/reporters/html_reporter.py`（**新**）~120 LOC

- 嵌入式 Jinja2 模板（不读外部 .html 文件）
- `Environment(autoescape=True)` 强制 HTML 转义
- 输出 `<!DOCTYPE html>` / `<head>` / `<body>` 完整文档
- 渲染 < 100ms（100 findings）

#### `app/core/reporters/sarif_reporter.py`（**新**）~80 LOC

- SARIF 2.1.0 子集 11 字段
- `$schema` URI 引用
- 输出 valid JSON（可被 `json.loads()` 解析）

### Phase A：CLI 辅助（commit #5-9）

- `app/cli/version.py`（~40 LOC）— `--version` 本地 + `--check-update` opt-in
- `app/cli/list_packs.py`（~60 LOC）— 双格式
- `app/cli/doctor.py`（~80 LOC）— 5 项检查
- `app/cli/main.py`（**改**）— 注册 3 个新子命令

### Phase B：Sprint 4.5 闭环（commit #10-11）

- `app/cli/_fix_then_check.py`（**改**）— 完整 changes.md schema
- `app/cli/fix.py`（**改**）— 默认做二次 check + `--skip-verify` opt-out
- `app/cli/check.py`（**改**）— 加 `--format html|sarif` 选项

### Phase C：pyproject.toml（commit #12-13）

- `pyproject.toml`（**新**）— PEP 621 metadata + `setuptools>=68` + entry_points
- `requirements.txt`（**改**）— 加 `jinja2>=3.1,<4`
- `app/__init__.py`（**新**）— `__version__ = "0.1.0"`

### Phase D：测试 + 冒烟（commit #14-17）

- `tests/unit/test_html_reporter.py`（~5 tests）
- `tests/unit/test_sarif_reporter.py`（~6 tests）
- `tests/integration/test_cli_version.py`（~3 tests）
- `tests/integration/test_cli_list_packs.py`（~3 tests）
- `tests/integration/test_cli_doctor.py`（~3 tests）
- `tests/integration/test_fix_loop.py`（~5 tests Sprint 4.5 闭环）
- `tests/integration/test_cli_check_html_sarif.py`（~5 tests）
- `scripts/sprint4_smoke.py`（~100 LOC 端到端）

### Phase E：docs（commit #18）

- `SKILL_USAGE.md`（**改**）— 加 HTML/SARIF/CLI 辅助章节
- `HANDOFF.md`（**改**）— v1.6 标记 Sprint 4 完成

---

## 4. Test Strategy

### 4.1 TDD 顺序

```
Phase 0（reporter TDD，commit #1-4）
  1. test_html_reporter 5 RED → html_reporter.py GREEN
  2. test_sarif_reporter 6 RED → sarif_reporter.py GREEN

Phase A（CLI 辅助，commit #5-9）
  3. test_cli_version 3 RED → version.py GREEN
  4. test_cli_list_packs 3 RED → list_packs.py GREEN
  5. test_cli_doctor 3 RED → doctor.py GREEN

Phase B（Sprint 4.5 闭环，commit #10-11）
  6. test_fix_loop 5 RED → _fix_then_check.py + fix.py 增强

Phase C（pyproject，commit #12-13）
  7. pip install -e . 验证

Phase D（smoke + 回归，commit #14-17）
  8. test_cli_check_html_sarif 5 RED → check.py 增强
  9. sprint4_smoke.py 端到端
  10. 全量回归 ~359 + 54 = ~413 tests
```

### 4.2 覆盖率目标

| 模块 | 目标 |
|------|------|
| `html_reporter.py` | ≥ 90% |
| `sarif_reporter.py` | ≥ 90% |
| `version.py` / `list_packs.py` / `doctor.py` | ≥ 85% |
| `_fix_then_check.py` | ≥ 85% |
| Sprint 4 增量整体 | ≥ 85% |

### 4.3 性能基线

| 操作 | 基线 |
|------|------|
| HTML 渲染 100 findings | < 100ms |
| SARIF 渲染 100 findings | < 100ms |
| `--version` | < 50ms |
| `list-packs` | < 200ms |
| `doctor` | < 500ms |
| smoke 端到端 | < 10s |

---

## 5. Commit Strategy（18 atomic commits）

| # | Commit | Scope |
|---|--------|-------|
| 1 | `test(sprint-4): RED — html_reporter 5 tests` | test |
| 2 | `feat(sprint-4): html_reporter.py (Jinja2 embedded, autoescape=True)` | feat |
| 3 | `test(sprint-4): RED — sarif_reporter 6 tests` | test |
| 4 | `feat(sprint-4): sarif_reporter.py (SARIF 2.1.0 subset 11 fields)` | feat |
| 5 | `chore(sprint-4): 加 jinja2>=3.1 依赖` | chore |
| 6 | `test(sprint-4): RED — CLI version 3 tests` | test |
| 7 | `feat(sprint-4): CLI version.py (本地默认 + --check-update opt-in)` | feat |
| 8 | `test(sprint-4): RED — CLI list-packs 3 tests` | test |
| 9 | `feat(sprint-4): CLI list_packs.py (text/JSON 双格式)` | feat |
| 10 | `test(sprint-4): RED — CLI doctor 3 tests` | test |
| 11 | `feat(sprint-4): CLI doctor.py (5 项检查)` | feat |
| 12 | `feat(sprint-4): main.py 注册 3 新子命令 + check.py 加 --format html/sarif` | feat |
| 13 | `test(sprint-4): RED — fix_loop 5 tests (Sprint 4.5 闭环)` | test |
| 14 | `feat(sprint-4): _fix_then_check.py 增强 (完整 changes.md schema) + fix.py --skip-verify` | feat |
| 15 | `feat(sprint-4): pyproject.toml (PEP 621 + entry_points paperguard CLI)` | feat |
| 16 | `test(sprint-4): CLI check html/sarif 5 tests + smoke` | test |
| 17 | `chore(sprint-4): sprint4_smoke.py + grep no PyQt` | chore |
| 18 | `docs(sprint-4): SKILL_USAGE.md + HANDOFF.md v1.6` | docs |

---

## 6. Risks & Mitigations

| # | 风险 | 缓解 |
|---|------|------|
| **R1** | Jinja2 XSS（用户输入含 `<script>`） | `Environment(autoescape=True)` 强制转义 |
| **R2** | SARIF 2.1 spec 复杂度 | 子集 11 字段够用 |
| **R3** | auto-update 网络超时 | 3s 超时 + opt-in |
| **R4** | Sprint 4.5 闭环二次 check 性能 | < 5s 仍满足（实测 100 段 ~3s） |
| **R5** | pyproject.toml 破坏 requirements.txt 用户 | requirements.txt 保留 |
| **R6** | `changes.md` 写入权限 | try/except IOError |
| **R7** | `__version__` 与 pyproject.toml version 不一致 | 用 importlib.metadata 动态读 |

---

## 7. Definition of Done

- [x] `pytest tests/` 全绿（完成时 `381 passed, 1 skipped`）
- [x] `python scripts/sprint4_smoke.py` 端到端通过（8 步）
- [x] `grep -r "PyQt" app/core/` 仅 docstring 注释
- [x] HTML reporter：`<!DOCTYPE html>` 有效文档
- [x] SARIF reporter：`"version": "2.1.0"`，可被 `json.loads()` 解析
- [x] `check --format html|sarif` 双格式全通
- [x] `list-packs --format json` 输出 `[{id, path}]` 数组
- [x] `doctor` 5 项独立 pass/fail
- [x] `version --check-update` 为 opt-in，默认无网络调用
- [x] Sprint 4.5 闭环：fix 默认二次 check + `changes.md` 含 summary 表格
- [x] `--skip-verify` 跳过二次 check
- [x] `pip install -e . --no-deps`：`paperguard --help` 全局可用
- [x] ADR 7 个全部用户拍板
- [x] SKILL_USAGE.md reporter + CLI 章节
- [ ] Push 到 GitHub main（本地 commit 已完成；是否推送由用户决定）

---

## 8. Open Questions for User（**已拍板并实施**）

| # | 问题 | **推荐** | 备选 |
|---|------|----------|------|
| **1** | HTML 模板引擎（ADR-1） | **A：Jinja2 第三方（autoescape=True XSS 防护）** | B：string.Template / C：内联字符串 |
| **2** | SARIF 输出范围（ADR-2） | **A：SARIF 2.1.0 子集 11 字段** | B：完整 2.1.0 |
| **3** | list-packs 输出格式（ADR-3） | **A：text+JSON 双格式** | B：仅 JSON / C：仅 text |
| **4** | doctor 检查范围（ADR-4） | **A：5 项（Python/依赖/packs/PyQt/网络）** | B：3 项 / C：7+ 项 |
| **5** | --version 增强（ADR-5） | **A：本地默认 + --check-update opt-in** | B：联网默认 / C：不实现 |
| **6** | Sprint 4.5 闭环范围（ADR-6） | **A：完整闭环 + changes.md schema** | B：简化 / C：恢复 --no-verify |
| **7** | pyproject.toml 时机（ADR-7） | **A：Sprint 4 引入 PEP 621 entry_points** | B：延后 / C：不引入 |

---

## 9. 附件：关键引用映射

| 引用 | 出处 |
|------|------|
| Sprint 4 / 4.5 计划 | `docs/design-proposal.md` §7 line 689-715 |
| reporter 层规划 | `docs/design-proposal.md` §4.1 line 341-348 |
| CLI 5 子命令设计 | `docs/design-proposal.md` §5 line 440-475 |
| pyproject.toml 规划 | `docs/design-proposal.md` §4 line 305 |
| Sprint 3 plan 模板 | `docs/sprints/sprint-3-plan.md` |
| Sprint 2.5 plan 模板 | `docs/sprints/sprint-2.5-plan.md` |
| Sprint 2 plan 模板 | `docs/sprints/sprint-2-plan.md` |
| TerminalReporter.report_findings | `app/core/reporters/terminal.py:112-167` |
| JSONReporter.report | `app/core/reporters/json_reporter.py:24-26` |
| Sprint 3 CLI main.py | `app/cli/main.py:1-41` |
| Sprint 3 _fix_then_check.py | `app/cli/_fix_then_check.py:1-50` |
| **Jinja2 autoescape=True（XSS 防护）** | Context7 — jinja.palletsprojects.com |
| **SARIF 2.1.0 subset 11 fields** | Context7 — docs.oasis-open.org/sarif |
| **PEP 621 entry_points 用法** | Context7 — packaging.python.org |
| **Windows console_scripts 行为** | Context7 — setuptools docs |

---

**版本**：v1.1（已完成归档）
**作者**：多代理 workflow（planner + Explore + Context7 docs-lookup）
**日期**：2026-06-09
**归档更新**：2026-06-11
**后续**：Sprint 5 mutator 成熟已开始
