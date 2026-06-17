# AGENTS.md

> 给未来 Codex 看的项目指南。人类可读版见 [`HANDOFF.md`](./HANDOFF.md)。

## 这是什么项目

**PaperGuard**（暂定代号）——**PyQt 实时约束 + CLI 事后批改**双模式论文规范软件。

- **PyQt 实时约束（主路径）**：学生边写边查，侧边栏提示 + 高亮 + 一键修复。
- **CLI 事后批改（辅路径）**：CI 集成、批量审稿、AI Agent 入口。

**当前阶段**：Sprint 5 mutator 成熟和真实样例回归第一版已完成；PyQt DOCX 批改面板已支持问题列表和逐条修复；下一阶段继续打磨定位反馈、异常提示和发布工程化。

## 当前提交与分支

- 当前分支：`codex/sprint-5-mutator`
- Sprint 4 commit：`5f36692 feat(sprint-4): add reporters cli helpers and fix verification`
- Sprint 5 首个 commit：`e60a05a feat(sprint-5): write expanded docx formatting`
- Sprint 5 收口 commit：`89c6b98 test(sprint-5): add mutator smoke`
- 真实样例回归 commit：`8793168 feat(regression): add synthetic sample matrix`
- PyQt docx 会话服务 commit：`5df9894 feat(pyqt): add docx review session`
- PyQt DOCX 批改 UI commit：`8ceb945 feat(pyqt): wire docx review ui`
- PyQt DOCX 问题列表/逐条修复 commit：`04bddbc feat(pyqt): add docx issue cards and single fixes`
- 最近验证：`python -m pytest -p no:cacheprovider -q` -> `502 passed, 1 skipped`
- 最近 smoke：`python scripts/pyqt_docx_session_smoke.py` -> `OK PyQt DOCX review session smoke passed`

## 关键文件清单

| 路径 | 用途 | 何时读 |
|------|------|--------|
| `HANDOFF.md` | 人类可读恢复指南，当前状态优先看这里 | 用户问“项目到哪了” |
| `docs/progress.md` | 持续进度清单：已实现、未实现、下一步、更新时间线 | 每次完成设计/实现节点后更新 |
| `docs/design-proposal.md` | 架构圣经：4 层架构、IR、YAML 规则 DSL、路线 | 实现细节不确定时 |
| `docs/sprints/sprint-3-plan.md` | Sprint 3 历史计划归档：多 pack + Click CLI | 追溯 Sprint 3 决策 |
| `docs/sprints/sprint-4-plan.md` | Sprint 4 历史计划归档：HTML/SARIF + CLI 辅助 + pyproject | 追溯 Sprint 4 决策 |
| `docs/sprints/sprint-5-plan.md` | Sprint 5 历史计划归档：mutator 成熟 + docx 闭环 | 追溯 Sprint 5 决策 |
| `SKILL.md` | AI agent 入口 | 用户问“AI 怎么调用” |
| `SKILL_USAGE.md` | 人类快速上手 | 用户问“我怎么用” |
| `docs/research-report.md` | 5 份 GitHub 调研综合 | 问“为什么这么设计” |
| `docs/format-thesis-docx-review.md` | 参考项目 945 行精读 | 想参考现成实现 |
| `tests/synthetic_regression_samples.py` | 合成真实场景样例矩阵生成器 | 扩充样例回归 |
| `tests/integration/test_synthetic_regression_samples.py` | 样例回归集成测试 | 改 docx loader/writer/fix 时 |
| `scripts/regression_samples_smoke.py` | 样例回归 smoke | 提交前或 CI 接入 |
| `app/services/docx_review_session.py` | PyQt-facing docx 导入/检查/修复/导出会话服务，含逐条修复 | 接 PyQt 正常使用链路 |
| `app/views/review_export_view.py` | 检查与导出工作区，含 DOCX 批改面板 | 打磨 PyQt 批改入口 |
| `tests/integration/test_pyqt_docx_review_ui.py` | PyQt DOCX 批改 UI 集成测试 | 改批改 UI/接线时 |
| `scripts/pyqt_docx_session_smoke.py` | docx 会话服务 smoke | 提交前或 CI 接入 |
| `references/format-thesis-docx/` | 参考项目源码 submodule | 不要直接改 |

## 命名约定

- 项目代号：`PaperGuard`
- CLI 命令：`paperguard <subcommand>`，全小写。
- 规则包 ID：`GB-T-7713-1-2006`，全大写、连字符。
- IR / fix target：代码内字段用 snake_case，如 `paragraph.line_spacing`、`run.font_eastasia`。
- 子命令：`check` / `fix` / `extract` / `init` / `validate-pack` / `list-packs` / `doctor` / `version`。

## 当前状态

| 维度 | 状态 |
|------|------|
| PyQt 桌面应用 | 已有骨架并接入 Live Checker |
| Core IR | 已有不可变 dataclass：Document / Section / Paragraph / Run / Finding |
| Rule engine | 已有 YAML loader + evaluator + visitor |
| 规则包 | 已有 `GB-T-7713-1-2006`、`GB-T-7714-1-2015`、`SIM-UNIV-generic` |
| Extract | 已有 FORMAT_WORDS 启发式与模板抽取链路 |
| Reporters | 已有 terminal / JSON / HTML / SARIF / PyQt reporter |
| CLI | 已有 8 个命令：check / fix / extract / init / validate-pack / list-packs / doctor / version |
| Fix / writer | `fix_applier` 已支持 set / insert / delete / runs.*；`docx_writer` 已扩展写回段落、run、section、页眉页脚、表格单元格段落、列表段落 |
| 样例回归 | 已有 3 pack x 合规/违规/修复后合成 `.docx` 样例矩阵 |
| PyQt docx session | 已有 `DocxReviewSession` 服务封装 `.docx` 导入、检查、逐条修复、批量修复、导出；`ReviewExportView` 已接入问题列表和修复入口 |
| Packaging | 已有 `pyproject.toml` 和 `paperguard` console script |
| 测试 | 当前待跑全量验证 |
| CI | 仍未配置 |

## 架构现状

- `app/core/` 是纯核心层，必须保持无 PyQt import。
- `app/cli/` 是 CLI 接入层，调用 `app/core`。
- `app/services/` 是 PyQt 服务层，逐步变成 core 的 thin wrapper。
- `app/views/` / `app/controllers/` 是 PyQt UI 层。

## 常见任务速查

### 用户问“项目到哪了”

读 `HANDOFF.md` 顶部“一句话状态”和“下次回来”小节。

### 用户问“为什么这么设计”

读 `docs/research-report.md` + `docs/design-proposal.md`。

### 用户问“AI 怎么调用”

读 `SKILL.md`。

### 用户说“继续项目”或“做下一阶段”

优先做 Sprint 6 方向：

- 打磨 DOCX 批改面板的定位反馈、异常提示和导出反馈。
- 将 DOCX 批改结果和现有侧边栏/高亮定位进一步融合。
- 配置 CI，并让 `python -m pytest -p no:cacheprovider -q`、`python scripts/sprint5_smoke.py`、`python scripts/regression_samples_smoke.py` 和 `python scripts/pyqt_docx_session_smoke.py` 成为基础检查。
- 继续扩充复杂长文档、真实学校模板匿名化样例、目录、题注、交叉引用和多级列表样例。

### 用户说“加新规则”

读 `docs/design-proposal.md` §3.3 YAML 规则包格式；新增规则优先放到 YAML pack，fix target 必须对应 IR 字段和 writer 支持能力。

### 用户说“批改论文”

当前 CLI 语法：

```bash
paperguard check paper.docx --pack GB-T-7713-1-2006 --format html --output report.html
paperguard fix paper.docx --pack GB-T-7713-1-2006 --output paper.fixed.docx
```

`fix` 默认二次 check 并写 `changes.md`；跳过二次 check 用 `--skip-verify`。

## 关键决策历史

1. YAML 规则包，不用 JSON / Rego。
2. `python-docx + lxml`，不用商业 docx 库。
3. 四层架构：数据 / 引擎 / 报告 / 接入。
4. `fix` 永远输出新文件，不破坏原文。
5. 修复后再 audit，输出 `changes.md`。
6. FORMAT_WORDS 启发式用于模板抽取。
7. PyQt 实时约束是主路径，CLI 是事后批改辅路。
8. HTML reporter 用 Jinja2 + autoescape。
9. SARIF 用 2.1.0 实用子集。
10. 网络检查默认关闭，仅 `version --check-update` / `doctor --check-network` 触发。
11. Sprint 5 mutator 成熟范围是安全可验证的 docx 修复闭环，不包含完整目录、交叉引用、复杂 numbering.xml 续接。
12. 样例回归优先用代码生成合成 `.docx`，不要把真实论文或学生作业 commit 进仓库。

## 不应该做的事

- 不要改项目代号，除非用户明确要求。
- 不要引入 Spire/Aspose 等商业 docx 库。
- 不要做 docx 在线编辑器。
- 不要让 `fix` 默认覆盖原文件。
- 不要把真论文 / 学生作业 commit 进来。
- 不要在 `app/core/` import PyQt。
- 不要直接改 `references/format-thesis-docx/` submodule。

## 工作流提示

- TDD 强制：新增功能先写测试。
- 小文件原则：单文件 < 800 行，单函数 < 50 行。
- 不可变 IR：`dataclass(frozen=True)`。
- 显式错误处理：不静默吞错。
- 每个 Sprint 节点 commit，使用 conventional commits。
- 提交前至少跑相关测试；完成节点跑 `python -m pytest -q`。

---

**版本**：v2.0（PyQt DOCX 问题列表与逐条修复）
**最后更新**：2026-06-17
