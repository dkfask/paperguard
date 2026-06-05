# wksudud/format-thesis-docx 源码精读与对比分析

> **目的**：在动手写第一行代码前，把和本项目目标最接近的参考实现读透，验证 `docs/design-proposal.md` 的假设，发现需要调整的地方。
> **方法**：克隆仓库到 `E:/projects/_review/format-thesis-docx`，逐行读 README / SKILL.md / checklist.md / 3 个核心脚本（共 945 行，含代码 672 行）。
> **结论摘要**：**本设计的整体方向与参考项目高度一致**；参考项目是"AI 编辑 JSON"派，本项目是"人写 YAML"派；本项目在"修复范围 / 报告形态 / 国标内置 / 修复后验证"四点应明确超越参考项目。

---

## 1. 参考项目速览

### 1.1 一句话定位
> 提取模板 docx 的格式要求 → 审计论文 docx → 输出带审计报告 + 修改后副本；**纯 stdlib、零依赖、不破坏原文**。

### 1.2 工作流（严格 3 阶段串行）

```
[template.docx]
       │
       ▼
extract_requirements.py ──► requirements.json + requirements.md
                                          │
                                          ▼
[paper.docx] ──► audit_format.py ──► format_audit.csv + format_audit.md
                                          │
                                          ▼
                       apply_format.py ──► paper_formatted.docx
                                          │
                                          ▼
                                       changes.csv + changes.md
```

**每阶段都落盘**——任何阶段都可以独立重跑，是真正的"无状态、可重放"流水线。

### 1.3 关键设计决策

| # | 决策 | 体现位置 |
|---|------|---------|
| 1 | **零依赖**（纯 Python 3 stdlib） | 3 个脚本无 import 第三方 |
| 2 | **不引入 IR**——直接拿 OOXML 标签当事实 | `run_props` / `para_props` / `section_props` 直接返回 `{font.ascii: ..., font.eastAsia: ...}` |
| 3 | **每条规则有 `evidence`** | 从模板抽取时附带 source_part + 文本片段 |
| 4 | **FORMAT_WORDS 启发式** | 用正则匹配中英文"格式要求"关键词，把"显式说明文字"抽成"待人工补充的规则" |
| 5 | **修复只覆盖 `style` + `section`** | 最高 ROI、最安全；其它标 manual |
| 6 | **不破坏原文** | `shutil.copy2` 先复制，再 unzip / 修改 / zip |
| 7 | **修复结果 4 状态分类** | changed / already-set / skipped / pass |
| 8 | **打包为 AI Skill** | `SKILL.md` + `agents/openai.yaml`，可用 Claude/Codex 调用 |

---

## 2. 数据模型（参考项目的 "Requirement" 实体）

```json
{
  "id": "R0001",
  "category": "style | section | paragraph-example | table | numbering | explicit-instruction | document",
  "target": "style:BodyText | section:1 | paragraph-example:word/document.xml:12 | ...",
  "property": "font.eastAsia | paragraph.spacing.line | section.margin.top | ...",
  "expected": "宋体 | 360 | 1440",
  "source_part": "word/styles.xml",
  "evidence": "paragraph style Body Text (BodyText)",
  "auto_applicable": true
}
```

**这是非常聪明的设计**——`category` + `target` + `property` 三个字段组合出**可机器定位的指针**，连修复引擎都不用解析 JSON Schema。

### 2.1 category 分类法
- `style` — styles.xml 里的样式定义（**唯一可自动修复的样式类**）
- `section` — 页面/页边距/纸张（**唯一可自动修复的节类**）
- `paragraph-example` — 模板段落里"示范用"的格式（**只能人工对照**）
- `table` / `numbering` — 表格/编号（**只能人工对照**）
- `explicit-instruction` — 模板正文里"用 XX 字体"这种说明文字（**必须 AI/人工补充**）
- `document` — 文档整体属性

### 2.2 `auto_applicable` 标志的本质
**这是对"规则无法精确比对"的承认**——很多规则因为 OOXML 表达不唯一（多种写法等价），所以**显式标记哪些可以自动修复、哪些不能**。这比"假装都能自动修"诚实得多。

---

## 3. 关键代码片段拆解

### 3.1 属性提取（参考项目最常用的模式）

```python
def run_props(rpr: ET.Element | None) -> dict[str, str]:
    """从 <w:rPr> 节点抽取字体字号等属性。"""
    if rpr is None:
        return {}
    fonts = rpr.find("w:rFonts", NS)
    props = {
        "font.ascii": attr(fonts, "w:ascii"),
        "font.eastAsia": attr(fonts, "w:eastAsia"),
        "font.hAnsi": attr(fonts, "w:hAnsi"),
        "font.cs": attr(fonts, "w:cs"),
        "font.size_half_points": attr(rpr.find("w:sz", NS), "w:val"),
        "font.bold": "true" if rpr.find("w:b", NS) is not None else None,
        # ...
    }
    return {k: v for k, v in props.items() if v not in (None, "")}
```

**关键发现**：
- 字号是 `**半磅**`（half-points）——"24" 表示 12pt，"22" 表示 11pt。这是 Word 内部表示。
- 中文字体是 `eastAsia`，西文是 `ascii`——**这个分离必须保留**，是中文论文最核心的格式点。
- 抽不到的属性**不写 key**（dict 过滤掉 None/""），让"该字段没设"和"该字段为空字符串"有明确区分。

### 3.2 修复引擎（核心 30 行）

```python
def set_run_property(rpr: ET.Element, prop: str, value: str) -> tuple[str, str] | None:
    """设置 run 的属性，返回 (before, after)。"""
    if prop.startswith("font."):
        if prop in {"font.ascii", "font.eastAsia", "font.hAnsi", "font.cs"}:
            fonts = ensure(rpr, "w:rFonts")          # 确保 rFonts 存在
            key = "w:" + prop.split(".", 1)[1]
            before = get_attr(fonts, key)
            set_attr(fonts, key, value)
            return before, value                       # 关键：返回 before/after
        # ... 其他 mapping
    return None
```

**关键发现**：
- 用 `ensure(parent, tag)` 模式——找不到子元素就建一个，避免覆盖用户已有设置。
- **返回 (before, after)** 是审计需要的——changes.csv 全靠它。
- `None` 返回值表示"这个 prop 我不识别，跳过"。

### 3.3 修复主循环

```python
for req in requirements:
    status_from_audit = audit_status.get(req["id"], "")
    if status_from_audit == "pass":
        continue                                       # 已经合规，不动
    if status_from_audit in {"manual", "not applicable"}:
        changes.append(...skipped...)
        continue                                       # 人工项，跳过
    result = None
    if req["category"] == "style" and req["target"].startswith("style:"):
        # ... 修样式
    elif req["category"] == "section" and req["target"].startswith("section:"):
        # ... 修节
    if result is None:
        changes.append(...skipped...)
    else:
        before, after = result
        status = "changed" if before != after else "already-set"
        changes.append(change_row(req, before, after, status, location))
```

**关键发现**：
- 修复前**先看 audit 状态**——已经合规的不动；人工项的不动；只修"明确 fail 且可自动修"的。
- **没有"修复后再 audit"的闭环**——这是参考项目的局限，本项目应该补上。

---

## 4. 与本设计（design-proposal.md）的对比矩阵

| 维度 | 参考项目 | 本设计 (PaperGuard) | 一致 / 差异 / 借鉴方向 |
|------|----------|---------------------|------------------------|
| **阶段数** | 3 个独立串行脚本 | 1 个 CLI 多子命令 | 差异 → **本设计更现代** |
| **依赖** | 0（纯 stdlib） | python-docx + lxml + pyyaml + json-logic + jmespath | 差异 → **本设计选易用而非零依赖**（合理） |
| **规则来源** | 模板自动抽取 + AI 补 JSON | YAML 规则包（GB/T 7713 内置） | 差异 → **本设计让人写 YAML，更系统** |
| **修复范围** | 只 style + section（段落级不修） | 段落级 + run 级 + 样式级 + 节级 | **本设计更强** |
| **修复后验证** | 无 | 应当加（apply 后自动 audit） | **本设计应补** |
| **规则可扩展** | AI 编辑 requirements.json | YAML pack + Python 插件 entry_points | **本设计更系统** |
| **用户门槛** | 中（懂三阶段 + 改 JSON） | 低（CLI 一个命令） | **本设计更友好** |
| **跨平台** | 全平台（零依赖） | 全平台（pywin32 仅 Windows，可选） | **本设计更受限但可控** |
| **输出报告** | CSV + MD | JSON + HTML + SARIF + redline docx | **本设计更丰富** |
| **不破坏原文** | ✅ 强约束 | ✅ 强约束 | ✅ 一致 |
| **每条规则带 evidence** | ✅ source_part + text | ✅ IR.source_path | ✅ 一致 |
| **自动/手动分类** | status: pass/fail/manual/skipped/changed | severity: error/warning/info + fix: 可空 | ✅ 等价 |
| **点分属性名** | `font.eastAsia` `paragraph.spacing.line` | IR 用 PascalCase `Run.font_eastasia` | **本设计应改回点分**（参考项目） |
| **FORMAT_WORDS 启发式** | ✅ 内置 | ❌ 未规划 | **本设计应抄** |
| **AI Skill 化** | ✅ 已有 SKILL.md + openai.yaml | V1 规划 | 一致 |

---

## 5. 可借鉴的 7 件事（直接抄）

### 5.1 点分属性名约定
参考项目用 `font.eastAsia` / `paragraph.spacing.line`，**比 PascalCase 更适合 YAML 表达**。
- **行动**：把 IR 的 `Run.font_eastasia` 改为序列化形式 `font.eastAsia`（属性名按 OOXML 原始 key）。
- **位置**：`docs/design-proposal.md` §3.1

### 5.2 Requirement 实体结构
参考项目的 `{id, category, target, property, expected, source_part, evidence, auto_applicable}` 设计已经非常成熟。
- **行动**：本项目的 `Finding` dataclass 直接对标，**额外加 `fix` 字段**（参考项目用 `auto_applicable` 标志，实际 fix 逻辑散在代码里）。
- **位置**：`docs/design-proposal.md` §3.2

### 5.3 "ensure(parent, tag)" 模式
不覆盖用户已有设置，找不到子元素才建。
- **行动**：本项目的 `engine/mutator.py` 写回 docx 时也用这个模式。
- **位置**：`docs/design-proposal.md` §4.1（mutator 模块）

### 5.4 FORMAT_WORDS 启发式
参考项目的核心"魔法"——能从模板正文里抽出"显式说明"的规则。
```python
FORMAT_WORDS = re.compile(
    r"(font|size|spacing|margin|header|footer|page|caption|reference|"
    r"标题|字体|字号|行距|缩进|页眉|页脚|页码|格式|要求|示例|不得|应|须|按|"
    r"目录|图|表|公式|参考文献|摘要|关键词|附录)"
)
```
- **行动**：本项目 V1 加 `paperguard extract` 子命令，把"AI 编辑 JSON"这个步骤产品化。
- **位置**：原 MVP Sprint 2.5（**新增**）

### 5.5 三阶段管线 + 每阶段落盘
每阶段输出独立文件（json/csv/md/docx），可独立重跑。
- **行动**：本项目每个子命令也输出文件（`requirements.json` / `findings.json` / `paper.fixed.docx` / `changes.csv`）。
- **位置**：`docs/design-proposal.md` §5（关键工作流）

### 5.6 修复结果 4 状态分类
`changed` / `already-set` / `skipped` / `pass`——清晰可审计。
- **行动**：本项目 `Finding.status` 用这 4 个值。
- **位置**：`docs/design-proposal.md` §3.2

### 5.7 AI Skill 打包
`SKILL.md` + `agents/openai.yaml` 是 2024+ 范式。
- **行动**：本项目 V1 加 `SKILL.md`（已规划），还应加 Codex / Gemini 的兼容 skill 描述。
- **位置**：`docs/design-proposal.md` §8

---

## 6. 必须超越的 5 件事（本项目优于参考项目）

| # | 限制 | 本项目做法 |
|---|------|-----------|
| 1 | 修复只覆盖 style + section，**段落级不修**（"没法定位"） | 通过 IR + 表达式（JMESPath / scope）定位具体段落 |
| 2 | **没有"修复后再 audit"闭环** | apply 后自动再跑 audit，给"是否合规"明确信号 |
| 3 | **规则全靠模板推导**——国标等通用规范要用户自己整理 | 内置 GB/T 7713 + GB/T 7714 等 30+ 通用规则 |
| 4 | **报告只有 CSV + MD**——CI 集成麻烦 | 加 SARIF reporter + HTML reporter + redline docx |
| 5 | **没区分"格式修复"和"内容生成"**——风险都堆给 AI | 严格 separation of concerns：本项目**只做格式**，不做内容生成 |

---

## 7. 必须避免的 3 件事（参考项目踩过的坑）

### 7.1 "OOXML 字符串精确比对"是脆弱的
参考项目判定 `pass` 的方式是 `actual == req["expected"]` 字符串相等。
- **坑**：同一份样式可能用 `<w:sz w:val="24"/>` 或 `<w:sz w:val="24.0"/>` 表达，比对失败。
- **本项目做法**：解析到 IR 后用**类型化比较**（数值 vs 字符串分开），`expected: 1.5` 就允许 `"1.5"`、`"360"`（半磅）、`"1.5倍"` 三种等价值。

### 7.2 "修复后不验证"
参考项目 apply 完就结束，**用户拿到 paper_formatted.docx 不一定真的合规**。
- **本项目做法**：在 `paperguard fix` 的最后自动跑一次 `check`，输出"修复后还有 N 个问题"清单。

### 7.3 "auto_applicable 标志散落"
参考项目用 `auto_applicable: true/false` 标志决定能不能修，**但修复逻辑是在代码里硬编码的 `if category == 'style'`**。
- **本项目做法**：让 `fix` 字段**显式声明**能做什么（`fix.action: set/insert/delete/wrap`），**让数据驱动代码**。

---

## 8. 对 MVP 路线的影响

### 8.1 原 MVP Sprint 1-6 全部保留，但加 2 个新 Sprint：

| Sprint | 原计划 | 调整 |
|--------|--------|------|
| **Sprint 1：骨架** | 1 周 | 不变 |
| **Sprint 2：规则引擎** | 1 周 | **+0.5 周**：增加"点分属性名约定"；IR 序列化形式用 `font.eastAsia` 而非 `font_eastasia` |
| **🆕 Sprint 2.5：模板自动抽取** | 0.5 周 | **抄** FORMAT_WORDS 启发式 + `extract_requirements.py` 逻辑；新增 `paperguard extract` 子命令 |
| **Sprint 3：GB/T 7713 规范包** | 1 周 | 不变 |
| **Sprint 4：报告 + CLI** | 1 周 | 不变 |
| **🆕 Sprint 4.5：修复后验证** | 0.5 周 | `paperguard fix` 完成后自动跑 `check` |
| **Sprint 5：修复闭环** | 1 周 | **+0.5 周**：用参考项目的 `set_run_property` / `set_para_property` 思路重写 mutator；用 `ensure()` 模式避免覆盖 |
| **Sprint 6：质量 + 文档** | 1 周 | 不变 |

**总时间**：6 周 → 7.5 周（多 1.5 周，**性价比极高**——Sprint 2.5 的"自动抽取"是参考项目的核心价值，4.5 的"修复后验证"是参考项目的最大短板）

### 8.2 IR 数据模型的具体修改

**原设计**（design-proposal.md §3.1）：
```python
@dataclass(frozen=True)
class Run:
    font_eastasia: str | None
    font_ascii: str | None
    font_size_pt: float | None
```

**修改后**（借鉴参考项目）：
```python
@dataclass(frozen=True)
class Run:
    raw_props: dict[str, str]  # 原始 OOXML 属性，键名用点分（如 "font.eastAsia"）
    
    @property
    def font_eastasia(self) -> str | None:
        return self.raw_props.get("font.eastAsia")
    
    @property
    def font_size_pt(self) -> float | None:
        half = self.raw_props.get("font.size_half_points")
        return int(half) / 2 if half else None
```

**理由**：
1. 跟参考项目的命名约定保持一致（`font.eastAsia`）
2. 保留"原始属性字典"以便写回 docx 时不丢字段
3. 类型化属性（`font_size_pt`）是便利接口，用于规则表达式

---

## 9. 立刻可做的 3 个落地动作

```bash
# 1. 把参考项目复制到本项目作为早期参考骨架
mkdir -p "E:/projects/论文书写模版格式规范软件/references"
cp -r "E:/projects/_review/format-thesis-docx" "E:/projects/论文书写模版格式规范软件/references/format-thesis-docx"

# 2. 用其 fixture 思路搭测试夹具
mkdir -p "E:/projects/论文书写模版格式规范软件/tests/fixtures/templates"
mkdir -p "E:/projects/论文书写模版格式规范软件/tests/fixtures/papers"
# 找一份真实学校模板 + 一份学生论文，作为首批 fixture

# 3. 写第一个 demo：直接包装参考项目为 paperguard v0
# 路径：paperguard/v0/wrap_format_thesis_docx.py
# 目的：让用户先体验"extract → audit → fix"完整流程，再逐步替换为 Python 重写版本
```

---

## 10. 一句话总结

> **参考项目验证了"三阶段管线 + 点分属性名 + FORMAT_WORDS 启发式 + 不破坏原文"的设计是对的；本项目在"修复范围 / 内置国标 / 修复后验证 / 报告形态"四点应明确超越，并在 V1 把"AI 编辑 JSON"升级为"人写 YAML + 内置 GB/T 7713"。**

---

**版本**：v1.0
**配套文档**：`docs/research-report.md` / `docs/design-proposal.md`
**下一步**：修订 `docs/design-proposal.md` 的 IR 数据模型（§3.1）和 MVP Sprint 计划（§7），把本报告的发现落地。
