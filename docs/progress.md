# PaperGuard Progress

> 本文件用于持续记录项目进度。以后每完成一个设计节点、实现节点或验证节点，都优先更新这里。

## 当前状态

- 当前阶段：Word 模板导入已支持从标题层级生成章节结构；PyQt DOCX 批改面板已支持问题列表、定位反馈、逐条修复和导出摘要。
- 当前分支：`codex/sprint-5-mutator`
- 最近提交：`9fcc480 feat(template): import word heading structure`
- 最近全量验证：`python -m pytest -p no:cacheprovider -q` -> `504 passed, 1 skipped`
- 最近 smoke：`python scripts/pyqt_docx_session_smoke.py` -> `OK PyQt DOCX review session smoke passed`
- 当前可用程度：CLI 批改/修复链路可内部试用；PyQt 实时约束仍是 beta。

## 已实现

### 核心数据与读取

- 不可变 IR：`Document`、`Section`、`Paragraph`、`Run`、`Finding`、`Location`、`Fix`、`Severity`。
- `.docx` loader：
  - 正文段落读取。
  - 页眉/页脚段落读取。
  - 表格单元格段落读取，含合并单元格去重。
  - 表格单元格段落定位：`table_index` / `table_row_index` / `table_cell_index`。
  - 列表段落元数据读取：`list_type` / `list_level`。
  - section 页面尺寸、页边距、方向、页眉页脚距离读取。
  - 段落行距、段前段后、首行缩进、对齐、分页前读取。
  - 参考文献悬挂缩进读取。
  - run 字体、字号、粗体、斜体、下划线、颜色、高亮、上下标读取。
  - 样式继承与 base style chain 的主要格式读取。

### 规则引擎与规则包

- YAML 规则包 loader，含 pydantic schema 校验。
- JMESPath scope + JSONLogic when 规则求值。
- visitor 生成 `Finding`，支持正文、页眉、页脚段落定位。
- 内置规则包：
  - `GB-T-7713-1-2006`
  - `GB-T-7714-1-2015`（含参考文献悬挂缩进规则；参考文献字体、字号、下划线、加粗规则已覆盖多 run）
  - `SIM-UNIV-generic`（含 Sprint 5 表格单元格字号、列表行距规则）

### CLI 与报告

- CLI 命令：
  - `check`
  - `fix`
  - `extract`
  - `init`
  - `validate-pack`
  - `list-packs`
  - `doctor`
  - `version`
- `check --format` 支持：
  - `text`
  - `json`
  - `html`
  - `sarif`
- 报告器：
  - terminal
  - JSON
  - HTML
  - SARIF
  - PyQt issue reporter
- `pyproject.toml` 已提供 `paperguard` console script。

### 自动修复与写回

- `fix_applier` 支持：
  - `set`
  - `runs.*.<attr>` 批量修复所有 run
  - `insert paragraph.before`
  - `insert paragraph.after`
  - `delete paragraph`
  - 正文、页眉、页脚段落定位
  - 负数 paragraph index 防护
- `docx_writer` 支持写回：
  - 段落行距
  - 段前段后
  - 首行缩进
  - 悬挂缩进
  - 段落对齐
  - 分页前
  - run 字体、字号、粗体、斜体、下划线、颜色、高亮、上下标
  - section 页面尺寸、页边距、方向、页眉页脚距离
  - 多 section
  - 页眉/页脚段落
  - run 为空时保留 paragraph text
  - 自定义段落样式，如 `Bibliography`
  - 表格单元格段落重建与写回。
  - 列表段落样式重建与写回。
- GB/T 7714 参考文献 run 级修复已覆盖多 run 段落，并有 `fix -> write -> load -> validate` 回归测试。
- SIM-UNIV 表格单元格字号、列表行距已覆盖 `fix -> write -> load -> validate` 回归测试。
- `fix` 默认二次 check 并写 `changes.md`。
- `--skip-verify` 可跳过二次 check，兼容隐藏别名 `--no-verify`。

### PyQt 主路径

- 已有桌面应用骨架。
- 已接入 Live Checker。
- 已有侧边栏 issue、格式高亮、LiveMutator 基础修复能力。
- 已有 PyQt-facing `DocxReviewSession` 服务，可执行 `.docx` 导入、规则检查、批量自动修复、导出新 `.docx`。
- `DocxReviewSession` 已支持 `apply_fix_at(index)` 逐条修复。
- `ReviewExportView` 已接入 DOCX 批改面板：规则包选择、导入、检查、问题列表、逐条修复、批量修复、导出。
- DOCX finding 卡片已支持“定位”反馈：显示当前规则、段落号和文本片段，并在状态栏同步当前定位。
- DOCX 导出后会在面板内显示导出摘要：文件名、剩余问题数和导出时间。
- `MainWindow` 已提供可测试的 DOCX 批改 UI 方法：`_open_docx_review_path`、`_check_docx_review_session`、`_fix_one_docx_review_finding`、`_fix_docx_review_session`、`_export_fixed_docx_review_path`。
- 已有 `scripts/pyqt_docx_session_smoke.py` 验证 3 个 pack 的违规样例会话闭环。

### 模板抽取

- 已有 FORMAT_WORDS 启发式。
- 已有模板抽取到规则包的基本链路。
- Word 模板导入已支持读取 `标题 1/2/3` 段落并生成模板章节结构，未识别到标题层级时继续回退默认结构。

### 合成真实场景样例回归

- 已有不含真实学生隐私的代码生成样例库：`tests/synthetic_regression_samples.py`。
- 覆盖 3 个内置规则包：
  - `GB-T-7713-1-2006`
  - `GB-T-7714-1-2015`
  - `SIM-UNIV-generic`
- 每个规则包覆盖 3 种状态：
  - 合规样例
  - 违规样例
  - 自动修复后样例
- 已有集成测试 `tests/integration/test_synthetic_regression_samples.py`，验证 `write -> load -> validate` 与 `fix -> write -> load -> validate` 闭环。
- 已有 smoke 脚本 `scripts/regression_samples_smoke.py`。

## 未实现 / 未完善

### 产品化缺口

- CI 未配置。
- Windows 桌面安装包未制作。
- 用户手册和面向普通用户的操作文档不完整。
- 异常恢复、日志、隐私提示、真实论文保护策略还不完整。

### PyQt 端缺口

- UI 仍需打磨到可交付状态。
- DOCX 批改入口已接入“检查与导出”工作区，并已有问题列表、定位反馈、逐条修复和导出摘要；异常提示仍需打磨。
- 保存/导出流程已有服务层和 UI 方法闭环，仍需做真实用户交互打磨。
- 一键修复后的真实 `.docx` 闭环已有服务层 smoke 和 PyQt 集成测试，仍需覆盖手工 GUI 冒烟。
- 长文档实时检查的体验和性能需要真实样例验证。

### 规则与 docx 能力缺口

- 目录、图表题注、交叉引用未成熟。
- 参考文献目前偏格式检查，不是完整 GB/T 7714 著录格式解析器。
- 复杂编号定义、编号续接、多级列表缩进仍需真实样例验证；基础列表样式闭环已完成。
- 学校规则包需要更多真实模板和匿名化/合成论文样例回归。
- 合成样例回归第一版已建立，但复杂长文档、真实学校模板、目录、题注、交叉引用样例仍需扩充。

## 当前可用性判断

- CLI 内部试用：可以。
  - 适合用样例文档或非重要文档跑 `check`、`fix`、HTML 报告和 `changes.md`。
- 给同学/老师直接使用：暂不建议。
  - PyQt 产品体验、安装包、真实模板覆盖和异常处理还没有完成。
- 作为论文规范产品 MVP：未完成。
  - 还需要更多复杂 docx 结构、PyQt 正常使用链路和发布流程。

## 后续路线

### P0：PyQt 正常使用链路

- 打磨 DOCX 批改面板的异常提示和失败恢复。
- 将 DOCX 批改结果和现有侧边栏/高亮定位进一步融合。
- 优化侧边栏交互、定位、高亮和一键修复反馈。
- 增加失败提示和不可自动修复项说明。

### P1：样例库扩充

- 增加复杂长文档、真实学校模板匿名化样例。
- 增加目录、图表题注、交叉引用、多级列表和编号续接样例。
- 把回归样例纳入后续 CI。

### P2：发布与工程化

- 配置 CI。
- 增加 release smoke。
- 制作 Windows 安装包。
- 完成 README、用户手册、故障排查文档。

## 预计时间

- CLI 内部试用稳定：约 1-2 周。
- 桌面软件可给同学/老师试用：约 4-6 周。
- 比较可靠的论文规范产品 MVP：约 2-3 个月。

以上估计基于当前代码状态，实际时间取决于真实学校模板数量、复杂 docx 结构覆盖范围和 UI 质量要求。

## 进度更新规则

以后每次完成设计或实现节点时，按下面格式更新本文件：

1. 更新 `当前状态`：
   - 当前阶段
   - 最近提交
   - 最近验证结果
   - 当前可用程度
2. 把新完成的能力加入 `已实现`。
3. 从 `未实现 / 未完善` 移走已经完成的事项。
4. 如果发现新风险或新缺口，加入对应小节。
5. 如果完成了一个明显节点，在 `更新记录` 追加一条。

## 更新记录

| 日期 | 提交/节点 | 内容 | 验证 |
|------|-----------|------|------|
| 2026-06-17 | `9fcc480` | Word 模板导入支持从 `标题 1/2/3` 生成章节结构，导入提示同步更新 | `504 passed, 1 skipped`; Sprint 5 smoke passed; regression smoke passed; PyQt session smoke passed |
| 2026-06-17 | `82509de` | PyQt DOCX 导出后在面板显示文件名、剩余问题数和导出时间摘要 | `503 passed, 1 skipped`; Sprint 5 smoke passed; regression smoke passed; PyQt session smoke passed |
| 2026-06-17 | `a39b81e` | PyQt DOCX finding 卡片支持定位反馈，显示段落号、文本片段并同步状态栏 | `503 passed, 1 skipped`; Sprint 5 smoke passed; regression smoke passed; PyQt session smoke passed |
| 2026-06-17 | `04bddbc` | PyQt DOCX 批改面板支持问题列表和逐条修复 | `502 passed, 1 skipped`; UI/session tests passed; smoke passed |
| 2026-06-17 | `8ceb945` | PyQt 检查与导出工作区接入 DOCX 批改入口：规则包选择、导入、检查、修复、导出 | `499 passed, 1 skipped`; UI integration passed; session smoke passed |
| 2026-06-17 | `5df9894` | PyQt docx 会话服务第一版：封装导入、检查、自动修复、导出 `.docx` 链路，补 smoke | `497 passed, 1 skipped`; session smoke passed; regression smoke passed; Sprint 5 smoke passed |
| 2026-06-16 | `8793168` | 真实样例回归第一版：3 个 pack x 合规/违规/修复后 3 状态生成样例，补集成测试与 smoke | `495 passed, 1 skipped`; regression smoke passed; Sprint 5 smoke passed |
| 2026-06-16 | `89c6b98` | Sprint 5 smoke：多 run 正文、参考文献悬挂缩进、表格单元格、列表段落 4 个修复闭环 | `492 passed, 1 skipped`; smoke passed |
| 2026-06-16 | `e141e41` | SIM-UNIV 新增表格单元格字号、列表行距规则，补真实 docx 往返闭环测试 | related tests passed |
| 2026-06-16 | `702b8c0` | 增加列表段落 `list_type/list_level` 读取、写回、to_dict 支持 | related tests passed |
| 2026-06-16 | `0200ad2` | 增加表格单元格段落定位与表格重建写回闭环 | related tests passed |
| 2026-06-16 | `6ba2738` | GB/T 7714 参考文献字号、中文字体、西文字体、下划线、加粗规则从首 run 扩展为多 run 覆盖，补真实 docx 往返闭环测试 | `478 passed, 1 skipped` |
| 2026-06-15 | `5399305` | 增加 `paragraph.hanging_indent_cm`，补参考文献悬挂缩进读取、写回、规则和 fix 闭环 | `477 passed, 1 skipped` |
| 2026-06-15 | `bd33c37` | 保留自定义 docx 段落样式，修复参考文献对齐规则字段，补参考文献 alignment 闭环测试 | `473 passed, 1 skipped` |
| 2026-06-15 | `2355866` | 页眉/页脚 paragraph alignment 定位、修复、写回、读取闭环 | `471 passed, 1 skipped` |
| 2026-06-15 | `858536b` | run 为空时写回保留 paragraph text | `468 passed, 1 skipped` |
| 2026-06-11 | Sprint 4 完成 | HTML/SARIF reporter、CLI helper、fix 二次 check、`changes.md`、`pyproject.toml` | `381 passed, 1 skipped` |
