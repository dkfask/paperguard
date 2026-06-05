# CLAUDE.md

> 给未来 Claude 看的项目指南。人类可读版见 [`HANDOFF.md`](./HANDOFF.md)。

## 这是什么项目

**PaperGuard**（暂定代号）——**PyQt 实时约束 + CLI 事后批改**双模式论文规范软件。
- **🔴 PyQt 实时约束**（主路径）：学生边写边查（Grammarly 模式），提交时已合规
- **🟡 CLI 事后批改**（辅路径）：CI 集成、批量审稿、AI Agent 入口

**当前阶段**：设计完成（v1.2，含实时约束），PyQt 桌面应用骨架已有（537 symbols），新核心 0 行代码。MVP Sprint 1 即将开始。

## 关键文件清单（按重要性）

| 路径 | 用途 | 何时读 |
|------|------|--------|
| **`docs/codebase-map.md`** | **代码图谱（codegraph 生成）**——37 文件 / 618 节点 / 1479 边的实际项目结构 | 任何"项目现在长啥样"的问题 |
| **`docs/design-proposal.md`** | **架构圣经**——4 层架构、IR 模型、YAML 规则 DSL、3 阶段路线 | 任何实现细节的疑问 |
| **`SKILL.md`** | AI agent 入口（Claude/Codex/Gemini 调用本项目的入口） | 用户问"AI 怎么调用"时 |
| **`SKILL_USAGE.md`** | 人类用户 5 分钟快速上手 | 用户问"我怎么用"时 |
| **`docs/research-report.md`** | 5 份 GitHub 调研综合 | 任何"为什么这么选"的问题 |
| **`docs/format-thesis-docx-review.md`** | 参考项目 945 行精读 | 想看"具体实现可参考什么"时 |
| **`docs/skill-spec-check.md`** | SKILL.md ↔ design-proposal.md 一致性自检 | 怀疑 SKILL.md 描述与设计文档脱节时 |
| **`HANDOFF.md`** | 人类可读恢复指南 | 用户问"上次到哪了"时 |
| **`agents/openai.yaml`** | OpenAI Codex/Gemini CLI 兼容 | 跨平台 skill 注册时 |
| **`references/format-thesis-docx/`** | 参考项目源码（158K） | 想要"现成可抄的代码"时 |

**总计文档**：1942 行 markdown/yaml。**代码**：0 行（未开工）。

## 命名约定（重要！）

- **项目代号**：`PaperGuard`（除非用户明确说改）
- **CLI 命令**：`paperguard <subcommand>`（**全小写、无连字符**）
- **规则包 ID**：`GB-T-7713-1-2006` 格式（**全大写、用连字符**）
- **IR 属性名**：点分命名 `font.eastAsia` / `paragraph.spacing.line`（与 OOXML 原始 key 对齐）—— **v1.1 修订的关键决策**
- **子命令**：extract / check / fix / init / validate-pack（4+1 个，**全部为动词**）

## 当前状态

| 维度 | 状态 |
|------|------|
| 调研 | ✅ 5 份子报告 + 1 份参考精读 + 1 份一致性自检 + **1 份代码图谱**（codegraph） |
| 设计 | ✅ 4 层架构 + IR + 规则包 + Sprint 计划（**v1.2 含实时约束**） |
| 文档 | ✅ 9 份文档（SKILL.md / SKILL_USAGE.md / 6 份 docs / HANDOFF.md / CLAUDE.md） |
| 已有代码 | ✅ **PyQt5 桌面应用骨架**（`app/` 5 controllers + 3 models + 9 services + 9 views，537 symbols） |
| 已有测试 | ✅ **`tests/test_workflow.py` 64 symbols**（workflow 集成测试） |
| 代码图谱 | ✅ `.codegraph/` 索引 37 文件 / 618 节点 / 1479 边（1.72 MB） |
| Fixture | ❌ 无（项目里现成的 .ptpl 可作早期 fixture） |
| 新核心代码 | ❌ 0 行（**`app/core/` 未建**——v1.2 决定放 app/core/，不是 src/paperguard/） |
| CI | ❌ 无 |

**架构现状**（基于 codegraph）：
- 已有：L4 PyQt 视图 + L4 控制器 + L1 业务模型（PyQt MVC 完整）
- 已有雏形：L2 services（`format_validator` 7 symbols / `docx_generator` 30 / `word_template_importer` 26）
- 完全缺失：CLI 入口 / YAML 规则包 / 真正的规则引擎 / 报告层 / 修复（mutator）

**下一步**：MVP Sprint 1 → Sprint 1.5（Live Checker 实时约束）→ ... → Sprint 6。

**Sprint 1 第一步**：`pytest tests/test_workflow.py` 让现有 64 symbols 测试通过，建立信心。

## 常见任务速查

### 用户问"项目到哪了"
→ 读 `HANDOFF.md` 的"项目状态"小节

### 用户问"为什么这么设计"
→ 读 `docs/research-report.md` 的"5 份子报告交叉对比" + `docs/design-proposal.md` 的"风险与对策"

### 用户问"AI 怎么调用"
→ 读 `SKILL.md`

### 用户说"开始写代码"
→ 进 plan mode，参考 `docs/design-proposal.md` §4 模块设计 + §7 Sprint 计划，先做 Sprint 1

### 用户说"加新规则"
→ 读 `docs/design-proposal.md` §3.3 YAML 规则包格式，按规范在 `src/paperguard/rules/_builtins/` 加 .py 文件

### 用户说"出学校 pack"
→ 跑 `paperguard init --school "X" --base GB-T-7713-1-2006 --out x.yaml`（V1 后才有此命令）

### 用户说"批改论文"
→ 跑 `paperguard check --paper x.docx --pack <pack>` → 报告 → 跑 `paperguard fix`

## 关键决策历史（避免重新讨论）

1. **YAML 规则包（不是 JSON）**—— 5 份子报告共识 + 用户可读性优先
2. **python-docx + lxml（不是 OpenXML SDK）**—— Python 生态 + 中文支持
3. **四层架构**（数据 / 引擎 / 报告 / 接入）—— 5 份子报告独立分析得出
4. **不破坏原文**—— 硬约束，所有 `fix` 输出新文件
5. **修复后再 audit**（Sprint 4.5）—— 参考项目短板，本项目必补
6. **FORMAT_WORDS 启发式**（Sprint 2.5）—— 参考项目最大优势，必抄
7. **MVP 7.5 周**（不是 6 周）—— 多花的 1.5 周用于 Sprint 2.5 + 4.5
8. **PyQt 实时约束是主路径**（v1.2，Sprint 1.5）—— 学生写作时实时检查 + 一键修复；CLI 是事后批改辅路

## 不应该做的事

- ❌ 改项目代号为别的（除非用户明确说改）
- ❌ 改 IR 属性名约定为 PascalCase（v1.1 已修订为点分）
- ❌ 引入商业 docx 库（Spire/Aspose）
- ❌ 做 docx 在线编辑器（复杂度极高；只做"上传→批改"）
- ❌ 走 Activiti/flowable（流程引擎过度工程）
- ❌ 让 `fix` 默认覆盖原文件（硬约束）
- ❌ **用 CLI 模拟"实时约束"**——CLI 调一次跑全文，不适合"边写边查"；实时约束只能在 PyQt 编辑器内做

## 工作流提示

- **TDD 强制**：新增功能先写测试（pytest，目标覆盖率 ≥ 80%）
- **小文件原则**：单文件 < 800 行，单函数 < 50 行
- **不可变 IR**：dataclass(frozen=True)
- **显式错误处理**：不静默吞错
- **每步 commit**：每完成一个 Sprint 节点就 commit（用 conventional commits：`feat:` / `docs:` / `test:` / `refactor:`）

## 与外部系统的关系

- **参考项目**：`references/format-thesis-docx/`（本地副本）
- **上游设计**：`docs/design-proposal.md` 是 single source of truth
- **AI 入口**：`SKILL.md` 是 single source of truth for AI agents
- **真值对照**：`docs/skill-spec-check.md` 验证 SKILL.md ↔ design-proposal.md 一致性

---

**版本**：v1.1（与 design-proposal.md 同步）
**最后更新**：2026-06-05
