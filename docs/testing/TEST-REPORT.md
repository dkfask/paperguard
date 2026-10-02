# PaperGuard GUI 测试报告

- 日期:2026-09-06
- 分支:`feat/template-import-and-stability`(round 3 基于修复后代码, commit `045d1f2`)
- 测试方式:常规测试(pytest + vite build)→ 浏览器黑盒 GUI 测试(web-gui-tester 规范)
- 测试栈:后端 uvicorn :8100(独立测试数据库)+ 前端 vite :5174 + 真实 Chrome 152(headless=new + CDP 真实键鼠/上传/下载)
- 截图目录：`../../gui-test-screenshots/`

## 〇-Z、AI 写作助手功能测试(round 5, 功能交付验证)

新增 AI 写作助手功能(系统自带 + 用户自带模型, 用户配置优先)的交付验证, **GUI E2E 11/11 通过 + 后端 8 个新用例全过 + 全量回归 440 passed**:

| # | 测试点 | 结果 |
|---|---|---|
| A1 | 块级润色走系统模型并回写(【AI润色】前缀 + 已保存) | ✅ |
| A2 | 块级续写追加到原文末尾 | ✅ |
| A3 | 摘要/Abstract/关键词/Key Words 四类章节 AI 生成, 内容正确(中英文关键词区分) | ✅ |
| A4 | AI 生成内容刷新后仍在(已入库, 乐观锁保存链路) | ✅ |
| A5 | 用户自带配置: 测试连接成功(绿条)→保存→来源徽标切为「自有模型 (user-model)」 | ✅ |
| A6 | AI 启用时 润色/续写/AI生成 按钮全部出现 | ✅ |
| A7 | 无任何配置时 AI 按钮全部隐藏(功能优雅降级) | ✅ |
| API | status 开关/用户配置 CRUD+优先级回落/config-test/输入校验/未登录 401 | ✅ (test_ai_api.py 8 用例) |

证据: ![AI生成章节](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_ai_generated_sections.png) ![AI设置弹窗-连接成功](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_ai_settings_dialog.png) ![无配置按钮隐藏](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_ai_disabled_hidden.png)

E2E 发现并当场修复的问题:
1. `english_keywords` 章节直接把 type 当 action 发送被后端 400 拒绝 → 前端映射为 `keywords`(Writer.vue)
2. `bodyContext()` 只收集 `type==='body'` 章节, GB7713 模板下拿不到正文(正文在 heading 小节段落里) → 改为排除封面/目录/四个 AI 目标章节后全量收集

## 〇-A、完整测试(round 3, Chrome + CDP 文件上传全链路)

前两轮被运行时阻塞的「文件上传」类功能本轮全部覆盖。**20/20 通过**。

| # | 测试点 | 结果 | 证据/产物 |
|---|---|---|---|
| E1-a~c | 上传检查:选 docx → 规则包检查报告(11 问题=0错/10警/1提) → 自动修复并下载 | ✅ | ![检查报告](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e1_check_report.png) |
| E1-验证 | 修复产物 `sample_paper-fixed.docx` 用 python-docx 打开:结构有效, 内容与原文件一致(4 段/28 字符, 无丢失) | ✅ | `/tmp/paperguard-dl/`(临时) |
| E2 | 按上传模板检查(gb7713-official.docx + sample_paper.docx)报告展示 | ✅ | ![按模板检查](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e2_template_check.png) |
| E3-a | 从 Word 导入:选文件 → 识别结果预览(5 样式表: Heading1/2/3/Title/BookTitle, 中西文字体/字号/行距全表) | ✅ | ![识别预览](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e3_import_preview.png) |
| E3-b | 「✅ 识别正确, 创建为模板」→ 生成 `zhejiang-style (导入)` 并进入编辑器 | ✅ | ![导入编辑器](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e3_imported_editor.png) |
| E4-a | 新建项目(显式选 GB7713 模板)→ 封面 7 字段保存 | ✅ | 截图 chrome_e4_all_sections.png |
| E4-b | 10 个必填章节逐一填写+自动保存, 完成度 10/10(按「未完成 ●」计数逐章节重试验证) | ✅ | ![全章节完成](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e4_all_sections.png) |
| E4-c | 满足约束后**正常导出**(无 409/无跳过)→ 下载 `Chrome 全流程论文 B.docx`(38.6KB) | ✅ | ![导出完成](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e4_export_done.png) |
| E4-验证 | 导出 docx 用 python-docx 验证:封面三字段/中英文摘要/关键词/各章内容 10/10 命中;第一章标题样式 = Times New Roman 18pt 加粗(模板锁定生效) | ✅ | python-docx 断言输出 |
| E5-a | 表格块插入(9 个单元格输入框渲染) | ✅ | ![表格图片](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e5_table_image.png) |
| E5-b | 图片块上传(点上传区 → 选 PNG)→ 预览渲染 + 自动保存 | ✅ | ![图片上传](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e5_image_uploaded.png) |
| E6-a | 上传 .txt 被拒:「⚠ 只支持 .docx 文件」, 无崩溃报告 | ✅ | ![拒绝非docx](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e6a_reject_non_docx.png) |
| E6-b | 注册 cdp_student 成功;重复注册被拒「⚠ 用户名已存在」 | ✅ | chrome_e6b_dup_username.png |
| E6-c | 删除项目(原生 confirm)→ 列表 4→3 | ✅ | chrome_e6c_project_deleted.png |
| E6-d | 新建模板(/templates/new/edit)→ 保存 → 列表出现 → 删除 → 列表恢复且内置模板无损 | ✅ | chrome_e6d_template_deleted.png |
| E7-a | 学生:模板页只读模式 + 🔒只读按钮, 无新建/导入入口 | ✅ | ![学生只读](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_e7_student_readonly.png) |
| E7-b | 服务端 RBAC:学生 token PUT /api/templates → **403「仅教师/管理员可管理模板」** | ✅ | API 响应体 |
| 日志 | 全程后端无 5xx/Traceback;仅预期 4xx(409 校验/重复名, 401/403 RBAC) | ✅ | /tmp/pg-backend.log |

round 3 发现的新观察(均已修复并回归验证, `frontend/src/views/Home.vue`):

4. ~~[P4-样式] 错误横幅重复渲染~~ **已修复**:移除检查区块内的重复 `v-if="error"`, 现只显示一次(V1 验证, count=1)。
5. ~~[P4-UX] 导入模板后「基于模板」默认选中变为新导入模板~~ **已修复**:默认选内置模板(default-gb7713), 其次保留用户已选项(V2 验证: 导入后仍选中「学位论文模板 (GB/T 7713)」)。

round 3 测试工程说明:Chrome headless 走 CDP `Input.dispatchMouseEvent/dispatchKeyEvent`(真实键鼠事件)、`DOM.setFileInputFiles`(真实文件选择)、`Browser.setDownloadBehavior` 捕获下载;上传控件定位时发现 `DOM.requestNode` 在 Chrome 152 协议下不可用, 改用 `DOM.getDocument+querySelector`。测试驱动为一次性脚本(/tmp/pg-e2e/, 已清理)。

### P4 修复验证(round 4, 2026-09-06)

两个 P4 观察项修复后用 Chrome 重跑验证,**3/3 通过**:

| # | 断言 | 结果 |
|---|---|---|
| V1 | 非 docx 上传错误横幅只出现一次 | ✅ ![单一错误横幅](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_v1_single_error_banner.png) |
| V2 | 导入模板后「基于模板」默认仍为内置模板 | ✅ ![默认模板保持](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_v2_default_template_kept.png) |
| R | 回归抽测:规则包检查报告正常 | ✅ |

## 〇-B、修复与 Chrome 回归(第二轮)

第一轮发现的 3 个 P3 问题已全部修复(`frontend/src/views/Login.vue`,+30/-7, commit `045d1f2`):

1. 模式切换清除旧错误:`switchMode()` 统一处理登录/注册切换并重置提示状态
2. 注册密码占位符「至少 6 位」→「至少 8 位」(与后端 `auth.py` 校验规则对齐)
3. 新增 `.notice-banner` 绿色成功样式,「注册成功」「密码已更新」不再误用红色错误横幅

**验证:vite build 通过;后端 pytest 回归 432 passed, 5 skipped。**

Chrome 回归(真实 Chrome 152,headless 新模式 + CDP 真实输入事件,8 个断言全部通过):

| # | 断言 | 结果 | 证据 |
|---|---|---|---|
| R1-a | 错误密码仍显红色「⚠ 用户名或密码错误」 | ✅ | ![Chrome 登录错误](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_r1a_login_error.png) |
| R1-b | 切到注册模式后旧错误消失 | ✅ | ![Chrome 切换无残留](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_r1b_switched_no_stale_error.png) |
| R2 | 注册密码占位符 =「至少 8 位」 | ✅ | 同上截图可见 |
| R3 | 注册成功显示绿色「✅ 注册成功, 请登录」 | ✅ | ![Chrome 注册成功绿色](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_r3_register_success_green.png) |
| R4-a/b | admin 首登强制改密;成功提示绿色「✅ 密码已更新…」 | ✅ | ![Chrome 改密成功绿色](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_r4_password_updated_green.png) |
| R5-a/b | 新密码登录进首页;模板管理页 Chrome 渲染正常 | ✅ | ![Chrome 首页](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_r5a_home.png) ![Chrome 模板页](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/chrome_r5b_templates.png) |

> 说明:browser-use 工具链只注册了内嵌浏览器(无 Chrome 扩展/CDP 后端);本机显示器处于熄屏状态导致桌面 GUI 自动化不可用,故第二轮用真实 Chrome 二进制(headless=new,独立临时 profile)+ CDP Input 域真实键鼠事件驱动,截图为 Chrome 真实渲染输出。测试后 Chrome 实例与临时 profile 已删除。

## 一、常规测试结果

| 项目 | 结果 |
|---|---|
| 后端 pytest | **432 passed, 5 skipped**(7.39s),3 个无害 warning |
| 前端 vite build | 通过(50 modules,208ms) |

## 二、环境准备(测试前置操作声明)

- 使用独立测试数据库 `paperguard-guitest.db`(现删),未触碰真实开发库 `paperguard.db`
- 通过 `PAPERGUARD_ADMIN_PASSWORD` 引导种子 admin 账号;学生账号 `guitest_student` 由 GUI 注册流程创建
- 5173/8000 被既有进程占用,测试使用 5174/8100;测后已停止服务并删除临时配置与测试数据

## 三、GUI 测试结果总览

### P0 主流程 — 全部通过 ✅

| # | 测试点 | 结果 | 证据 |
|---|---|---|---|
| T1-1 | 未登录访问 `/` 被守卫重定向到登录页 | ✅ | ![登录页](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t1_login_initial.png) |
| T1-2 | admin 首登强制改密(`?forceChange=1`)→ 改密成功提示 → 新密码重登 | ✅ | ![强制改密](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t1_force_change_password.png) ![改密成功](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t1_password_updated_msg.png) |
| T2-1 | 模板列表显示内置模板(18 章节) | ✅ | ![模板列表](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t2_templates_list.png) |
| T2-2 | 模板编辑器完整渲染(7 封面字段/18 章节/16 类样式) | ✅ | ![编辑器](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t2_template_editor.png) |
| T2-3 | 修改名称→保存(绿条「模板已保存」)→返回列表持久化验证→恢复原名 | ✅ | ![保存成功](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t2_template_save_success.png) |
| T3-1 | 新建论文→跳转写作页,章节树按模板生成,模板自动检查通过 | ✅ | ![写作页](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t3_writer_cover_empty.png) |
| T3-2 | 封面 7 个必填字段保存(「已保存 11:57:18」) | ✅ | ![封面已保存](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t3_cover_saved.png) |
| T3-3 | 章节正文输入自动保存,完成度 0/10→1/10、总字数 118→200 实时更新 | ✅ | ![自动保存](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t3_cover_saved.png) |
| T4-1 | 内容不足时导出被拦截:红色面板列出 9 个未完成必填项(后端 409) | ✅ | ![导出拦截](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t4_export_blocked_validation.png) |
| T4-2 | 「仍要导出(跳过检查)」→ 后端 200,浏览器下载事件触发 | ✅ | ![导出成功](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/t4_export_success_panel_closed.png) |

### P1 交互反馈 — 全部通过 ✅

| # | 测试点 | 结果 | 证据 |
|---|---|---|---|
| P1-1 | 错误密码登录:红色警示「用户名或密码错误」 | ✅ | ![登录失败](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/p1_login_error.png) |
| P1-2 | 退出登录回到登录页,受保护路由再次被守卫拦截 | ✅ | 同上 |
| P1-3 | 写作页「重新检查」正常返回模板合规结果 | ✅ | ![重新检查](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/p1_recheck_pass.png) |
| P1-4 | 学生 RBAC:模板管理变只读模式,编辑/新建/导入按钮隐藏 | ✅ | ![学生只读](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/p1_student_templates_readonly.png) |

### P2 输入边界 — 全部通过 ✅

| # | 测试点 | 结果 | 证据 |
|---|---|---|---|
| P2-1 | 2 字符用户名注册被拦截:「用户名至少 3 个字符」 | ✅ | ![短用户名](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/p2_register_short_username.png) |
| P2-2 | 3 位密码注册被拦截:「密码至少 8 位」 | ✅ | ![短密码](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/p2_register_short_password.png) |
| P2-3 | 合法学生账号注册成功,提示「注册成功, 请登录」,新账号可登录(角色徽标「学生」) | ✅ | ![注册成功](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/p2_register_success.png) ![学生首页](file:///Users/a1234/Documents/perpetual/paperguard/gui-test-screenshots/p1_student_home.png) |

### 无法执行的测试点

| 测试点 | 原因 |
|---|---|
| T5 上传检查(选择/拖入 .docx) | 浏览器运行时(IAB)不支持文件选择器,黑盒 GUI 无法模拟上传;该链路已由后端 pytest 覆盖(432 用例含 API 测试) |
| 从 Word 导入模板 | 同上,依赖文件上传 |
| 图片/表格块插入 | 图片依赖上传,未测;表格块未覆盖 |

## 四、发现的问题(均为低优先级,不阻塞主流程)

1. **[P3-UX] 模式切换不清除旧错误**:登录失败后在登录页点「注册学生账号」,注册表单顶部仍显示「⚠ 用户名或密码错误」(与注册场景无关的旧错误)。
2. **[P3-文案] 注册密码规则不一致**:注册框占位符写「至少 6 位」,实际校验为「密码至少 8 位」。
3. **[P3-样式] 成功提示使用错误样式**:「注册成功, 请登录」「密码已更新…」均为正常流程成功提示,却复用红色 ⚠ 错误横幅样式,易误解为出错。

## 五、其他观察

- 空标题点「新建论文」:允许创建,侧栏标题回退为模板名(合理降级,建议确认是否符合预期)
- 完成度只统计写作章节(封面填写不计入),与导出校验的 9 项必填清单一致
- 后端测试日志无 5xx/Traceback;前端 console 日志因运行时限制无法采集,以页面可见错误为准(全程未见异常)

## 六、测试执行备注

- 会话内浏览器截图子系统偶发超时(重试即恢复),与被测应用无关
- Playwright locator click 在该应用上持续 actionability 超时,所有点击改用坐标点击(真实用户行为模拟),已用 `elementFromPoint` 只读验证无遮挡
