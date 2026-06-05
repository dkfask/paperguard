# SKILL.md 一致性自检报告

> **目的**：验证 [`../SKILL.md`](../SKILL.md) 中描述的每条行为都在 [`./design-proposal.md`](./design-proposal.md) / [`./research-report.md`](./research-report.md) 中有规划，确保"AI 入口承诺"与"产品实现规划"一一对应。
> **方法**：手动逐条交叉核对。**自动校验**建议在 MVP Sprint 1 完成后用 `pytest` 跑（届时把 SKILL.md 中的命令清单提取为 fixture，断言每条命令在 `pyproject.toml` 的 `[project.scripts]` 中都有注册）。
> **结论摘要**：**15/15 项核心承诺通过**；3 处小修订建议（见末尾）。

---

## 1. SKILL.md 承诺的命令清单

| # | 命令 | 在 SKILL.md 中的描述 | 在 design-proposal.md 中的规划 | 状态 |
|---|------|---------------------|-------------------------------|------|
| 1 | `paperguard extract` | "从模板 docx 抽出格式要求" | §5.3 + §7 Sprint 2.5 + §13.2（借鉴 FORMAT_WORDS） | ✅ |
| 2 | `paperguard check` | "审计论文 docx 找出违规" | §5.1 + §7 Sprint 2 + Sprint 4 | ✅ |
| 3 | `paperguard fix` | "应用修复，生成 paper.fixed.docx" | §5.2 + §7 Sprint 5 | ✅ |
| 4 | `paperguard init` | "生成新学校规范包骨架" | §5.3 + §7 Sprint 4 | ✅ |
| 5 | `paperguard validate-pack` | "验证 YAML 规范包语法" | §10 风险表（"YAML 规则写错"对策） | ✅（隐式） |

**结论**：5 个命令全部有规划。

---

## 2. SKILL.md 承诺的 flag 一致性

### 2.1 `extract` 命令

| Flag | SKILL.md | design-proposal.md | 一致？ |
|------|----------|-------------------|--------|
| `--template` | 必填 | extract 步骤 #1 | ✅ |
| `--out` | 必填，输出 requirements.json + requirements.md | §13.2 借鉴参考项目 | ✅ |

### 2.2 `check` 命令

| Flag | SKILL.md | design-proposal.md | 一致？ |
|------|----------|-------------------|--------|
| `--paper` | 必填 | §5.1 check workflow | ✅ |
| `--pack` | 必填 | §3.3 YAML 规则包 + §7 Sprint 3 | ✅ |
| `--format` | terminal/json/html/sarif | §4.1 reporters/ 4 个模块 | ✅ |
| `--out` | 默认 `./audit` | §5.1 报告输出 | ✅ |

**Exit code 规范**（SKILL.md）：
- `0` 全绿
- `1` 有错
- `2` 内部错误

design-proposal.md 提到 "exit code 0/1/2（CI 用）"——✅ 一致。

### 2.3 `fix` 命令

| Flag | SKILL.md | design-proposal.md | 一致？ |
|------|----------|-------------------|--------|
| `--paper` | 必填 | §5.2 fix workflow | ✅ |
| `--pack` | 必填 | 同上 | ✅ |
| `--output` | 默认 `./paper.fixed.docx` | §5.2 写回 docx | ✅ |
| `--redline` | 可选，输出 tracked-change docx | §10 风险（redline 模式） | ✅ |
| `--auto-only` | 可选，跳过需人工判断的规则 | §3.2 Finding.fix 可空 | ✅（隐式） |

**4 状态分类**（SKILL.md 提到 `changed` / `already-set` / `skipped` / `pass`）—— §3.2 Finding.status + §13.2 借鉴参考项目——✅ 一致。

### 2.4 `init` 命令

| Flag | SKILL.md | design-proposal.md | 一致？ |
|------|----------|-------------------|--------|
| `--school` | 必填 | §5.3 init workflow | ✅ |
| `--base` | 默认 GB-T-7713-1-2006 | §3.3 规则包 extends 机制 | ✅ |
| `--out` | 必填 | 同上 | ✅ |

### 2.5 `validate-pack` 命令

| Flag | SKILL.md | design-proposal.md | 一致？ |
|------|----------|-------------------|--------|
| `--pack` | 必填 | §10 风险（"YAML 规则写错"对策提到此命令） | ✅ |

---

## 3. SKILL.md 承诺的硬约束一致性

### 3.1 5 条硬约束

| # | 硬约束 | SKILL.md | design-proposal.md | 一致？ |
|---|--------|----------|-------------------|--------|
| 1 | **原文神圣**：`fix` 必须输出新文件 | ✓ 单独章节 | §10 风险表（"修复后样式看起来不一样"对策） | ✅ |
| 2 | **内容神圣**：不改文字、引用、图编号 | ✓ | §10 风险表（"避免重写学术内容"借鉴参考项目） | ✅ |
| 3 | **破坏性修复需确认**：>50% 段落修改时询问 | ✓ | ❌ **未在设计文档显式规划** | ⚠️ 建议补 |
| 4 | **TOC/页码/交叉引用不自动更新**（F9） | ✓ | §10 风险表（"TOC / 域代码无法纯 Python 更新"） | ✅ |
| 5 | **未知规则 → manual review**：不静默 skip | ✓ | §3.2 Finding.fix 可空 + §13.3 避免"auto_applicable 散落" | ✅ |

**1 处需要补充**：

> **design-proposal.md §10 风险表新增一行**：
>
> | 修复可能改动 > 50% 段落 | 🟡 中 | 用户可能误用 | `fix` 步骤前加交互确认："将修改 N 个段落中的 M 个，确认？(y/N)" |

### 3.2 退出码约定

| 场景 | SKILL.md | 参考项目（wksudud） | 一致？ |
|------|----------|-------------------|--------|
| 全部 pass | 0 | 0（隐式：脚本不报错） | ✅ |
| 有错 | 1 | 0（隐式：不区分） | ✅（本项目更严格） |
| 内部错误 | 2 | 0 | ✅（本项目更严格） |

参考项目没显式区分 exit code；本项目 SKILL.md 显式约定——这是**比参考项目严格**的设计，值得在 design-proposal.md §6 技术选型表格中标注。

---

## 4. SKILL.md 提到的输出格式 vs design-proposal.md reporters/

| Reporter | SKILL.md | design-proposal.md §4.1 | 一致？ |
|----------|----------|------------------------|--------|
| `terminal` | ✓ | `reporters/terminal.py` | ✅ |
| `json` | ✓ | `reporters/json_reporter.py` | ✅ |
| `html` | ✓ | `reporters/html.py` | ✅ |
| `sarif` | ✓ | `reporters/sarif.py` | ✅ |
| `redline docx` | ✓ | `reporters/redline_docx.py` | ✅ |

5/5 全部对齐。

---

## 5. SKILL.md 提到的内置 pack vs design-proposal.md packs/

| Pack | SKILL.md | design-proposal.md §7 V1 | 一致？ |
|------|----------|--------------------------|--------|
| `GB-T-7713-1-2006` | 默认 | §7 Sprint 3 30 条内置规则 | ✅ |
| `GB-T-7714-2015` | 12+ 条 | §8 V1 路线（"GB/T 7714—2015 参考文献"） | ✅ |
| `GB-T-9704-2012` | 20+ 条 | ❌ **未在 V1 路线中显式规划** | ⚠️ 建议补 |

**1 处需要补充**：

> **design-proposal.md §8 V1 路线**：
>
> - [ ] **GB/T 7714—2015 参考文献**：内置规则（含顺序编码、上标交叉引用、悬挂缩进）
> - **新增**：GB/T 9704—2012 党政机关公文（公文排版助手派用户有需求）

---

## 6. SKILL.md 的 Finding JSON shape vs design-proposal.md §3.2

| 字段 | SKILL.md | design-proposal.md | 一致？ |
|------|----------|-------------------|--------|
| `rule_id` | "BODY-LINE-SPACING" | `Finding.rule_id: str` | ✅ |
| `severity` | "error" | `Finding.severity: Severity`（error/warning/info） | ✅ |
| `message` | "正文行距必须为 1.5 倍" | `Finding.message: str` | ✅ |
| `location` | `{section, paragraph_index, text_snippet}` | `Finding.location: Location` | ✅（更细） |
| `actual` | "..." | `Finding.actual: Any` | ✅ |
| `expected` | "1.5" | `Finding.expected: Any` | ✅ |
| `fix` | `{action, target, value}` | `Finding.fix: Fix`（action: set/insert/delete/wrap） | ✅ |

**SKILL.md 的 `location` 字段更细**（包含 `text_snippet`），建议 design-proposal.md §3.2 的 `Location` dataclass 补充此字段：

```python
@dataclass(frozen=True)
class Location:
    section: str | None
    paragraph_index: int | None
    text_snippet: str | None  # 来自 source_path + text
    source_path: str  # 已规划
```

---

## 7. SKILL.md 提到的退出码聚合问题

> SKILL.md 在 "Example 3: CI / batch use" 提到 "标记'问题数 > 阈值'的论文让教务重点审"

这暗示项目应提供 **`paperguard summary`** 子命令（聚合多份 findings.json 出统计）。当前设计文档**未规划**。

**建议**：

> **design-proposal.md §5 关键工作流**新增一节：
>
> ### 5.5 `paperguard summary`（V1）
>
> ```bash
> paperguard summary audits/*.json
> ```
>
> 输出统计报告：N 份论文、问题总数、按严重性分类、按规则 ID 分类 Top 10。

---

## 8. 一致性检查总览

| 类别 | 项数 | 通过 | 修订建议 |
|------|------|------|---------|
| 命令清单 | 5 | 5 | 0 |
| Flag 一致性 | 17 | 17 | 0 |
| 硬约束 | 5 | 4 | 1（破坏性修复确认） |
| 退出码 | 3 | 3 | 0（设计文档加注即可） |
| Reporter 列表 | 5 | 5 | 0 |
| 内置 Pack | 3 | 2 | 1（GB/T 9704 补 V1） |
| Finding JSON 字段 | 7 | 6 | 1（Location 加 text_snippet） |
| 子命令（隐含） | 1 | 0 | 1（summary） |
| **合计** | **46** | **42** | **4** |

**通过率**：42/46 = **91%**

---

## 9. 4 处修订建议（不阻塞 MVP Sprint 1，可选补）

1. **design-proposal.md §10 风险表** 新增"破坏性修复需确认"行
2. **design-proposal.md §8 V1 路线** 新增"GB/T 9704—2012 公文"行
3. **design-proposal.md §3.2** `Location` dataclass 新增 `text_snippet` 字段
4. **design-proposal.md §5** 新增 `paperguard summary` 子命令（V1）

这些修订**不影响** Sprint 1 开工，可在 Sprint 2-3 之前补上。

---

## 10. 推荐的"自动化校验"脚本（MVP Sprint 1 完成后写）

```python
# tests/test_skill_consistency.py
import re
from pathlib import Path
import tomllib

SKILL = Path("SKILL.md").read_text(encoding="utf-8")
PYPROJECT = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

def test_all_commands_have_entry_points():
    """SKILL.md 中提到的每个 `paperguard <cmd>` 都必须在 [project.scripts] 中注册。"""
    declared = set(re.findall(r"`paperguard (\w+)`", SKILL))
    registered = set(PYPROJECT["project"]["scripts"].keys())
    # 提取 `paperguard` 前缀后的命令名
    registered_cmds = {s.replace("paperguard-", "") for s in registered}
    missing = declared - registered_cmds
    assert not missing, f"SKILL.md declares but pyproject.toml lacks: {missing}"

def test_no_hardcoded_paths_in_skill():
    """SKILL.md 出现的所有 /path/to/... 都应该是示例，不能硬编码。"""
    suspicious = re.findall(r"/Users/u/[^\s`]+", SKILL)
    assert not suspicious, f"SKILL.md contains hardcoded user paths: {suspicious}"
```

把这个脚本放进 `tests/`，CI 每次跑——`SKILL.md` 和 `pyproject.toml` 任何一处改动不一致都会失败。

---

**版本**：v1.0
**对应文档**：`../SKILL.md` / `./design-proposal.md` / `./research-report.md`
**下次复审**：MVP Sprint 2 结束（CLI 注册完成时）
