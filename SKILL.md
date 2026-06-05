---
name: paperguard
description: Universal thesis/dissertation/paper DOCX formatting toolkit. Extract formatting requirements from any school template, audit a student's DOCX against GB/T 7713-2006, GB/T 7714-2015, or any school-specific rule pack (YAML), apply safe auto-fixes to produce a corrected copy without modifying the original, and report findings in JSON/HTML/SARIF/redline DOCX. Use when the user asks to format, check, audit, lint, normalize, repair, or revise a Chinese graduation thesis, academic paper, course report, journal manuscript, or any Word document that must conform to a specific school template, government standard (GB/T 7713, GB/T 7714, GB/T 9704), or custom rule set covering fonts, font sizes, line spacing, first-line indentation, page margins, heading levels, page numbers, headers, footers, table of contents, figure/table captions, references, three-line tables, and page setup. Compatible with Claude Code, Codex CLI, Gemini CLI, Cursor, and any agent that reads SKILL.md.
---

# PaperGuard — Universal Thesis Format Toolkit

Use this skill when a user wants a `.docx` thesis, dissertation, course paper, or journal manuscript **checked, audited, or repaired** against a specific format standard or school template. PaperGuard is **format-only**: it never rewrites content, translates text, or alters citations. It only adjusts typography, spacing, fonts, margins, and structural markers.

## When to Use This Skill

**Trigger** when the user's request matches ANY of:

- 帮我检查 / 审阅 / 批改 / 排版 / 规范化 这篇论文/报告/作业
- 这个 .docx 符合 GB/T 7713 / 7714 / 9704 吗
- 把这份论文改成南信大/清华/浙大 2026 规范的版本
- 这篇论文的字号/行距/页边距/字体有什么问题
- 自动修复 docx 的格式问题，保留原文
- 批量审稿学生论文是否符合规范
- 论文写完了，跑一遍格式检查
- 我需要把 docx 套用学校模板
- Word 论文排版不对，能批量改吗

**Do NOT use** for:

- 论文内容生成 / 改写 / 润色（用 Claude 的写作能力）
- LaTeX 论文（PaperGuard V2 才支持）
- PDF / .doc / WPS 文件（先转 .docx）
- 在 Word 里手动拖拽样式（用户应直接用 Word）

## Available Commands (4 + 1)

| 命令 | 用途 | 是否改文件 |
|------|------|----------|
| `paperguard extract` | 从模板 docx 抽出格式要求 | ❌ 只读 |
| `paperguard check` | 审计论文 docx 找出违规 | ❌ 只读 |
| `paperguard fix` | 应用修复，生成 paper.fixed.docx | ✅ 输出新文件 |
| `paperguard init` | 生成新学校规范包骨架 | ✅ 写新文件 |
| `paperguard validate-pack` | 验证 YAML 规范包语法 | ❌ 只读 |

## Required Three-Stage Workflow

**Always follow this order; never skip a stage.**

1. **Identify files**: Confirm the user has a `.docx` template (optional, for `extract`) and a `.docx` paper. If they provide `.doc` / `.pdf` / `.wps` files, ask them to convert first.
2. **Run `extract` on the template** (if a template is provided). This produces `requirements.json` and `requirements.md`. Review both.
3. **Run `check`** with the appropriate rule pack: `GB-T-7713-1-2006` (default), `GB-T-7714-2015` (references), or a school-specific pack.
4. **Run `fix`** ONLY after the user has reviewed the check report. The fix writes to `paper.fixed.docx`; the original is never modified.
5. **Verify**: `fix` automatically runs `check` again and appends "修复后剩余 N 个问题" to the report. If N > 0, explain to the user what remains manual.

## Command Reference

### `paperguard extract`

Extract detectable formatting requirements from a template DOCX.

```bash
paperguard extract \
  --template "/path/to/template.docx" \
  --out "./work/requirements"
```

**Output**:
- `work/requirements/requirements.json` — structured rules
- `work/requirements/requirements.md` — human-readable table with evidence

**7 rule categories** that may be extracted:
- `style` — style definitions (auto-fixable)
- `section` — page/margin/columns (auto-fixable)
- `paragraph-example` — example paragraphs (manual)
- `table` — table styles (manual)
- `numbering` — list/numbering format (manual)
- `explicit-instruction` — instructions in template text (needs AI/manual review)
- `document` — document-wide properties (manual)

### `paperguard check`

Audit a paper against a rule pack.

```bash
paperguard check \
  --paper "/path/to/paper.docx" \
  --pack "GB-T-7713-1-2006" \
  --format json \
  --out "./work/audit"
```

**Flags**:
- `--paper` (required): path to the paper
- `--pack` (required): rule pack name or path to YAML file. Common values:
  - `GB-T-7713-1-2006` — Chinese dissertation standard (default)
  - `GB-T-7714-2015` — references format
  - `GB-T-9704-2012` — government document format
  - `<path-to-school-pack.yaml>` — custom school pack
- `--format` (default: `terminal`): `terminal` | `json` | `html` | `sarif`
- `--out` (default: `./audit`): output directory

**Exit codes**:
- `0` — all required rules pass (warnings allowed)
- `1` — one or more required rules fail
- `2` — internal error (e.g., paper file corrupted)

**Output**:
- `work/audit/findings.json` — structured findings
- `work/audit/findings.html` — human-readable report (if `--format html`)
- `work/audit/findings.sarif` — SARIF 2.1.0 (if `--format sarif`)

### `paperguard fix`

Apply safe auto-fixes. **Never modifies the original file.**

```bash
paperguard fix \
  --paper "/path/to/paper.docx" \
  --pack "GB-T-7713-1-2006" \
  --output "./work/paper.fixed.docx" \
  --redline
```

**Flags**:
- `--paper` (required): path to the paper
- `--pack` (required): rule pack name or YAML path
- `--output` (default: `./paper.fixed.docx`): path for corrected file
- `--redline` (optional): also produce `paper.fixed.redline.docx` with tracked-changes showing all modifications
- `--auto-only` (optional): skip rules marked as needing human judgment

**Output**:
- `<output>` — corrected DOCX
- `<output>.redline.docx` (if `--redline`) — DOCX with tracked-changes
- `work/fixed/changes.csv` — exact before/after for every change
- `work/fixed/changes.md` — human-readable change log
- `work/fixed/post_check.json` — re-audit after fix (proves "did the fix work")

**Change statuses** (4 values in `changes.csv`):
- `changed` — property was different, now matches
- `already-set` — property already matched, no edit made
- `skipped` — cannot auto-fix, see notes
- `pass` — property already compliant, no edit needed

### `paperguard init`

Generate a new school-specific rule pack skeleton.

```bash
paperguard init \
  --school "南京信息工程大学" \
  --base "GB-T-7713-1-2006" \
  --out "./paperguard/packs/NJUIST-2026.yaml"
```

**Flags**:
- `--school` (required): school name (used in pack metadata)
- `--base` (default: `GB-T-7713-1-2006`): base pack to inherit from
- `--out` (required): output path for the new YAML pack

**Output**: A YAML file with:
- 30+ inherited rules from the base pack
- Empty fields to fill: page-margin specifics, school logo placement, dissertation committee field

### `paperguard validate-pack`

Validate a YAML pack for syntax and schema errors.

```bash
paperguard validate-pack --pack "./my-school-pack.yaml"
```

**Exit codes**:
- `0` — valid
- `1` — schema/syntax errors (printed to stderr with line numbers)

## Built-in Rule Packs

| Pack ID | 适用 | 规则数 |
|---------|------|-------|
| `GB-T-7713-1-2006` | 中国学位论文（默认） | 30+ |
| `GB-T-7714-2015` | 参考文献 | 12+ |
| `GB-T-9704-2012` | 党政机关公文 | 20+ |

V1 路线将预制 10 所高校（清华 / 浙大 / 北大 / 武大 / 复旦 / 上交 / 南大 / 中科大 / 哈工大 / 西交）。

## Output Format Details

### JSON finding shape

```json
{
  "rule_id": "BODY-LINE-SPACING",
  "severity": "error",
  "message": "正文行距必须为 1.5 倍",
  "location": {
    "section": "正文",
    "paragraph_index": 42,
    "text_snippet": "本文研究的主要问题是..."
  },
  "actual": "{\"line\": 360, \"lineRule\": \"auto\"}",
  "expected": "1.5",
  "fix": {
    "action": "set",
    "target": "paragraph.spacing.line",
    "value": "360"
  }
}
```

### HTML report

Self-contained HTML with collapsible sections, color-coded by severity, clickable anchors per finding. Suitable for emailing to students.

### SARIF 2.1.0

Compatible with GitHub Code Scanning, Azure DevOps, reviewdog, and most CI systems.

### Redline DOCX

Word tracked-changes mode: every fix is shown as an inline insertion/deletion, so the user can see exactly what PaperGuard changed and accept/reject per change in Word.

## How to Interpret Findings

When reporting findings to the user:

1. **Group by severity**: errors first (must fix), warnings next (should review), info last (FYI).
2. **Group by location**: cover page, abstract, TOC, body, references, appendix.
3. **Highlight the user's pain points**: typically line spacing, first-line indent, font size, page margins.
4. **Distinguish "auto-fixed" from "manual"**: errors with a `fix` field will be auto-fixed in `--fix` mode; errors without `fix` need user attention.
5. **Cite the source rule**: e.g., "GB/T 7713.1—2006 §6.1" if available.

## Example Conversations

### Example 1: User provides a paper and a school name

> User: 帮我把这份论文改成南信大 2026 规范

> Agent:
> 1. 确认有 `/Users/u/paper.docx`（用户已提供）
> 2. 南信大 2026 规范我们还没有预制 pack，先用国标 GB/T 7713—2006 跑一遍
> 3. 跑 `paperguard check --pack GB-T-7713-1-2006`
> 4. 出 8 个 error、3 个 warning
> 5. 询问用户是否要自动修复
>
> User: 修吧
>
> Agent: 跑 `paperguard fix`，输出 `paper.fixed.docx` 和 `changes.md`
> 报告：自动修了 7 个；剩 1 个 "三级标题应为黑体四号" 需要人工确认（因为模板用了 inherit 样式，机器无法 100% 判断）

### Example 2: User provides a template

> User: 这是我们学校的论文模板，能用你的工具审稿吗

> Agent:
> 1. 先 `paperguard extract --template /Users/u/school-template.docx` 抽出 50+ 条规则
> 2. 用户补充"我校要求：封面页下加'指导教师：XXX'，用楷体三号"——这是模板里没有的
> 3. 把这条加到 `requirements.json`
> 4. 跑 `paperguard check --requirements ./work/requirements/requirements.json --paper /Users/u/student-paper.docx`
> 5. 出报告

### Example 3: CI / batch use

> User: 我们教务处要批量审 200 篇论文

> Agent:
> 1. 建议用 `paperguard check --format sarif`，输出可被 GitHub/Azure DevOps 消费
> 2. 跑批量脚本：`for f in papers/*.docx; do paperguard check --paper "$f" --pack GB-T-7713-1-2006 --format json --out "audits/$(basename $f .docx)"; done`
> 3. 聚合所有 `findings.json` 出统计报告
> 4. 标记"问题数 > 阈值"的论文让教务重点审

## Failure Modes & Recovery

| Error | Cause | Recovery |
|-------|-------|----------|
| `Paper file is not a valid DOCX` | 用户给了 .doc / .pdf / .wps | 提示用户先 `pandoc -o paper.docx paper.pdf` 转换 |
| `Pack not found: XX` | 拼写错误或路径错 | 提示 `paperguard init` 生成新 pack，或用 `GB-T-7713-1-2006` 默认 |
| `Rule schema invalid: line 42` | YAML 写错 | 提示 `paperguard validate-pack` 看具体错 |
| `Fix did not converge: N issues remain` | 修复后仍有问题 | 报告剩余 N 个，告诉用户"这些需要人工处理" |
| `Permission denied writing to /output/path` | 路径无写权限 | 提示用户换路径或 `chmod` |

## Hard Constraints (NEVER VIOLATE)

1. **Original paper is sacred** — `fix` MUST write to a new file. The original is never modified. If the user asks you to "just modify in place", refuse and explain.
2. **Content is sacred** — PaperGuard never changes text, citations, figure numbering, or academic content. Only typography.
3. **Destructive fixes require confirmation** — if `fix` would change > 50% of paragraphs, ask the user first.
4. **TOC / page numbers / cross-references** are NOT auto-updated by `fix`. The user must open the result in Word and press F9. (This is a known limitation; V1 will add Word COM post-processing for Windows users.)
5. **Unknown rules → manual review** — if a rule cannot be safely auto-fixed, mark it as `skipped` with a reason, never silently skip.

## Privacy

PaperGuard runs **100% locally**. It does not upload papers or templates to any server. The only network access is `pip install paperguard` at install time. You can safely use it on confidential/unpublished theses.

## Reference

- 项目仓库：`./README.md`
- 调研报告：`./docs/research-report.md`
- 设计方案：`./docs/design-proposal.md`
- 参考实现：`./references/format-thesis-docx/`（wksudud/format-thesis-docx，945 行精读）
- 人类快速上手：`./SKILL_USAGE.md`
