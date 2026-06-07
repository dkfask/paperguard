# Sprint 1.5 — Live Checker 实时约束 PLAN

> **状态**：v1.0 草案（来自多代理 workflow，2026-06-07）
> **作者**：规划工作流（Research + Map + Draft + Review agents）
> **前置**：Sprint 1 完成（commit `6097817`，56 tests 验证）
> **后续**：实施前需用户拍板第 8 节 7 个开放问题
>
> **与 `docs/design-proposal.md` §4.1.2 的有意偏离**：design-proposal 原设计 `PyQtReporter(QObject)` 在 `app/core/reporters/`（会 import PyQt，违反 §4.1"严格无 PyQt 依赖"硬约束）。本 plan 把"Finding → QtIssue 投影"做成纯函数 + 纯 dataclass（无 QObject），Qt signal 仍由 `LiveFormatChecker` 发出。这是从 v1.1 review 反馈（Phase 4 Review Issue 1.4）确认的更优解。

---

## 1. Goal

Sprint 1.5 在不破坏 `app/core/` 零 PyQt 依赖的硬约束下，把规则引擎结果以"Grammarly 风格"实时推送到 PyQt 编辑器：学生在 `ContentEditor` 边写文本，0.5s debounce 后引擎跑出 `Finding[]`，经 `pyqt_reporter` 转 `QtIssue[]` 通过 Qt signal 推到侧边栏（IssueSidebar）+ 段落红色波浪线（FormatHighlighter），点击侧边栏项或一键修复按钮（LiveMutator）反向写回 `Project` 模型并触发新一轮检查。CLI `paperguard check`（Sprint 4）共用同一份 `app/core/engine/`，差异仅在报告层。

---

## 2. Architecture Decision Records

### ADR-1：IR ↔ PyQt 边界（Finding → QtIssue 投影）

- **状态**：decided
- **Context**：`app/core/ir/finding.py` 已定义 `Finding(rule_id, severity, message, location: Location, actual, expected, fix: Fix | None)`。`Location(section, paragraph_index, text_snippet)` 不可变（`@dataclass(frozen=True)`）。Sprint 1 设计文档（`docs/design-proposal.md` §4.1.2，line 447-482）规定 PyQt 边界走纯 dataclass `QtIssue`，不在 `app/core/` 内 import PyQt。
- **Decision**：新增 `QtIssue` frozen dataclass，**仅暴露 6 字段**：`rule_id: str`、`severity: str`（来自 `Severity.value`）、`message: str`、`location_text: str`、`paragraph_index: int`（高亮定位；找不到时 `-1`）、`fix_suggestion: str | None`。`Finding.fix` 投影为人类可读字符串（如 "设置行距为 1.5"），不暴露 `Fix.action/target/value` 内部细节。
- **Consequences**：
  - **优点**：`app/core/reporters/pyqt_reporter.py` 内 `import` 仅为 stdlib（dataclass），仍可被 CLI 共享；UI 改字段不影响核心。
  - **代价**：丢失结构化 `Fix`，修复按钮只能调 `LiveMutator.apply_fix(issue)`，由 `issue.rule_id + issue.paragraph_index` 反查原始 `Finding`。Sprint 1.5 在 `LiveMutator` 内部建 `dict[(rule_id, paragraph_index)] = Fix` 临时缓存；`no_issues` signal 时清空。

### ADR-2：Debounce 机制

- **状态**：decided
- **Context**：`ContentEditor.editor.textChanged` 每次按键都发信号（PyQt5 `QTextEdit` 默认行为）。设计文档（`docs/design-proposal.md` §4.1.1，line 414-444）选 `QTimer(setSingleShot=True)` 0.5s debounce。
- **Decision**：用 `QTimer.setSingleShot(True)` + `start(debounce_ms)`，学生每按一键重置 timer，0.5s 内未继续输入则触发 `_run_check`。
- **Consequences**：
  - **优点**：PyQt 原生、随主事件循环工作、不引入 `threading.Timer` / `asyncio` 跨线程复杂度。
  - **代价**：阻塞主线程 → 100 段全文检查必须 < 200ms（设计文档 §7 Sprint 1.5 性能基线）。Sprint 1.5 接受"全量检查"（V1.3 再做增量）；如 > 200ms 临时把 debounce 提到 1s。
  - **备选被拒**：`threading.Timer` 需要手动跨线程 emit Qt signal（`QMetaObject.invokeMethod` + `Qt.QueuedConnection`），代码量翻倍；`asyncio` 与 Qt 事件循环共存需 `qasync`，本项目不依赖。

### ADR-3：Issue 更新策略

- **状态**：decided
- **Decision**：**全量替换 + signal-based**。`LiveFormatChecker` 计算完一次性 `issues_found.emit(List[QtIssue])`；UI 三处（侧边栏 / 高亮 / 状态栏）各自完整重建。空时发 `no_issues`。
- **Consequences**：
  - **优点**：状态机简单，UI 不需要 diff；侧边栏 / 高亮 / 状态栏三者自然一致。
  - **代价**：100 个问题 → 100 行重建（毫秒级，可接受）。撤销/重做场景下用 Qt 的 `QTextEdit` 自带 undo stack，不影响本策略。
  - **备选被拒**：diff（"add/remove/update"）开销大、本场景收益小；item-model 增量更新要引入 `QAbstractListModel`（设计文档未要求，V1 可升级）。

### ADR-4：Fix 应用路径

- **状态**：decided
- **Decision**：**`Project` 模型 in-place mutation**。`LiveMutator.apply_fix(QtIssue)` 接收 `QtIssue`，从 checker 内部缓存 `dict[(rule_id, paragraph_index), Fix]` 取回结构化 `Fix`，按 `Fix.target`（点分属性名，如 `"paragraph.line_spacing"`）修改 `Project` 对应 `SectionContent` 的 HTML 字符串（用 `QTextEdit.setHtml` 重新载入或就地 `QTextCursor` 修改），再 `editor.textChanged` 自动触发新一轮 `request_check`。
- **Consequences**：
  - **优点**：MVP 阶段不写 `writers/docx_writer.py`（Sprint 5 才有）；修复立即可见，自然驱动 debounce 重新检查（自洽闭环）。
  - **代价**：内存态修复，关闭应用前必须 `Project.save()`（Sprint 5 引入 `docx_generator.save_project()` 即可解决）。
  - **备选被拒**：engine 写新 `Document` → `docx_loader` 保存（绕过 PyQt UI，违反"实时反馈"承诺）；直接修改 `QTextDocument` 底层（耦合太重）。

### ADR-5：Threading 模型

- **状态**：decided
- **Decision**：**全主线程（Qt event loop）**。`LiveFormatChecker` 是 `QObject` 子类；`QTimer` + 同步执行 `_run_check`（同步 `project_to_ir` + `engine.validate`）；结果通过 `pyqtSignal` 直发（PyQt 自动跨 direct connection 同线程 emit）。
- **Consequences**：
  - **优点**：零线程安全代码，Qt signal/slot 简单直接。
  - **代价**：检查期间 UI 短暂不响应（必须 < 200ms 满足设计文档性能基线）。
  - **备选被拒**：`QThread` + `QObject.moveToThread`（复杂度高，要处理 worker 生命周期、cancel 中途检查）；`QThreadPool` + `QRunnable`（同样需要跨线程 signal）。
  - **性能兜底**：如检查超 200ms，Sprint 1.5 临时把 debounce_ms 从 500 → 1000；Sprint 1.5 不实现增量检查（V1.3 计划）。

---

## 3. File-Level Deliverables

### `app/core/reporters/`（新文件）

#### `app/core/reporters/pyqt_reporter.py`
- **Purpose**：把 `Finding[]` 投影成 `QtIssue[]`，无 PyQt 依赖（纯 dataclass）。
- **Public API**：
  ```python
  @dataclass(frozen=True)
  class QtIssue:
      rule_id: str
      severity: str          # "error" | "warning" | "info"
      message: str
      location_text: str     # "第 3 章 第 5 段"
      paragraph_index: int   # -1 if unknown
      fix_suggestion: str | None

  def to_qt_issue(finding: Finding) -> QtIssue: ...
  def to_qt_issues(findings: Iterable[Finding]) -> list[QtIssue]: ...
  ```
- **Approx LOC**：~80
- **Test**：`tests/unit/test_pyqt_reporter.py`，~10 tests（field mapping / severity 枚举 → 字符串 / fix→text 转换 / 空列表 / paragraph_index=-1 fallback）
- **Dependencies**：`app.core.ir.finding`（already in Sprint 1）

### `app/core/loaders/`（新文件 — Must-fix #2）

#### `app/core/loaders/project_to_ir.py`（**新增**）
- **Purpose**：`project_to_ir(project, section_id) -> Document` — 把 PyQt `Project` 转成 `Document` IR。**Sprint 1 + Sprint 2 都没建**，但 `LiveFormatChecker._run_check` 必须有 IR 才能 check。`ContentEditor` 只显示单个 section，所以 converter 只覆盖当前 section。
- **Public API**：
  ```python
  def project_to_ir(project: Project, section_id: str) -> Document: ...
  ```
- **Approx LOC**：~50
- **Test**：`tests/unit/test_project_to_ir.py`，~6 tests（空 section / 单段 / 多段 / 标题层级 / 字体提取 / 不存在的 section_id 抛 ValueError）
- **Dependencies**：`app.core.ir`、`app.models.project`

### `app/core/engine/`（新文件 — Q1 决策）

#### `app/core/engine/stub_engine.py`（**新增** — Q1 用户决策）
- **Purpose**：Sprint 1.5 写 5 条规则的内存 stub engine，让性能基线可被真测试。Sprint 2 在此基础上加 YAML pack loader + 25+ 规则。
- **5 条规则**（Q2 决策的 6 类 Fix 对应的 5 条检测）：
  1. `R001_line_spacing` — 检查 `paragraph.line == 1.5`（行距必须 1.5）
  2. `R002_font_size` — 检查 `run.font.size == 12.0pt`（正文小四）
  3. `R003_first_line_indent` — 检查 `paragraph.first_line_indent == 0.74cm`（2 字符）
  4. `R004_font_family` — 检查 `run.font.eastAsia == "宋体"`
  5. `R005_paragraph_spacing` — 检查 `paragraph.space_before/after == 0pt`
  - 6. （可选）`R006_page_margin` — 在 Sprint 2 YAML pack 里再实现；Sprint 1.5 stub 不必含
- **Public API**：
  ```python
  def validate_docx(ir: Document, pack: str = "stub-GB-T-7713-1-2006") -> list[Finding]: ...
  ```
- **Approx LOC**：~120（每条规则 ~20 LOC）
- **Test**：`tests/unit/test_stub_engine.py`，~10 tests（每条规则 1-2 个 positive + 1-2 个 negative 测试）
- **Dependencies**：`app.core.ir`

### `app/services/`（新文件）

#### `app/services/live_format_checker.py`（**Q3 决策：自适应 debounce**）
- **Purpose**：实时检查器（`QObject`），**自适应** debounce + Qt signal。
- **Public API**：
  ```python
  class LiveFormatChecker(QObject):
      issues_found = pyqtSignal(list)  # List[QtIssue]
      no_issues = pyqtSignal()
      check_failed = pyqtSignal(str)   # 失败容忍：异常信息

      def __init__(self, project, pack_name: str = "stub-GB-T-7713-1-2006",
                   parent=None): ...  # Q3: 不再接受 debounce_ms，改为自适应
      def request_check(self) -> None: ...
      def _compute_debounce_ms(self, paragraph_count: int) -> int:
          # Q3: < 30 → 300ms; 30-100 → 500ms; > 100 → 1000ms
          ...
      def get_fix(self, issue: QtIssue) -> Fix | None: ...  # 给 LiveMutator 用
  ```
- **Approx LOC**：~140（**Q3 调整**：120→140，+20 用于自适应 debounce 计算）
- **Test**：`tests/unit/test_live_format_checker.py`，~15 tests（**Q3 调整**：12→15，+3 自适应边界测试）（debounce 触发 / 异常吞掉不崩 / 连续 request_check 重置 timer / 空 findings 走 no_issues / get_fix 缓存命中 / **自适应 debounce: 29 段=300ms / 30 段=500ms / 100 段=500ms / 101 段=1000ms**）
- **Dependencies**：`app.core.ir`、`app.core.engine.stub_engine`（Q1 决策）、`app.core.reporters.pyqt_reporter`、`app.models.project`、PyQt5.QtCore

#### `app/services/live_mutator.py`（**Must-fix #1 + #3 + Q2 决策**）
- **Purpose**：把 `QtIssue` 对应的 `Fix` 应用到 `Project` 模型。**关键点（Must-fix #3）**：`SectionContent` 存的是 `blocks: list[ContentBlock]`（不是单一 HTML 字符串，`content_html` 是 computed property via `blocks_to_editor_html`）。mutator 必须定位到 `blocks[i]`，就地修改，再让 `QTextEdit.setHtml(section.content_html)` 重新载入（用 `editor.blockSignals(True/False)` 包裹以避免触发新一轮 check — Must-fix #1.3 提到的"修一次触发两次 check"问题）。
- **Q2 决策：6 类 Fix 全部支持**：
  1. `font_size_pt` — 修改 `ParagraphBlock.run.font.size`
  2. `line_spacing` — 修改 `ParagraphBlock.line_spacing`（或 `QTextCharFormat.setLineHeight`）
  3. `first_line_indent_cm` — 修改 `ParagraphBlock.first_line_indent`（或 `QTextBlockFormat.setIndent`）
  4. `font_family` — 修改 `ParagraphBlock.run.font.eastAsia`（或 `QTextCharFormat.setFontFamily`）
  5. `paragraph_spacing` — 修改 `ParagraphBlock.space_before/after`（或 `QTextBlockFormat.setTopMargin/setBottomMargin`）
  6. `page_margin` — 修改 `Section.margins`（Sprint 1.5 stub engine 不出此 rule，**Sprint 2 才实现**；LiveMutator 提前支持以便 Sprint 2 即插即用）
- **Public API**：
  ```python
  class LiveMutator:
      def __init__(self, project): ...
      def apply_fix(self, issue: QtIssue) -> bool: ...  # True=成功（MUST-FIX #1: 单参数，Fix 从 checker 缓存取）
      def can_apply(self, issue: QtIssue) -> bool: ...
      # Q6 决策：不需要 undo() 方法（QTextEdit 自带 Ctrl+Z）
  ```
- **Approx LOC**：~350（**Q2 调整**：220→350，每类 Fix ~50 LOC + ~20 LOC 共享框架）
- **Test**：`tests/unit/test_live_mutator.py`，~18 tests（**Q2 调整**：12→18，每类 Fix 2 tests + 共享 6 tests）（line_spacing set / font_size set / first_line_indent set / **font_family set / paragraph_spacing set / page_margin set** / 不能 fix 时返回 False / 改完 blocks 而不是 HTML / 改完触发 re-check 由调用方负责 / QTextEdit.setHtml 用 blockSignals 包裹 / 不存在的 paragraph_index 返 False / 修复后属性值正确）
- **Dependencies**：`app.core.ir.finding.Fix`、`app.models.project`

#### `app/services/issue_sidebar.py`（**Q4 决策** — 内嵌到可分离浮窗）
- **Purpose**：`QWidget` 侧边栏内容，展示 `QtIssue[]` 列表。被 `floating_panel.py` 包裹。
- **Public API**：
  ```python
  class IssueSidebar(QWidget):
      issue_activated = pyqtSignal(QtIssue)  # 用户点击行
      fix_requested = pyqtSignal(QtIssue)    # 用户点"修复"
      def update_issues(self, issues: list[QtIssue]) -> None: ...
      def clear(self) -> None: ...
  ```
- **Approx LOC**：~150
- **Test**：`tests/unit/test_issue_sidebar.py`，~6 tests（Qt offscreen 渲染 / 列表更新 / 点击发出 signal）
- **Dependencies**：`app.core.reporters.pyqt_reporter.QtIssue`、PyQt5

#### `app/services/floating_panel.py`（**新增** — Q4 用户决策）
- **Purpose**：把 `IssueSidebar` 包成 `QDockWidget`，可拖出主窗口右侧。多显示器友好。
- **Public API**：
  ```python
  class FloatingPanel(QDockWidget):
      def __init__(self, parent=None): ...
      def get_sidebar(self) -> IssueSidebar: ...
  ```
- **Approx LOC**：~50
- **Test**：`tests/unit/test_floating_panel.py`，~3 tests（widget 包含 IssueSidebar / 可拖动 / 默认停靠右侧）
- **Dependencies**：`app.services.issue_sidebar`、PyQt5

#### `app/services/format_highlighter.py`
- **Purpose**：在 `QTextEdit` 段落上画红色波浪线。
- **Public API**：
  ```python
  class FormatHighlighter:
      def __init__(self, text_edit: QTextEdit): ...
      def mark(self, issues: list[QtIssue]) -> None: ...
      def clear(self) -> None: ...
  ```
- **Approx LOC**：~90
- **Test**：`tests/unit/test_format_highlighter.py`，~5 tests（paragraph_index 越界降级 / 重复 paragraph 合并 / clear 清掉所有 extraSelections）
- **Dependencies**：`app.core.reporters.pyqt_reporter.QtIssue`、PyQt5.QtGui

### `app/views/`（修改现有文件）

#### `app/views/main_window.py`（**新增改动** — Q4 决策）
- **改动**：在 `MainWindow.__init__` 加 1 行：`self.addDockWidget(Qt.RightDockWidgetArea, FloatingPanel(self))`。让浮窗默认停靠右侧，可拖动分离。
- **Approx LOC delta**：+5
- **Test**：间接覆盖（手动启动验证）
- **Dependencies**：`app.services.floating_panel.FloatingPanel`

#### `app/views/project_workspace_view.py`（**Q4 决策调整**）
- **改动**：原来在右侧加 QTabWidget 改为**不再改这个文件**（sidebar 改在主窗口级，主窗口持有）。
- **Approx LOC delta**：0（不改动）

#### `app/views/content_editor.py`（修改）
- **改动**：在 `__init__` 末尾追加 ~30 行：
  1. 创建 `self.checker = LiveFormatChecker(project=self._resolve_project())`（Q3 决策：自适应 debounce）
  2. 创建 `self.mutator = LiveMutator(project=self._resolve_project())`
  3. 创建 `self.highlighter = FormatHighlighter(self.editor)`
  4. 信号连接：`editor.textChanged → checker.request_check`、`checker.issues_found → self._on_issues`、`checker.no_issues → self._on_clean`、`checker.check_failed → self._on_check_failed`
  5. 暴露 `set_project(project)` / `get_status_bar_text()` 给上层
- **新增 3 个 slot**：`_on_issues(issues)` / `_on_clean()` / `_on_check_failed(msg)`
- **Approx LOC delta**：+50 / -0
- **Test**：`tests/integration/test_content_editor_live.py`（新文件）
- **Dependencies**：上面 5 个新 services

### `app/controllers/`（按需小改）

- **判定**：Sprint 1.5 不动 `controllers/`（`workspace_ctrl.py` / `project_ctrl.py` 都不调 LiveFormatChecker）。如运行发现 controller 阻塞信号连接，再补 ~10 行。

### `tests/unit/`（新文件）

| 文件 | 测试数估计 |
|------|----------|
| `tests/unit/test_pyqt_reporter.py` | 10 |
| `tests/unit/test_live_format_checker.py` | 12 |
| `tests/unit/test_live_mutator.py` | 8 |
| `tests/unit/test_issue_sidebar.py` | 6 |
| `tests/unit/test_format_highlighter.py` | 5 |
| **小计** | **~41 新 unit tests** |

### `tests/integration/`（新文件）

#### `tests/integration/test_live_checker_pyqt.py`（**Must-fix #2.5**）
- **场景**：用 `QApplication` + `QT_QPA_PLATFORM=offscreen` 跑：
  1. 创建 `Project` + `ContentEditor`
  2. 注入 100 段 HTML 到 `QTextEdit`（构造"学生写论文"模拟）
  3. 连 `textChanged` → 触发 checker
  4. **用 `QTest.qWait(550)` 等待 550ms（> debounce）让 QTimer 触发**（**Nice-to-have #2**：不要用 `time.sleep(0.6)`，它不 pump Qt 事件循环，timer 永远不会 fire；CI 上必出问题）
  5. 断言 sidebar 显示 N 条 issue
  6. 模拟点击侧边栏 → 调 `mutator.apply_fix`
  7. 断言 HTML 改变 + 状态栏从 "⚠ N" 变 "✓ 合规"
- **Approx LOC**：~150
- **Test 数**：4（端到端 × 4 场景：metadata / 标题层级 / 字体 / 全文清洁）

#### `tests/integration/test_live_checker_perf.py`
- **场景**：100 段论文 < 200ms 跑完一次 check。
- **Test 数**：2（baseline + 带 fix 路径）

### `scripts/`（新文件）

#### `scripts/sprint1_5_smoke.py`
- **Purpose**：复制 `sprint1_smoke.py` 模式，构造内存 `Project` → 启 `QApplication(offscreen)` → 灌 50 段 HTML → 启 `LiveFormatChecker` → sleep 0.6s → 打印 sidebar 文本 → 断言 issue 数。
- **Approx LOC**：~80
- **使用**：`python scripts/sprint1_5_smoke.py`

---

## 4. Test Strategy

### 4.1 TDD 顺序（RED → GREEN → REFACTOR）

0. **project_to_ir（Must-fix #2）** — 6 tests RED first
1. **pyqt_reporter** — 4 tests RED → GREEN
2. **stub_engine（🆕 Q1 决策）** — 10 tests RED first（每条规则 2 个）：
   - `test_R001_line_spacing_1.5_passes`、`test_R001_line_spacing_1.0_fails`
   - `test_R002_font_size_12pt_passes`、`test_R002_font_size_14pt_fails`
   - `test_R003_first_line_indent_0.74cm_passes`、`test_R003_first_line_indent_0_fails`
   - `test_R004_font_family_songti_passes`、`test_R004_font_family_arial_fails`
   - `test_R005_paragraph_spacing_0pt_passes`、`test_R005_paragraph_spacing_6pt_fails`
3. **live_format_checker（Q3 决策 +3 tests）** — 8 tests：
   - `test_request_check_starts_timer`
   - `test_continuous_request_resets_timer`
   - `test_run_check_emits_issues_found`（**Q1 改用真 stub engine，不用 mock**）
   - `test_run_check_empty_emits_no_issues`
   - `test_engine_exception_emits_check_failed`
   - **🆕 Q3 决策**：`test_adaptive_debounce_29_paragraphs_uses_300ms`
   - **🆕 Q3 决策**：`test_adaptive_debounce_30_paragraphs_uses_500ms`
   - **🆕 Q3 决策**：`test_adaptive_debounce_101_paragraphs_uses_1000ms`
4. **live_mutator（🆕 Q2 决策 +6 tests）** — 12 tests（每类 Fix 2）：
   - `test_apply_fix_line_spacing_sets_block`
   - `test_apply_fix_font_size_sets_block`
   - `test_apply_fix_first_line_indent_sets_block`
   - `🆕 test_apply_fix_font_family_sets_block`（Q2 +1）
   - `🆕 test_apply_fix_paragraph_spacing_sets_block`（Q2 +1）
   - `🆕 test_apply_fix_page_margin_sets_section`（Q2 +1，Sprint 2 才用）
   - `test_apply_fix_single_arg`（Must-fix #1）
   - `test_apply_fix_calls_blocksignals_around_sethhtml`（Must-fix #1.3）
   - `test_can_apply_unknown_rule_returns_false`
   - `test_apply_fix_modifies_blocks_not_html`（Must-fix #3）
   - `test_paragraph_index_out_of_range_returns_false`
   - `test_apply_fix_returns_true_on_success`
5. **issue_sidebar + format_highlighter + floating_panel（🆕 Q4 +3 tests）** — 14 tests（6 sidebar + 5 highlighter + 3 floating_panel）
6. **integration** — 6 tests（3 e2e + 2 perf + 1 qt_offscreen smoke）

### 4.2 覆盖率目标

- `app/core/reporters/pyqt_reporter.py` ≥ 95%（纯函数 + 投影）
- `app/core/engine/stub_engine.py` ≥ 95%（**🆕 Q1**，纯函数规则）
- `app/core/loaders/project_to_ir.py` ≥ 85%
- `app/services/live_format_checker.py` ≥ 85%（**Q3 自适应边界全覆盖**）
- `app/services/live_mutator.py` ≥ 85%（**Q2 6 类 Fix 全覆盖**）
- `app/services/issue_sidebar.py` / `format_highlighter.py` / `floating_panel.py` ≥ 75%（Qt widget 部分手工覆盖）
- 整体 Sprint 1.5 增量覆盖率 ≥ 80%（`docs/design-proposal.md` §6 CLAUDE.md 硬指标）
- **总测试数预期**：56（现有） + 56（unit：6 project_to_ir + 4 pyqt_reporter + 10 stub_engine + 8 live_checker + 12 live_mutator + 6 sidebar + 5 highlighter + 3 floating_panel + 2 fix_suggestion mapping）+ 6（integration） = **~118 tests**

### 4.3 手动冒烟测试

`scripts/sprint1_5_smoke.py`：
```bash
cd D:/projects/paperguard
QT_QPA_PLATFORM=offscreen python scripts/sprint1_5_smoke.py
```
预期输出包含："OK Sprint 1.5 端到端跑通" + "实时检查: N 个格式问题"。

### 4.4 Integration Test 模式

参考 `tests/test_workflow.py:11-12` 已建立的模式：
```python
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
cls.app = QApplication.instance() or QApplication([])
```
所有 PyQt 测试无需真实显示器。Sprint 1.5 integration tests 复用同一 fixture。

---

## 5. Commit Strategy（**Q1-Q4 决策后 12 commits**）

| # | Commit | Scope | 说明 |
|---|--------|-------|------|
| 1 | `test(sprint-1.5): RED — project_to_ir 转换测试` | test | 6 个 RED（Must-fix #2） |
| 2 | `feat(sprint-1.5): project_to_ir converter` | feat | `app/core/loaders/project_to_ir.py` |
| 3 | `test(sprint-1.5): RED — pyqt_reporter 投影测试` | test | 4 个 RED |
| 4 | `feat(sprint-1.5): pyqt_reporter + QtIssue dataclass` | feat | `app/core/reporters/pyqt_reporter.py` |
| 5 | `test(sprint-1.5): RED — stub_engine 5 规则测试` | test | **🆕 Q1 决策**，10 个 RED（5 规则 × 2） |
| 6 | `feat(sprint-1.5): stub_engine 5 规则（line_spacing/font_size/indent/font_family/paragraph_spacing）` | feat | **🆕 Q1 决策**，`app/core/engine/stub_engine.py` |
| 7 | `test(sprint-1.5): RED — live_format_checker 自适应 debounce 测试` | test | 8 个 RED（**🆕 Q3 决策** +3 边界） |
| 8 | `feat(sprint-1.5): LiveFormatChecker (QObject + 自适应 debounce)` | feat | `app/services/live_format_checker.py` |
| 9 | `feat(sprint-1.5): LiveMutator (Q2 6 类 Fix) + IssueSidebar + FormatHighlighter + FloatingPanel` | feat | 4 个 services 一起（**🆕 Q2 +3 Fix 类型 / Q4 浮窗**） |
| 10 | `feat(sprint-1.5): ContentEditor 集成 + main_window 浮窗挂载` | feat | view 改动（**🆕 Q4 改 main_window.py**） |
| 11 | `test(sprint-1.5): integration tests + perf baseline` | test | 6 个 integration（含 `sample_paper_100paras.docx` fixture） |
| 12 | `chore(sprint-1.5): scripts/sprint1_5_smoke.py` | chore | 冒烟脚本 |
| 13 | `docs(sprint-1.5): HANDOFF.md 标记 Sprint 1.5 完成` | docs | 配合 Phase E |
| 14 | `chore(sprint-1.5): verify grep app/core/ 无 PyQt import` | chore | DoD grep 检查 |

**总计**：**14 atomic commits**（Q1-Q4 决策后从 10 → 14；新增 stub_engine 4 commits + Q4 浮窗 1 commit + Q2 修复扩展 1 commit）。

---

## 6. Risks & Mitigations

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| **1** | ~~`engine.validate()`（Sprint 2 才有）尚未实现~~ | ~~RED 测试无 callable~~ | **🆕 Q1 决策消除**：Sprint 1.5 自带 5 规则 stub engine（`app/core/engine/stub_engine.py`），性能基线可被真测试。Sprint 2 在 stub 基础上加 YAML pack loader + 25+ 规则 |
| **2** | `Project` 模型用 `blocks: list[ContentBlock]` 存内容（`content_html` 是 computed property），`Fix.target` 如何映射到 blocks 修改？ | mutator 实现复杂 | **🆕 Q2 决策扩展**：支持 6 类 Fix（font_size_pt / line_spacing / first_line_indent_cm / font_family / paragraph_spacing / page_margin）。mutator 走 `blocks[i]` 就地修改 + `QTextEdit.setHtml(section.content_html)` 重新载入（用 `editor.blockSignals` 包裹避免双触发 check）。Sprint 5 写 `docx_writer` 后再用 docx 路径保存 |
| **3** | 100 段论文检查 > 200ms 触发 UI 卡顿 | 用户体验差 | **🆕 Q3 决策缓解**：自适应 debounce — < 30 段 300ms / 30-100 段 500ms / > 100 段 1000ms。性能基线测试 `test_live_checker_perf.py` 仍必须 < 200ms。V1.3 增量检查（仅查变化段）作为后续优化 |

---

## 7. Definition of Done

- [ ] `pytest tests/` 全绿（**~118 tests**，含 56 现有 + ~62 新增）
- [ ] `python scripts/sprint1_5_smoke.py` 端到端通过
- [ ] `grep -r "PyQt" app/core/` 仅匹配 docstring 注释（零 import，**Sprint 1 硬约束保持**）
- [ ] 现有 56 tests 仍全绿（无 regression）
- [ ] PyQt 桌面应用启动后，在 ContentEditor 输入文本，**自适应 debounce** 后侧边栏出现 issue，状态栏显示 "⚠ N 个格式问题"
- [ ] 点击侧边栏 issue 旁的"修复"按钮，对应段落 blocks 修改，**Ctrl+Z 可撤回**（Q6 决策），状态栏变 "✓ 格式合规"
- [ ] **Q4 决策**：浮窗可从主窗口右侧拖出到副屏；停靠位置记忆
- [ ] **Q1 决策**：5 条 stub 规则全部工作；性能基线 < 200ms（用真 engine，不再 mock）
- [ ] **Q2 决策**：6 类 Fix 全部可应用（page_margin 仅 Sprint 2 用）
- [ ] **Q3 决策**：不同论文长度触发对应 debounce（断言测试通过）
- [ ] `HANDOFF.md` 标记 Sprint 1.5 完成
- [ ] Push 到 GitHub `main` 分支（commit hash 更新到 `HANDOFF.md`）

---

## 8. Decisions (Resolved Open Questions — 2026-06-07 修订)

> 以下 7 个原开放问题已基于 **用户决策** + **设计文档** + **Risk 缓解策略**作出。**6 个原默认决策被用户推翻**（Q1-Q6），仅 Q7 保持。

| # | 原问题 | **最终决策**（用户选） | 理由 / 影响 |
|---|--------|------|------|
| 1 | `validate_docx` mock 策略 | **🆕 Sprint 1.5 写 5 条规则 stub engine**（line_spacing / font_size / first_line_indent / font_family / paragraph_spacing） | Sprint 1.5 不再 mock；性能基线可被真测试。Sprint 2 在 stub 基础上加 YAML pack loader + 25+ 规则 |
| 2 | `Project` Fix 类型范围 | **🆕 6 类**：font_size_pt / line_spacing / first_line_indent_cm / **font_family** / **paragraph_spacing** / **page_margin** | 全部 user-applicable；LiveMutator 220→350 LOC，tests 12→18 |
| 3 | Debounce 默认值 | **🆕 自适应**：< 30 段 300ms / 30-100 段 500ms / > 100 段 1000ms | 大论文不卡；小论文响应快；`_compute_debounce_ms(paragraph_count)` 函数 |
| 4 | 侧边栏挂载位置 | **🆕 右侧浮窗（可分离）**：`QDockWidget` 顶部对齐右侧，可拖出主窗口 | 多显示器友好；学生在副屏盯 sidebar，主屏写论文。`main_window.py` 需加 addDockWidget |
| 5 | 失败容忍错误归宿 | **状态栏 + 控制台日志**（保持默认） | Python `logging` 模块；状态栏显 "⚠ 检查失败" |
| 6 | 一键修复按钮的"撤销" | **QTextEdit 自带 undo**（保持默认） | 不实现自定义 undo；学生用 Ctrl+Z |
| 7 | `sample_paper_100paras.docx` fixture | **新增**（保持默认） | 性能基线测试 `test_live_checker_perf.py` 需要 |

### 8.1 决策对 Sprint 1.5 规模的影响

| 维度 | 原默认 | 用户调整后 | 增量 |
|------|--------|------------|------|
| 新增文件 | 9 | 11（+2：stub_engine.py, floating_panel.py） | +2 |
| 新增 LOC | ~640 | **~920** | +280 |
| 新增测试 | 47 | **62** | +15 |
| Commit 数 | 10 | **12**（+2：stub engine） | +2 |
| 估算工作量 | ~1 周 | **~1.5 周** | +0.5 周 |
| PyQt 主窗口改动 | 无 | **+30 LOC**（main_window.py addDockWidget） | +30 |

---

## 附件：关键引用映射

| 决策 | 出处 | 行号 |
|------|------|------|
| Sprint 1.5 在 MVP 路径的位置 | `docs/design-proposal.md` §7 | line 704-714 |
| `QtIssue` 数据类设计 | `docs/design-proposal.md` §4.1.2 | line 447-482 |
| `LiveFormatChecker` API | `docs/design-proposal.md` §4.1.1 | line 407-445 |
| ContentEditor 集成代码示例 | `docs/design-proposal.md` §4.1.3 | line 484-502 |
| PyQt 实时约束工作流 | `docs/design-proposal.md` §5.6 | line 585-635 |
| 风险：实时检查 UI 卡顿 | `docs/design-proposal.md` §10 | line 788 |
| IR 不可变 + 点分属性名 | `docs/design-proposal.md` §3.1 | line 125-186 |
| Finding/Loc/Fix 完整定义 | `docs/design-proposal.md` §3.2 | line 192-209 |
| "核心完全无 PyQt 依赖" | `docs/design-proposal.md` §4.1 | line 504 |
| Sprint 1 已落地的 IR/Finding | `app/core/ir/finding.py` | line 1-47 |
| 现有 ContentEditor 结构 | `app/views/content_editor.py` | line 1-277 |
| 现有 format_validator 模式 | `app/services/format_validator.py` | line 1-115 |
| 现有 Project 模型（HTML 存内容） | `app/models/project.py` | line 1-80 |
| 测试 Qt offscreen 模式 | `tests/test_workflow.py` | line 11-12, 47-50 |
| Sprint 1.5 性能基线 | `docs/design-proposal.md` §7 | line 714（"100 段论文 < 200ms"） |
