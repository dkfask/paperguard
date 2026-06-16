# PaperGuard Progress

> 本文件用于持续记录项目进度。以后每完成一个设计节点、实现节点或验证节点，都优先更新这里。

## 当前状态

- 当前阶段：Sprint 5，mutator 成熟与真实 docx 修复闭环强化。
- 当前分支：`codex/sprint-5-mutator`
- 最近提交：`6ba2738 fix(sprint-5): cover reference multi-run formatting`
- 最近全量验证：`python -m pytest -p no:cacheprovider -q` -> `478 passed, 1 skipped`
- 当前可用程度：CLI 批改链路可内部试用；PyQt 实时约束仍是 beta。

## 已实现

### 核心数据与读取

- 不可变 IR：`Document`、`Section`、`Paragraph`、`Run`、`Finding`、`Location`、`Fix`、`Severity`。
- `.docx` loader：
  - 正文段落读取。
  - 页眉/页脚段落读取。
  - 表格单元格段落读取，含合并单元格去重。
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
  - `SIM-UNIV-generic`

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
- GB/T 7714 参考文献 run 级修复已覆盖多 run 段落，并有 `fix -> write -> load -> validate` 回归测试。
- `fix` 默认二次 check 并写 `changes.md`。
- `--skip-verify` 可跳过二次 check，兼容隐藏别名 `--no-verify`。

### PyQt 主路径

- 已有桌面应用骨架。
- 已接入 Live Checker。
- 已有侧边栏 issue、格式高亮、LiveMutator 基础修复能力。

### 模板抽取

- 已有 FORMAT_WORDS 启发式。
- 已有模板抽取到规则包的基本链路。

## 未实现 / 未完善

### 产品化缺口

- CI 未配置。
- Windows 桌面安装包未制作。
- 用户手册和面向普通用户的操作文档不完整。
- 异常恢复、日志、隐私提示、真实论文保护策略还不完整。

### PyQt 端缺口

- UI 仍需打磨到可交付状态。
- 保存/导出流程需要和 core fix/write 更深整合。
- 一键修复后的真实 `.docx` 闭环还需要继续覆盖。
- 长文档实时检查的体验和性能需要真实样例验证。

### 规则与 docx 能力缺口

- 表格规则与表格写回未成熟。
- 编号、目录、图表题注、交叉引用未成熟。
- 参考文献目前偏格式检查，不是完整 GB/T 7714 著录格式解析器。
- 编号缩进、列表样式等复杂段落结构还需补；基础悬挂缩进闭环已完成。
- 学校规则包需要更多真实模板和真实论文样例回归。
- 真实样例论文回归库尚未建立。

## 当前可用性判断

- CLI 内部试用：可以。
  - 适合用样例文档或非重要文档跑 `check`、`fix`、HTML 报告和 `changes.md`。
- 给同学/老师直接使用：暂不建议。
  - PyQt 产品体验、安装包、真实模板覆盖和异常处理还没有完成。
- 作为论文规范产品 MVP：未完成。
  - 还需要更多真实样例、复杂 docx 结构和发布流程。

## 后续路线

### P0：继续 Sprint 5 mutator 成熟

- 补表格段落与表格格式的读取、规则、写回闭环。
- 补悬挂缩进、编号缩进、列表样式闭环。
- 补更多 `fix -> write -> load -> validate` 真实 docx 回归测试。
- 继续清理规则包中无法二次验证闭环的字段名和 fix target。

### P1：真实样例回归

- 建立不含真实学生隐私的合成论文样例库。
- 为 GB/T 7713、GB/T 7714、SIM-UNIV 各准备至少 3 类样例：
  - 完全合规
  - 部分违规
  - 自动修复后应合规

### P2：PyQt 正常使用链路

- 打通导入 `.docx`、实时检查、修复、导出 `.docx`。
- 优化侧边栏交互、定位、高亮和一键修复反馈。
- 增加失败提示和不可自动修复项说明。

### P3：发布与工程化

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
| 2026-06-16 | `6ba2738` | GB/T 7714 参考文献字号、中文字体、西文字体、下划线、加粗规则从首 run 扩展为多 run 覆盖，补真实 docx 往返闭环测试 | `478 passed, 1 skipped` |
| 2026-06-15 | `5399305` | 增加 `paragraph.hanging_indent_cm`，补参考文献悬挂缩进读取、写回、规则和 fix 闭环 | `477 passed, 1 skipped` |
| 2026-06-15 | `bd33c37` | 保留自定义 docx 段落样式，修复参考文献对齐规则字段，补参考文献 alignment 闭环测试 | `473 passed, 1 skipped` |
| 2026-06-15 | `2355866` | 页眉/页脚 paragraph alignment 定位、修复、写回、读取闭环 | `471 passed, 1 skipped` |
| 2026-06-15 | `858536b` | run 为空时写回保留 paragraph text | `468 passed, 1 skipped` |
| 2026-06-11 | Sprint 4 完成 | HTML/SARIF reporter、CLI helper、fix 二次 check、`changes.md`、`pyproject.toml` | `381 passed, 1 skipped` |
