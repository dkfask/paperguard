# Sprint 3 — 多规范包 + Click CLI PLAN

> **状态**：已完成归档（2026-06-09，实施完成；2026-06-11 更新归档状态）
> **前置**：Sprint 2 + Sprint 2.5 已完成；Sprint 2 follow-up B1/T0 已在 Sprint 3 中修复
> **实施结果**：commit `08d9cd3` ~ `33c712e`；核心实现 commit `aa8b180`，归档文档 commit `33c712e`
> **目标**：把"1 个 pack → 多 pack 生态"和"PyQt 实时路径 → CLI 事后批改路径"同时铺开——新增 GB/T 7714 参考文献 pack（10 条）+ 1 所自制学校样式 pack（20+ 条，**SIM-UNIV-generic.yaml**）+ Click 8.4.x CLI 5 子命令 + JSON reporter + Sprint 4.5 fix 骨架；同时修复 Sprint 2 follow-up B1（YAML PARAGRAPH-SPACING fix 不全）和 T0（perf 断言失效）。性能 CLI check < 5s（100 段论文）；`app/core/` 零 PyQt 硬约束保持。

---

## 1. Goal

Sprint 3 在 4 处同时落地：

1. **`app/core/rules/packs/`** — 新增 `GB-T-7714-1-2015.yaml`（10 条参考文献规则）+ `SIM-UNIV-generic.yaml`（20+ 条学校样式规则，**自制模拟零版权**）；B1 修复把现有 `GB-T-7713-1-2006.yaml` 的 `PG-BODY-PARAGRAPH-SPACING` 拆 2 条独立规则
2. **`app/cli/`** — Click 8.4.x CLI 框架，5 子命令（check / fix / extract / init / validate-pack），默认 text + `--format json`；退出码 success 0 / has findings 1 / error 2
3. **`app/core/reporters/`** — `json_reporter.py`（SARIF-like 结构化 JSON；CSV 报告延 Sprint 4）
4. **`app/core/writers/`** — `fix_applier.py` 最小实现（python-docx 回写 IR 属性）+ `docx_writer.py` + `app/cli/_fix_then_check.py`（Sprint 4.5 闭环预铺）

同时修复 Sprint 2 follow-up B1（避免新 pack 触发相同 bug）+ T0（perf 断言失效）。

---

## 0. Completion Summary

Sprint 3 已按计划完成并进入历史归档：

- 新增 `GB-T-7714-1-2015.yaml`（10 条参考文献规则）。
- 新增 `SIM-UNIV-generic.yaml`（20 条自制学校样式规则，零版权风险）。
- 修复 Sprint 2 follow-up B1：正文段前/段后拆成 `PG-BODY-SPACE-BEFORE` / `PG-BODY-SPACE-AFTER`。
- 修复 T0：性能测试断言失效。
- 落地 Click CLI：`check` / `fix` / `extract` / `init` / `validate-pack`。
- 新增 JSON reporter、`fix_applier`、`docx_writer`、`_fix_then_check` 闭环预铺。
- Sprint 3 完成时记录为 **359 tests 全绿**，端到端 smoke 通过。

后续 Sprint 4 已完成，Sprint 5 已开始；本文件仅作为 Sprint 3 计划与决策归档。

---

## 2. Architecture Decision Records

### ADR-1：CLI 框架（Click 8.4.x vs Typer 0.26+ vs argparse stdlib）

- **状态**：**已拍板并实施：A**
- **Context**：design-proposal §5 CLI 5 子命令设计（line 440-475）；当前 `app/core/extract/` 纯函数无 CLI
- **Options**：
  - **A（推荐）**：**Click 8.4.x**（`pip install "click>=8.4"` 锁版）— 标准事实；`@click.group` / `@click.command` / `path_type=Path` 原生 Windows 友好；CliRunner 测试与 pytest 同构（ms 级）
  - **B**：Typer 0.26+（基于 Click 包装）— 类型提示更优雅但 stack trace 多一层
  - **C**：纯 stdlib argparse — 5 子命令代码膨胀 250-400 行；无 `path_type=Path` 零摩擦；测试需 `subprocess.run` 慢且不稳
- **推荐**：**A**，原因：① Context7 调研确认 Click 8.4 已修 Windows + UTF-8 + ANSI 颜色（8.0/8.1 有 bug）；② CliRunner 1ms 内跑完测试，无需 subprocess；③ `path_type=Path` 与 PyQt 端 `pathlib` 风格一致；④ Typer 多一层 wrapper 调试不如 Click 直白

### ADR-2：第一所学校 pack（清华 vs 浙大 vs 自制）

- **状态**：**已拍板并实施：A**
- **Options**：
  - **A（推荐）**：**自制 SIM-UNIV-generic.yaml**（20+ 条通用高校样式，零版权风险）
  - **B**：清华模板（真实学校需 EULA 授权）
  - **C**：浙大模板（同上）
  - **D**：武大模板（同上）
- **推荐**：**A**，理由：Sprint 2.5 ADR-6 已决策自制；自制 pack 可自由 GitHub/PyPI 分发；真实模板如被 PaperGuard 用户传播存在 EULA 风险

### ADR-3：GB/T 7714 规则数（10 核心 / 20 标准 / 30 完整）

- **状态**：**已拍板并实施：A**
- **Context**：design-proposal §7 Sprint 3 写"30 条内置规则"；GB/T 7714-1-2015 参考文献格式核心约 10 条
- **Options**：
  - **A（推荐）**：**10 核心规则**（序号/作者姓前名后/标题引号/来源斜体/年份位置/页码/电子资源URL/期刊全称/并列责任者/文献类型标识符）
  - **B**：20 标准规则（+ 译者/出版地/ND/多层级）
  - **C**：30 完整规则（全量 GB/T 7714-1-2015 条款）
- **推荐**：**A**，10 条覆盖 80% 常见错误；与 §7"30 条"不冲突（Sprint 3 + Sprint 4 合计 30）

### ADR-4：报告格式（仅 JSON / JSON+Text / JSON+HTML）

- **状态**：**已拍板并实施：A**
- **Context**：design-proposal §4.1 reporter 层规划 terminal / JSON / HTML / SARIF；Sprint 4 规划 JSON+HTML+SARIF；当前仅有 terminal
- **Options**：
  - **A（推荐）**：**JSON + Text（默认）**；HTML 延 Sprint 4
  - **B**：仅 JSON（text reporter 废弃）
  - **C**：JSON + HTML（不要 text）
- **推荐**：**A**，text reporter 已有；CI 集成用 `--format json`；HTML Sprint 4 再做

### ADR-5：Sprint 4.5 起步范围（仅 fix_applier 骨架 / 完整 fix→check 闭环 / CLI 集成）

- **状态**：**已拍板并实施：A**
- **Options**：
  - **A（推荐）**：**fix_applier + docx_writer + CLI 集成 _fix_then_check**（完整闭环；`paperguard fix` 跑完输出 `changes.md` + remaining findings）
  - **B**：仅 fix_applier 骨架
  - **C**：完整闭环
- **推荐**：**A**，Sprint 4.5 核心价值就是 fix 后再 audit；MVP 早做闭环省返工

### ADR-6：Sprint 2 follow-up B1+T0 修顺序

- **状态**：**已拍板并实施：A**
- **Context**：Sprint 2 follow-up 有 3 真 bug；B1（YAML fix 不全）在写新 pack 必触发；T0（perf 断言失效）让性能基线无意义
- **Options**：
  - **A（推荐）**：**Sprint 3 起手第一件事修 B1 + T0**（commit #1-2）；B2 延后
  - **B**：Sprint 3 末尾修（B1 + T0 + B2 一起）
  - **C**：不修（Sprint 4 再做）
- **推荐**：**A**，B1 拆 YAML 2 规则 ~20 LOC；T0 1 行修复；B2 暂缓（当前 evaluator `__index__` 字段无暴露，不阻塞）

---

## 3. File-Level Deliverables

### Phase 0：Sprint 2 follow-up 修复（commit #1-2）

#### `app/core/rules/packs/GB-T-7713-1-2006.yaml`（**改**，B1 修复）

- **改动**：把 `PG-BODY-PARAGRAPH-SPACING` 拆为：
  - `PG-BODY-SPACE-BEFORE`（仅 before）
  - `PG-BODY-SPACE-AFTER`（仅 after）
- **Approx LOC delta**：+15 行

#### `tests/unit/test_rules_pack_gb7713.py`（**改**，B1 测试补充）

- 拆 `test_PG_BODY_PARAGRAPH_SPACING_violated_by_6pt_before` → 2 独立测试

#### `tests/integration/test_engine_perf.py`（**改**，T0 修复）

- `assertGreaterEqual(len(findings), 0)` → `assertGreater(len(findings), 0)`

### Phase A：GB/T 7714 参考文献 pack

#### `app/core/rules/packs/GB-T-7714-1-2015.yaml`（**新**）

10 条规则覆盖参考文献格式核心场景。scope 用 `paragraphs[?style_id == 'Bibliography' || ...]` 过滤候选条目。Fix.target 全部 `paragraph.*` 或 `run.font_size_pt`。~130 行。

#### `tests/unit/test_rules_pack_gb7714.py`（**新**）~12 tests

### Phase B：学校 pack（自制）

#### `app/core/rules/packs/SIM-UNIV-generic.yaml`（**新**）

20+ 条规则覆盖封面（4）+ 摘要（4）+ 正文（6，与 GB-T-7713 重复但独立维护）+ 标题（4）+ 图表（2）+ 参考文献（2）。~260 行。

#### `tests/unit/test_packs_sim_univ.py`（**新**）~22 tests

### Phase C：CLI 入口（app/cli/）

#### `requirements.txt`（**改**）

```diff
+ click>=8.4  # Sprint 3 锁 8.4.x+（修复 Windows + UTF-8 + ANSI 颜色）
```

#### `app/cli/main.py`（**新**）~50 LOC

`@click.group()` + `version_option` + `verbose count` + `add_command` 注册 5 子命令。

#### `app/cli/check.py`（**新**）~80 LOC

`@click.command() check(paper, --pack, --format, --output)`：跑 validate_docx + reporter 输出 + 退出码 0/1/2。

#### `app/cli/fix.py`（**新**）~80 LOC

`@click.command() fix(paper, --pack, --output, --no-verify)`：调 fix_applier + 写新 docx（不破坏原文）+ 调 _fix_then_check。

#### `app/cli/extract.py`（**新**）~60 LOC

`@click.command() extract(template, --out, --json-out)`：Sprint 2.5 extract 包入口。

#### `app/cli/init.py`（**新**）~50 LOC

`@click.command() init(--school, --base, --out)`：生成空白 pack 骨架。

#### `app/cli/validate_pack.py`（**新**）~50 LOC

`@click.command() validate_pack(pack_file)`：pydantic schema 校验。

#### `app/cli/_fix_then_check.py`（**新**）~40 LOC

CLI 内部 helper：fix 后自动 check + 输出 `changes.md` + remaining findings。

### Phase D：报告层

#### `app/core/reporters/json_reporter.py`（**新**）~80 LOC

SARIF-like 结构化 JSON 输出（与 Explore 推荐 #3 CSV 兼容：字段命名借鉴 `format_audit.csv` 9 列）。

#### `app/core/reporters/terminal.py`（**改**）~+20 LOC

加 `report_findings(findings) -> str` 方法与 JSONReporter 接口对齐。

### Phase E：Sprint 4.5 起步

#### `app/core/writers/fix_applier.py`（**新**）~80 LOC

`apply_fix(ir, finding) -> Document`（不可变）+ `apply_all_fixes(ir, findings) -> (Document, remaining)`。支持 `set` action（`insert`/`delete`/`wrap` 留 Sprint 5）。

#### `app/core/writers/docx_writer.py`（**新**）~60 LOC

`write_ir_to_docx(ir, output_path)` 用 python-docx 回写。

#### `app/core/writers/__init__.py`（**新**）

### Phase F：测试 + 冒烟

#### `tests/unit/test_json_reporter.py`（**新**）~6 tests

#### `tests/unit/test_fix_applier.py`（**新**）~8 tests

#### `tests/integration/test_cli_*.py`（**新**）

5 个文件 × ~3-5 tests = ~19 tests。**用 Click `CliRunner`**（Context7 推荐，1ms 内跑完）。

#### `scripts/sprint3_smoke.py`（**新**）~80 LOC

端到端：CLI check → fix → check remaining → exit code。

---

## 4. Test Strategy

### 4.1 TDD 顺序

```
Phase 0（修复，commit #1-2）
  1. test_rules_pack_gb7713 拆 2 test → GB-T-7713-1-2006.yaml 拆 2 rule
  2. test_engine_perf T0 修复 → assertGreater

Phase A（JSON reporter，commit #3-4）
  3. test_json_reporter 6 RED → json_reporter.py GREEN

Phase B（fix_applier，commit #5-6）
  4. test_fix_applier 8 RED → fix_applier.py + docx_writer.py GREEN

Phase C（pack 扩展，commit #7-10）
  5. test_rules_pack_gb7714 12 RED → GB-T-7714-1-2015.yaml
  6. test_packs_sim_univ 22 RED → SIM-UNIV-generic.yaml

Phase D（CLI，commit #11-15）
  7. test_cli_check 5 RED → check.py
  8. test_cli_fix 5 RED → fix.py + _fix_then_check
  9. test_cli_extract 3 RED → extract.py
  10. test_cli_init 3 RED → init.py
  11. test_cli_validate_pack 3 RED → validate_pack.py

Phase E（smoke + 回归，commit #16-18）
  12. sprint3_smoke.py 端到端
  13. 全量回归 ~345 tests
  14. HANDOFF + SKILL_USAGE 更新
```

### 4.2 覆盖率目标

| 模块 | 目标 |
|------|------|
| `json_reporter.py` | ≥ 90% |
| `fix_applier.py` / `docx_writer.py` | ≥ 85% |
| CLI 5 子命令 | ≥ 85% |
| 2 个新 pack | ≥ 85% |
| Sprint 3 增量 | ≥ 85% |

### 4.3 总测试数预期

- 现有 ~295 tests + Sprint 3 新增 ~50 = **~345 tests**

---

## 5. Commit Strategy（18 atomic commits）

| # | Commit | 说明 |
|---|--------|------|
| 1 | `fix(sprint-3): B1 — 拆 PG-BODY-PARAGRAPH-SPACING 为 2 条独立规则` | GB-T-7713-1-2006.yaml B1 修复 |
| 2 | `fix(sprint-3): T0 — perf test assertGreaterEqual → assertGreater` | test_engine_perf.py |
| 3 | `test(sprint-3): RED — json_reporter 6 tests` | |
| 4 | `feat(sprint-3): json_reporter.py (SARIF-like)` | |
| 5 | `test(sprint-3): RED — fix_applier 8 tests` | |
| 6 | `feat(sprint-3): fix_applier.py + docx_writer.py` | |
| 7 | `test(sprint-3): RED — GB-T-7714 12 tests` | |
| 8 | `feat(sprint-3): GB-T-7714-1-2015.yaml (10 参考文献规则)` | |
| 9 | `test(sprint-3): RED — 学校 pack 22 tests` | |
| 10 | `feat(sprint-3): SIM-UNIV-generic.yaml (20+ 学校样式规则)` | |
| 11 | `chore(sprint-3): 加 click>=8.4 依赖` | 锁版 8.4.x+ |
| 12 | `feat(sprint-3): CLI main.py + check.py + terminal.report_findings` | Click group + check |
| 13 | `feat(sprint-3): CLI fix.py + _fix_then_check (fix→check 闭环)` | |
| 14 | `feat(sprint-3): CLI extract.py + init.py + validate_pack.py` | |
| 15 | `test(sprint-3): CLI 5 子命令 integration tests (19 tests, CliRunner)` | |
| 16 | `chore(sprint-3): sprint3_smoke.py + grep no PyQt` | DoD 验证 |
| 17 | `docs(sprint-3): HANDOFF.md v1.5 标记 Sprint 3 完成` | |
| 18 | `docs(sprint-3): SKILL_USAGE.md 加 CLI 用法章节` | 人类上手 |

---

## 6. Risks & Mitigations

| # | 风险 | 缓解 |
|---|------|------|
| **B1 复现** | 写 GB-T-7714-1-2015.yaml 时含 paragraph-spacing 触发 B1 | Phase 0 先修 B1；新 pack 写完后 test 验证不混设 |
| **Click Windows 兼容性** | 8.0/8.1 在 Win + UTF-8 有 bug | **锁版 8.4.x+**（已修）+ 用 `click.echo()` 而非 `print()`（自动编码处理） |
| **学校 pack 版权** | 真实模板 EULA | ADR-2 A 自制 SIM-UNIV-generic |
| **CLI check 性能** | 100 段 CLI < 5s | docx_to_ir（python-docx 读）~2-3s + validate_docx ~200ms = 远低于 5s |
| **fix_applier 覆盖** | MVP 只支持 `set` action | insert/delete/wrap Sprint 5；无 fix 的 rule 正确跳过 |
| **YAML pack 继承** | GB-T-7713 与 SIM-UNIV-generic 各有 6 条重复正文规则 | ADR-8 Sprint 2 决定 MVP 不实现 extends；V1+ 再做 |
| **B2 暂缓** | evaluator `__index__` 字段泄漏 | 已在 follow-up 记录；当前无规则触发 |

---

## 7. Definition of Done

- [x] `python -m unittest tests.test_*.py tests/unit/test_*.py tests/integration/test_*.py` 全绿（完成时记录 359 tests）
- [x] `python scripts/sprint3_smoke.py` 端到端通过（CLI check → fix → check → exit code）
- [x] `grep -r "PyQt" app/core/` 仅匹配 docstring 注释
- [x] **B1 + T0 已修复**：GB-T-7713-1-2006.yaml 2 条独立规则 + test_engine_perf assertGreater
- [x] **35+ 新规则**：GB-T-7714-1-2015（10）+ SIM-UNIV-generic（20）+ B1 修复
- [x] **5 CLI 子命令全部可用**
- [x] **退出码正确**：0 / 1 / 2
- [x] **JSON reporter**：通过 `json.loads()` 解析
- [x] **fix_applier 不可变**：原 IR 不变
- [x] **性能**：CLI check < 5s（100 段）
- [x] **ADR 6 个全部用户拍板**
- [x] HANDOFF.md v1.5 + SKILL_USAGE.md CLI 章节
- [ ] Push 到 GitHub main（本地归档已完成；是否推送由用户决定）

---

## 8. Open Questions for User（**已拍板并实施**）

| # | 问题 | **推荐** | 备选 |
|---|------|----------|------|
| **1** | CLI 框架（ADR-1） | **A：Click 8.4.x 锁版** | B：Typer / C：argparse |
| **2** | 第一所学校 pack（ADR-2） | **A：自制 SIM-UNIV-generic** | B：清华 / C：浙大 / D：武大 |
| **3** | GB/T 7714 规则数（ADR-3） | **A：10 核心** | B：20 标准 / C：30 完整 |
| **4** | 报告格式（ADR-4） | **A：JSON + Text 默认** | B：仅 JSON / C：JSON + HTML |
| **5** | Sprint 4.5 起步范围（ADR-5） | **A：fix_applier + CLI 集成完整闭环** | B：仅骨架 / C：完整闭环 |
| **6** | Sprint 2 follow-up 修顺序（ADR-6） | **A：Sprint 3 起手修 B1 + T0** | B：末尾 / C：不修 |

---

## 9. 附件：关键引用映射

| 引用 | 出处 |
|------|------|
| Sprint 3/4/4.5 计划 | `docs/design-proposal.md` §7 line 683-715 |
| CLI 5 子命令设计 | `docs/design-proposal.md` §5 line 440-475 |
| 学校 pack 扩展 | `docs/design-proposal.md` §13.3 line 858-865 |
| Sprint 2 follow-up B1 bug | `docs/sprint-2-followup.md` §🔴 B1 line 12-24 |
| Sprint 2 follow-up T0 bug | `docs/sprint-2-followup.md` §🔴 T0 line 44-58 |
| Sprint 2.5 extract 包 | `app/core/extract/__init__.py` |
| Sprint 2 engine validate_docx | `app/core/engine/__init__.py:22-43` |
| Sprint 2 loader RulePack | `app/core/engine/loader.py:77-94` |
| Sprint 2 YAML pack 模板 | `app/core/rules/packs/GB-T-7713-1-2006.yaml` |
| Sprint 2.5 requirements_to_pack | `app/core/extract/to_pack.py` |
| **Click 8.4.x 文档** | Context7 — click.palletsprojects.com |
| **Click CliRunner 测试** | Context7 — click docs/testing.md |
| **Typer vs Click 对比** | Context7 — typer.tiangolo.com |
| **CSV 报告模式借鉴** | Explore — `references/.../format_audit.csv` 9 列 |
| **apply_format mutator 借鉴** | Explore — `references/.../apply_format.py:40-67` |
| Sprint 2 plan 模板 | `docs/sprints/sprint-2-plan.md` |
| Sprint 2.5 plan 模板 | `docs/sprints/sprint-2.5-plan.md` |

---

**版本**：v1.1（已完成归档）
**作者**：多代理 workflow（planner + Explore + Context7 docs-lookup）
**日期**：2026-06-09
**归档更新**：2026-06-11
**后续**：Sprint 4 已完成，Sprint 5 mutator 成熟已开始
