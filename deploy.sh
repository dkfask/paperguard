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
  git pull --ff-only || echo "警告: git pull 失败, 继续使用当前代码"
fi

echo "==> [3/5] 构建镜像"
if [[ "${1:-}" == "--rebuild" ]]; then
  docker compose up -d --build
else
  docker compose up -d
fi

echo "==> [4/5] 等待后端就绪"
BACKEND_PORT=$(docker compose port backend 8000 2>/dev/null | sed 's/.*://' || echo 8000)
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
    echo "    后端健康检查通过 (${i}s)"
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "错误: 后端 30s 内未就绪, 查看日志: docker compose logs backend"
    exit 1
  fi
  sleep 1
done

echo "==> [5/5] 部署完成"
FRONTEND_PORT=$(docker compose port frontend 80 2>/dev/null | sed 's/.*://' || echo 8080)
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "服务器IP")
echo ""
echo "  前端: http://${IP}:${FRONTEND_PORT}"
echo "  后端: http://${IP}:${BACKEND_PORT}/api/health"
echo "  默认管理员: admin / admin123 (上线后请立即修改)"
echo ""
echo "  常用命令:"
echo "    查看日志   docker compose logs -f"
echo "    停止服务   docker compose down"
echo "    数据备份   docker run --rm -v paperguard_data:/data -v \$(pwd):/backup alpine tar czf /backup/paperguard-backup.tar.gz -C /data ."
