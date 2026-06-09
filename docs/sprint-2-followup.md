# Sprint 2 Follow-up — ocr review 遗留 findings

> **来源**：`docs/reports/sprint-2-ocr-review-2026-06-08.md`（alibaba/open-code-review v1.2.4 跑 Sprint 2 15 commits 产出）
> **决策**：[D 选项] 暂不修；记录于此，**Sprint 3 或更晚**处理
> **理由**：3 个真 bug 影响不阻塞功能（YAML 修复缺另一字段是 UX 缺陷不是数据错误；`__index__` 泄漏当前没规则作者会用；perf test 失效但其他 e2e test 覆盖）
> **重启时机**：① Sprint 3 起手前若要做 pack 扩展，必须先修 B1+B2（否则新规则会暴露问题）；② 任何用户报"修复后规则仍触发"问题时；③ 任何加新 evaluator 字段时

---

## 🔴 真 Bug（3 个，必修，建议 Sprint 3 起手第一件事）

### B1 · YAML `PG-BODY-PARAGRAPH-SPACING` fix 不完整

**位置**：`app/core/rules/packs/GB-T-7713-1-2006.yaml:74-89`

**问题**：规则同时检测 `space_before_pt != 0` OR `space_after_pt != 0`，但 fix 只设 `paragraph.space_before_pt = 0`。当段落仅 `space_after_pt` 不合规时，应用 fix 后规则依然触发（用户体验：点修复→还报错）。

**修复方案**：拆成两条独立规则：
- `PG-BODY-SPACE-BEFORE`（scope/when/fix 都针对 space_before_pt）
- `PG-BODY-SPACE-AFTER`（同上但 after）

测试也要加 2 条：每条仅触发其中一个字段的 negative case。

**注**：原 Sprint 1.5 `stub_engine.py` R005 也有此问题，已随 stub 删除一同消失，但 YAML 等价规则继承了它。

---

### B2 · evaluator `__index__` 字段污染 node dict

**位置**：`app/core/engine/evaluator.py:80` 注入 `__index__`；`tests/unit/test_engine_evaluator.py:131-139` 暴露

**问题**：`evaluate_scope` 给每个 paragraph dict 注入 `__index__` 字段（用于 visitor 反查 paragraph_index），**但没清理**就把节点传给 `evaluate_when`。如果规则作者写 `{"var": "__index__"}`（如 `paragraphs[?@.__index__ > 5]`），会被 jsonlogic 误识别成数据字段。

**修复方案**（两选一）：
1. **A：注入私有副本** — `evaluate_scope` 返回 (paragraph_index, node_copy_without_index)，原 dict 不动
2. **B：用更稀有名** — 改 `__index__` 为 `__paperguard_internal_paragraph_index__`（仍可能冲突但概率极低）

推荐 A，~10 LOC 改动。

**注**：评估时 test_scope_index_injection_does_not_leak 测试名误导（它实际"验证 index 注入了"而非"不泄漏"）— 这个测试本身也要重命名为 `test_scope_injects_paragraph_index_for_lookup`。

---

### T0 · perf test 断言失效

**位置**：`tests/integration/test_engine_perf.py:56-57`

**问题**：
```python
self.assertGreaterEqual(len(findings), 0, "fixture 跑出 findings 应 >= 0")
```
`len()` 永远 ≥ 0，**断言完全失效**。注释暗示预期 > 0（"fixture 已知含违规段"）。

**修复方案**：参考 `tests/integration/test_live_checker_pyqt.py:194`，改为：
```python
self.assertGreater(len(findings), 0, "100-para fixture 应触发至少 1 个 finding")
```

---

## 🟡 测试质量改进（12 个，Sprint 3+ 视情况处理）

### test_engine_loader.py（4 处）

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| T1 | `test_engine_loader.py:161-176` | 裸 `Exception` 捕获 | `from pydantic import ValidationError` + `assertRaises(ValidationError)` |
| T2 | `test_engine_loader.py:178-188` | 同上（severity 缺失） | 同上 |
| T3 | `test_engine_loader.py:196-212` | 同上（severity=critical） | 同上 |
| T4 | `test_engine_loader.py:122-123` | `ValueError, YAMLError` 元组捕获过宽 | 只用 `assertRaises(yaml.YAMLError)` |
| T5 | `test_engine_loader.py:236-237` | `replace()` 替换 pack_id 脆弱 | `_write_pack` 接受显式 pack_id 参数 |

### test_yaml_engine_e2e.py（1 处）

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| T6 | `test_yaml_engine_e2e.py:8-9` | docstring 写 `test_severity_distribution` 但方法名 `test_severity_propagates_as_enum` | 二选一：① 改方法名匹配 docstring + 加 severity 分布断言；② 改 docstring 匹配现状 |

### test_ir_to_dict.py（6 处）

| # | 位置 | 问题 | 建议 |
|---|------|------|------|
| T7 | `test_ir_to_dict.py:33-48` | TestRunToDict 声称验"全部 12 字段"但只断言 6 字段 + 3 个 assertIn | 补齐 12 字段值断言（含 font_cs/highlight/vert_align） |
| T8 | `test_ir_to_dict.py:95-99` | TestSectionToDict 仅 4/9 字段（缺 page_height/margin_bottom/right/header/footer） | 补齐 9 字段 |
| T9 | `test_ir_to_dict.py:68-73` | TestParagraphToDict 未覆盖 `is_page_break_before` | 加断言 |
| T10 | `test_ir_to_dict.py:102` | 缺少 `DocumentMetadata.to_dict()` 独立测试类 | 加 `TestDocumentMetadataToDict`（5 字段） |
| T11 | `test_ir_to_dict.py:117-119` | 缺边界测试：空集合 `runs=()` / `paragraphs=()` / `sections=()`；None 字段 | 加 4-6 个边界 case |
| T12 | `test_ir_to_dict.py:152-154` | jmespath 集成测试仅覆盖单层 filter，未测 `runs.0.font_size_pt` 数组索引和 `||` 多条件 OR | 加 3-4 个集成测试匹配 YAML pack 实际表达式 |

---

## 🟢 ocr 给出的好评（1 个）

- `app/core/engine/__init__.py` — 整体设计正确、无逻辑错误（点赞 ✅）

---

## 📊 ocr 第一轮 review 跑批统计

| 指标 | 值 |
|------|---|
| Review 范围 | Sprint 2 commits `f7384dd..b22c851`（15 commits, +3016/-449 行） |
| Reviewed files | 24 / 27 changed（3 个非代码文件被 ext 过滤） |
| Comments | **16 个** |
| Tokens | ~1.5M（input 1.4M + output 84k + cache read 1M）≈ $1-1.5（MiniMax M2.7 价格） |
| 用时 | 3m40s |
| 失败文件 | 3 个（MiniMax 429 限流，concurrency=8 太激进） |

### 限流失败未 review 的文件（R4.5 未执行）

1. `app/core/engine/loader.py` — **核心模块**，强烈建议未来重跑（要等限流窗口过期或改 `--concurrency 1`）
2. `app/services/live_format_checker.py` — 仅改 2 行（pack 切换），低优先级
3. `scripts/sprint1_5_smoke.py` — 冒烟脚本，低优先级

**重跑建议**：未来想审 loader.py 时，用 `ocr review --commit 6a904df --concurrency 1`，专审那一个 commit。

---

## ⚠️ ocr 自己的误判（我审 ocr 的发现）

1. **WARNING：找不到 `stub_engine.py` 在 HEAD** — ocr 把"被删除的文件"当成 review 目标。这是 ocr 的轻微 bug（应该自动跳过 deleted files），但不影响其他 findings 质量。

2. **`test_scope_index_injection_does_not_leak` 测试名歧义** — 测试本身就误命名（它测的是"注入"而非"不泄漏"）。ocr 指出测试名与行为不符是对的，但也帮我们意识到测试**写错了名字**而非测试逻辑错了。

---

## 重启操作指南（未来要修时）

```bash
# 1. 修 B1（YAML 拆 2 规则）
cd E:/projects/论文书写模版格式规范软件
$EDITOR app/core/rules/packs/GB-T-7713-1-2006.yaml
# 把 PG-BODY-PARAGRAPH-SPACING 拆成 PG-BODY-SPACE-BEFORE + PG-BODY-SPACE-AFTER
$EDITOR tests/unit/test_rules_pack_gb7713.py
# 把 test_PG_BODY_PARAGRAPH_SPACING_violated_by_6pt_before 拆为 2 个

# 2. 修 B2（evaluator __index__ 不泄漏）
$EDITOR app/core/engine/evaluator.py
# 返回 (paragraph_index, node_copy)，copy.pop("__index__", None)
$EDITOR tests/unit/test_engine_evaluator.py
# 改测试名 + 新增 test_scope_node_does_not_contain_internal_index

# 3. 修 T0（perf 断言）
$EDITOR tests/integration/test_engine_perf.py
# 改 assertGreaterEqual(..., 0) → assertGreater(..., 0)

# 4. 跑全回归
python -m unittest tests.test_workflow tests.unit.* tests.integration.*

# 5. 选做：重跑 ocr 单审 loader.py
export PATH=$PATH:/f/npmPackage
ocr review --commit 6a904df --concurrency 1
```

---

**版本**：v1.0
**作者**：Claude Code（基于 alibaba/open-code-review 输出整理）
**日期**：2026-06-09
**关联报告**：[docs/reports/sprint-2-ocr-review-2026-06-08.md](reports/sprint-2-ocr-review-2026-06-08.md)
