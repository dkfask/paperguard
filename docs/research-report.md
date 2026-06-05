# GitHub 论文规范软件调研综合报告

> **日期**：2026-06-05
> **范围**：GitHub 上与"论文 / 学位论文 / 课程报告的规范、排版、检测、管理"相关的开源项目
> **方法**：`gh search repos` / `gh search code`（gh CLI v2.93.0 本机已安装但未认证）+ tavily / exa MCP 公开页面补全
> **5 份子报告**（位于 `research/s1.json` ~ `s8.json`，分别对应下表 ID）：
>
> | 子报告 | 主题 | 候选项目数 | 核心结论摘要 |
> |--------|------|----------|------|
> | S1 | 论文 Word 模板生成器 | 26 | 主流走 LaTeX + 参考 docx 兜底；2024+ 趋势是 AI Agent Skill 化 |
> | S2 | Word/docx 排版技术栈 | 20 | python-docx + lxml + pandoc + 可选 Word COM 是事实标准 |
> | S3 | 规则 DSL 引擎 | 15 | Vale/Kyverno 嫁接 = YAML + JSONLogic + JMESPath 三元组最贴合"论文规范" |
> | S4 | 论文管理系统 | 12 | 没有任何系统把"格式自动检测"做成内置模块——显著差异化机会 |
> | S5 | 论文格式检查器 | 15 | 规则层走 YAML 学校规范包；报告层支持 SARIF/HTML/redline 多形态 |
>
> **本报告不重复子报告细节**——子报告已经是表格 + 分析。请把本报告当作"决策摘要"。

---

## 1. 一句话结论

**做"通用论文规范软件"的最优路径**：

> **CLI + AI Agent Skill 双入口的桌面/服务器工具，规则层 = YAML 学校规范包 + 内置 Python 函数（参考 Vale/markdownlint 插件模型），应用层 = python-docx + lxml + pandoc（必要时 Word COM 收尾），反馈层 = JSON + HTML + 修订模式 docx。**

这是 GitHub 现有项目交集的"最大公约数"。

---

## 2. 五份子报告的横向对比

### 2.1 三个共性维度

| 维度 | 子报告给的最优解 | 共性度 | 备注 |
|------|-----------------|--------|------|
| **规范定义** | S1 = YAML/JSON（趋势）+ 参考 docx；S3 = YAML 规则 + JSONLogic 表达式；S5 = YAML 学校规范包 | **⭐⭐⭐⭐⭐ 高度一致** | 所有 5 份都指出"硬编码"是反模式；YAML + 配置文件是 2024+ 共识 |
| **规范应用** | S2 = python-docx + lxml + pandoc + Word COM；S5 = python-docx + lxml 下钻 | **⭐⭐⭐⭐ 高度一致** | python-docx 是中文论文场景的"入场券"；lxml 下钻处理 eastAsia/ascii 双字体 |
| **用户接入** | S1 = AI Agent Skill（趋势）+ CLI + Word 插件；S4 = Web 门户；S5 = GUI + CI | **⭐⭐⭐ 三方分歧** | 国际偏 Web，国内偏 GUI/CLI；2024+ AI Skill 是新维度 |

### 2.2 三个差异维度（决定产品定位）

| 差异点 | "模板生成"派 | "格式检查"派 | "流程管理"派 |
|--------|-------------|------------|------------|
| 典型项目 | huangwb8/ChineseResearchLaTeX、zouchenzhen/docx-template-translator-skill | wksudud/format-thesis-docx、153lsr/thesis-typeset | ls1intum/thesis-management、allenlili/GTMS |
| **核心价值** | 把内容套上规范样式 | 把已写稿纠正到规范 | 把整条流程管起来 |
| **痛点** | 模板碎片化、规则散落 | 缺可扩展规则、缺一站式 | 模板与格式脱节，无自动批改 |
| **本项目应该补** | 把"规则"沉淀为可复用 | 把"自动批改"做成产品 | **没有任何系统集成"自动批改"** ← 空白 |

**关键洞察**：三者**正交**而非互斥。一个真正的"通用论文规范软件"应该至少覆盖**前两者**（生成 + 检查），并以可插拔方式对接第三者的数据。

### 2.3 隐藏的"四层架构"共识

5 份子报告独立分析、却都得出相似的四层结构：

```
┌─────────────────────────────────────────────────────────┐
│ L4 用户接入层 │ CLI / AI Skill / Word 插件 / Web 上传 / 桌面 GUI │
├─────────────────────────────────────────────────────────┤
│ L3 报告/反馈层 │ 终端 / JSON / SARIF / HTML / redline-docx / PDF 批注 │
├─────────────────────────────────────────────────────────┤
│ L2 规则引擎层 │ YAML 规则 + JSONLogic 条件 + 修复动作 + 插件加载器 │
├─────────────────────────────────────────────────────────┤
│ L1 数据访问层 │ python-docx（写）/ lxml（细粒度）/ pandoc（互转）/ Word COM（终稿） │
└─────────────────────────────────────────────────────────┘
```

**这一致性非常强**。可以直接采用作为本项目的顶层架构骨架。

---

## 3. 跨子报告的 12 条关键发现

按对本项目影响力排序：

### 🔴 决定性（必须做对）

1. **【差异化空白】没有任何 GitHub 项目把"格式自动批改"内置进"全流程管理系统"**（S4 结论）。本项目如果定位为"轻量级检查 + 可插拔接入"，能直接吃掉这一空白。

2. **【规则层选型】YAML + 简单 JSONLogic 子集 是"论文规范"场景的最优解**（S3 结论）。不要走 Rego（学习曲线陡）、不要走 JSON Schema（缺 fix 能力）、不要硬编码 if-else（无法扩展）。

3. **【数据层选型】python-docx 是入场券，但单独不够**——中文场景必须 lxml 下钻处理 `<w:rFonts w:eastAsia="宋体" w:ascii="Times New Roman"/>` 这种双字体（多个子报告共识）。

### 🟡 强建议（影响竞争力）

4. **【可执行修复】fix 能力是分水岭**。JSON Schema / textidote / LaTeX 静态约束都"无 fix"；vale / markdownlint / PaperFormatDetect / thesis-formatter 都"有 fix"——后者用户体验高一个量级。

5. **【AI Skill 化是 2024+ 趋势】** S1 发现 2024 后所有新项目都包装成 Claude/Codex Skill。这是用户群分水岭：会命令行的用 CLI，不会命令行的用 Skill 自然语言。

6. **【学校规范包化】** "一个学校一个仓库"是反模式。ZyhSechub 提出的"M1 默认规范 / M2 样文 deep_merge / M3 学校模板底座 / M4 已有初稿"四象限值得抄。

7. **【国标先行】** GB/T 7713.1（论文结构）+ GB/T 7714—2015（参考文献）必须内置。比逐校适配更省力，且学校自定义可叠加。

8. **【可审计性是 2025+ 用户最在意的可信度信号】** wksudud/format-thesis-docx 的 `changes.csv` 模式应该抄过来：所有修改都可审计、可回滚。

### 🟢 加分项

9. **【CI 集成】** SARIF 输出 + GitHub Code Scanning + reviewdog 风格 PR 评论，可让"提交时挡住"，是教育/科研场景的杀手锏。

10. **【修订模式回写】** 用 tracked-change 把"我帮你改了哪里"标出来，比直接覆盖原稿可信得多（参考 JSv4/Python-Redlines）。

11. **【中文字号常量表】** 初号 42pt、小四 12pt、五号 10.5pt 这种 THUTHESIS 公开的对照表是基础设施，应该内置。

12. **【避免 VBA】** xkonglong/gw 的成功证明 VBA 在国内有用户，但跨平台差。**留给 Word 插件作为"杀手锏备选"**，主推 CLI + Skill。

---

## 4. 竞品坐标图（4 象限）

```
                     自动批改强
                          ▲
                          │
       153lsr/thesis-typeset
       wksudud/format-thesis-docx
       siyuanzhou/PaperFormatDetect   ★ 本项目目标位
                          │          （中等批改 + 强规则 + 通用）
       Lang-Li-1/thesis-formatter
                          │
   ──────────────────────┼──────────────────────► 规则可扩展
       硬编码单校                        │
       lilanlan11/thesis-formatter      │
       xkonglong/gw (VBA)               │
       ThesisFZU (.dotm)                │
                          │         huangwb8/ChineseResearchLaTeX
                          │         ZyhSechub/chinese-thesis-workbench
                          │         zouchenzhen/docx-template-translator-skill
                          ▼
                     自动批改弱
```

**本项目的目标象限**：右上"中等批改 + 强规则 + 通用"——目前 GitHub 几乎无人占据。

---

## 5. 5 份子报告的代表项目速查表

| 子报告 | 项目 | URL | 语言 | 借鉴点 |
|--------|------|-----|------|--------|
| S1 模板生成 | wksudud/format-thesis-docx | https://github.com/wksudud/format-thesis-docx | Python (stdlib) | 提取规范→审计→改写三步 |
| S1 模板生成 | huangwb8/ChineseResearchLaTeX | https://github.com/huangwb8/ChineseResearchLaTeX | LaTeX+Python | PDF 主线+DOCX 副线+像素级验收 |
| S1 模板生成 | ZyhSechub/chinese-thesis-workbench-skill | https://github.com/ZyhSechub/chinese-thesis-workbench-skill | Python | M1-M4 四象限交付路径 |
| S1 模板生成 | zouchenzhen/docx-template-translator-skill | https://github.com/zouchenzhen/docx-template-translator-skill | Python+pandoc+COM | inspect/adaptive 三步 |
| S2 排版工具 | python-openxml/python-docx | https://github.com/python-openxml/python-docx | Python | 基础库，必备依赖 |
| S2 排版工具 | elapouya/python-docx-template | https://github.com/elapouya/python-docx-template | Python | docx + Jinja2 模板 |
| S2 排版工具 | siyuanzhou/PaperFormatDetect | https://github.com/siyuanzhou/PaperFormatDetect | C# / OpenXML | 唯一 OpenXML 路线产品化项目 |
| S3 规则 DSL | errata-ai/vale | https://github.com/errata-ai/vale | Go | YAML 规则 + style pack |
| S3 规则 DSL | open-policy-agent/opa | https://github.com/open-policy-agent/opa | Go | Rego mutate 修复模式 |
| S3 规则 DSL | kyverno/kyverno | https://github.com/kyverno/kyverno | Go | YAML + JMESPath 思路 |
| S3 规则 DSL | textlint/textlint | https://github.com/textlint/textlint | JavaScript | "ESLint for natural language" |
| S4 管理系统 | ls1intum/thesis-management | https://github.com/ls1intum/thesis-management | TypeScript | 唯一生产级 SaaS（200★） |
| S4 管理系统 | allenlili/thesis-management-system | https://github.com/allenlili/thesis-management-system | Java/Activiti | 国内唯一严肃工作流引擎 |
| S5 检查器 | 153lsr/thesis-typeset | https://github.com/153lsr/thesis-typeset | Python/PyQt | YAML 学校规范包 + GUI |
| S5 检查器 | textidote | https://github.com/sylvainhalle/textidote | Java | LaTeX AST 保留源坐标 |
| S5 检查器 | DavidAnson/markdownlint | https://github.com/DavidAnson/markdownlint | JS | 规则可插拔、CI 友好 |
| S5 检查器 | ltex-plus/vscode-ltex-plus | https://github.com/ltex-plus/vscode-ltex-plus | Kotlin/Java | editor 实时 + quick fix |

---

## 6. 结论：给"通用论文规范软件"的战略建议

### 6.1 产品定位（一句话）
**让任何高校都能用一份 YAML 规范包，自动把学生的 .docx 论文批改到符合本校格式**。

### 6.2 三阶段路线

| 阶段 | 周期 | 目标 | 关键里程碑 |
|------|------|------|-----------|
| **MVP** | 1-2 月 | 跑通单校（GB/T 7713 + GB/T 7714 国标） | 30 条规则、CLI 工具、HTML 报告 |
| **V1** | 3-6 月 | 学院可自助配置 | YAML 规则包 + 修复动作 + Web UI（上传 docx → 出报告） |
| **V2** | 6-12 月 | 全国/跨校通用 | AI Skill 入口 + Word 插件 + CI 集成 + redline 回写 |

### 6.3 必读源码（实施前 5 件事）

1. **克隆并精读** `wksudud/format-thesis-docx`——它和本项目目标最接近，且代码量小（~1000 行），是最佳骨架参考。
2. **克隆并精读** `153lsr/thesis-typeset`——看它怎么用 YAML + Word COM 收尾，理解 GUI 入口的全栈。
3. **克隆并精读** `ZyhSechub/chinese-thesis-workbench-skill`——M1-M4 四象限的"用户决策树"是非常聪明的设计。
4. **API 文档走 Context7**：`python-docx`、`docxtpl`、`lxml`、`pywin32`（可选）。
5. **看 THUTHESIS / ZJUTHESIS 的字号-pt 对照表**——这是中文论文格式的"通用字典"。

### 6.4 不要做的事

- ❌ **不要从零写 OOXML 解析**——直接用 python-docx + lxml 下钻
- ❌ **不要做"docx 在线编辑器"**——复杂度极高，参考 siyuanzhou 是"上传→批改"而非"在线编辑"
- ❌ **不要走 Activiti/flowable**——除非用户明确要自定义流程图，否则过度工程
- ❌ **不要押注单一技术栈**——python-docx 处理不了的（如 TOC 自动更新），用 Word COM 收尾；pandoc 解决不了的（如校模板套用），用 python-docx
- ❌ **不要硬编码单校规则**——第一条 YAML 规则就要可被替换

---

## 7. 详细设计见姊妹文档

- **架构设计 + 模块拆分 + MVP 路径** → `docs/design-proposal.md`

