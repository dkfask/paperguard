# PaperGuard 文档目录

项目文档统一放在本目录下。

| 目录 | 内容 |
|---|---|
| [product/](./product/) | 产品、业务、架构和界面设计方案 |
| [testing/](./testing/) | 测试策略、测试用例、回归清单、测试报告和缺陷记录 |
| [operations/](./operations/) | 性能、无障碍等专项移交材料 |
| [evidence/](./evidence/) | 未决项和验收证据索引 |
| [generalization/](./generalization/) | 通用化改造设计与实施文档 |
| [releases/](./releases/) | 发布记录和发布证据 |
| [reports/](./reports/) | 历史重构和专项报告 |
| [security/](./security/) | 安全复核记录 |
| [web-mockups/](./web-mockups/) | Web 界面视觉稿 |
| [miniprogram-mockups/](./miniprogram-mockups/) | 小程序界面视觉稿 |

仓库根目录的 `README.md` 保留为开发入口；本地生成的 `architecture/CODEGRAPH.md` 不入库。测试夹具、用户上传文档、缓存和构建产物继续放在各自的运行目录中。

## SaaS 平台后续设计入口

- [PaperGuard SaaS 平台产品与技术设计（2026-10-01）](./product/saas-platform-design-20261001.md)：租户与身份、资源隔离、权益、AI 计量、账务、运营、恢复迁移、开发工作包和验收矩阵的完整目标设计；目标设计不等于全部实现。
- [SaaS 运行时基础实施状态（2026-10-04）](./tenancy/implementation-status-20261004.md)：PR #34 当前已实现边界、验证状态和未决项。
- [商业化范围与放行条件（2026-09-30）](./product/商业化范围与放行条件_20260930.md)：C0/C1/C2 阶段边界。
- [未决项与验收证据](./evidence/未决项_20260913.md)：保持历史验证状态与生产残余风险。
