#!/usr/bin/env bash
# PaperGuard 前端发布脚本（生产）
#
# 解决两个问题：
#   1. 之前的前端发布是临时 SSH 手敲的，步骤不可复现、不可审计
#      （生产 nginx 配置与目录换名方式都没进仓库，见 ops/nginx/paperguard.site.conf）
#   2. rsync 复制了 macOS 的 AppleDouble 垃圾文件（._* / .DS_Store）到生产
#
# 用法（在服务器上执行）：
#   ./ops/deploy-frontend.sh <构建产物目录> <版本号>
# 例：
#   ./ops/deploy-frontend.sh /tmp/paperguard-dist 1cbc8f5
#
# 特性：先传到同级临时目录，校验通过后再原子换名，失败不影响现网。

set -euo pipefail

SRC="${1:-}"
VERSION="${2:-}"
WEBROOT="${PAPERGUARD_WEBROOT:-/var/www/paperguard}"
RELEASES="${PAPERGUARD_RELEASES:-/home/ubuntu/paperguard/releases}"
MARKER="${PAPERGUARD_MARKER:-/home/ubuntu/paperguard/current-release}"

if [[ -z "$SRC" || -z "$VERSION" ]]; then
  echo "用法: $0 <构建产物目录> <版本号>" >&2
  exit 2
fi
if [[ ! -f "$SRC/index.html" ]]; then
  echo "错误: $SRC 下没有 index.html，不是有效的构建产物" >&2
  exit 1
fi

STAGE="${WEBROOT}.staging-${VERSION}"
BACKUP="${WEBROOT}.prev-${VERSION}-$$"

# /var/www 属 root:root，而 /var/www/paperguard 属 ubuntu:ubuntu，
# 所以以 ubuntu 身份运行时对 WEBROOT 的创建与换名都需要提权。
# 这里自动探测，脚本既能以 root 跑，也能以 ubuntu 直接跑。
WEBROOT_PARENT="$(dirname "$WEBROOT")"
SUDO=""
if [[ ! -w "$WEBROOT_PARENT" ]]; then
  if ! command -v sudo >/dev/null 2>&1 || ! sudo -n true 2>/dev/null; then
    echo "错误: 无权写入 ${WEBROOT_PARENT}，且 sudo 不可免密使用" >&2
    echo "      请改用具备权限的账号执行本脚本" >&2
    exit 1
  fi
  SUDO="sudo -n"
  echo "==> 0/5 ${WEBROOT_PARENT} 需要提权，使用 sudo 执行目录操作"
fi

echo "==> 1/5 传输到暂存目录 ${STAGE}"
${SUDO} rm -rf "$STAGE"
${SUDO} mkdir -p "$STAGE"
# --exclude 是本次修复的核心：macOS 上构建会把 ._* 与 .DS_Store 一起打包，
# 之前这些 163 字节的 AppleDouble 文件被原样发布到了生产静态目录。
# rsync 必须与 mkdir 一样提权：STAGE 由 sudo 创建，属主是 root，
# 只给 mkdir 加 sudo 而不加 rsync 会写不进去（首次尝试即因此失败）。
${SUDO} rsync -a --delete \
  --exclude '._*' \
  --exclude '.DS_Store' \
  --exclude '__MACOSX' \
  "$SRC"/ "$STAGE"/

echo "==> 2/5 校验暂存目录"
test -f "$STAGE/index.html" || { echo "错误: 暂存目录缺少 index.html" >&2; exit 1; }
ENTRY=$(grep -oE '/assets/index-[A-Za-z0-9_-]+\.js' "$STAGE/index.html" | head -1)
if [[ -z "$ENTRY" ]]; then
  echo "错误: index.html 里找不到入口 JS，构建可能不完整" >&2
  exit 1
fi
test -f "$STAGE${ENTRY}" || { echo "错误: 入口 ${ENTRY} 不存在，构建产物不完整" >&2; exit 1; }
if find "$STAGE" -name '._*' -o -name '.DS_Store' | grep -q .; then
  echo "错误: 暂存目录仍含 AppleDouble 文件，rsync 排除规则未生效" >&2
  exit 1
fi
echo "    入口 ${ENTRY}"
echo "    文件数 $(find "$STAGE" -type f | wc -l | tr -d ' ')"

# Snapshot both markers before switching bytes. EXIT also covers explicit
# failed-probe exits; an ERR-only trap would miss those failure branches.
MARKER_STATE=$(mktemp -d)
MARKER_JSON="$(dirname "$MARKER")/current-release.json"
for key in plain json; do
  file="$MARKER"; [[ "$key" == json ]] && file="$MARKER_JSON"
  if [[ -f "$file" ]]; then cp -p "$file" "$MARKER_STATE/$key"; fi
done
SWAP_STARTED=0
rollback_on_exit() {
  code=$?
  trap - EXIT
  if [[ "$code" != 0 && "$SWAP_STARTED" == 1 ]]; then
    echo "错误: 切换后验证失败，恢复前端与版本标记" >&2
    if [[ -d "$BACKUP" ]]; then
      ${SUDO} rm -rf "$WEBROOT"
      ${SUDO} mv "$BACKUP" "$WEBROOT" || code=1
    elif [[ "$HAD_WEBROOT" == 0 ]]; then
      ${SUDO} rm -rf "$WEBROOT"
    fi
    for key in plain json; do
      file="$MARKER"; [[ "$key" == json ]] && file="$MARKER_JSON"
      if [[ -f "$MARKER_STATE/$key" ]]; then
        cp -p "$MARKER_STATE/$key" "$file" || code=1
      else
        rm -f "$file"
      fi
    done
  fi
  rm -rf "$MARKER_STATE"
  exit "$code"
}
trap rollback_on_exit EXIT
HAD_WEBROOT=0
[[ -d "$WEBROOT" ]] && HAD_WEBROOT=1
SWAP_STARTED=1
echo "==> 3/5 原子换名（先备份现网）"
if [[ -d "$WEBROOT" ]]; then
  ${SUDO} mv "$WEBROOT" "$BACKUP"
fi
${SUDO} mv "$STAGE" "$WEBROOT"
# 保持与现网一致的属主，避免后续以 ubuntu 维护时处处需要 sudo
WEB_OWNER="$(stat -c '%U:%G' "$BACKUP" 2>/dev/null || echo "ubuntu:ubuntu")"
${SUDO} chown -R "$WEB_OWNER" "$WEBROOT"
echo "    属主 ${WEB_OWNER}"

echo "==> 4/5 记录版本标记"
mkdir -p "${RELEASES}/${VERSION}"
find "$WEBROOT" -type f ! -name '._*' -print0 \
  | sort -z \
  | xargs -0 sha256sum \
  | sed "s#${WEBROOT}/##" > "${RELEASES}/${VERSION}/frontend.sha256"
echo "$VERSION" > "${RELEASES}/${VERSION}/frontend-entry.txt"
printf '%s\n' "$VERSION" > "${MARKER}.new-$$"
mv "${MARKER}.new-$$" "$MARKER"
echo "    current-release = $VERSION"

echo "==> 5/5 冒烟检查"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080/")
echo "    GET / -> $CODE"
[[ "$CODE" == "200" ]] || { echo "错误: 首页未返回 200" >&2; exit 1; }
for probe in /icon-shield.png "${ENTRY}"; do
  PCODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080${probe}")
  echo "    GET ${probe} -> ${PCODE}"
  [[ "$PCODE" == "200" ]] || { echo "错误: ${probe} 未返回 200" >&2; exit 1; }
done
# 修复 F-02：缺失静态资源必须 404，不能再回落到 index.html
FCODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080/favicon.ico")
echo "    GET /favicon.ico -> ${FCODE} (期望 404)"
[[ "$FCODE" == "404" ]] || echo "    警告: /favicon.ico 仍返回 ${FCODE}，nginx 站点配置可能未同步"

# JSON 标记只能在产物与后端源码均核验后更新，不能从 VERSION 猜测完整源码身份。
if [[ -n "${PAPERGUARD_RELEASE_SOURCE_MANIFEST:-}" ]]; then
  python3 "$(dirname "${BASH_SOURCE[0]}")/sync-release-markers.py" sync \
    --root "$(dirname "$MARKER")" --webroot "$WEBROOT" \
    --expected-release "$VERSION" \
    --source-manifest "$PAPERGUARD_RELEASE_SOURCE_MANIFEST" --write
else
  echo "    JSON 发布标记未同步：请提供 PAPERGUARD_RELEASE_SOURCE_MANIFEST 并完成摘要核验"
fi

echo "==> 发布完成。回滚：mv ${BACKUP} ${WEBROOT} 并把 ${MARKER} 改回旧版本号"
