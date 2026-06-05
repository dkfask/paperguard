# 代码图谱摘要（codegraph 自动生成）

> **生成方式**：`codegraph init` + `codegraph status` + `codegraph files` + `codegraph query/callers/callees/impact`
> **索引时间**：2026-06-05
> **索引规模**：37 文件 / 618 节点 / 1479 边 / 1.72 MB DB
> **本目录是 `true` 的"项目代码长啥样"**——和 `docs/design-proposal.md` 的"未来架构"对照看。

## 0. 2026-06-05 v1.2 更新

**产品定位升级**：从"事后批改工具"升级为"**PyQt 实时约束 + CLI 事后批改**双模式"。

**关键洞察**：PyQt 现有 `app/views/content_editor.py` 23 symbols 本身**就是学生写作工具**——PaperGuard 实时约束不需要新写编辑器，**只需在 ContentEditor 里加一个 LiveFormatChecker 即可**。

详见 [`docs/design-proposal.md`](./design-proposal.md) v1.2 修订记录 + §4.1 新增模块设计。

---

## 1. 一句话总结

> 项目里**已经有一套 PyQt5 桌面应用骨架**（MVC 三层 + services），核心 service 命名 (`format_validator` / `docx_generator` / `word_template_importer`) 高度匹配本设计文档。可以**复用现有 PyQt 代码**作为 L4 用户接入层（GUI 入口），把新核心 (CLI 规则引擎) 放在 `src/paperguard/` 与之并行。

---

## 2. 索引统计

| 维度 | 值 |
|------|-----|
| Files | 37 |
| Nodes | 618 |
| Edges | 1,479 |
| DB | 1.72 MB |
| 后端 | node:sqlite（WAL 模式） |
| 语言 | Python (35) + YAML (2) |

**节点类型分布**：
- `method` × 250（PyQt 类方法为主）
- `import` × 181（依赖关系）
- `function` × 82
- `class` × 39
- `file` × 35
- `variable` × 31

---

## 3. 目录结构（symbols 数）

```
论文书写模版格式规范软件/
├── main.py                                  7 symbols
├── app/
│   ├── __init__.py                          1
│   ├── controllers/                         54 symbols
│   │   ├── export_ctrl.py                   8
│   │   ├── project_ctrl.py                  16
│   │   ├── review_ctrl.py                   10  ← 用 format_validator
│   │   ├── template_ctrl.py                 15  ← 用 template_parser
│   │   └── workspace_ctrl.py                5
│   ├── models/                              62 symbols
│   │   ├── format_config.py                 19
│   │   ├── project.py                       26
│   │   └── template.py                      17
│   ├── services/                            154 symbols（**最重的一层**）
│   │   ├── content_blocks.py                41
│   │   ├── demo_seed_service.py             12
│   │   ├── docx_generator.py                30  ← 被 export_ctrl 用
│   │   ├── format_validator.py              7   ← **核心规则引擎雏形**
│   │   ├── navigation_state_service.py      9
│   │   ├── recent_items_service.py          19
│   │   ├── template_parser.py               9   ← 被 template_ctrl 用
│   │   └── word_template_importer.py        26  ← 被 main_window 用
│   └── views/                               203 symbols（**第二重**）
│       ├── content_editor.py                23
│       ├── dashboard_view.py                12
│       ├── help_workspace_view.py           5
│       ├── main_window.py                   60  ← **最大文件，PyQt 主窗**
│       ├── project_workspace_view.py        17
│       ├── review_export_view.py            10
│       ├── template_editor.py               36
│       ├── template_workspace_view.py       23
│       └── tree_panel.py                    16
├── tests/
│   └── test_workflow.py                     64 symbols（**最大测试文件**）
├── references/format-thesis-docx/          (submodule, 未索引)
├── docs/                (7 个 markdown，未索引)
├── SKILL.md / SKILL_USAGE.md / agents/      (未索引)
└── research/                                  (未索引 — 太大)
```

---

## 4. 核心调用链

### 4.1 应用入口

```
main.py:14  →  from app.views.main_window import MainWindow
                  ↓
              app/views/main_window.py:38  class MainWindow
                  ├── __init__ (line 41)
                  ├── _setup_ui (line 62)
                  ├── _create_menus
                  ├── _build_review_summary (line 241)  ← 关键
                  └── ...
```

### 4.2 论文格式验证链路

```
ReviewController.build_summary  (app/controllers/review_ctrl.py:28)
        ↓ 调用
validate_project  (app/services/format_validator.py:24)
        ↓ 只 import 3 个东西
   • dataclasses
   • app.models.project
   • app.models.template
```

**输出**：`ValidationIssue` dataclass + 验证结果列表

**测试覆盖**：3 处（`tests/test_workflow.py:243` / `:271` / `:351`）

### 4.3 论文导出链路

```
ExportController  (app/controllers/export_ctrl.py:9)
        ↓ 调用
DocxGenerator  (app/services/docx_generator.py)
        ↓ 输出
   .docx 文件
```

### 4.4 模板导入链路（**对应本设计的 `extract` 功能**）

```
MainWindow  (app/views/main_window.py:30)
        ↓ 调用
import_word_template  (app/services/word_template_importer.py)
        ↓ 输出
   解析后的 .ptpl 模板对象
```

`word_template_importer.py` 26 symbols——**几乎是 `format-thesis-docx/extract_requirements.py` 的 PyQt 等价物**。可以借鉴它的设计来反向抽出"规则"。

### 4.5 模板持久化链路

```
TemplateController  (app/controllers/template_ctrl.py:8)
        ↓ 调用
load_template / save_template  (app/services/template_parser.py)
        ↓ 也被
demo_seed_service.py:9
        ↓ 也被
tests/test_workflow.py
```

---

## 5. 影响图：改 `validate_project` 会怎样？

**9 个符号受影响**：

```
validate_project (format_validator.py:24)
  ↓ 影响
  ├── format_validator.py (file + function)
  ├── ReviewController.build_summary (review_ctrl.py:28)
  │     ↓
  │   ReviewController (class, review_ctrl.py:25)
  │     ↓
  │   main_window.py:241  _build_review_summary
  │     ↓
  │   tests/test_workflow.py:351  test_review_export_view_shows_last_export_result_summary
  │   tests/test_workflow.py:243  test_required_section_empty_is_error_and_blocks_export
  │   tests/test_workflow.py:271  test_validator_skips_toc_and_uses_cover_metadata_rules
  │   WorkflowTests (class, test_workflow.py:47)
```

**含义**：`validate_project` 是**关键单点**——改它要跑整个 `test_workflow.py` 验证。

---

## 6. 与 docs/design-proposal.md 的 4 层架构对照

| 设计文档（L1-L4） | PyQt 现有代码 | 对应关系 |
|------------------|--------------|---------|
| **L4 用户接入层**：CLI / Web / Skill | `app/views/` (PyQt 9 个) | ✅ 已有 GUI 入口 |
| **L4 路由/控制器** | `app/controllers/` (5 个) | ✅ 已有 |
| **L2 规则引擎 + L1 数据访问** | `app/services/` (9 个) | ⚠️ 雏形，**需升级**为真正的规则引擎 |
| **L1 业务模型** | `app/models/` (3 个) | ✅ 已有 |
| **CLI 子命令（paperguard xxx）** | ❌ | ❌ 缺失 |
| **报告层 (L3)** | ❌ 散落在 views | ⚠️ 应抽离 |
| **YAML 规则包** | ❌ 散落在 `.ptpl` | ⚠️ `.ptpl` 是二进制，需 YAML 化 |
| **修复（mutator）** | ❌ 仅"导出"无"修复" | ❌ 缺失 |

**结论**：
- ✅ **L4 + L1 已有**（PyQt 视图 + 业务模型）
- ⚠️ **L2 services 是雏形**——`format_validator` 只 7 symbols，是"模板验证"而非"通用规则引擎"
- ❌ **CLI 入口** 完全缺失
- ❌ **YAML 规则包** 完全缺失（`.ptpl` 是项目里既有的 PyQt 私有格式，不通用）

---

## 7. 重构建议（基于 codegraph 洞察）

### 7.1 短期（Sprint 1-2）—— 不动 PyQt，做并行核心
```
src/paperguard/          ← 新建（按 docs/design-proposal.md §4）
├── ir/                  ← 不可变 dataclass
├── loaders/             ← 读 docx → IR
├── engine/              ← YAML 规则 + JSONLogic 表达式
├── rules/_builtins/     ← 30+ 内置规则
├── reporters/           ← terminal / json / html / sarif
└── writers/             ← 写回 docx
```

**理由**：PyQt 现有代码与"通用规则引擎"目标差距大，强行在 PyQt 里改反而慢。

### 7.2 中期（Sprint 3-5）—— 把 services 重构为 core 的 thin wrapper
```python
# app/services/format_validator.py 重写为：
from paperguard.engine import validate_docx
from paperguard.ir import IR

def validate_project(project) -> list[ValidationIssue]:
    """Thin wrapper: 复用 paperguard core 给 PyQt 用"""
    ir = project_to_ir(project)
    findings = validate_docx(ir, pack=project.template.pack_name)
    return [to_validation_issue(f) for f in findings]
```

**好处**：
- CLI 和 GUI 共享同一个 core（避免双轨维护）
- PyQt 现有 UI 不变，service 实现替换

### 7.3 长期（V1-V2）—— 完全统一
- `app/services/` 全部变成 thin wrapper
- 真正的规则定义都在 `paperguard/rules/packs/*.yaml`
- GUI 只是另一种 "reporter"

---

## 8. 关键文件清单（按"未来会用"排序）

| 优先级 | 文件 | 用途 |
|--------|------|------|
| 🔴 P0 | `app/services/format_validator.py` | **核心规则引擎雏形**——Sprint 1 直接 fork 它做升级 |
| 🔴 P0 | `app/services/word_template_importer.py` | **extract 功能的基础** |
| 🔴 P0 | `tests/test_workflow.py` | **现有测试基础设施**——Sprint 1 复用 |
| 🔴 P0 | `app/views/content_editor.py` | **🆕 v1.2 实时约束挂载点**——Sprint 1.5 集成 LiveFormatChecker |
| 🟡 P1 | `app/services/docx_generator.py` | **writer**——Sprint 5 复用 |
| 🟡 P1 | `app/services/template_parser.py` | **YAML 解析参考** |
| 🟡 P1 | `app/models/template.py` | **模板数据模型**——需要泛化为 "Rule Pack" |
| 🟢 P2 | `app/models/format_config.py` | **格式配置数据模型** |
| 🟢 P2 | `app/services/content_blocks.py` | 内容块拼装（V1 复用） |
| 🆕 v1.2 | `app/services/live_format_checker.py` | **新建**——Sprint 1.5 实现，0.5s debounce 实时检查 |
| 🆕 v1.2 | `app/core/reporters/pyqt_reporter.py` | **新建**——Sprint 1.5 实现，把 Finding[] 转为 QtIssue[] |
| ⚪ P3 | `app/views/main_window.py` 等 | UI 层——Sprint 1-5 几乎不动 |
| ⚪ P3 | `app/controllers/*` | 控制器层——Sprint 1-5 几乎不动 |

---

## 9. 一个意外发现

项目里**已有 64 symbols 的 workflow 测试**——这意味着：

- ✅ 现有功能有覆盖
- ✅ Sprint 1 的 TDD 目标有参照样板
- ✅ 重构 services 时有"防回归网"

**建议**：Sprint 1 第一件事是 `pytest tests/test_workflow.py` 让现有测试通过，建立信心。然后再 fork `format_validator` 升级为新规则引擎。

---

## 10. 维护这个文档

每次大改后跑：

```bash
# 重新索引
codegraph sync

# 重新统计
codegraph status -p .

# 看改了哪些文件的依赖
codegraph impact <symbol>
```

把这个文档作为 MVP Sprint 1 开工的"地图"。

---

**配套文档**：
- `docs/design-proposal.md`（未来架构，4 层）
- `docs/format-thesis-docx-review.md`（参考项目，945 行）
- `docs/research-report.md`（5 份调研综合）
- `references/format-thesis-docx/`（submodule，参考项目源码）
