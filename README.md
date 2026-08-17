# PaperGuard — 论文格式规范检查与自动修复

基于 **FastAPI + Vue 3** 的 Web 应用:上传 `.docx` 论文,按 YAML 规则包(国标/校规)自动检查格式问题,并可一键修复后下载新文件。**原文件永不被修改,服务器不保存论文内容。**

## 架构

```
┌─────────────┐   HTTP    ┌─────────────────────┐   python-docx   ┌──────────────┐
│  Vue 3 前端  │ ───────▶ │  FastAPI 后端        │ ──────────────▶ │  核心引擎     │
│ 上传/报告/   │  /api/*  │  check / fix / packs │  IR + 规则包    │  (无 GUI)    │
│ 预览/下载    │          └─────────────────────┘                 └──────────────┘
└─────────────┘
```

- **backend/** — FastAPI API + 核心引擎(不可变 IR → YAML 规则包 → JMESPath 作用域过滤 → JSONLogic 条件求值 → 修复回写)
- **frontend/** — Vue 3 + Vite 单页应用,拖拽上传、检查报告、docx 预览、一键修复下载
- 规则包:`backend/app/core/rules/packs/*.yaml`(GB/T 7713、GB/T 7714、SIM-UNIV 通用,共 64 条规则)

## 快速开始(本地开发)

```bash
# 后端 (Python 3.10+)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload          # http://localhost:8000

# 前端 (Node 18+)
cd frontend
npm install
npm run dev                            # http://localhost:5173 (自动代理 /api)
```

浏览器打开 http://localhost:5173 ,拖入 `.docx` 即可检查。

## Docker 部署

```bash
docker compose up --build
# 前端 http://localhost:5173,后端 API http://localhost:8000
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/packs` | 规则包列表 |
| GET | `/api/packs/{id}` | 规则包详情(规则条数/列表) |
| POST | `/api/check` | 上传 docx + pack → 检查报告 JSON |
| POST | `/api/fix` | 上传 docx + pack → 返回修复后的新 docx |

## 命令行

核心引擎也保留了 Click CLI(`pip install -e backend` 后):

```bash
paperguard check paper.docx --pack GB-T-7713-1-2006
paperguard fix paper.docx -o fixed.docx --pack GB-T-7713-1-2006
paperguard list-packs
```

## 测试

```bash
cd backend
pytest -q        # 380+ 测试:IR / 规则引擎 / docx 读写 / 修复闭环 / API
```

## 规则包

内置三个规范包,可仿照编写自己的学校规范(`backend/app/core/rules/packs/`):

- `GB-T-7713-1-2006` — 学位论文编写规则(29 条)
- `GB-T-7714-1-2015` — 参考文献著录规则(12 条)
- `SIM-UNIV-generic` — 通用学校样式规范(23 条,自制零版权)

规则结构:每条规则包含 `scope`(JMESPath 过滤作用域)、`when`(JSONLogic 违规条件)、`message` 和可选的 `fix`(set/insert/delete/wrap)。
