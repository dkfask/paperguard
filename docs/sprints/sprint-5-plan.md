# Sprint 5 Plan — Mutator 成熟与 docx 修复闭环

> **状态**：已完成归档（2026-06-16，收口 commit `89c6b98`）
> **前置**：Sprint 4 完成（commit `5f36692`）
> **目标**：让 `fix -> write -> load -> validate` 成为可依赖的核心闭环，覆盖论文格式修复中最常见、风险可控的 docx 字段。

---

## 目标

Sprint 5 不做完整 Word 在线编辑器，也不做复杂目录、交叉引用、完整 numbering.xml 续接。范围限定为：

- 扩展不可变 IR 的可修复字段。
- 扩展 `docx_loader` 对 writer 已支持字段的读取闭环。
- 扩展 `docx_writer` 写回能力。
- 扩展 `fix_applier` 安全修复能力。
- 增加真实 docx 往返回归测试。
- 深化 GB/T 7714 和 SIM-UNIV 规则包中能自动修复并二次验证的规则。

---

## 已交付

### IR / loader

- `Paragraph.hanging_indent_cm`
- `Paragraph.table_index`
- `Paragraph.table_row_index`
- `Paragraph.table_cell_index`
- `Paragraph.list_type`
- `Paragraph.list_level`
- loader 读取：
  - 段落 alignment
  - page break before
  - hanging indent
  - run underline / color / highlight / vert_align
  - 样式继承和 base style chain
  - 页眉/页脚段落
  - 表格单元格段落定位，含合并单元格去重
  - 列表段落元数据

### writer / fix_applier

- `runs.*.<attr>` 批量修复所有 run。
- `insert paragraph.before`
- `insert paragraph.after`
- `delete paragraph`
- 写回：
  - 段落行距、段前段后、首行缩进、悬挂缩进、对齐、分页前
  - run 字体、字号、粗体、斜体、下划线、颜色、高亮、上下标
  - section 页面尺寸、页边距、方向、页眉页脚距离
  - 多 section
  - 页眉/页脚段落
  - 自定义段落样式
  - run 为空时保留 paragraph text
  - 表格单元格段落重建
  - 列表段落样式重建

### 规则包

- GB/T 7714：
  - 参考文献悬挂缩进规则
  - 参考文献字体、字号、下划线、加粗规则从首 run 扩展为多 run 覆盖
- SIM-UNIV：
  - 表格单元格字号规则
  - 列表段落行距规则

### 验证

- `python -m pytest -p no:cacheprovider -q` -> `492 passed, 1 skipped`
- `python scripts/sprint5_smoke.py` -> `OK Sprint 5 smoke passed`
- `app/core/` PyQt import 扫描无输出
- `git diff --check` 无空白错误

---

## 完成节点

| Commit | 内容 |
|--------|------|
| `e60a05a` | 扩展 docx writer 写回字段 |
| `2355866` | 页眉/页脚 alignment 修复闭环 |
| `bd33c37` | 自定义段落样式保留与参考文献 alignment 闭环 |
| `5399305` | 参考文献悬挂缩进闭环 |
| `6ba2738` | 参考文献多 run 格式规则闭环 |
| `0200ad2` | 表格单元格段落定位和重建闭环 |
| `702b8c0` | 列表段落元数据读取和写回闭环 |
| `e141e41` | SIM-UNIV 表格/列表规则闭环 |
| `89c6b98` | Sprint 5 smoke |

---

## 留到下一阶段

- 真实样例论文回归库。
- CI。
- PyQt 导入、实时检查、修复、导出完整产品链路。
- Windows 安装包。
- 复杂 numbering.xml 续接、多级列表缩进、目录、交叉引用、图表题注完整解析。
- 完整 GB/T 7714 著录格式解析器。
