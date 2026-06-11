# PaperGuard 快速上手（5 分钟）

> 写给人类用户的快速指南。
> AI agent 入口请看 [`SKILL.md`](./SKILL.md)。

## 这是什么

PaperGuard 是**两层产品**：

1. **🔴 PyQt 桌面应用**（推荐）—— 学生边写边检查（Grammarly 模式）
2. **🟡 CLI 命令行**（辅）—— 事后批改、CI 集成、批量审稿

**两层共用同一套规则引擎**。CLI 的功能 PyQt 都有；PyQt 的实时反馈 CLI 不会有。

它**不**：
- 改你的论文文字、引用、图编号
- 修改你的原稿（永远输出新文件）
- 需要联网（除了 `pip install`）

## 5 分钟上手

### 0 步：选你要的入口

| 你想 | 用这个 |
|------|--------|
| **学生在 PyQt app 里写论文，边写边查** | 启动 PyQt app，**不需要装 CLI** |
| 写完跑一遍事后批改 | CLI（往下） |
| 教务批量审稿 / CI 集成 | CLI（往下） |
| AI Agent 帮你跑 | 用 `SKILL.md` 入口 |

**PyQt 实时约束**（推荐给学生）：启动 PyQt app → 开始写论文 → 停手 0.5 秒看到侧边栏提示 + 段落高亮 → 点"一键修复"按钮。

**CLI 事后批改**（推荐给教务/导师/AI）：往下 6 步。

```bash
# 方式 A：pipx（推荐，隔离环境）
pipx install paperguard

# 方式 B：pip
pip install --user paperguard
```

验证安装：

```bash
paperguard --version
# 期望输出：paperguard 0.1.0
```

### 第 2 步：找一份论文

把你的论文 docx 放到你能找到的地方，比如 `~/Desktop/my_thesis.docx`。

### 第 3 步：跑一遍检查（30 秒）

```bash
paperguard check \
  ~/Desktop/my_thesis.docx \
  --pack GB-T-7713-1-2006 \
  --format text
```

**预期输出**（节选）：

```
✗ BODY-LINE-SPACING      正文行距必须为 1.5 倍              (×42)
✗ BODY-FIRST-LINE-INDENT 正文首行应缩进 2 字符              (×38)
✗ HEADING1-FONT          一级标题应为黑体二号                (×5)
⚠ PAGE-MARGIN-TOP        页边距上应为 2.54 cm               (×1)
✓ BODY-EASTASIA-FONT     正文字体为宋体                      (PASS)

Total: 5 errors, 1 warning. Use `paperguard fix` to auto-repair.
```

**退出码**：
- `0` = 全绿（恭喜）
- `1` = 有错（继续看）
- `2` = 内部错误（看错误消息）

### 第 4 步：看 HTML 报告（30 秒，可选）

终端报告太挤？出 HTML：

```bash
paperguard check \
  ~/Desktop/my_thesis.docx \
  --pack GB-T-7713-1-2006 \
  --format html \
  --output ~/Desktop/findings.html
```

打开 `~/Desktop/findings.html` 看彩色报告。CI / GitHub Code Scanning 可改用 `--format sarif --output findings.sarif`。

### 第 5 步：自动修复（30 秒）

```bash
paperguard fix \
  ~/Desktop/my_thesis.docx \
  --pack GB-T-7713-1-2006 \
  --output ~/Desktop/my_thesis.fixed.docx
```

**预期输出**：

```
Fixed and written to ~/Desktop/my_thesis.fixed.docx
3 issues remain. See ~/Desktop/changes.md
```

**重要**：
- 原文件 `my_thesis.docx` **没动**
- 新文件 `my_thesis.fixed.docx` 已生成
- `changes.md` 会列出修复前数量、已自动修复、仍剩余、跳过项
- 如果只想写出修复文件、不做二次检查，可加 `--skip-verify`

### 第 6 步：用 Word 打开（30 秒）

打开 `my_thesis.fixed.docx`：
- 按 `Ctrl+A` 全选
- 按 `F9` 更新所有域（**重要！**这会刷新目录、页码、交叉引用）
- 保存

如果用 `redline.docx`：Word 会问你"接受/拒绝每条修订"——这是审计模式，确保你看到 AI 改了哪里。

---

## 常见问题

### Q1：报告里出现"skipped"怎么办？

`skipped` 表示 PaperGuard **故意不自动修**。常见原因：
- 修改可能损坏内容（比如改标题层级可能改错章节编号）
- 模板信息不明确（比如没指定页边距具体值）
- 需要人工判断（比如"图题应放在图下方"这种语义判断）

**操作**：打开 `changes.md` 末尾的 "Unresolved Manual Items" 清单，自己处理。

### Q2：学校没有预制 pack 怎么办？

两种办法：

**A. 用国标跑一遍**（覆盖率 60-80%）：

```bash
paperguard check thesis.docx --pack GB-T-7713-1-2006
```

**B. 从学校模板抽取规则**（覆盖率 80-95%）：

```bash
# 1. 用学校的 .dotx 模板抽规则
paperguard extract --template ~/Downloads/school-template.docx --out ./work/req

# 2. 审阅并补充：打开 ./work/req/requirements.md
#    模板里没写、但学校说"必须用 X"的，手动加到 requirements.json

# 3. 用抽出的规则审计
paperguard check thesis.docx --pack ./work/req/requirements.json
```

### Q3：能不能修改原文，不输出新文件？

**不能**。这是硬约束。理由：
- 你的原稿是宝贵的，自动修改有风险
- 万一改错了，没法回滚
- 输出新文件 + redline 是审计友好的做法

如果你真的只想在原文件上改，先用 `--output` 写到原路径：

```bash
paperguard fix --paper thesis.docx --output thesis.docx
```

（不推荐，但允许。）

### Q4：批量审稿怎么搞？

```bash
mkdir -p audits
for f in papers/*.docx; do
  name=$(basename "$f" .docx)
  paperguard check \
    "$f" \
    --pack GB-T-7713-1-2006 \
    --format sarif \
    --output "audits/$name.sarif"
done
```

SARIF 格式可以直接喂给 GitHub Code Scanning 或 Azure DevOps。

教务场景建议流程：
1. 收论文 → 批量 `check`
2. 把 `findings.html` 发给学生："这是你论文的格式问题清单"
3. 学生自己跑 `fix` 修
4. 学生重交，再 `check` 一次（如果还有问题，发回学生重做）

### Q5：和 Word 里手动排版有什么不同？

| | Word 手动 | PaperGuard |
|---|----------|-----------|
| 100 页论文排版耗时 | 4-8 小时 | 1 分钟 |
| 排版一致性 | 看你自己 | 100% 一致 |
| 可审计 | 难 | 完整 `changes.csv` |
| 可复用 | 每个学校重来 | 换 pack 即可 |
| 适合谁 | 写一份的学生 | 教务 / 答辩秘书 / 模板维护者 |

---

## 下一步

- 想看可用规则清单？跑 `paperguard init --school "我的大学" --out my-pack.yaml` 然后打开生成的 YAML
- 想自己写规则？读 [`docs/design-proposal.md` §3.3](./docs/design-proposal.md) 的 YAML 格式说明（V1 完成）
- 想批量审稿？读 [`SKILL.md`](./SKILL.md) 的 "CI / batch use" 章节
- 想让 AI 帮你跑？读 [`SKILL.md`](./SKILL.md) 的"示例对话"

---

## 反馈与帮助

- 命令行加 `--help` 看完整 flag
- `paperguard --version` 看版本
- `paperguard list-packs` 看内置规则包
- `paperguard doctor` 诊断本地环境
- 项目仍在 MVP 阶段，遇到问题欢迎提 Issue
