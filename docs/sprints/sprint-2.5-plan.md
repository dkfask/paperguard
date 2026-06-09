# Sprint 2.5 — 模板自动抽取（FORMAT_WORDS 启发式）PLAN

> **状态**：v1.0 草案（2026-06-09，多代理 workflow：planner + Explore + Context7）
> **前置**：Sprint 2 完成（commit `b22c851`，YAML engine + 25 规则）+ Sprint 2 follow-up 已记录 3 个真 bug
> **后续**：实施前需用户拍板第 8 节 6 个开放问题
> **目标**：把"用户上传 docx 模板 → 自动抽取格式约束 → 生成 YAML pack 骨架"做出来。输出与 Sprint 2 loader.py 兼容的 YAML pack 骨架，作为 Sprint 3 规则包的种子。

---

## 1. Goal

Sprint 2.5 在 `app/core/extract/` 子包实现模板自动抽取：实现 `Requirement` frozen dataclass（含 7 类别）、FORMAT_WORDS 中英文 28 关键词正则（13 英 + 15 中）、`extract_template(docx_path) -> list[Requirement]` 主入口、`requirements_to_pack(reqs, pack_id, name) -> RulePack` 转换器（与 Sprint 2 loader.py 输出完全兼容）、**混合 python-docx + lxml 抽取**（6 类高层 + 4 类 lxml 兜底，**含中文字体 eastAsia 必走 lxml**）、5+ fixture 模板（自制 + 国标公开）、端到端冒烟脚本，全程 `app/core/` 零 PyQt import 硬约束保持。生成的 YAML pack 骨架可直接被 `load_pack()` 加载并跑通 engine/visitor 全流程。

---

## 2. Architecture Decision Records

### ADR-1：Requirement 7 类别全用 vs MVP 子集

- **状态**：**待用户拍板**
- **Context**：参考项目 7 个 category；设计文档 §3.2 明确 `style / section / paragraph-example / table / numbering / explicit-instruction / document`
- **Options**：
  - **A（推荐）**：7 类别**全实现**
  - **B**：MVP 子集 4 类（style / section / paragraph-example / explicit-instruction）
  - **C**：仅 2 类（style / explicit-instruction）
- **推荐**：**A**，理由：设计文档已明确；`requirements_to_pack` 按 category 分类，扩展容易；参考项目就是 7 类无额外风险

### ADR-2：输出格式

- **状态**：**待用户拍板**
- **Options**：
  - **A（推荐）**：同时输出 `requirements.json`（审计中间格式）+ `*.yaml` pack 骨架
  - **B**：仅 YAML pack 骨架
  - **C**：仅 JSON
- **推荐**：**A**，JSON 便于调试 + AI agent 消费

### ADR-3：requirements_to_pack 映射策略

- **状态**：**待用户拍板**
- **Options**：
  - **A（推荐）**：生成**可直接使用的 YAML pack**（含 when/fix）
  - **B**：仅骨架（fix=null）
  - **C**：半自动（when 有 fix 无）
- **推荐**：**A**，真正可用才有价值；B1 follow-up 由 `to_pack` 拆 before/after 规避

### ADR-4：FORMAT_WORDS 词典管理

- **状态**：**待用户拍板**
- **Options**：
  - **A（推荐）**：硬编码 `format_words.py` 28 个中英文正则
  - **B**：YAML 配置 `format_words.yaml`
  - **C**：jieba 分词
- **推荐**：**A**，MVP 28 词够；V1+ 配置化预埋升级路径

### ADR-5：extract CLI 范围

- **状态**：**待用户拍板**
- **Options**：
  - **A（推荐）**：`app/core/extract/` + `scripts/sprint2_5_smoke.py`；CLI Sprint 4 才加
  - **B**：同期加 `paperguard extract` CLI
- **推荐**：**A**，职责清晰，Sprint 4 集中做 CLI

### ADR-6：fixture 模板法律风险

- **状态**：**待用户拍板**
- **Context**：**已查证参考项目 LICENSE 是 MIT**（不是 BSD），可商业使用 + 重写。但清华/浙大模板可能有 EULA 限制
- **Options**：
  - **A（推荐）**：自制最小可用模板 + GB/T 7713 国标官方模板
  - **B**：清华/浙大模板
  - **C**：全部公开模板
- **推荐**：**A**，零版权风险；自制脚本生成可复制

---

## 3. File-Level Deliverables

### 3.1 `app/core/extract/`（新子包）

#### `app/core/extract/__init__.py`
- 导出 `extract_template` / `requirements_to_pack` / `Requirement`

#### `app/core/extract/requirement.py`（~50 LOC）
```python
@dataclass(frozen=True)
class Requirement:
    id: str                  # "R0001" 4 位序号
    category: str            # 7 种之一
    target: str              # "style:Normal" / "paragraph-example:word/document.xml:3"
    property: str            # 点分形式 "font.eastAsia"（与 IR v1.1 一致）
    expected: str            # 期望值，强制 str
    source_part: str         # "word/styles.xml"
    evidence: str            # 证据文本，前 300 字符
    auto_applicable: bool = True
```
Tests: `test_extract_requirement.py` ~8

#### `app/core/extract/xml_utils.py`（**新，关键工具**，~30 LOC）
```python
NS = {
    "w":   "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "r":   "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
def qn(tag: str) -> str: ...        # "w:rFonts" → "{http://...}rFonts"
def attr(el, name: str) -> str|None: ...
def load_part(zf, name: str): ...  # 单个 OOXML part lxml 加载（None 安全）
```
**借鉴**：参考项目 `extract_requirements.py:27-33` qn/attr 模式（Explore 推荐 #1）

#### `app/core/extract/format_words.py`（~30 LOC）
**28 词词典**（13 英文 + 15 中文，参考项目 line 20-24）：
```python
FORMAT_WORDS = re.compile(
    r"(font|size|spacing|margin|header|footer|page|caption|reference|"
    r"format|requirement|"  # +2 词扩展
    r"标题|字体|字号|行距|缩进|页眉|页脚|页码|格式|要求|示例|不得|应|须|按|"
    r"目录|图|表|公式|参考文献|摘要|关键词|附录)"
)
def match_format_words(text: str) -> bool: ...
```
Tests: `test_extract_format_words.py` ~10

#### `app/core/extract/run_props.py` / `para_props.py` / `section_props.py`（每个 ~40 LOC）
OOXML → 点分 property dict（参考项目 line 63-128 的完整 mapping）。
**关键陷阱**（Context7 发现）：python-docx 的 `font.name` **不暴露 `w:eastAsia` 中文字体名**，必须用 lxml 走 `<w:rFonts w:eastAsia="宋体"/>` 直拆。Sprint 2.5 在 `run_props.py` 用 lxml，不用 python-docx 高层。

#### `app/core/extract/extractor.py`（~250 LOC，**主入口**）
- `extract_template(docx_path) -> list[Requirement]`
- 内部 7 个 `_extract_*` 子函数
- **混合策略**：
  - python-docx 走 5 类：`sections` / `tables` / `core_properties` / `styles` 高层属性 / `paragraphs` 样本
  - lxml 走 4 类：`numbering.xml` / `comments.xml` / `settings.xml` / **eastAsia 字体**（中文字体必走）
- `read_parts(zf)` 借鉴参考项目 line 49-60 批量读取模式
- 主流程：解压 docx → read_parts → 7 类抽取 → 去重 → 返回 Requirement[]

Tests: `test_extract_extractor.py` ~15

#### `app/core/extract/to_pack.py`（~130 LOC，**B1 规避关键**）
`requirements_to_pack(reqs, pack_id, name, version="0.1.0") -> RulePack`

**Category → Rule 映射**：
| Requirement category | scope | when | fix |
|---|---|---|---|
| style | `"paragraphs[?style_id == 'X']"` | `{"!=":[{var:"runs.0.font_eastasia"},"宋体"]}` | `Fix("set", "run.font_eastasia", "宋体")` |
| section | `"sections[*]"` | match attr | `Fix("set", "section.margin_top_cm", 2.54)` |
| paragraph-example | 同 style | 同 | 同 |
| table | `"tables[*]"` | match | possibly `fix=None`（depending on category） |
| numbering | `"paragraphs[?@.numbering_id == 'X']"` | match | `fix=None` MVP |
| explicit-instruction | `"paragraphs[*]"` 含关键词 | description only | `fix=None` 强制 |
| document | `"metadata"` | match | varies |

**B1 follow-up 规避**：当 `property == "paragraph.spacing.before"` 或 `"paragraph.spacing.after"` 时，**强制拆成两条独立 Rule**（`PG-EXTRACT-SPACE-BEFORE` / `PG-EXTRACT-SPACE-AFTER`），各自 fix 只设一个字段。避免 Sprint 2 已知 bug 再次出现。

**半磅 → pt 转换**：`font.size_half_points` 期望值要 `/2.0`（如 "24" → 12.0）

Tests: `test_extract_to_pack.py` ~12（含 1 个 B1 规避专门测试）

### 3.2 `scripts/generate_fixtures.py` + `tests/fixtures/templates/`

5 个自制 docx fixture（用 python-docx 程序生成，可复制）：
- `minimal.docx`（2 页）
- `gb7713-official.docx`（国标官方公开内容）
- `tsinghua-style.docx`（自制模拟，非真实模板）
- `zhejiang-style.docx`（同上）
- `wuhan-style.docx`（同上）

### 3.3 `scripts/sprint2_5_smoke.py`

端到端冒烟：minimal.docx → extract_template → requirements_to_pack → validate_docx → assert findings > 0。

### 3.4 测试一览（~81 tests）

| 文件 | tests |
|------|------|
| test_extract_requirement.py | 8 |
| test_extract_format_words.py | 10 |
| test_extract_xml_utils.py | 5 |
| test_extract_run_props.py | 8 |
| test_extract_para_props.py | 10 |
| test_extract_section_props.py | 6 |
| test_extract_extractor.py | 15 |
| test_extract_to_pack.py | 12 |
| test_extract_template_fixtures.py | 5 |
| test_extract_e2e.py | 3 |
| **小计** | **~82** |

### 3.5 requirements.txt

**不改**。仅用标准库（zipfile / re / pathlib）+ Sprint 2 已引入的 lxml / pyyaml / pydantic / json-logic / jmespath / python-docx。

---

## 4. Test Strategy

### 4.1 TDD 顺序

1. test_extract_requirement (8) → requirement.py
2. test_extract_format_words (10) → format_words.py
3. test_extract_xml_utils (5) → xml_utils.py
4. test_extract_run_props (8) → run_props.py
5. test_extract_para_props (10) → para_props.py
6. test_extract_section_props (6) → section_props.py
7. test_extract_extractor (15) → extractor.py
8. test_extract_to_pack (12) → to_pack.py
9. generate_fixtures.py 生成 5 个 docx
10. test_extract_template_fixtures (5) → fixture 验证
11. test_extract_e2e (3) → 全流程
12. sprint2_5_smoke.py 端到端冒烟

### 4.2 覆盖率目标

所有 extract 子模块 ≥ 85%。

### 4.3 总测试数预期

204 现有 + 82 新增 = **~286 tests** 全绿。

---

## 5. Commit Strategy（14 atomic commits）

| # | Commit | Scope |
|---|--------|-------|
| 1 | `chore(sprint-2.5): scripts/generate_fixtures.py + 5 fixture docx` | chore |
| 2 | `test(sprint-2.5): RED — Requirement frozen dataclass 8 tests` | test |
| 3 | `feat(sprint-2.5): Requirement dataclass + 7 category` | feat |
| 4 | `test(sprint-2.5): RED — FORMAT_WORDS 28 词正则 10 tests` | test |
| 5 | `feat(sprint-2.5): FORMAT_WORDS + match_format_words` | feat |
| 6 | `test(sprint-2.5): RED — xml_utils qn/attr/load_part 5 tests` | test |
| 7 | `feat(sprint-2.5): xml_utils（借鉴参考项目 qn/attr）` | feat |
| 8 | `test(sprint-2.5): RED — run/para/section_props 24 tests` | test |
| 9 | `feat(sprint-2.5): run_props + para_props + section_props（含中文字体 lxml）` | feat |
| 10 | `test(sprint-2.5): RED — extract_template 15 tests` | test |
| 11 | `feat(sprint-2.5): extract_template 主入口 + 7 抽取器（混合 python-docx + lxml）` | feat |
| 12 | `test(sprint-2.5): RED — requirements_to_pack 12 tests（含 B1 规避）` | test |
| 13 | `feat(sprint-2.5): requirements_to_pack（兼容 Sprint 2 loader，B1 规避）` | feat |
| 14 | `test(sprint-2.5): integration + smoke + grep app/core/ 无 PyQt + HANDOFF 更新` | test+docs |

---

## 6. Risks & Mitigations

| # | 风险 | 缓解 |
|---|------|------|
| **B1 follow-up** | `to_pack` 生成 PARAGRAPH-SPACING 时 fix 不全 | 在 `to_pack.py` 强制拆 before/after 为两条规则 |
| **模板版权** | 清华/浙大模板有 EULA | ADR-6 A：全部自制 + 国标公开 |
| **python-docx 不暴露 eastAsia 中文字体** | 抽取中文字体规则失败 | 用 lxml 走 `<w:rFonts w:eastAsia>` 直拆（Context7 调研确认） |
| **python-docx 不暴露 numbering.xml** | numbering category 失败 | lxml + zipfile 直拆 word/numbering.xml |
| **FORMAT_WORDS 词典维护** | 长期负担 | ADR-4 A：MVP 28 词够；ADR 预埋配置化 |
| **YAML pack 过大** | 100+ 规则难维护 | to_pack 对相同 property+expected 去重 |
| **B2 follow-up 泄漏** | extract 用 dict 不受影响 | 隔离：smoke 跑 validate_docx 用新 IR |

---

## 7. Definition of Done

- [ ] `pytest tests/` 全绿（~286 tests）
- [ ] `python scripts/sprint2_5_smoke.py` 端到端通过
- [ ] `grep -r "PyQt" app/core/extract/` 仅 docstring 注释（零 import）
- [ ] 生成的 YAML pack 可被 `load_pack()` 加载
- [ ] `requirements_to_pack` 输出 RulePack 可被 `visit_document` 消费 ≥ 1 Finding
- [ ] **B1 规避**：模板含 space_before/after 时生成两条独立规则
- [ ] **中文字体提取**：`extract_template` 对含中文字体的 fixture 抽出 `font.eastAsia` Requirement
- [ ] Coverage ≥ 85%
- [ ] HANDOFF.md 标记 Sprint 2.5 完成
- [ ] Push 到 GitHub main

---

## 8. Open Questions for User（**已于 2026-06-09 全部拍板 A 推荐**）

| # | 问题 | **用户决策** | 影响 |
|---|------|--------------|------|
| **1** | Requirement 类别（ADR-1） | **✅ A：7 类别全实现** | extractor.py ~250 LOC + 7 个 _extract_* 子函数 |
| **2** | 输出格式（ADR-2） | **✅ A：JSON + YAML 双输出** | extract_template 返回 list[Requirement]，scripts 同时写 .json + .yaml |
| **3** | to_pack 映射策略（ADR-3） | **✅ A：生成可直接用的 YAML（含 when/fix）** | to_pack.py 实现完整 mapping，B1 内部规避（拆 before/after） |
| **4** | FORMAT_WORDS 词典（ADR-4） | **✅ A：硬编码 28 词** | format_words.py 30 LOC，V1+ 可配置化（ADR 预埋） |
| **5** | extract CLI（ADR-5） | **✅ A：仅函数，CLI 延 Sprint 4** | 无 cli/ 子包；scripts/sprint2_5_smoke.py 验证端到端 |
| **6** | fixture 模板（ADR-6） | **✅ A：自制 + 国标公开模板** | scripts/generate_fixtures.py 生成 5 个 docx；零版权风险 |

**决策一致性**：全部 A 推荐 → 实施按 §3-7 plan 原版执行，无调整。

---

## 9. Decisions（已 decided，不需用户拍板）

| 决策 | 结论 | 理由 |
|------|------|------|
| 不修 Sprint 2 follow-up B1/B2/T0 | 仅在 to_pack 层规避 B1 | Sprint 3 之事 |
| 零 PyQt 依赖 | extract 仅用标准库 + Sprint 2 已有依赖 | CLAUDE.md 硬约束 |
| 不可变 Requirement | frozen dataclass | Python coding style |
| 点分 property 命名 | `font.eastAsia` 等 | IR v1.1 关键决策 |
| 生成的 YAML 兼容 loader | 同 RulePack/Rule/Fix dataclass | 全流程打通 |
| 借鉴方式 | 重写非复制 | 参考项目 MIT，可商业用但注明来源 |

---

## 10. 与参考项目（wksudud/format-thesis-docx）借鉴清单

> 已查证：参考项目 LICENSE 是 **MIT**（不是 BSD），可商业使用 + 重写。借鉴时在源码注释注明 "Based on format-thesis-docx (MIT, Copyright (c) 2026 manbo)" 即可。

| # | 参考位置 | Sprint 2.5 落地 | 借鉴方式 |
|---|------|------|------|
| 1 | `extract_requirements.py:27-33` qn/attr 工具 | `extract/xml_utils.py` | 直接移植（OOXML 标准技巧） |
| 2 | `extract_requirements.py:63-128` run/para/section_props mapping | `extract/{run,para,section}_props.py` | 重写保持命名一致 |
| 3 | `extract_requirements.py:131-143` add_item ID 生成 | `extract/requirement.py` | dataclass 化 |
| 4 | `extract_requirements.py:49-60` read_parts 批量读取 | `extract/extractor.py` | 重写（含 header/footer 动态发现） |
| 5 | `extract_requirements.py:20-24` FORMAT_WORDS 正则 | `extract/format_words.py` | 扩展 13 词 → 28 词 |

---

## 11. 附件：关键引用映射

| 引用 | 出处 |
|------|------|
| Sprint 2.5 原始计划 | `docs/design-proposal.md` §7 line 675-682 |
| 借鉴清单 | `docs/design-proposal.md` §13.2 #4 line 853 |
| 参考项目 extract_requirements.py | `references/format-thesis-docx/scripts/extract_requirements.py` |
| 参考项目 LICENSE | `references/format-thesis-docx/LICENSE`（MIT, Copyright (c) 2026 manbo） |
| Sprint 2 follow-up B1 bug | `docs/sprint-2-followup.md` §🔴 真 Bug B1 |
| Sprint 2 loader Rule dataclass | `app/core/engine/loader.py:77-94` |
| Sprint 2 validate_docx 入口 | `app/core/engine/__init__.py:22-26` |
| IR Finding + Fix | `app/core/ir/finding.py:15-47` |
| Sprint 2 plan 模板 | `docs/sprints/sprint-2-plan.md` |
| **python-docx eastAsia 不暴露**（关键陷阱） | Context7 调研：`style.font.name` 仅暴露 ascii，eastAsia 必走 lxml |
| **lxml 性能基线** | < 100ms 抽完整模板 7 类别（Context7 调研） |

---

**版本**：v1.0
**作者**：多代理 workflow（planner + Explore + Context7 docs-lookup）
**日期**：2026-06-09
**下次评审**：用户拍板第 8 节 6 个问题后实施
