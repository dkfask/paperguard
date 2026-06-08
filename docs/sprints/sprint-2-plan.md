# Sprint 2 — YAML 规则引擎 + 25+ 规则 PLAN

> **状态**：v1.0 草案（2026-06-08，多代理 workflow 综合：planner + Context7 docs-lookup + Explore 参考项目）
> **前置**：Sprint 1 完成（commit `6097817`，IR + docx_loader）+ Sprint 1.5 完成（commit `f7384dd`，PyQt 实时约束 5 stub 规则）
> **后续**：实施前需用户拍板第 8 节 8 个开放问题
> **目标**：把 `stub_engine.py`（5 条硬编码 Python 规则）替换为 YAML pack 驱动的通用引擎 + 25+ 规则 + LiveFormatChecker 切换到正式 pack `GB-T-7713-1-2006`，性能基线保持 < 200ms（100 段论文）

---

## 1. Goal

Sprint 2 用 YAML pack 驱动替代 Sprint 1.5 的硬编码 stub engine：实现 `engine/loader.py`（YAML → `RulePack` dataclass + pydantic schema 验证）、`engine/evaluator.py`（JSONLogic `when` + JMESPath `scope` 表达式求值）、`engine/visitor.py`（遍历 IR + 按 scope 命中规则 → `Finding[]`），在 `engine/__init__.py` 重导出 `validate_docx` 入口替换 stub_engine，实现 `rules/packs/GB-T-7713-1-2006.yaml` 包含 25+ 条规则（覆盖 R001-R005 等价 + 20+ 新规则：page_margin / heading_level / font_hansi / table_caption / figure_caption / reference_format 等），同时 `LiveFormatChecker` 的 `pack_name` 参数从 `"stub-GB-T-7713-1-2006"` 切换为 `"GB-T-7713-1-2006"`，性能基线维持 < 200ms（100 段），所有新模块覆盖率 ≥ 90%，`app/core/` 零 PyQt import 硬约束保持。

---

## 2. Architecture Decision Records

### ADR-1：表达式库选择（jsonlogic + jmespath 第三方库 vs 自实现子集）

- **状态**：**待用户拍板**
- **Context**：`docs/design-proposal.md` §6 技术选型（line 649）选 `json-logic + jmespath.py`；`requirements.txt` 缺这两个库。Context7 调研：
  - `json-logic-py` PyPI 成熟，**原生支持点分 var**（`{"var": "font.eastAsia"}`），与 PaperGuard IR v1.1 点分约定完美对齐
  - `jmespath` PyPI 官方维护，可 `compile()` LRU 缓存，性能 100 段 < 1ms；**唯一限制**：只吃 dict/list，IR 需加 `to_dict()` 方法（~20 LOC）
- **Options**：
  - **A（推荐）**：装 `json-logic-py` + `jmespath` 两个第三方库 + IR 加 `to_dict()`
  - **B**：自实现 JSONLogic 子集（约 200 LOC）+ 简化 scope 选择器（如只支持 `paragraphs[?style_id == X]` 一种模式）
- **推荐**：**A**，原因：① 设计文档 §3.3 示例就是 JSONLogic + JMESPath；② 边界条件（None var / 跨类型比较 / 短路求值）库已处理；③ IR 加 `to_dict()` 是一次性 20 LOC

### ADR-2：Schema 校验库（Context7 新推荐）

- **状态**：**待用户拍板**
- **Context**：PyYAML 不内置 schema 验证；用户写错 YAML pack 字段会一路报错到引擎层。
- **Options**：
  - **A（推荐）**：装 `pydantic` v2 — `BaseModel.model_validate(raw_yaml_dict)`，类型注解 + IDE 提示 + 错误消息带字段路径
  - **B**：自实现简易 schema 校验（约 60 LOC）— 检查必填字段 + 类型
  - **C**：暂不做 schema 校验（YAML 解析失败时才报错）
- **推荐**：**A**，原因：① 错误消息友好（带字段路径）；② pydantic v2 是当前 Python 强类型模型生态最广；③ PyQt 端模型层（`app/models/`）后续可复用

### ADR-3：scope 表达式语法

- **状态**：**待用户拍板**
- **Context**：design-proposal §3.3 用 JMESPath：`scope: "paragraphs[?style_name == 'Body Text']"`
- **Options**：
  - **A（推荐）**：完整 JMESPath（与设计文档一致；`jmespath` 库支持）
  - **B**：简化子集（只 `paragraphs[style_id=Body Text]`，无 `[?]` lambda）
- **推荐**：**A**

### ADR-4：Fix.target 命名约定（**已 decided，不需用户拍板**）

- **状态**：**decided**
- **Context**：LiveMutator 内部已硬编码 `"paragraph.line_spacing"` / `"run.font_size_pt"` 等点分形式（Sprint 1.5 plan §2 ADR-4）。Sprint 2 YAML pack 的 `fix.set` 必须保持点分形式。
- **Decision**：YAML `fix.set` 用点分 key（如 `paragraph.line_spacing: 1.5`），loader 直接映射为 `Fix(action="set", target="paragraph.line_spacing", value=1.5)`，**不改成嵌套对象**。
- **Consequences**：stub_engine 的 5 条 Fix.target 已是点分，Sprint 2 YAML pack 保持一致；test 每条 Finding 断言 `fix.target` 格式正确。

### ADR-5：pack 加载错误策略

- **状态**：**待用户拍板**
- **Options**：
  - **A（推荐）**：`fail-fast` — YAML 解析或 schema 验证失败立刻 raise（启动时即暴露问题）
  - **B**：graceful degradation — 跳过坏规则 + warn 记录
- **推荐**：**A**，原因：MVP 阶段 pack 少且团队维护；坏 pack 应立刻修；设计文档 §10 已有此风险对策

### ADR-6：stub_engine 何去何从

- **状态**：**待用户拍板**
- **Options**：
  - **A（推荐）**：直接删除 `stub_engine.py`；`LiveFormatChecker` 默认 pack 改为 `"GB-T-7713-1-2006"`；改 `scripts/sprint1_5_smoke.py` 用新 pack
  - **B**：保留 thin wrapper — `validate_docx()` 检测 `pack == "stub-..."` 时调旧 hardcoded 规则，否则 forward 到 YAML engine
  - **C**：保留 stub_engine.py 不动 + `engine/__init__.py` 优先导入新 YAML engine
- **推荐**：**A**，原因：① Sprint 1.5 smoke 是冒烟脚本，改 pack 参数即可；② 切换 pack 是 Sprint 2 明确目标；③ 删除代码比维护两套干净

### ADR-7：rule_id 命名约定

- **状态**：**待用户拍板**
- **Context**：stub_engine 用 R001-R005；design-proposal §3.2 示例用 `PG-LAYOUT-LINE-SPACING` 语义化 ID；参考项目用 R0001-R9999（4 位序号）
- **Options**：
  - **A**：语义化 ID（`PG-LINE-SPACING` / `PG-FONT-SIZE` / `PG-HEADING1-FONT`）
  - **B**：4 位数字 ID（R0001-R9999，参考项目模式，CSV 排序友好）
  - **C**：3 位数字 ID（R001-R030，与 Sprint 1.5 一致）
- **推荐**：**A（语义化）**，原因：① 报告/SARIF 中暴露应有可读性；② 设计文档示例就是语义化；③ CLI `--filter PG-LINE-SPACING` 直观

### ADR-8：是否实现 pack 继承 `extends`

- **状态**：**待用户拍板**
- **Options**：
  - **A（推荐）**：MVP 不实现 `extends`；`GB-T-7713-1-2006.yaml` 独立完整；学校 pack 通过 `paperguard init` 复制生成
  - **B**：实现 `extends` — loader 支持递归合并（后者覆盖前者）
- **推荐**：**A**，原因：Sprint 2 只有 1 个 pack；继承是 V1+ 功能；提前实现增加 loader ~100 LOC 复杂度

---

## 3. File-Level Deliverables

### 3.1 `app/core/engine/`（核心引擎模块）

#### `app/core/engine/loader.py`（**新**）
- **Purpose**：YAML pack → `RulePack` + `Rule` dataclass，pydantic schema 验证（fail-fast）
- **Public API**：
  ```python
  from pydantic import BaseModel

  class RuleSchema(BaseModel):
      id: str
      severity: str   # "error" | "warning" | "info"
      scope: str       # JMESPath
      when: dict       # JSONLogic
      message: str
      fix: dict | None = None

  class RulePackSchema(BaseModel):
      id: str
      name: str
      version: str
      rules: list[RuleSchema]

  @dataclass(frozen=True)
  class Rule:
      id: str
      severity: Severity
      scope: str
      when: dict
      message: str
      fix: Fix | None

  @dataclass(frozen=True)
  class RulePack:
      id: str
      name: str
      version: str
      rules: tuple[Rule, ...]

  def load_pack(pack_id: str) -> RulePack: ...
  def list_packs() -> list[str]: ...
  ```
- **Approx LOC**：~140
- **Tests**：`tests/unit/test_engine_loader.py`，~12 tests（load valid pack / missing pack / invalid YAML / duplicate rule_id / missing required field / invalid severity / schema validation 错误消息 / list_packs / Fix 字段映射点分 target / 空 rules 列表 / pack id 与文件名不一致 / unicode pack 名）
- **Dependencies**：`app.core.ir`、`pyyaml`（已有）、`pydantic`（ADR-2 A）

#### `app/core/engine/evaluator.py`（**新**）
- **Purpose**：表达式求值器 — `scope` 用 JMESPath 过滤；`when` 用 JSONLogic 求布尔
- **Public API**：
  ```python
  def evaluate_scope(scope: str, document: Document) -> list[tuple[int, Any]]:
      """返回 (paragraph_index, node) 列表。"""

  def evaluate_when(when_expr: dict, node: Any) -> bool:
      """在节点上求 JSONLogic 条件。"""
  ```
- **关键设计**：`evaluate_scope` 内先调 `document.to_dict()` 转 dict，再 `jmespath.compile(scope).search(dict)`；返回时把 dict 节点映射回 IR 对象（用 paragraph_index 索引）。`jmespath.compile()` 结果用 `functools.lru_cache(maxsize=128)` 复用 AST
- **Approx LOC**：~160
- **Tests**：`tests/unit/test_engine_evaluator.py`，~15 tests（JMESPath paragraphs filter / runs index access / JSONLogic == / != / and / or / not / var with dot path / 嵌套 var / 缺失字段返回 None / 复杂组合 / scope compile cache hit / 类型不匹配 short-circuit / 数字 vs 字符串比较）
- **Dependencies**：`app.core.ir`、`json-logic-py`（ADR-1 A）、`jmespath`（ADR-1 A）

#### `app/core/engine/visitor.py`（**新**）
- **Purpose**：遍历 IR，按 pack 每条 rule 的 scope + when 命中，产生 `Finding[]`
- **Public API**：
  ```python
  def visit_document(document: Document, pack: RulePack) -> list[Finding]: ...
  ```
- **Approx LOC**：~110
- **Tests**：`tests/unit/test_engine_visitor.py`，~12 tests（empty doc / no rules match / one rule one match / one rule multi match / scope reduces candidates / when false skips / Fix 透传 / rule with no fix / paragraph_index 正确 / 多 rule 排序稳定 / Severity 字符串→enum 转换 / location.text_snippet 填充）
- **Dependencies**：`app.core.ir`、`evaluator`、`loader.RulePack`

#### `app/core/engine/__init__.py`（**改**）
- **Purpose**：**替换 `validate_docx` 入口**
- **改动**（替换约 5 行）：
  ```python
  from app.core.engine.loader import load_pack
  from app.core.engine.visitor import visit_document
  from app.core.ir import Document, Finding

  def validate_docx(ir: Document, pack: str = "GB-T-7713-1-2006") -> list[Finding]:
      rule_pack = load_pack(pack)
      return visit_document(ir, rule_pack)

  __all__ = ["validate_docx"]
  ```
- **Tests**：`tests/unit/test_engine_init.py`，~3 tests（validate_docx forwards / unknown pack raises / signature 兼容 LiveFormatChecker）

### 3.2 `app/core/ir/`（IR 新增 to_dict 方法）

#### `app/core/ir/doc.py` / `paragraph.py` / `run.py` / `section.py`（**改**）
- **Purpose**：每个 frozen dataclass 加 `to_dict()` 方法，供 JMESPath 消费（ADR-1 关键依赖）
- **Approx LOC delta**：+10 LOC per dataclass = ~40 LOC
- **示例**：
  ```python
  # paragraph.py
  def to_dict(self) -> dict:
      return {
          "style_id": self.style_id,
          "text": self.text,
          "level": self.level,
          "line_spacing": self.line_spacing,
          "space_before_pt": self.space_before_pt,
          "space_after_pt": self.space_after_pt,
          "first_line_indent_cm": self.first_line_indent_cm,
          "is_page_break_before": self.is_page_break_before,
          "runs": [r.to_dict() for r in self.runs],
      }
  ```
- **Tests**：`tests/unit/test_ir_to_dict.py`，~6 tests（每个 dataclass 1 test + 1 嵌套 test）

### 3.3 `app/core/rules/packs/`（**新目录**）

#### `app/core/rules/packs/GB-T-7713-1-2006.yaml`（**新**）
- **Purpose**：25+ 条规则的完整 YAML pack
- **覆盖规则**（25 条最小，目标 25-30）：
  - **段落级 5 条**（R001-R005 等价）：BODY-LINE-SPACING / BODY-FONT-SIZE / BODY-FIRST-LINE-INDENT / BODY-FONT-EASTASIA / BODY-PARAGRAPH-SPACING
  - **页边距 4 条**：PAGE-MARGIN-TOP / PAGE-MARGIN-BOTTOM / PAGE-MARGIN-LEFT / PAGE-MARGIN-RIGHT
  - **标题字体 6 条**：HEADING1-FONT-EASTASIA / HEADING1-FONT-SIZE / HEADING2-FONT-EASTASIA / HEADING2-FONT-SIZE / HEADING3-FONT-EASTASIA / HEADING3-FONT-SIZE
  - **标题层级 3 条**：HEADING1-LEVEL / HEADING2-LEVEL / HEADING3-LEVEL
  - **西文字体 2 条**：BODY-FONT-ASCII（Times New Roman）/ HEADING-FONT-ASCII
  - **章前分页 1 条**：PAGE-BREAK-BEFORE-CHAPTER
  - **格式扩展 4 条**：BOLD-CONSISTENCY / ITALIC-CONSISTENCY / UNDERLINE-FORBIDDEN-BODY / FONT-COLOR-BLACK-BODY
- **Approx LOC**：~220（YAML，每条规则 ~8 行）
- **Tests**：`tests/unit/test_rules_pack_gb7713.py`，~27 tests（每条规则 1-2 个）
- **示例规则**：
  ```yaml
  - id: PG-BODY-LINE-SPACING
    severity: warning
    scope: "paragraphs[?style_id == 'Body Text' || style_id == 'Normal']"
    when:
      "!=":
        - var: "line_spacing"
        - 1.5
    message: "正文行距应为 1.5"
    fix:
      action: set
      target: "paragraph.line_spacing"
      value: 1.5
  ```

### 3.4 `requirements.txt`（**改**）
```diff
 PyQt5>=5.15.0
 python-docx>=1.0.0
 lxml>=4.9.0
 pyyaml>=6.0
+json-logic-py>=1.2.0
+jmespath>=1.0.1
+pydantic>=2.5.0
```

### 3.5 `app/services/live_format_checker.py`（**改**）
- **改动**：第 30 行 `from app.core.engine.stub_engine import validate_docx` 改为 `from app.core.engine import validate_docx`；第 54 行默认 `pack_name="stub-GB-T-7713-1-2006"` 改为 `"GB-T-7713-1-2006"`
- **Approx LOC delta**：2 行
- **Tests**：现有 `test_live_format_checker.py` 测试需更新默认 pack 期望值（~2-3 个 test 更新）

### 3.6 删除 `app/core/engine/stub_engine.py`（**删**，ADR-6 A）
- **影响**：`tests/unit/test_stub_engine.py` 同步删除（或改为测 YAML pack 等价规则）
- **scripts/sprint1_5_smoke.py**：改第 N 行 `pack="stub-GB-T-7713-1-2006"` → `pack="GB-T-7713-1-2006"`

### 3.7 `tests/`（新测试一览）

| 文件 | 测试数 | 覆盖内容 |
|------|-------|---------|
| `tests/unit/test_engine_loader.py` | ~12 | YAML 解析 / pydantic schema / 错误处理 |
| `tests/unit/test_engine_evaluator.py` | ~15 | JMESPath / JSONLogic / 缺失字段 |
| `tests/unit/test_engine_visitor.py` | ~12 | 遍历 IR / 命中 / Fix 透传 |
| `tests/unit/test_engine_init.py` | ~3 | validate_docx 入口 |
| `tests/unit/test_ir_to_dict.py` | ~6 | IR dataclass to_dict |
| `tests/unit/test_rules_pack_gb7713.py` | ~27 | 每条 YAML 规则 1-2 个 |
| `tests/integration/test_yaml_engine_e2e.py` | ~5 | 端到端 |
| `tests/integration/test_engine_perf.py` | ~2 | 100 段 < 200ms |
| **小计** | **~82 新测试** | |

### 3.8 `scripts/sprint2_smoke.py`（**新**）
- **Purpose**：构造内存 `Document` IR → `validate_docx(doc, "GB-T-7713-1-2006")` → 断言 N 条 Finding + Fix.target 点分
- **Approx LOC**：~80

---

## 4. Test Strategy

### 4.1 TDD 顺序

1. **test_ir_to_dict** (6 RED) → IR 加 `to_dict()` (GREEN)
2. **test_engine_loader** (12 RED) → loader.py (GREEN)
3. **test_engine_evaluator** (15 RED) → evaluator.py (GREEN)
4. **test_engine_visitor** (12 RED) → visitor.py (GREEN)
5. **test_engine_init** (3 RED) → engine/__init__.py 改写 (GREEN)
6. **GB-T-7713-1-2006.yaml** 手写 25+ 规则
7. **test_rules_pack_gb7713** (27 RED) → YAML 调试 (GREEN，无需写 Python)
8. **test_yaml_engine_e2e** (5 RED) → 集成验证 (GREEN)
9. **test_engine_perf** (2 RED) → 性能调优 (GREEN)
10. **删除 stub_engine.py + test_stub_engine.py** → 改 LiveFormatChecker 默认 pack → smoke 脚本验证

### 4.2 覆盖率目标

| 模块 | 目标 |
|------|------|
| `loader.py` | ≥ 90% |
| `evaluator.py` | ≥ 90% |
| `visitor.py` | ≥ 90% |
| `engine/__init__.py` | ≥ 85% |
| Sprint 2 增量整体 | ≥ 80% |

### 4.3 总测试数预期

- 现有：~124 tests（56 Sprint 1 + 68 Sprint 1.5）
- Sprint 2 新增：~82
- **总预期**：**~206 tests** 全绿

### 4.4 性能基线

- `tests/integration/test_engine_perf.py`：100 段论文 × 25 规则 < 200ms
- 优化手段：① `jmespath.compile()` 用 `functools.lru_cache` 缓存；② JSONLogic 短路求值；③ scope 过滤优先减少候选 paragraphs

---

## 5. Commit Strategy（17 atomic commits）

| # | Commit | Scope | 说明 |
|---|--------|-------|------|
| 1 | `chore(sprint-2): 加 json-logic-py + jmespath + pydantic 依赖` | chore | requirements.txt + `pip install` 验证 |
| 2 | `test(sprint-2): RED — IR to_dict 6 tests` | test | 4 个 dataclass + 嵌套 |
| 3 | `feat(sprint-2): IR dataclass 加 to_dict()` | feat | Document/Paragraph/Run/Section 各加 10 LOC |
| 4 | `test(sprint-2): RED — engine_loader pydantic schema 测试` | test | 12 个 RED |
| 5 | `feat(sprint-2): engine_loader (RulePack/Rule + pydantic + load_pack)` | feat | ~140 LOC |
| 6 | `test(sprint-2): RED — evaluator JMESPath + JSONLogic 测试` | test | 15 个 RED |
| 7 | `feat(sprint-2): engine_evaluator (scope + when + lru_cache)` | feat | ~160 LOC |
| 8 | `test(sprint-2): RED — visitor 遍历+命中+Fix 透传测试` | test | 12 个 RED |
| 9 | `feat(sprint-2): engine_visitor visit_document` | feat | ~110 LOC |
| 10 | `feat(sprint-2): engine/__init__.py 替换 validate_docx 入口` | feat | 组合 loader+visitor，~10 LOC |
| 11 | `feat(sprint-2): GB-T-7713-1-2006.yaml 25+ 规则` | feat | YAML pack（5 body + 20 新规则） |
| 12 | `test(sprint-2): RED — rules pack 每规则 1-2 测试` | test | 27 个 RED |
| 13 | `refactor(sprint-2): 删除 stub_engine.py + LiveFormatChecker 切 pack` | refactor | ADR-6 A 落地，改 2 行 + 删 1 文件 |
| 14 | `test(sprint-2): YAML engine e2e 集成测试` | test | 5 个 integration |
| 15 | `test(sprint-2): engine perf 基线（100 段 < 200ms）` | test | 2 个 perf |
| 16 | `chore(sprint-2): scripts/sprint2_smoke.py + grep app/core/ 无 PyQt` | chore | smoke + DoD grep |
| 17 | `docs(sprint-2): HANDOFF.md 标记 Sprint 2 完成` | docs | 配合 Phase E |

---

## 6. Risks & Mitigations

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| 1 | jsonlogic/jmespath 库的中文文档/边界 case（var 缺失返回 None vs 抛错） | 规则命中失败或误报 | evaluator.py 写边界测试（None / missing / type mismatch）；IR `to_dict()` 用 dataclass 直接序列化避免类型问题 |
| 2 | YAML pack 表达力不够（如 table cross-reference 多步推理） | 部分规则无法 YAML 化 | MVP YAML 只覆盖段落/run/section 直接属性；复杂规则 Sprint 2.5 之后用 Python 函数 escape hatch |
| 3 | 25 条规则 vs 5 条规则的性能（100 段 < 200ms） | UI 卡顿 | visitor.py scope 过滤优先；`jmespath.compile()` LRU 缓存；JSONLogic 短路；test_engine_perf 强制 200ms 上限 |
| 4 | LiveFormatChecker 切 pack 后 Fix.target 不兼容 LiveMutator | 修复按钮失效 | ADR-4 决定 Fix.target 点分形式；loader 强制点分 target；每条 Finding 测试断言 target 格式 |
| 5 | JMESPath 表达式语法错误（引号/括号） | pack 无法加载 | ADR-5 fail-fast；test_engine_loader 覆盖 invalid scope；将来 `paperguard validate-pack` CLI |
| 6 | stub_engine 删除后 sprint1_5_smoke 失败 | Sprint 1.5 成果不可验证 | smoke 脚本改 pack 参数即可；YAML pack 含 R001-R005 等价 5 规则 |
| 7 | IR `to_dict()` 后无法反映射回 IR 对象（visitor 需要 IR） | visitor 拿不到 Paragraph 对象 | evaluator 返回 `(paragraph_index, dict)` 元组；visitor 用 index 从 `ir.paragraphs[i]` 反查；不在 dict 层产生 Finding |

---

## 7. Definition of Done

- [ ] `pytest tests/` 全绿（**~206 tests**，含 124 现有 + 82 新增）
- [ ] `python scripts/sprint2_smoke.py` 端到端通过（YAML pack → 25+ findings）
- [ ] `grep -r "PyQt" app/core/` 仅匹配 docstring 注释（零 import，Sprint 1 硬约束保持）
- [ ] 现有 124 tests 仍全绿（无 regression）
- [ ] **LiveFormatChecker 默认 pack 切换为** `"GB-T-7713-1-2006"`；PyQt 编辑器实时检查跑出 25+ 规则全部 findings
- [ ] **Fix.target 全部点分形式**：YAML pack 每条 Finding 的 `fix.target` 经验证为 `"paragraph.line_spacing"` / `"run.font_size_pt"` 等点分形式
- [ ] **性能基线**：`pytest tests/integration/test_engine_perf.py` — 100 段论文 < 200ms
- [ ] **validate_docx 签名兼容**：`(ir: Document, pack: str = "GB-T-7713-1-2006") -> list[Finding]`，LiveFormatChecker 调用无需修改（仅默认 pack 值变）
- [ ] **ADR 拍板**：第 8 节 8 个开放问题全部用户拍板（实施前）
- [ ] `HANDOFF.md` 标记 Sprint 2 完成 + commit hash 更新
- [ ] Push 到 GitHub `main` 分支

---

## 8. Open Questions for User（**已于 2026-06-08 用户拍板全部 A 推荐**）

| # | 问题 | **用户决策** | 影响 |
|---|------|--------------|------|
| **1** | 表达式库（ADR-1） | **✅ A：装 json-logic-py + jmespath + pydantic 三个第三方库 + IR 加 to_dict()** | requirements.txt +3 行；IR 4 个 dataclass 各加 ~10 LOC `to_dict()` |
| **2** | Schema 校验（ADR-2） | **✅ A：装 pydantic v2**（与 Q1 合并） | loader.py 用 `RulePackSchema(BaseModel)` 验证；错误消息带字段路径 |
| **3** | scope 语法（ADR-3） | **✅ A：完整 JMESPath**（与 Q1 隐式确定） | evaluator.py `jmespath.compile(scope).search(doc.to_dict())` |
| **4** | pack 加载错误（ADR-5） | **✅ A：fail-fast** | loader.py YAML/schema 错误立刻 raise；test_engine_loader 覆盖 invalid case |
| **5** | stub_engine 何去何从（ADR-6） | **✅ A：直接删除** | commit #13 删 `stub_engine.py` + 改 `live_format_checker.py` 默认 pack + 改 smoke 脚本 |
| **6** | rule_id 命名（ADR-7） | **✅ A：语义化 PG-XXX**（如 PG-BODY-LINE-SPACING） | YAML pack 全部用 PG- 前缀；test 期望值随改 |
| **7** | pack 继承 extends（ADR-8） | **✅ A：MVP 不实现** | loader.py 简单（不递归合并）；学校 pack V1 才支持 |
| **8** | 25+ 规则范围（补充） | **✅ A：25 条**（段落 5 + 边距 4 + 标题 9 + 西文 2 + 章前 1 + 格式 4） | GB-T-7713-1-2006.yaml ~220 行；test_rules_pack_gb7713 ~27 tests |

**决策一致性**：所有 8 个问题用户均选 A 推荐方案 → 实施可走 §3-7 plan 原版，无需调整。

---

## 9. 与参考项目（wksudud/format-thesis-docx）借鉴清单

> 由 Explore agent 在 `references/format-thesis-docx/` 中调研得出（详见任务 #1 输出）。

| # | 参考项目位置 | Sprint 2 落地位置 | 借鉴方式 |
|---|------|------|----------|
| 1 | `scripts/extract_requirements.py:131-143` `add_item()` 规则 item 结构 | `loader.py` `Rule` dataclass | 字段集 `{id, severity, scope, when, message, fix}` |
| 2 | `scripts/apply_format.py:40-67` `set_run_property()` mapping 模式 | Sprint 5 `writers/docx_writer.py`（暂记，Sprint 2 不实现） | property→OOXML 二级 mapping |
| 3 | `scripts/audit_format.py:116-147` 两阶段（collect_facts + 内存比对） | `visitor.py` scope 过滤优先 | 先收集再比对，减少重复 IR 遍历 |

---

## 10. 附件：关键引用映射

| 引用 | 出处 | 行号 |
|------|------|------|
| YAML Rule Pack 模型 | `docs/design-proposal.md` §3.3 | 211-297 |
| engine 子模块结构 | `docs/design-proposal.md` §4 | 301-403 |
| 技术选型表 | `docs/design-proposal.md` §6 | 639-657 |
| Sprint 2 原始计划 | `docs/design-proposal.md` §7 | 669-674 |
| stub_engine 5 规则 | `app/core/engine/stub_engine.py` | 12-17 |
| stub_engine Fix.target 点分 | `app/core/engine/stub_engine.py` | 53-151 |
| LiveFormatChecker 调用 validate_docx | `app/services/live_format_checker.py` | 102 |
| LiveFormatChecker 默认 pack | `app/services/live_format_checker.py` | 54 |
| IR 定义 | `app/core/ir/` | — |
| Sprint 1.5 plan 模板 | `docs/sprints/sprint-1.5-plan.md` | 1-437 |
| Sprint 1.5 ADR-4 Fix 点分约定 | `docs/sprints/sprint-1.5-plan.md` §2 ADR-4 | 50-55 |
| requirements.txt 当前 | `requirements.txt` | 1-4 |
| 现有 fixtures | `tests/fixtures/sample_paper.docx` / `sample_paper_100paras.docx` | — |

---

**版本**：v1.0
**作者**：多代理 workflow（planner + Context7 docs-lookup + Explore）
**日期**：2026-06-08
**下次评审**：用户拍板第 8 节 8 个问题后实施
