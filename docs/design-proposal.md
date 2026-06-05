# 通用论文规范软件 — 设计方案

> **基于**：`docs/research-report.md`（5 份 GitHub 调研综合）
> **精读参考**：`docs/format-thesis-docx-review.md`（wksudud/format-thesis-docx 945 行源码逐行精读）
> **目标**：让任何高校/期刊用一份 YAML 规范包，就能把学生的 .docx 论文自动批改到符合本校格式
> **代号**（暂定）：**PaperGuard**（取"论文守门员"之意）
> **首期范围**：Word (`.docx`) 输入 → 规范校验 → 报告 + 修复

## 修订记录

| 版本 | 日期 | 修订人 | 修订内容 |
|------|------|--------|---------|
| v1.0 | 2026-06-05 | 调研 agent | 初稿：4 层架构 + IR + 规则包 + 3 阶段路线 |
| v1.1 | 2026-06-05 | 本次 | ① §3.1 IR 属性名改为**点分命名**（`font.eastAsia`）以对齐 OOXML 原始 key；② §7 MVP 路线新增 Sprint 2.5（模板自动抽取）和 Sprint 4.5（修复后验证）；③ §13 新增"与 format-thesis-docx 的核心差异" |

---

## 1. 产品定位

| 维度 | 定位 |
|------|------|
| **用户** | 高校学生（写论文）+ 教务 / 答辩秘书（批量审稿）+ 导师（终审） |
| **场景** | 学生写完初稿 → 跑 PaperGuard → 看报告 → 一键修复 → 重新跑直到全绿 → 提交 |
| **差异化** | GitHub 上**没有任何项目**同时满足：① 规则可配置（YAML）；② 自动批改 + 修复；③ 跨校通用；④ AI Agent 入口 |
| **不做什么** | 不做 docx 在线编辑器（复杂度极高）；不做全流程管理（教学管理系统做）；不做内容生成（AI 写作助手做） |

---

## 2. 核心架构：四层 + 双向数据流

```
                ┌─────────────────────────────────────────────────────┐
                │           L4 用户接入层 (User Surface)               │
                │                                                     │
                │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────┐ │
                │  │ CLI      │  │ AI Skill │  │ Web UI   │  │Word│ │
                │  │ paperguard│  │ (Claude  │  │ (上传 → │  │插件│ │
                │  │  check   │  │  Codex)  │  │  报告)  │  │(V2)│ │
                │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──┬─┘ │
                └───────┼─────────────┼─────────────┼───────────┼───┘
                        │             │             │           │
                        ▼             ▼             ▼           ▼
                ┌─────────────────────────────────────────────────────┐
                │        L3 报告与反馈层 (Reporting)                   │
                │  · terminal plain  · JSON  · SARIF  · HTML         │
                │  · redline docx（修订模式）  · PDF 批注            │
                └────────────────────────┬────────────────────────────┘
                                         │ (Finding[])
                                         ▼
                ┌─────────────────────────────────────────────────────┐
                │        L2 规则引擎层 (Rule Engine) ←──── 核心       │
                │                                                     │
                │  ┌─────────────────────────────────────────────┐  │
                │  │ YAML 规则包加载器（合并/覆盖/继承）         │  │
                │  └────────────────────┬────────────────────────┘  │
                │                       ▼                            │
                │  ┌─────────────────────────────────────────────┐  │
                │  │ 规则注册表 (RuleRegistry)                   │  │
                │  │  · 30+ 内置规则 (国标 + 通用)                │  │
                │  │  · 第三方包通过 entry_points 加载            │  │
                │  └────────────────────┬────────────────────────┘  │
                │                       ▼                            │
                │  ┌─────────────────────────────────────────────┐  │
                │  │ 规则执行器 (visitor pattern)                │  │
                │  │  · 遍历 docx AST                            │  │
                │  │  · 命中规则 → 收集 finding                  │  │
                │  │  · 修复模式 → 应用 fix                      │  │
                │  └────────────────────┬────────────────────────┘  │
                │                       ▼                            │
                │  ┌─────────────────────────────────────────────┐  │
                │  │ 表达式求值 (JSONLogic 子集 + JMESPath)      │  │
                │  └─────────────────────────────────────────────┘  │
                └────────────────────────┬────────────────────────────┘
                                         │ (Raw docx)
                                         ▼
                ┌─────────────────────────────────────────────────────┐
                │        L1 数据访问层 (Data Access)                  │
                │                                                     │
                │  ┌────────────┐  ┌────────────┐  ┌────────────┐   │
                │  │ python-docx│  │ lxml       │  │ pandoc     │   │
                │  │ (高层 API) │  │ (细粒度)   │  │ (md→docx)  │   │
                │  └────────────┘  └────────────┘  └────────────┘   │
                │           │                          │             │
                │           └──────────┬───────────────┘             │
                │                      ▼                             │
                │           ┌────────────────────┐                   │
                │           │ Word COM (可选)    │  ← TOC / 页码 / 域更新│
                │           │ Windows + MS Word  │                   │
                │           └────────────────────┘                   │
                └─────────────────────────────────────────────────────┘
```

**双向数据流**：
- **正向**：docx → L1 解析 → L2 规则 → L3 报告 → L4 展示
- **反向（修复）**：L4 `--fix` → L3 写出 redline → L2 mutate 引擎 → L1 写回 docx

---

## 3. 核心数据模型

### 3.1 统一 IR（中间表示）

无论 L1 用什么解析器，最终都吐出一个**统一的 IR 树**给 L2。这是为了解耦"解析"与"规则"——以后换 OpenXML SDK 不用改规则代码。

```python
# paperguard/ir/doc.py
@dataclass(frozen=True)
class Document:
    sections: tuple[Section, ...]
    paragraphs: tuple[Paragraph, ...]
    tables: tuple[Table, ...]
    images: tuple[Image, ...]
    styles: dict[str, Style]  # name → Style
    metadata: DocumentMetadata

@dataclass(frozen=True)
class Paragraph:
    style_name: str
    runs: tuple[Run, ...]
    line_spacing: float
    space_before_pt: float
    space_after_pt: float
    first_line_indent_chars: float
    is_page_break_before: bool
    raw_text: str
    source_path: str  # 用于定位
    level: int | None  # 从 style 推断（Heading 1 → 1）

@dataclass(frozen=True)
class Run:
    text: str
    # **点分属性名约定**（v1.1 修订）：键名直接用 OOXML 原始 key
    #   如 "font.eastAsia" / "font.ascii" / "font.size_half_points"
    #   1) 与 wksudud/format-thesis-docx 的命名一致（详见 §13）
    #   2) 写回 docx 时不丢字段
    #   3) 类型化属性（font_size_pt）作为便利接口
    raw_props: dict[str, str]
    @property
    def font_eastasia(self) -> str | None:
        return self.raw_props.get("font.eastAsia")
    @property
    def font_ascii(self) -> str | None:
        return self.raw_props.get("font.ascii")
    @property
    def font_size_pt(self) -> float | None:
        # 字号在 OOXML 中是半磅（"24" = 12pt）
        half = self.raw_props.get("font.size_half_points")
        return int(half) / 2 if half else None
    @property
    def bold(self) -> bool:
        return "font.bold" in self.raw_props
    @property
    def italic(self) -> bool:
        return "font.italic" in self.raw_props

@dataclass(frozen=True)
class Section:
    page_width_cm: float
    page_height_cm: float
    top_margin_cm: float
    bottom_margin_cm: float
    left_margin_cm: float
    right_margin_cm: float
    header_distance_cm: float
    footer_distance_cm: float
```

**不可变数据 + 显式属性**是后续"diff/审计/redline"的基础。

### 3.2 Finding 模型

```python
@dataclass(frozen=True)
class Finding:
    rule_id: str           # 如 "PG-LAYOUT-LINE-SPACING"
    severity: Severity     # error | warning | info
    message: str
    location: Location     # 定位（章节、段落号、文本片段）
    actual: Any            # 实际值
    expected: Any          # 期望值
    fix: Fix | None        # 可自动修复时填

@dataclass(frozen=True)
class Fix:
    action: Literal["set", "insert", "delete", "wrap"]
    target: str            # JMESPath 表达式
    value: Any
    description: str
```

### 3.3 Rule Pack 模型（YAML 文件结构）

```yaml
# rules/packs/GB-T-7713-1-2006.yaml
id: GB-T-7713-1-2006
name: "学位论文编写规则（GB/T 7713.1-2006）"
version: 1.0.0
author: PaperGuard
extends: []  # 可继承其他 pack
inherits_from_defaults: true

# 1. 页面/版心
page_setup:
  paper: A4
  margins:
    top_cm: 2.54
    bottom_cm: 2.54
    left_cm: 3.17
    right_cm: 3.17

# 2. 字体字号（中英区分）
fonts:
  body:
    eastAsia: 宋体
    ascii: Times New Roman
    size_pt: 12        # 小四
  heading_1:
    eastAsia: 黑体
    ascii: Times New Roman
    size_pt: 18        # 二号
  heading_2:
    eastAsia: 黑体
    ascii: Times New Roman
    size_pt: 15        # 三号
  heading_3:
    eastAsia: 黑体
    ascii: Times New Roman
    size_pt: 14        # 四号

# 3. 段落
paragraph:
  body_line_spacing: 1.5
  body_first_line_indent_chars: 2
  space_before_pt: 0
  space_after_pt: 0

# 4. 规则列表
rules:
  - id: BODY-LINE-SPACING
    severity: error
    scope: "paragraphs[?style_name == 'Body Text']"
    when:
      "!=":
        - {var: "line_spacing"}
        - 1.5
    message: "正文行距必须为 1.5 倍"
    fix:
      set: {line_spacing: 1.5}

  - id: BODY-FIRST-LINE-INDENT
    severity: error
    scope: "paragraphs[?style_name == 'Body Text']"
    when:
      "!=":
        - {var: "first_line_indent_chars"}
        - 2
    message: "正文首行应缩进 2 字符"
    fix:
      set: {first_line_indent_chars: 2}

  - id: HEADING1-FONT-MATCH
    severity: error
    scope: "paragraphs[?level == 1]"
    when:
      any:
        - "!=":
            - {var: "runs[0].font_eastasia"}
            - "黑体"
        - "!=":
            - {var: "runs[0].size_pt"}
            - 18
    message: "一级标题应为黑体二号"
    fix:
      set:
        runs[0].font_eastasia: "黑体"
        runs[0].size_pt: 18
```

---

## 4. 核心模块设计

```
paperguard/
├── pyproject.toml
├── README.md
├── src/paperguard/
│   ├── __init__.py
│   ├── __main__.py            # CLI 入口（python -m paperguard）
│   ├── cli/
│   │   ├── main.py            # argparse
│   │   ├── check.py           # paperguard check
│   │   ├── fix.py             # paperguard fix
│   │   ├── init.py            # paperguard init（生成新 pack）
│   │   └── validate.py        # paperguard validate-pack
│   ├── ir/                    # 中间表示（不可变 dataclass）
│   │   ├── doc.py
│   │   ├── paragraph.py
│   │   ├── run.py
│   │   ├── section.py
│   │   └── finding.py
│   ├── loaders/               # L1：解析 docx
│   │   ├── docx_loader.py     # python-docx + lxml 下钻
│   │   ├── ooxml_helpers.py   # 双字体、字号等中文特化处理
│   │   └── pandoc_loader.py   # V2：md → IR（可选）
│   ├── engine/                # L2：规则引擎
│   │   ├── registry.py        # RuleRegistry：注册/发现规则
│   │   ├── loader.py          # YAML pack 加载与合并
│   │   ├── visitor.py         # 遍历 IR 树
│   │   ├── evaluator.py       # JSONLogic + JMESPath 表达式
│   │   ├── mutator.py         # 应用 Fix 到 IR
│   │   └── scope.py           # scope 解析（"paragraphs[?...]"）
│   ├── rules/                 # 内置规则
│   │   ├── _builtins/
│   │   │   ├── layout.py      # 页边距、纸张
│   │   │   ├── font.py        # 字体、字号
│   │   │   ├── paragraph.py   # 行距、缩进、段距
│   │   │   ├── heading.py     # 标题层级
│   │   │   ├── figure_table.py# 图/表编号
│   │   │   ├── reference.py   # 参考文献格式
│   │   │   ├── toc.py         # 目录
│   │   │   └── page_number.py # 页眉页脚页码
│   │   └── packs/             # 预制规范包
│   │       ├── GB-T-7713-1-2006.yaml
│   │       ├── GB-T-7714-2015.yaml
│   │       └── README.md
│   ├── reporters/             # L3：报告输出
│   │   ├── terminal.py
│   │   ├── json_reporter.py
│   │   ├── sarif.py           # GitHub Code Scanning
│   │   ├── html.py
│   │   └── redline_docx.py    # 修订模式 docx
│   ├── writers/               # L1：写回 docx
│   │   ├── docx_writer.py
│   │   └── word_com_fixer.py  # 可选：Windows + Word COM 收尾
│   └── plugins.py             # 第三方规则包发现（entry_points）
├── tests/
│   ├── unit/
│   │   ├── test_ir.py
│   │   ├── test_loader.py
│   │   ├── test_engine.py
│   │   ├── test_evaluator.py
│   │   └── test_rules/
│   ├── integration/
│   │   ├── test_cli.py
│   │   ├── test_pack_gb_t_7713.py
│   │   └── test_redline.py
│   └── fixtures/
│       ├── sample_thesis_good.docx
│       ├── sample_thesis_bad.docx
│       └── expected_findings.json
└── docs/
    ├── research-report.md
    ├── design-proposal.md     # 本文件
    ├── rule-author-guide.md   # V1 写
    └── architecture.md        # V1 写
```

### 4.1 关键模块职责

| 模块 | 单一职责 | 依赖 |
|------|----------|------|
| `ir/` | 定义不可变数据结构 | 仅 dataclass |
| `loaders/docx_loader.py` | docx → IR | python-docx + lxml |
| `engine/loader.py` | YAML pack → Rule 对象 | pyyaml + json-logic + jmespath |
| `engine/visitor.py` | IR 树 + 规则集 → Finding[] | engine/* |
| `engine/mutator.py` | Finding.fix → 修改后的 IR | lxml on IR |
| `rules/_builtins/*.py` | 30+ 内置规则的纯函数 | ir, engine |
| `reporters/*.py` | Finding[] → 各种格式 | 各自第三方库 |
| `writers/docx_writer.py` | 修改后的 IR → docx | python-docx + lxml |

---

## 5. 关键工作流

### 5.1 `paperguard check`（默认只报告，不修改）

```
docx ─┐
      ├──► docx_loader ──► IR ──► engine.visitor ──► Finding[]
pack ─┘                            │
                                   ▼
                          engine.evaluator (JSONLogic)
                                   │
                                   ▼
                            reporters (--format json|html|terminal|sarif)
                                   │
                                   ▼
                            exit code 0/1/2（CI 用）
```

### 5.2 `paperguard fix`（应用修复，写出新文件）

```
docx ─► docx_loader ──► IR ──► engine.visitor ──► Finding[].fix
                                                │
                                                ▼
                                       engine.mutator ──► IR'
                                                │
                                                ▼
                                       writers.docx_writer ──► paper.fixed.docx
                                                │
                                                ▼
                                       （可选）word_com_fixer ──► 更新 TOC/页码
```

### 5.3 `paperguard init`（生成新 pack 骨架）

```
paperguard init --school "南京信息工程大学" --base GB-T-7713-1-2006
   │
   ▼
生成：~/.paperguard/packs/NJUIST-2026.yaml
   + 内置 30 条规则（继承自 GB/T 7713.1）
   + 待填字段：学校徽标位置、页眉内容、答辩委员会字段
```

### 5.4 AI Agent Skill 入口（V1）

```
用户（自然语言）："请把这份论文改成符合南信大 2026 规范的版本"
   │
   ▼
Claude/Codex 调用 paperguard CLI：
   paperguard check --pack NJUIST-2026 --format json
   paperguard fix --pack NJUIST-2026 --redline --output paper.fixed.docx
   │
   ▼
paperguard 返回结构化结果，agent 据此决定下一步
```

---

## 6. 技术选型

| 层 | 选型 | 理由 | 风险/替代 |
|----|------|------|----------|
| **语言** | Python 3.11+ | 5 份子报告共识、python-docx 生态 | 用 Rust 重写性能关键路径（V2） |
| **docx 解析** | python-docx + lxml | 共识、入门低 | OpenXML SDK（C# 子进程，V2） |
| **docx 写回** | python-docx | 同上 | 同上 |
| **docx 终稿** | pywin32 (Word COM) | TOC/域更新必须 Word 跑 | 仅 Windows+MS Word |
| **pandoc** | pandoc | md→docx 互转 | 非必需，V2 加 |
| **规则配置** | PyYAML | 人读、版本化 | — |
| **表达式** | json-logic + jmespath.py | 共识最小集 | V2 可加 JMESPath 完整版 |
| **报告 HTML** | Jinja2 | 模板灵活 | — |
| **报告 SARIF** | 手写 | SARIF 2.1.0 JSON 模式固定 | — |
| **报告 redline** | python-docx + lxml | tracked-change 标签 | 也可调 Word COM |
| **CLI** | argparse | 标准库 | Typer（更友好，V1 可换） |
| **测试** | pytest + pytest-cov | 80%+ 覆盖率硬指标 | — |
| **打包** | uv（首选）/ Poetry | 现代化 | — |
| **插件** | entry_points (`importlib.metadata`) | 标准 Python 插件 | — |
| **CLI 分发** | PyPI + pipx | 跨平台零依赖 | 也可 uv tool install |

---

## 7. MVP 路径（1-2 月）

### Sprint 1：骨架（1 周）
- [ ] 项目脚手架（uv init + pyproject.toml + 目录结构）
- [ ] `ir/` 数据结构 + 单元测试（采用 v1.1 修订的**点分属性名**约定）
- [ ] `loaders/docx_loader.py` MVP：能读 docx 吐 IR
- [ ] `reporters/terminal.py`：打印段落基本信息

### Sprint 2：规则引擎核心（1 周）
- [ ] `engine/loader.py`：加载单个 YAML pack
- [ ] `engine/evaluator.py`：JSONLogic 子集 + JMESPath
- [ ] `engine/visitor.py`：遍历 IR + 命中规则 → Finding[]
- [ ] 5 条规则 demo：line-spacing / first-line-indent / font-size / page-margin / heading-level

### 🆕 Sprint 2.5：模板自动抽取（0.5 周，新增于 v1.1）
> 借鉴 `references/format-thesis-docx/scripts/extract_requirements.py` 的 FORMAT_WORDS 启发式。
- [ ] `extract/` 子包：解包 docx + 遍历 styles/sections/numbering/comments
- [ ] FORMAT_WORDS 正则（中英文 25+ 关键词）识别"显式说明文字"
- [ ] 抽出的 7 类别 Requirement（style / section / paragraph-example / table / numbering / explicit-instruction / document）
- [ ] `paperguard extract` CLI：模板 docx → requirements.json + requirements.md
- [ ] 单元测试：5+ fixture 模板（清华 / 浙大 / 武大 / 国标 / 自制）

### Sprint 3：GB/T 7713 规范包（1 周）
- [ ] 30 条内置规则实现
- [ ] `rules/packs/GB-T-7713-1-2006.yaml` 完整版
- [ ] 集成测试：用 2 份 fixture（好/坏）跑全流程
- [ ] **复用** Sprint 2.5 的 `extract` 输出作为 pack 验证基准

### Sprint 4：报告 + CLI 闭环（1 周）
- [ ] JSON reporter
- [ ] HTML reporter
- [ ] SARIF reporter（让 GitHub Action 能消费）
- [ ] `paperguard check` CLI
- [ ] `paperguard init` CLI
- [ ] `paperguard extract` CLI（Sprint 2.5 的成果挂上 CLI）

### 🆕 Sprint 4.5：修复后验证（0.5 周，新增于 v1.1）
> 参考项目 `apply_format.py` 修复后不验证——本项目必须补。
- [ ] `paperguard fix` 完成后自动调用 `paperguard check`
- [ ] 输出"修复后剩余 N 个问题"清单到 `changes.md` 末尾
- [ ] 修复前后对比指标：before_N / after_N / fixed / skipped / pass
- [ ] 单元测试：模拟"修完仍不通过"的场景，确认能正确告知

### Sprint 5：修复闭环（1 周）
- [ ] `engine/mutator.py` 实现 set/insert/delete
- [ ] `writers/docx_writer.py`：把修改后 IR 写回 docx
- [ ] **借鉴** `references/format-thesis-docx/scripts/apply_format.py` 的 `set_run_property` / `set_para_property` / `set_section_property` 函数
- [ ] 用 `ensure(parent, tag)` 模式避免覆盖用户已有设置
- [ ] `paperguard fix` CLI
- [ ] 默认输出 `paper.fixed.docx`（不覆盖原文件）
- [ ] redline docx 报告（修订模式）

### Sprint 6：质量 + 文档（1 周）
- [ ] 覆盖率 ≥ 80%
- [ ] CLI 集成测试
- [ ] README + 快速上手
- [ ] `SKILL.md`（Claude/Codex 入口）
- [ ] PyPI 发布准备

### MVP 验收标准
- ✅ 一份 GB/T 7713 YAML 跑通
- ✅ CLI `extract` / `check` / `fix` / `init` 四个命令
- ✅ HTML + JSON + SARIF 三种报告
- ✅ 至少 30 条内置规则
- ✅ **5+ fixture 模板自动抽取** 验证
- ✅ **修复后自动 audit** 闭环
- ✅ 测试覆盖率 ≥ 80%
- ✅ PyPI 可安装

**MVP 总时长**：6 周 → **7.5 周**（多 1.5 周用于"模板自动抽取"和"修复后验证"——这两点是参考项目的最大优势和最大短板）

---

## 8. V1 路线（3-6 月）

- [ ] **学校规范包生态**：`paperguard pack install <name>`，预制 10 所高校
- [ ] **AI Agent Skill 入口**：打包为 Claude Code / Codex Skill，提供 `SKILL.md` + `AGENTS.md`
- [ ] **GB/T 7714—2015 参考文献**：内置规则（含顺序编码、上标交叉引用、悬挂缩进）
- [ ] **pandoc 中间路径**：md → docx 也能用
- [ ] **Web UI（最小版）**：FastAPI 后端 + 静态前端（上传 docx → 出报告）
- [ ] **GitHub Action**：`paperguard-action` 让 `.github/workflows` 直接挂

---

## 9. V2 路线（6-12 月）

- [ ] **Word 插件**（VSTO / Office AddIn）
- [ ] **OpenXML SDK 优化路径**（C# 子进程，做极致保真度）
- [ ] **跨校规则联邦**：规则包仓库 + GitOps 同步
- [ ] **SaaS 版**（多租户、机构账号）
- [ ] **LLM 增强规则**：当规则无法覆盖时，调用 LLM 给"软建议"

---

## 10. 风险与对策

| 风险 | 等级 | 影响 | 对策 |
|------|------|------|------|
| python-docx 抽象不够，中文字体取不到 | 🟡 高 | 数据层崩溃 | L1 阶段就准备好 `ooxml_helpers.py` 直接 lxml 解析 `<w:rFonts>` |
| TOC / 域代码无法纯 Python 更新 | 🟡 高 | 用户最痛的体验 | 提供 Word COM 后处理（仅 Windows）；macOS/Linux 用 pandoc --update 兜底 |
| YAML 规则写错 | 🟡 中 | 用户不会写 | 提供 `paperguard validate-pack` + 详细 schema 错误信息 + 模板生成器 `init` |
| 修复后样式"看起来不一样" | 🟡 中 | 用户不信任 | 默认走 redline 模式而非直接覆盖；生成 `changes.csv` 让用户审计 |
| 商业 docx 库（Spire/Aspose）锁定 | 🟢 低 | 长期被绑 | MVP 全用 python-docx + lxml；不引入商业依赖 |
| 学校规范年年变 | 🟡 中 | 维护负担 | pack 与代码解耦；学校可独立更新 pack 而不用等主版本 |
| 性能（论文动辄 200 页） | 🟢 低 | 用户卡顿 | 增量遍历 + 流式输出 Finding；V2 用 Rust 重写热路径 |
| 与"论文管理系统"重叠 | 🟢 低 | 内部竞争 | 明确定位："规范 + 批改"是工具；"流程管理"是系统——后者集成前者 |

---

## 11. 与 5 份子报告的对应关系

| 子报告洞察 | 本方案对应 |
|------------|------------|
| S1：M1-M4 四象限交付 | `paperguard init` 走 M1 默认；`--base <pack>` 走 M2/M3；`paperguard fix` 走 M4 |
| S1：AI Skill 化趋势 | V1 路线中显式规划 Claude/Codex Skill |
| S1：审计与可回滚 | 默认 `--redline` + `paper.fixed.docx` 不覆盖原稿 |
| S2：python-docx + lxml + Word COM 三件套 | L1 数据访问层直接抄这个组合 |
| S3：YAML + JSONLogic + JMESPath | 规则包格式就是这套三元组 |
| S3：Vale 的"style pack"分发 | V1 路线预制 10 校 pack |
| S4：格式自动批改是空白 | 本方案的核心差异化 |
| S4：避免 Activiti 重型 | 显式不做"流程引擎"；只做"规范引擎" |
| S5：避免硬编码 | 第一条规则就是 YAML |
| S5：SARIF / CI 集成 | Sprint 4 显式做 SARIF reporter |
| S5：学校规范走 YAML | `rules/packs/GB-T-7713-1-2006.yaml` 就是这个示范 |

---

## 12. 立即可启动的 3 件事

1. **本机试装** `wksudud/format-thesis-docx`（✅ 已完成 — 仓库在 `references/format-thesis-docx/`）：
   ```bash
   git clone https://github.com/wksudud/format-thesis-docx
   cd format-thesis-docx
   # 跑它的 demo，把输出和源码读一遍
   ```
   原因：它和本项目目标最接近、代码量小、最佳骨架参考。

2. **查 Context7**：`python-docx` / `docxtpl` / `lxml` 的最新 API 与中文文档处理。

3. **搭项目骨架**：
   ```bash
   cd "E:/projects/论文书写模版格式规范软件"
   uv init --package paperguard
   # 建目录结构（按 §4）
   # 写第一个 commit
   ```

---

## 13. 与 format-thesis-docx 的核心差异（v1.1 新增）

> 详细对比见 `docs/format-thesis-docx-review.md`；本节只列关键差异点。

### 13.1 本项目优于参考项目的 5 点

| # | 限制（参考项目） | 本项目做法 |
|---|----------------|------------|
| 1 | 修复只覆盖 style + section，段落级不修 | 通过 IR + scope 表达式定位具体段落 |
| 2 | 没有"修复后再 audit"闭环 | Sprint 4.5：fix 完成后自动 check |
| 3 | 规则全靠模板推导，国标要用户整理 | 内置 GB/T 7713 + GB/T 7714 等 30+ 通用规则 |
| 4 | 报告只有 CSV + MD | JSON + HTML + SARIF + redline docx |
| 5 | 风险都堆给 AI（规则改 JSON 难） | 严格 separation：人写 YAML，机器跑规则 |

### 13.2 本项目**直接借鉴**的 7 件事

| # | 借鉴点 | 落地位置 |
|---|--------|---------|
| 1 | 点分属性名（`font.eastAsia`） | §3.1 IR 修订 |
| 2 | Requirement 实体结构 | §3.2 Finding 模型 |
| 3 | `ensure(parent, tag)` 模式 | Sprint 5 mutator |
| 4 | FORMAT_WORDS 启发式 | Sprint 2.5 `extract` |
| 5 | 三阶段管线 + 每阶段落盘 | §5 关键工作流 |
| 6 | 修复结果 4 状态分类 | §3.2 Finding.status |
| 7 | AI Skill 打包 | Sprint 6 + V1 路线 |

### 13.3 本项目**必须避免**的 3 件事

| # | 坑 | 对策 |
|---|----|------|
| 1 | "OOXML 字符串精确比对"脆弱 | 解析到 IR 后用类型化比较；`expected: 1.5` 允许多种等价值 |
| 2 | "修复后不验证" | Sprint 4.5：fix 完成后自动 check |
| 3 | "auto_applicable 标志散落" | `fix` 字段显式声明能做什么（`fix.action: set/insert/delete/wrap`） |

---

**版本**：v1.1
**最后更新**：2026-06-05
**配套文档**：
- `docs/research-report.md`（5 份 GitHub 调研综合）
- `docs/format-thesis-docx-review.md`（945 行源码精读）
- `references/format-thesis-docx/`（参考项目源码，945 行）
**下次评审**：MVP Sprint 3 结束（30 条规则实现后）
