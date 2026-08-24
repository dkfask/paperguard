#!/usr/bin/env bash
#
# PaperGuard 一键部署 / 升级脚本 (在服务器上执行)
#
# 用法:
#   bash deploy.sh             # 拉取最新代码 + 构建 + 启动 + 健康检查
#   bash deploy.sh --no-pull   # 跳过 git pull (用当前代码)
#   bash deploy.sh --rebuild   # 强制重新构建镜像 (依赖变更时)
#
# 要求: 服务器已安装 Docker 与 Docker Compose
set -euo pipefail

cd "$(dirname "$0")"

echo "==> [1/5] 检查 Docker 环境"
if ! command -v docker >/dev/null 2>&1; then
  echo "错误: 未找到 docker, 请先安装: https://docs.docker.com/engine/install/"
  exit 1
fi
docker compose version >/dev/null 2>&1 || { echo "错误: docker compose 不可用"; exit 1; }

echo "==> [2/5] 拉取最新代码"
if [[ "${1:-}" != "--no-pull" ]]; then
  git pull --ff-only
fi

echo "==> [3/5] 构建镜像"
docker compose up -d --build

echo "==> [4/5] 等待服务就绪"
FRONTEND_PORT=$(docker compose port frontend 80 2>/dev/null | sed 's/.*://' || echo 8080)
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${FRONTEND_PORT}/api/health" >/dev/null 2>&1; then
    echo "    服务健康检查通过 (${i}s)"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "错误: 服务 30s 内未就绪, 查看日志: docker compose logs"
    exit 1
  fi
  sleep 1
done

echo "==> [5/5] 部署完成"
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "服务器IP")
echo ""
echo "  前端: http://${IP}:${FRONTEND_PORT}"
echo "  后端通过前端反向代理提供, 不直接暴露端口"
echo "  管理员密码由 PAPERGUARD_ADMIN_PASSWORD 配置"
echo "  常用命令:"
echo "    查看日志   docker compose logs -f"
echo "    停止服务   docker compose down"
echo "    数据备份   docker run --rm -v paperguard_data:/data -v \$(pwd):/backup alpine tar czf /backup/paperguard-backup.tar.gz -C /data ."
