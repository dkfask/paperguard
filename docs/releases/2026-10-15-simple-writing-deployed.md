# 2026-10-15 模板写作简单闭环生产发布

生产站点 <https://paper.thunderuni.com> 已部署本次发布，服务器制品摘要、运行版本及公网只读检查通过。最终运行核验时间为 2026-10-15 18:58:18（Asia/Shanghai）。

## 发布身份与范围

| 项目 | 值 |
| --- | --- |
| 发布提交 | `4b79f9638b5ed7f6240b01a51063fc57ce62eafa` |
| 发布标记 | `4b79f96-simple-writing-b20261015T104714Z` |
| 主线基准 | `37a87af0ec5f299917c1a4de2f4cfe666767119d` |
| 发布分支 | `codex/simple-writing-release-20261015` |
| 代码审查 | [PR #40](https://github.com/dkfask/paperguard/pull/40)，草稿，尚未合入 main |
| 上一版本 | `98b51d7-role-workflows-b20261012T085040Z` |

发布包含最新主线、模板写作简单闭环和本地字体制品。检查会先保存正文，再展示规范与模板缺项；空章节、只有标题的章节不算完成；模板必填章节不能通过删除或降级绕过。学生预览及同步、异步导出执行必填门禁，教师和管理员保留草稿导出权限。

原工作区既有的其他未提交界面和文档修改没有带入发布。发布源来自独立工作区，提交后保持干净。最终远端核验确认 main 仍为上述基准，发布分支与生产提交相同；不能将生产版本、main 和原始未提交工作区称为完全一致。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端全量测试，项目 Python 3.12 虚拟环境 | 1129 passed，2 条既有依赖弃用警告 |
| 前端单元测试 | 33 文件，146 passed |
| 前端类型、学院管理检查、构建 | 全部通过 |
| 指定写作闭环、全生命周期、界面与密码恢复浏览器回归 | 16 passed，2 项按既有设备分工跳过 |
| Docker 后端、worker、前端构建 | 全部通过；冻结身份后的后端镜像身份检查通过 |
| 发布提交 CI | 8 项 completed/success，全部绑定本次完整提交 SHA |
| 生产 Python 3.10.12 隔离启动 | 通过，198 条 OpenAPI 路径 |
| 生产文件摘要 | 后端 150 文件、前端 90 文件与制品清单一致 |
| 服务状态 | paperguard、paperguard-worker、nginx 均 active |
| 公网 live、ready、version | 全部 HTTP 200，version 返回本次完整提交 SHA |
| 公网桌面与手机登录页 | 品牌、密码找回入口、字体正常；无控制台错误，手机无横向溢出 |

本地浏览器回归覆盖建稿、填写模板必填项、检查和 Word 下载，也覆盖主线已有的导入审核归档、保存失败重试、密码重置及旧会话失效。邮件传输在隔离测试中模拟。详细命令与初次失败分类见发布提交中的 [准备记录](https://github.com/dkfask/paperguard/blob/4b79f9638b5ed7f6240b01a51063fc57ce62eafa/docs/releases/2026-10-15-simple-writing-preparation.md)。

公网浏览器第一次独立 API 探针曾收到一次 522；随后独立 HTTP 探针、多次版本核对和完整浏览器复核均通过，未复现，原因尚未确认。最终浏览器复核记录 0 次应用业务写请求，另有一次 Cloudflare `/cdn-cgi/rum` 遥测 POST；没有发送验证码、邮件或调用 AI。

## 备份、切换与回滚

在停止 API 和 worker 后使用仓库现有备份能力生成一致性备份，并在服务器内恢复到演练目录，核对完整性和迁移账本后再切换。备份及恢复副本留在服务器，没有下载业务数据库或用户文件。

- 备份位置：`/home/ubuntu/paperguard/backups/pre-4b79f96-simple-writing-b20261015T104714Z/data.zip`
- 备份 SHA-256：`2624867c03deb936a815243917dcd04008ae1147a11fec714b0a8c5ffcde3d82`，21 个文件，恢复演练验证通过。
- 迁移账本仍为 14 项，发布前后相同；没有修改生产配置或后端依赖。
- 前端使用现有原子切换脚本，后端保留上一版目录；发布执行器具备失败回滚逻辑，本次未触发回滚。
- 服务器发布目录：`/home/ubuntu/paperguard/releases/4b79f96-simple-writing-b20261015T104714Z`。

## 证据与边界

- [服务器发布结果](../evidence/simple-writing-release-20261015/deployment-result.json)、[备份验证](../evidence/simple-writing-release-20261015/backup-result.json)、[生产解释器隔离启动](../evidence/simple-writing-release-20261015/staging-result.json)。
- [同提交 CI](../evidence/simple-writing-release-20261015/ci-checks.json)、[最终运行核验](../evidence/simple-writing-release-20261015/final-runtime-check.json)、[公网浏览器结果](../evidence/simple-writing-release-20261015/public-smoke-result.json)。
- [后端摘要](../evidence/simple-writing-release-20261015/source-checksums.json)、[前端摘要](../evidence/simple-writing-release-20261015/frontend-checksums.json)。
- [桌面截图](../evidence/simple-writing-release-20261015/public-smoke-login-desktop.png)、[手机截图](../evidence/simple-writing-release-20261015/public-smoke-login-mobile.png)。

本次完成受控生产发布与公网入口核验。生产端没有执行真实学生业务写入或邮件实际投递，不将这些检查称为真实 UAT。历史归档稿保存冲突与草稿合并恢复不在本次范围；小程序真机和完整多租户隔离也不在本次交付口径内。
