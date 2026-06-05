# HANDOFF.md — 论文规范软件项目 · 阶段归档

> **这是给你（人类）看的**——下次打开项目时，30 秒读这一份就够。
> 给 AI 看的版本见 [`CLAUDE.md`](./CLAUDE.md)。
> 完整 AI 入口见 [`SKILL.md`](./SKILL.md)。

---

## 一句话状态

> 调研 + 设计 + 文档全部完成，**0 行代码**。下次回来直接进 MVP Sprint 1。

---

## 项目代号

**PaperGuard**（论文守门员）——除非你想换名字。

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

---

## 下次回来的 5 件事（按优先级）

### P0：MVP Sprint 1（项目脚手架 + IR）
```bash
cd "E:/projects/论文书写模版格式规范软件"
uv init --package paperguard
# 复制 docs/design-proposal.md §4 的目录结构
# 写 src/paperguard/ir/ + 单元测试
# 写 src/paperguard/loaders/docx_loader.py MVP
# 写 src/paperguard/reporters/terminal.py
# 写第一个 commit
```

### P1：MVP Sprint 2（规则引擎）
- 写 `engine/loader.py` / `engine/evaluator.py` / `engine/visitor.py`
- 5 条规则 demo

### P2：MVP Sprint 2.5（**新增于 v1.1**，**必做**）
- 抄 `references/format-thesis-docx/scripts/extract_requirements.py` 的 FORMAT_WORDS 启发式
- 新增 `paperguard extract` 子命令

### P3：MVP Sprint 3（GB/T 7713 规范包）
- 30 条内置规则
- `rules/packs/GB-T-7713-1-2006.yaml`

### P4：MVP Sprint 4 / 4.5（报告 + CLI + 修复后验证）
- JSON / HTML / SARIF reporter
- `paperguard check` / `init` CLI
- **`fix` 完成后自动 `check`**

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
| 2026-06-05 | **当前：归档，等待下次回来** |
| _待定_ | MVP Sprint 1 开工 |
| _待定_ | MVP 全部完成（7.5 周后） |

---

## 一句话建议

> 下次回来，直接说"开始 Sprint 1"——我会读 CLAUDE.md / HANDOFF.md / design-proposal.md，然后给你 `pyproject.toml` + 第一个 commit。

---

**版本**：v1.0
**最后更新**：2026-06-05
**配套文件**：`CLAUDE.md` / `SKILL.md` / `docs/*.md` / `references/format-thesis-docx/`
