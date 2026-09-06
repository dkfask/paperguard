# PaperGuard — 论文格式全流程约束系统

基于 **FastAPI + Vue 3** 的 Web 应用,把论文格式从"事后检查"变为"**写作时约束**":

- **模板约束写作**:教师在线配置论文模板(章节结构 + 每类元素的字体/字号/行距/缩进/分页规则 + 封面字段),学生从零开始写作时,结构由模板生成、排版样式自动应用且不可修改,只需填写内容
- **一键导出 docx**:写作内容按模板生成符合规范的 `.docx`,可直接提交
- **上传检查兜底**:对已有 `.docx` 按 YAML 规则包(GB/T 7713、GB/T 7714、校规)检查并自动修复,原文件永不被修改,上传文件仅在处理期间使用

## 架构

```
┌──────────────────────────┐   HTTP    ┌──────────────────────────────┐
│  Vue 3 前端               │ ───────▶ │  FastAPI 后端                 │
│  · 模板管理 (章节+样式)    │  /api/*  │  · 模板 CRUD / 项目 / 导出     │
│  · 结构化写作 (锁定样式)   │          │  · 核心引擎 (检查/修复引擎)     │
│  · 上传检查 / 报告         │          └──────────────────────────────┘
└──────────────────────────┘
```

- **backend/app/models/** — 模板(章节树 + TextStyle)与写作项目(段落块)数据模型
- **backend/app/core/export/** — 按模板生成 docx 的导出引擎
- **backend/app/core/** — 检查引擎(不可变 IR → YAML 规则包 → JMESPath/JSONLogic → 修复回写)
- **backend/app/db.py** — SQLite 存储(可用 `PAPERGUARD_DB_PATH` 覆盖)
- **frontend/** — Vue 3 + Vite:模板编辑页、写作页、上传检查页

## 快速开始(本地开发)

```bash
# 后端管理员 bootstrap 密码(至少 12 位, 含字母和数字)
export PAPERGUARD_ADMIN_PASSWORD='请替换为强密码'
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload          # http://localhost:8000

# 前端 (Node 18+)
cd frontend
npm install
npm run dev                            # http://localhost:5173 (自动代理 /api)
```

浏览器打开 http://localhost:5173:

1. **模板管理** — 编辑内置模板或新建(章节结构、每类元素样式、封面字段)
2. **我的论文** — 选择模板新建论文,进入结构化写作(左侧章节树,右侧锁定样式编辑器)
3. **导出 docx** — 写作页右上角一键导出
4. **上传检查** — 对已有 docx 做格式检查/修复
5. **AI 写作助手** — 写作页内润色/续写段落,按正文一键生成 摘要/Abstract/关键词

## AI 写作助手

两级模型配置,用户配置优先于系统默认,均走 OpenAI 兼容 `/chat/completions` 协议:

- **系统默认**(管理员配置,全体用户可用):

  ```bash
  export PAPERGUARD_AI_BASE_URL='https://api.openai.com/v1'  # OpenAI/智谱/DeepSeek/中转站均可
  export PAPERGUARD_AI_API_KEY='sk-xxx'
  export PAPERGUARD_AI_MODEL='gpt-4o-mini'
  # 可选: PAPERGUARD_AI_TIMEOUT(秒, 默认 60)
  ```

  本地开发也可以把这三行写进仓库根目录的 `.env`(参考 `.env.example`, 已被 gitignore), 后端与 docker-compose 会自动加载; 已显式设置的环境变量优先于 `.env`。

- **用户自带**:写作页左上角「✨AI」→ 填自己的 Base URL / API Key / 模型,可先「测试连接」再保存。仅对本账号生效,优先于系统默认。

说明:用户 API Key 以明文存储在本机 SQLite(与服务器环境变量同信任域,请勿把数据库文件外传);两者都未配置时 AI 按钮自动隐藏。

## Docker 部署(服务器)

```bash
# 1. 服务器安装 Docker + Docker Compose 后:
git clone https://github.com/dkfask/paperguard.git
cd paperguard
docker compose up -d --build

# 2. 访问
#    前端:  http://服务器IP:8080
#    后端不直接暴露, 由前端 Nginx 代理 /api
```

- 数据(论文项目 SQLite + 上传图片)持久化在 Docker volume `paperguard_data`,重建/升级容器不丢数据
- 首次部署前必须设置强密码环境变量 `PAPERGUARD_ADMIN_PASSWORD`(至少 12 位, 含字母和数字), 不再提供固定默认密码
- 上传检查和自动修复需要登录; 项目与图片按用户权限隔离
- 上传检查文件只在处理期间使用; 写作项目和图片会持久化到 Docker volume, 删除项目后按引用回收图片
- 导出和自动修复生成的临时文件在响应完成后清理

### 反向代理(可选,HTTPS/域名)

用 Nginx/Caddy 把 80/443 转发到 `http://127.0.0.1:8080` 即可,无需改应用配置。

### 升级

```bash
git pull
docker compose up -d --build
```

### 备份数据

```bash
docker run --rm -v paperguard_data:/data -v $(pwd):/backup alpine tar czf /backup/paperguard-backup.tar.gz -C /data .
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/templates` | 模板列表 |
| GET/POST/PUT/DELETE | `/api/templates/{id}` | 模板 CRUD(结构+样式+封面字段) |
| GET | `/api/projects` | 项目列表 |
| POST | `/api/projects` | 基于模板创建写作项目 |
| GET/PUT/DELETE | `/api/projects/{id}` | 项目读写 |
| PUT | `/api/projects/{id}/section/{key}` | 保存章节内容(标题+段落) |
| PUT | `/api/projects/{id}/meta` | 保存封面字段 |
| POST | `/api/projects/{id}/export` | 按模板导出 docx |
| GET | `/api/packs` | 检查规则包列表 |
| POST | `/api/check` | 上传 docx 检查 |
| POST | `/api/fix` | 上传 docx 自动修复并下载 |

## 命令行

核心引擎也保留了 Click CLI:

```bash
paperguard check paper.docx --pack GB-T-7713-1-2006
paperguard fix paper.docx -o fixed.docx --pack GB-T-7713-1-2006
```

## 测试

```bash
cd backend
pytest -q        # 423 个测试: 引擎 / docx 读写 / 模板/项目 API / 导出验证 / 安全回归
```

## 模板与规则

- **模板**(`/api/templates`):章节树 + 每类元素样式 + 封面字段,在线配置
- **检查规则包**(`backend/app/core/rules/packs/*.yaml`):GB/T 7713(29 条)、GB/T 7714(12 条)、SIM-UNIV 通用(23 条),每条含 `scope`(JMESPath)、`when`(JSONLogic)、`message` 和可选 `fix`
