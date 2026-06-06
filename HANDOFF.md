# HANDOFF.md — 论文规范软件项目 · 阶段归档

> **这是给你（人类）看的**——下次打开项目时，30 秒读这一份就够。
> 给 AI 看的版本见 [`CLAUDE.md`](./CLAUDE.md)。
> 完整 AI 入口见 [`SKILL.md`](./SKILL.md)。

---

## 一句话状态

> ✅ **Sprint 1 已完成**（2026-06-05，commit `6097817`）：`app/core/` 核心库骨架就位 — 5 个不可变 IR dataclass + docx_loader + TerminalReporter，**16 个新文件 / ~720 行 / 56 tests 全绿 / 零 PyQt 依赖**。调研 + 设计 + 文档全部完成（含 **v1.2 实时约束**）。PyQt 桌面应用骨架已有 537 symbols。下一步：**Sprint 1.5（Live Checker 实时约束）**。

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

### 🚧 P0（下一个）：MVP Sprint 1.5（**Live Checker 实时约束** — v1.2 主路径）
```bash
cd D:/projects/paperguard   # 当前路径
# 写 app/core/reporters/pyqt_reporter.py（PyQtReporter + QtIssue）
# 写 app/services/live_format_checker.py（QObject + 0.5s debounce）
# app/views/content_editor.py 集成：连接 Qt signal
# 侧边栏 + 高亮 + 一键修复
# 单元 + 集成测试
```

**注意**：Sprint 1.5 的所有 `app/core/` 新代码**不能 import PyQt** —— PyQt 隔离是 Sprint 1 的硬约束。PyQt 部分放 `app/services/` 和 `app/views/`。

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
| 2026-06-05 | 初始化 git 仓库 + 推送到 GitHub private |
| 2026-06-05 | **Sprint 1 完成**（commit `6097817`）：app/core/ 核心库骨架（IR + docx_loader + terminal reporter），16 文件 / ~720 行 / 56 tests 全绿 / 零 PyQt 依赖 |
| 2026-06-05 | HANDOFF.md 刷新到 v1.1（标记 Sprint 1 完成 + 提级 Sprint 1.5） |
| _待定_ | MVP Sprint 1.5 开工（Live Checker 实时约束） |
| _待定_ | MVP 全部完成（7.5 周后） |

---

## 一句话建议

> 下次回来，直接说"开始 Sprint 1"——我会读 CLAUDE.md / HANDOFF.md / design-proposal.md，然后给你 `pyproject.toml` + 第一个 commit。

---

**版本**：v1.1（标记 Sprint 1 完成）
**最后更新**：2026-06-05
**配套文件**：`CLAUDE.md` / `SKILL.md` / `docs/*.md` / `references/format-thesis-docx/`
**最新 commit**：`6097817 feat(Sprint 1): 搭建 app/core/ 核心库骨架`
