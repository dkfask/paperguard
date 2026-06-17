# HANDOFF.md — 论文规范软件项目 · 阶段归档

> **这是给你（人类）看的**——下次打开项目时，30 秒读这一份就够。
> 给 AI 看的版本见 [`CLAUDE.md`](./CLAUDE.md)。
> 完整 AI 入口见 [`SKILL.md`](./SKILL.md)。
> 持续进度清单见 [`docs/progress.md`](./docs/progress.md)。

---

## 一句话状态

> 🚧 **Word 模板导入已支持章节结构，PyQt DOCX 批改链路继续打磨**（2026-06-17）：Word `.docx/.dotx` 导入现在会从 `标题 1/2/3` 生成模板章节树；PyQt DOCX 批改面板已有问题列表、定位反馈、逐条修复和导出摘要。下一步：**打磨异常提示 + CI/发布工程化**。

---

## 项目代号

**PaperGuard**（论文守门员）——除非你想换名字。

---

## Sprint 1 交付清单（2026-06-05，commit `6097817`）

| 模块 | 文件 | 行数（约） | 说明 |
|------|------|-----------|------|
| IR 数据类 | `app/core/ir/{run,section,paragraph,doc,finding}.py` | ~150 | 5 个不可变 dataclass（`@dataclass(frozen=True)`） |
| Docx 加载 | `app/core/loaders/docx_loader.py` | ~120 | `load_docx(path) → Document` IR |
| 终端报告 | `app/core/reporters/terminal.py` | ~80 | `TerminalReporter().report(doc) → str` |
| 单元测试 | `tests/unit/{test_ir,test_docx_loader,test_terminal_reporter}.py` | ~360 | 36 个新测试（IR 18 + loader 10 + reporter 8） |
| 冒烟脚本 | `scripts/sprint1_smoke.py` | ~50 | 端到端验证 |

**关键约束达成**：
- ✅ `app/core/` 零 PyQt 依赖（grep 验证：仅 docstring 注释提及，无 import）
- ✅ PyQt 现有代码未动（`git diff --stat` 仅 `requirements.txt` +2 行）
- ✅ 不可变 IR（全部 `@dataclass(frozen=True)`）
- ✅ TDD 严格（每个 .py 都有对应 unittest，先 RED 再 GREEN）
- ✅ 56 tests 全绿（17 workflow + 36 IR/loader/reporter + 3 烟测）

---

## 两台电脑同步（2026-06-05 起）

项目已初始化为 git 仓库并推送到 GitHub 私有仓库。

| 项 | 值 |
|----|-----|
| 仓库 | https://github.com/dkfask/paperguard（**private**） |
| 默认分支 | `main` |
| Submodule | `references/format-thesis-docx`（指向 wksudud/format-thesis-docx） |
| 用户 | dkfask（gh auth 已认证） |

### 第一台电脑（本机，已经做完）

```bash
# 已经完成的事：
# 1. git init -b main
# 2. .gitignore 写好
# 3. git submodule add https://github.com/wksudud/format-thesis-docx.git references/format-thesis-docx
# 4. git add . && git commit -m "..."
# 5. git remote add origin https://github.com/dkfask/paperguard.git
# 6. git push -u origin main
```

### 第二台电脑（首次同步）

```bash
# 1. 装 gh CLI（如未装）
winget install --id GitHub.cli -e --source winget

# 2. 认证 GitHub
gh auth login --web
# 浏览器开 https://github.com/login/device，输设备码，授权

# 3. 克隆项目（含 submodule）
gh repo clone dkfask/paperguard "E:/projects/论文书写模版格式规范软件"
cd "E:/projects/论文书写模版格式规范软件"
git submodule update --init --recursive

# 4. （可选）配 git 身份
git config --global user.name "你的名字"
git config --global user.email "your-github-id@users.noreply.github.com"
# 推荐用 GitHub noreply 邮箱：https://github.com/settings/emails

# 5. 验证
gh auth status
git status
ls references/format-thesis-docx/scripts/  # 应该有 3 个 .py
```

### 日常同步（两台电脑都用）

```bash
# 拉取最新
cd "E:/projects/论文书写模版格式规范软件"
git pull --recurse-submodules

# 如果 submodule 报错
git submodule sync
git submodule update --init --recursive

# 写完代码，提交
git add .
git commit -m "feat: xxx"   # feat/fix/docs/refactor/test/chore/perf/ci
git push

# 切到新分支做实验
git checkout -b feature/sprint-1-skeleton
# 写代码
git add . && git commit -m "feat(sprint-1): 脚手架"
git push -u origin feature/sprint-1-skeleton
# 合并到 main（在 GitHub 上提 PR，或本地 merge）
```

### 冲突解决

如果两台电脑改了同一文件：

```bash
git pull
# 如果冲突：编辑冲突文件 → 删冲突标记 → 保存
git add <冲突文件>
git commit -m "merge: 解决冲突"
git push
```

### 注意事项

- ⚠️ **不要把真论文 / 学生作业 commit 进来**——.gitignore 已排除 `papers/` 和 `fixtures/private/`
- ⚠️ **references/format-thesis-docx/ 是 submodule**——不要直接改里面的文件，要改就在原仓库改
- ⚠️ **app/ 目录是 PyQt 桌面应用**（之前就有的代码）——如果要做"无 GUI CLI"，建议并行分支 `feature/cli-core`，不要直接覆盖
- ⚠️ **第一次 clone 后必须跑 `git submodule update --init --recursive`**，否则 references/ 是空目录

### 私有 vs 公开

- 当前是 **private**——只有您自己能访问
- 如果想公开：到 https://github.com/dkfask/paperguard/settings → Danger Zone → Change visibility → Public
- 项目毕业 / 论文发布前可保持 private

---

## 项目结构

```
论文书写模版格式规范软件/
├── CLAUDE.md              ← 未来 Claude 接手指南
├── HANDOFF.md             ← 本文件：你看的
├── README.md              ← 已存在（项目里原有的）
├── SKILL.md               ← AI agent 入口（295 行）
├── SKILL_USAGE.md         ← 人类 5 分钟上手（217 行）
├── agents/
│   └── openai.yaml        ← OpenAI Codex 兼容（16 行）
├── docs/
│   ├── research-report.md          ← 5 份 GitHub 调研综合（193 行）
│   ├── design-proposal.md          ← 架构圣经 v1.1（647 行）
│   ├── format-thesis-docx-review.md← 参考项目 945 行精读（331 行）
│   └── skill-spec-check.md         ← SKILL.md 一致性自检（243 行）
├── references/
│   └── format-thesis-docx/         ← 参考项目源码（158K, 945 行）
├── research/
│   └── s1-s8.json          ← 5 份 agent 留的 raw search results
├── main.py / app/ / tests/ ← 项目原有的代码骨架（未动）
├── requirements.txt        ← 项目原有的依赖
└── *.ptpl / *.docx         ← 项目原有的样例文件
```

**累计新文档**：1942 行 markdown/yaml + 158K 参考源码 + 5 份 raw 搜索结果。

---

## 文档阅读顺序

| 你想 | 先读 | 再读 |
|------|------|------|
| **快速了解项目** | `HANDOFF.md`（本文件） | `CLAUDE.md` |
| **理解为什么这么设计** | `docs/research-report.md` | `docs/design-proposal.md` |
| **动手写代码** | `docs/design-proposal.md` §4 + §7 | `references/format-thesis-docx/scripts/` |
| **给 AI 用** | `SKILL.md` | `SKILL_USAGE.md` |
| **自检 / 验收** | `docs/skill-spec-check.md` | `docs/design-proposal.md` §7 Sprint 计划 |

---

## 7 个关键决策（不要重新讨论）

1. **项目名**：`PaperGuard`
2. **规则格式**：**YAML**（不是 JSON；不是 Rego；不是 Schema）
3. **解析层**：`python-docx + lxml`（不是 OpenXML SDK；不是 VBA；不是纯 stdlib）
4. **架构**：**4 层**（数据 / 引擎 / 报告 / 接入）—— 5 份子报告独立得出
5. **硬约束**：`fix` 永远输出新文件，不破坏原文
6. **修复后再 audit**：Sprint 4.5（参考项目短板）
7. **FORMAT_WORDS 启发式**：Sprint 2.5（参考项目最大优势）
8. **🆕 PyQt 实时约束是主路径**（v1.2）：学生在 PyQt 编辑器内边写边查（Grammarly 模式），CLI 是事后批改辅路

## 产品形态（v1.2 双模式）

| 模式 | 用户 | 入口 | 时机 |
|------|------|------|------|
| **🔴 实时约束（主）** | 学生 | PyQt 编辑器 | 写作时（0.5s debounce + 软约束） |
| **🟡 事后批改（辅）** | 教务/AI | CLI / Web | 提交后 |

**两者共用同一套规则引擎**（`app/core/engine/`）。

---

## 下次回来的 5 件事（按优先级）

### ✅ P0：MVP Sprint 1（项目脚手架 + IR）— **已完成 2026-06-05**

> 交付：`app/core/ir/`（5 个不可变 dataclass：Run/Section/Paragraph/Document/DocumentMetadata/Finding/Location/Fix/Severity）+ `app/core/loaders/docx_loader.py`（load_docx → Document IR）+ `app/core/reporters/terminal.py`（TerminalReporter）。36 个新单元测试 + 17 个原有 workflow 测试 = **56 tests 全绿**。PyQt 现有代码未动。Commit `6097817`。

### ✅ P0：MVP Sprint 1.5（**Live Checker 实时约束** — v1.2 主路径）— **已完成 2026-06-07**

> 交付：3 个 core（project_to_ir / pyqt_reporter / stub_engine 5 规则）+ 4 个 services（LiveFormatChecker 500ms debounce / LiveMutator 3 fix 类型 / IssueSidebar 严重度配色 / FormatHighlighter 红色波浪线）+ 改 `app/views/content_editor.py` 集成。**68 个新测试 + 56 旧 = 124 全绿**。性能基线 < 200ms 实测 < 100ms。`app/core/` 零 PyQt 依赖保持。共 10 atomic commits。**commit `0480d22`（fix: LiveMutator 接受点分 fix.target）**。

### ✅ P0：MVP Sprint 2（**YAML 规则引擎 + 25 规则**）— **已完成 2026-06-08**

> 交付：`app/core/engine/loader.py`（pydantic schema）+ `evaluator.py`（jmespath + json-logic）+ `visitor.py`（IR 遍历）+ `__init__.py`（入口替换）+ `app/core/rules/packs/GB-T-7713-1-2006.yaml`（25 条 PG-XXX 规则）+ IR 4 dataclass 加 `to_dict()` + 删 `stub_engine.py` + LiveFormatChecker 默认 pack 切换。**80 新测试 + 124 旧（删 10 stub）= 204 全绿**（含 9 integration）。**性能**：100 段 × 25 规则 → 270 findings → ~48ms（200ms 预算的 24%）。**新依赖**：json-logic-qubit + jmespath + pydantic v2。共 14 atomic commits。**commit `91309ba`（test: integration perf + e2e）**。

### ✅ P0：MVP Sprint 3（**多规范包 + Click CLI**）— **已完成 2026-06-09**

> 交付：`GB-T-7714-1-2015.yaml`（10 参考文献规则）+ `SIM-UNIV-generic.yaml`（20 学校样式规则，**自制零版权**）；修复 Sprint 2 follow-up B1（拆 `PG-BODY-PARAGRAPH-SPACING` 为 2 独立规则 PG-BODY-SPACE-BEFORE/AFTER）+ T0（perf 断言失效）；Click 8.x CLI 5 子命令（check / fix / extract / init / validate-pack）；`app/core/reporters/json_reporter.py`（SARIF-like 结构化 JSON）+ `app/core/writers/{fix_applier,docx_writer}.py`（不可变 IR 修复 + python-docx 回写）；`app/cli/_fix_then_check.py`（Sprint 4.5 闭环预铺）；`TerminalReporter.report_findings()` 与 JSONReporter 接口对齐。**64 新测试 / 359 全绿**（含 19 CLI integration + 9 extract integration）。**smoke 端到端跑通**：check 5 findings → fix 写 36811 bytes 新文件 → 3 packs validate-pack 全过 → init 194 bytes 骨架。`app/core/` 零 PyQt 依赖保持。共 15 atomic commits。**最新 commit `aa8b180`（feat: CLI 4 subcommands full impl）**。

### ✅ P0：MVP Sprint 4（**HTML/SARIF reporter + CLI 增强 + fix 闭环**）— **已完成 2026-06-11**

> 交付：`app/core/reporters/html_reporter.py`（Jinja2 + autoescape）+ `sarif_reporter.py`（SARIF 2.1.0 子集）；`check --format html|sarif`；`list-packs` / `doctor` / `version`；`fix --skip-verify` + 完整 `changes.md`；`pyproject.toml` console_scripts；`scripts/sprint4_smoke.py`。保留原有 `text/json` 输出、`--version`、隐藏 `--no-verify` 兼容。

### ✅ P0：MVP Sprint 5（**mutator 成熟 + 学校 pack 深化**）— **已完成 2026-06-16**

> 交付：`fix_applier` 支持 set / insert / delete / `runs.*`；`docx_writer` 与 `docx_loader` 完成段落、run、section、页眉页脚、表格单元格段落、列表段落的写回/读取闭环；GB/T 7714 参考文献悬挂缩进、多 run 字体/字号/下划线/加粗规则闭环；SIM-UNIV 表格单元格字号、列表行距规则；`scripts/sprint5_smoke.py`。**最近验证：492 passed, 1 skipped**；smoke 通过。收口 commit `89c6b98`。

### ✅ P0：真实样例回归第一版 — **已完成 2026-06-16**

> 交付：`tests/synthetic_regression_samples.py` 代码生成 9 个合成 `.docx` 样例，覆盖 GB/T 7713、GB/T 7714、SIM-UNIV 三个 pack 的合规、违规、修复后三类状态；`tests/integration/test_synthetic_regression_samples.py` 验证 `write -> load -> validate` 与 `fix -> write -> load -> validate`；`scripts/regression_samples_smoke.py` 提供本地 smoke。样例运行时写入临时目录，不提交真实论文或学生作业。提交 `8793168`。

### 🚧 P0（下一个）：PyQt 正常使用链路 + CI/发布工程化
- 打磨 DOCX 批改面板的异常提示和失败恢复。
- 将 DOCX 批改结果和现有侧边栏/高亮定位进一步融合。
- 配置 CI，把全量 pytest、Sprint 5 smoke 和 regression samples smoke 纳入基础检查。
- 继续扩充复杂长文档、真实学校模板匿名化样例、目录、题注、交叉引用和多级列表样例。

完整 Sprint 计划见 `docs/design-proposal.md` §7。

---

## 待补的 4 处小修订（不阻塞）

`docs/skill-spec-check.md` 第 9 节列出的 4 处修订：

1. design-proposal.md §10 风险表补"破坏性修复需确认"
2. design-proposal.md §8 V1 路线补"GB/T 9704—2012 公文"
3. design-proposal.md §3.2 `Location` dataclass 补 `text_snippet` 字段
4. design-proposal.md §5 补 `paperguard summary` 子命令

合并这 4 处约 10 分钟活。

---

## 5 份子报告速查

| 报告 | 主题 | 关键发现 |
|------|------|---------|
| S1 | 论文 Word 模板生成器 | 2024+ 趋势：AI Agent Skill 化 + YAML 规则包 |
| S2 | docx 排版技术栈 | python-docx + lxml + pandoc + Word COM 是事实标准 |
| S3 | 规则 DSL 引擎 | Vale/Kyverno 嫁接 = YAML + JSONLogic + JMESPath |
| S4 | 论文管理系统 | **没有任何系统集成"格式自动批改"** = 显著空白 |
| S5 | 论文格式检查器 | 153lsr/thesis-typeset 的"学校规范 YAML 包" + GUI 是最贴近模式 |

---

## 关键 GitHub 仓库

| 仓库 | 用途 | 位置 |
|------|------|------|
| `wksudud/format-thesis-docx` | 最佳骨架参考（945 行） | `references/format-thesis-docx/` |
| `python-openxml/python-docx` | 基础库（必依赖） | （pip 安装） |
| `elapouya/python-docx-template` | 备选（如果走 Jinja2 模板派） | — |
| `errata-ai/vale` | YAML 规则 DSL 灵感 | — |
| `153lsr/thesis-typeset` | GUI + YAML 学校规范包（V1 借鉴） | — |
| `huangwb8/ChineseResearchLaTeX` | PDF+DOCX 双线（V2 借鉴） | — |

---

## 项目时间线

| 日期 | 里程碑 |
|------|--------|
| 2026-06-05 | GitHub CLI 安装 + 5 份子报告 + 设计文档 v1.0 |
| 2026-06-05 | 参考项目 945 行精读 + 设计文档升级到 v1.1 |
| 2026-06-05 | SKILL.md 体系 + 一致性自检 |
| 2026-06-05 | 初始化 git 仓库 + 推送到 GitHub private |
| 2026-06-05 | **Sprint 1 完成**（commit `6097817`）：app/core/ 核心库骨架（IR + docx_loader + terminal reporter），16 文件 / ~720 行 / 56 tests 全绿 / 零 PyQt 依赖 |
| 2026-06-05 | HANDOFF.md 刷新到 v1.1（标记 Sprint 1 完成 + 提级 Sprint 1.5） |
| 2026-06-07 | **Sprint 1.5 完成**（commits `48cbd24` ~ `0480d22`，10 atomic）：app/core/ 加 project_to_ir / pyqt_reporter / stub_engine（5 规则）；app/services/ 加 LiveFormatChecker / LiveMutator / IssueSidebar / FormatHighlighter；app/views/content_editor.py 集成。**+~1400 LOC / +68 tests / 124 全绿**。性能 < 100ms（基线 200ms） |
| 2026-06-07 | HANDOFF.md 刷新到 v1.2（标记 Sprint 1.5 完成 + 提级 Sprint 2） |
| 2026-06-08 | **Sprint 2 完成**（commits `99e3c17` ~ `91309ba`，14 atomic）：app/core/engine/ 加 loader.py + evaluator.py + visitor.py + __init__.py（YAML pipeline）；IR 4 dataclass 加 to_dict()；app/core/rules/packs/GB-T-7713-1-2006.yaml（25 PG- 规则）；删 stub_engine.py + test_stub_engine.py；LiveFormatChecker 切默认 pack。**+~1100 LOC / +80 tests / 204 全绿**（含 9 integration）。性能：100 段 × 25 规则 → 270 findings → ~48ms（预算 200ms） |
| 2026-06-08 | HANDOFF.md 刷新到 v1.3（标记 Sprint 2 完成 + 提级 Sprint 2.5） |
| 2026-06-09 | **Sprint 2.5 完成**（commits `ae9fac0` ~ `7e224bf`，14 atomic）：app/core/extract/ 新子包 7 模块（requirement / format_words / xml_utils / run_props / para_props / section_props / extractor / to_pack）；5 fixture docx 自制（minimal/gb7713/tsinghua/zhejiang/wuhan）；95 新测试 / 295 全绿；Sprint 2 follow-up B1 规避 e2e 验证；smoke 端到端跑通（367 reqs→359 rules→52 findings） |
| 2026-06-09 | HANDOFF.md 刷新到 v1.4（标记 Sprint 2.5 完成 + 提级 Sprint 3） |
| 2026-06-09 | **Sprint 3 完成**（commits `08d9cd3` ~ `aa8b180`，15 atomic）：fix Sprint 2 follow-up B1+T0；GB-T-7714-1-2015.yaml (10 refs) + SIM-UNIV-generic.yaml (20 学校样式)；Click 8.x CLI 5 子命令 + json_reporter + fix_applier/docx_writer + _fix_then_check；64 新测试 / 359 全绿；smoke 端到端跑通（check 5 findings / fix 36811 bytes / 3 packs validate-pass / init 194 bytes 骨架） |
| 2026-06-09 | HANDOFF.md 刷新到 v1.5（标记 Sprint 3 完成 + 提级 Sprint 4） |
| 2026-06-11 | **Sprint 4 完成**：HTML/SARIF reporter；`check --format html|sarif`；`list-packs` / `doctor` / `version`；完整 `changes.md` fix 闭环；`pyproject.toml`；Sprint 4 smoke |
| 2026-06-16 | **Sprint 5 完成**：mutator/docx 写回闭环成熟；表格单元格段落、列表段落、参考文献悬挂缩进、多 run 修复闭环；SIM-UNIV 表格/列表规则；492 passed, 1 skipped；Sprint 5 smoke 通过 |
| 2026-06-16 | **真实样例回归第一版**：3 pack x 合规/违规/修复后合成 `.docx` 样例矩阵；集成测试和 smoke 覆盖 `fix -> write -> load -> validate` |
| 2026-06-17 | **PyQt docx 会话服务第一版**：`DocxReviewSession` 封装 `.docx` 导入、检查、自动修复、导出新 `.docx`，补 session smoke；提交 `5df9894` |
| 2026-06-17 | **PyQt DOCX 批改 UI 接入**：`ReviewExportView` 增加 DOCX 批改面板，`MainWindow` 接入导入、检查、修复、导出方法；提交 `8ceb945` |
| 2026-06-17 | **PyQt DOCX 问题列表与逐条修复**：`DocxReviewSession.apply_fix_at()`；批改面板渲染 finding 卡片并支持单项修复；提交 `04bddbc` |
| 2026-06-17 | **PyQt DOCX 定位反馈**：finding 卡片新增定位按钮，显示规则、段落号、文本片段并同步状态栏；提交 `a39b81e`；`503 passed, 1 skipped` |
| 2026-06-17 | **PyQt DOCX 导出反馈**：导出后在面板显示文件名、剩余问题数和导出时间摘要；提交 `82509de`；`503 passed, 1 skipped` |
| 2026-06-17 | **Word 模板章节结构导入**：从 Word `标题 1/2/3` 段落生成模板章节树，未识别标题时回退默认结构；提交 `9fcc480`；`504 passed, 1 skipped` |
| _待定_ | MVP 全部完成（3 周后） |

---

## 一句话建议

> 下次回来，直接说“打磨 DOCX 批改异常提示”或“配置 CI”——我会先读 `docs/progress.md`、`docs/sprints/sprint-5-plan.md`、`AGENTS.md`，然后做异常提示、失败恢复，或把 pytest 与 smoke 纳入基础检查。

---

**版本**：v2.3（Word 模板章节结构导入）
**最后更新**：2026-06-17
**配套文件**：`CLAUDE.md` / `SKILL.md` / `docs/*.md` / `references/format-thesis-docx/` / `docs/sprints/*.md`
**最新实现 commit**：`9fcc480 feat(template): import word heading structure`
