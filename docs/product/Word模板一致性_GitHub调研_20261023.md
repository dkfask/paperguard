# Word 模板一致性：GitHub 项目调研

调研日期：2026-10-23。对象：PaperGuard 的原模板保留、内容绑定、独立成品检查和 Word/WPS 验收需求。

本轮通过 GitHub 连接器读取仓库文档、许可证及部分源码，并核对官方文档。没有安装候选库、运行候选项目或提交用户模板到外部服务。下文的“建议”属于技术判断，不代表候选库已经通过本项目验收。

## 1. 调研结论

GitHub 上有可复用的模板填充、内容控件绑定、文档比较和文件结构校验实现。PaperGuard 可以减少底层重复开发。

本次核查没有找到一个能够直接接入，并证明“未经准备的任意学校模板，填入任意长度内容后，在 Word 和 WPS 中都完全一致”的现成系统。这个结论限定于本次核查范围，不能推断所有开源项目都没有相关能力。

建议先验证两条生成路线：

- **现有 Python 服务 + docxtpl**：适合准备过占位符的模板，接入成本较低。
- **现有 Python 服务 + docx4j 独立组件**：适合以内容控件和 XML 数据绑定组织复杂模板，需要增加 Java 运行环境。

独立结构校验可评估 Open XML SDK；文档差异算法可参考 Open-Xml-PowerTools。项目版本、模板摘要、输出摘要、业务规则及所有下载入口的强制门禁仍由 PaperGuard 负责。这个分工是根据各项目公开接口作出的设计建议。

## 2. 候选项目

| 项目 | 已核实的能力 | 对本需求的边界 | PaperGuard 建议 |
|---|---|---|---|
| [python-docx-template / docxtpl](https://github.com/elapouya/python-docx-template) | Python；用 Word 制作模板，再通过 Jinja 风格标记填充内容；提供表格及页眉页脚处理 | 要先准备标记；普通标记有 run 范围限制；生成并不等于结构和分页已验收 | Python 路线的首个验证对象 |
| [docx4j](https://github.com/plutext/docx4j) | Java；打开和修改 OOXML 包；内容控件、CustomXML 绑定、重复和条件内容 | 需要模板绑定设计及 Java 环境；通用文档合并属于其商业扩展范围 | 复杂模板的重点候选 |
| [poi-tl](https://github.com/Sayi/poi-tl) | Java / Apache POI；标记驱动的文字、图片、表格、循环、SDT 等 | 仍需模板准备；README 的样式保留声明不能替代真实模板测试 | Java 路线的备选 |
| [docxtemplater](https://github.com/open-xml-templating/docxtemplater) | JavaScript；占位符、循环和条件；可在 Node.js 生成文档 | 多种高级能力是付费模块；官方明确不计算分页和目录页码 | 需要 Node.js 生成服务时再评估 |
| [Open XML SDK](https://github.com/dotnet/Open-XML-SDK) | .NET；操作 OOXML 包；官方示例可返回校验错误、部件及节点路径 | 文件格式校验不能直接证明校规符合性、源模板保留或视觉一致 | 可选的独立文件结构检查层 |
| [Open-Xml-PowerTools](https://github.com/OpenXmlDev/Open-Xml-PowerTools) | .NET；模板组装、替换、WmlComparer 文档比较和修订输出 | 比较结果还需解释哪些变化被允许；README 有寻找维护者的说明 | 借鉴差异算法和测试样例，暂不直接作为放行门禁 |
| [docxcompose](https://github.com/4teamwork/docxcompose) | Python；拼接 DOCX；提供整份文档比较的测试辅助工具 | README 明确：后续文档的页眉页脚被忽略，使用首份文档的定义 | 不用它直接拼装要求各节保真的论文 |
| [documents4j](https://github.com/documents4j/documents4j) | Java；把转换交给本地或远端原生应用；附带 Windows Word 转换适配器 | Word 适配器要求 Windows、已安装并激活的 Word；转换成功不代表模板一致 | 后续真实 Word 渲染证据的候选工具 |

### 2.1 docxtpl：接入方便，但需要验证修改范围

[官方文档](https://docxtpl.readthedocs.io/en/latest/)要求先在 Word 文档中插入模板标记。普通标记不能任意跨段落、表格行和 run；复杂区域有专用语法。页眉页脚中的图片可通过已有占位图片替换，但动态新增存在限制。

检查[渲染源码](https://github.com/elapouya/python-docx-template/blob/master/docxtpl/template.py#L473)后可见，`render()` 会处理正文、表格修正、绘图 ID、页眉页脚和部分属性。因此不能把“沿用原模板”解释为“所有未绑定 XML 部件自动逐字节不变”。PaperGuard 必须核对实际输出，并区分格式等价变化和未经允许的结构变化。

适用方式：在原件的副本中制作可填模板，保留原件和版本关系；每次输出由独立检查器复检。是否满足严格原包保留要求，留待真实模板实验裁定。

### 2.2 docx4j：最值得参考的复杂模板绑定设计

[官方入门文档](https://github.com/plutext/docx4j/blob/VERSION_17_3_3/docs/Docx4j_GettingStarted.md)比较了文本替换和内容控件绑定：复杂文档更适合后者。文档也说明了 XPath 定位、表格行重复、关系部件和页眉页脚的处理方式。

这与 PaperGuard 的“先确认可写位置，再填入内容”设计相符。需要注意，它不会自动理解学校模板中哪些示例必须保留、哪些内容应该删除；这些语义仍需模板准备流程确定。

目录更新也有条件：官方文档说明其纯 Java 目录处理依赖特定的内容控件结构。不能把该能力扩大为所有目录域都能准确自动更新。

### 2.3 docxtemplater：区分保留已有部件和导入新部件

[仓库 README](https://github.com/open-xml-templating/docxtemplater#modules)列出的图片、HTML、子模板和 subsection 等属于付费模块。不能据此推断“保留原模板已有页眉页脚”也必须购买 subsection；后者针对从其他文档导入分节等能力。

[官方 FAQ](https://docxtemplater.com/faq/#is-it-possible-to-generate-a-table-of-contents)明确说明该库不负责排版计算，不能据此获得准确页码或更新目录。这是生成引擎与排版引擎需要分工的直接依据。

### 2.4 独立校验和差异检查需要分层

[微软校验示例](https://learn.microsoft.com/en-us/office/open-xml/word/how-to-validate-a-word-processing-document)使用 `OpenXmlValidator` 返回错误及 XML 定位信息，适合补充文件结构检查。PaperGuard 还要另外判断封面字段、章节完整性、允许修改区域和模板关系是否符合业务要求。

[PowerTools README](https://github.com/OpenXmlDev/Open-Xml-PowerTools)及其[比较测试](https://github.com/OpenXmlDev/Open-Xml-PowerTools/blob/vNext/OpenXmlPowerTools.Tests/WmlComparerTests.cs)提供了修订比较和复杂结构测试参考。文档比较、模板约束校验及视觉检查分别回答不同问题，不能共用一个“通过”状态。

## 3. 与论文业务接近的项目

除了通用引擎，也查到了直接面向论文格式的项目：

- [emptyinkpot/thesis-format-checker](https://github.com/emptyinkpot/thesis-format-checker)：README 展示了学校规则预设、自定义规则、JSON 输出、自动修正，以及页眉、封面字段、目录等检查项。可参考规则组织和报告方式。本轮没有核实其完整实现和测试质量，也没有看到足以证明本需求全部完成的证据。
- [ctzlyj/csu-thesis-format-Skill](https://github.com/ctzlyj/csu-thesis-format-Skill)：提供学校模板、分节/页眉页脚/目录域审计脚本及真实修订流程。README 明确将脚本定位为启发式检查，并保留人工视觉核对。适合补充测试清单；代码和模板资源的具体复用许可仍需单独核实。

这类项目的价值主要是学校规则、故障样例和验收经验。本次调研不将其列为可以直接替换 PaperGuard 的产品。

## 4. 对当前物联网工程模板的判断

本轮重新只读检查了用户提供的 DOCX 包，确认正文有 1 张封面表格、9 个 `sectPr`，包内有 7 个页眉部件和 2 个页脚部件。部件数量不等于实际显示的页眉页脚数量，仍须解析每节引用及继承关系。

`footer1.xml` 的域指令是 `PAGE`，带 `MERGEFORMAT` 开关，缓存显示为罗马数字 `IV`。不能仅凭缓存不是阿拉伯数字就判定该页脚无法支持。

[python-docx 官方说明](https://python-docx.readthedocs.io/en/latest/user/hdrftr.html)已描述逐节页眉页脚和向前继承机制。结合上述模板引擎能力，封面表格、多分节、页眉页脚应作为 PaperGuard 的实现和验证任务逐项补齐，不能把这些常见结构统称为模板不合格。

这份文件仍需完成字段和正文区域映射：哪些单元格填姓名、哪些示例正文可替换、哪些分节边界必须保留，都不能由通用库可靠猜测。

## 5. 对现有方案的补充建议

以下为本轮调研建议，尚未修改现有技术设计或业务代码。

1. **先比较真实模板结果，再锁定生成库。** 首轮只比较现有受控锚点路线、docxtpl 和 docx4j；poi-tl 与 docxtemplater 保留为备选，控制集成范围。
2. **显式建立模板准备版本。** 原始 DOCX 保持不可变；带标记或内容控件的准备版本、字段映射和允许变化范围分别绑定摘要。模板准备本身也要核对结构差异。
3. **把域保留与域更新分开。** 保留 `PAGE` / `TOC` 指令及格式，不代表页码缓存已更新。只有经过指定排版引擎计算并检查后，才能记录域更新成功。
4. **分开记录三类证据。** 文件结构有效、符合模板约束、指定 Word/WPS 环境视觉验收分别记录。某一层通过不能替代其余层。
5. **每次准备和渲染后都复检。** 检查器不能只调用生成库自身的成功标志。所有下载和归档入口继续绑定项目快照、模板及实际输出文件摘要。

建议优先采用“现有 Python 主服务 + 小范围验证外部库”的方式。若 docxtpl 的修改范围和富内容处理不能满足验收，再依据实验结果引入 docx4j 组件；当前没有充分证据支持立即整体改成 Java 或 Node.js。

## 6. 后续验证的判定标准

本节是建议的实验范围，本轮没有执行。

| 实验 | 必须观察的结果 |
|---|---|
| 原样打开并保存 | 能解释的规范化差异与真正结构损失分别列出；未绑定媒体和关系可核对 |
| 填入封面字段 | 只改绑定单元格内容；网格、合并、边框和段落格式符合预期 |
| 填入较长正文和图表 | 9 个分节及有效页眉页脚引用按映射约定保留；内容完整 |
| 处理目录和页码 | 区分域指令保留、缓存更新和实际显示结果；不以旧页码缓存通过验收 |
| 人为破坏结构 | 删除页眉关系、改分节、损坏表格或替换输出后，独立检查能够阻断 |
| Word / WPS 打开 | 记录应用版本、字体环境和实际页面；允许内容引起正常分页，检查异常空白、裁切和表格跨页 |

若后续采用 documents4j，其 Windows Word 环境要求以[官方 README](https://github.com/documents4j/documents4j#microsoft-word-converter)为准。该项目是格式转换桥接器，不能直接视为完整的域更新与视觉验收系统。

## 7. 许可证核查范围

这里只记录已读取的一手声明，不作商业部署的许可结论。最终采用时仍需对所选版本及传递依赖逐项登记。

| 项目 | 本轮核实的许可声明 |
|---|---|
| docxtpl | [LGPL 2.1](https://github.com/elapouya/python-docx-template/blob/master/LICENSE.txt) |
| docx4j | [Apache 2.0；依赖声明另见 NOTICE](https://github.com/plutext/docx4j#legal-information) |
| poi-tl | [Apache 2.0](https://github.com/Sayi/poi-tl/blob/master/LICENSE) |
| docxtemplater 核心 | [MIT 或 GPLv3 双许可](https://github.com/open-xml-templating/docxtemplater/blob/master/LICENSE.md)；付费模块单独处理 |
| Open XML SDK | [MIT](https://github.com/dotnet/Open-XML-SDK#license) |
| Open-Xml-PowerTools | [MIT](https://github.com/OpenXmlDev/Open-Xml-PowerTools) |
| docxcompose | [MIT](https://github.com/4teamwork/docxcompose/blob/master/LICENSE) |

## 8. 本轮交付状态

已完成本次候选项目的一手资料调研、部分源码核对及用户模板结构复核。仅新增本报告；没有替换生成引擎、安装依赖、执行候选项目测试或 Office 验收，也没有提交、推送、合并和部署。
