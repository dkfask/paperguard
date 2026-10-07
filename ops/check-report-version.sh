#!/usr/bin/env bash
#
# 核对报告里声称的生产版本与服务器上的真实版本是否一致。
#
# 为什么需要它：本项目的审查报告里，「当前生产版本」这个标注已经陈旧过
# 两次——先是报告写 b418904 时生产已到 b674943，后是报告写 b674943 时
# 生产已到 56eae5d-rc9。根因是**在只会追加的历史表里手工维护「当前」这种
# 易变状态**：每发一次版它就必然过期一次，而过期的部署信息比没有更危险
#（回滚时会照着错的版本号去找不存在的目录）。
#
# 这个脚本把「报告声称的版本」和「服务器 current-release 标记」对一遍，
# 不一致就非零退出。报告已改为不再内联标注当前版本，本脚本改为核对
# 部署记录表**最后一行**的版本号——那是本次交付真正上线的那个版本。
#
# 用法：
#   ./ops/check-report-version.sh                      # 用默认主机与报告路径
#   PAPERGUARD_UAT_BASE_URL=... ./ops/check-report-version.sh
#
# 退出码：0 一致；1 不一致；2 输入/环境不满足。

set -euo pipefail

REPORT="${PAPERGUARD_REPORT:-docs/reports/UI界面审查发现_20260929.md}"
SSH_TARGET="${PAPERGUARD_SSH_TARGET:-perpetual}"
SSH_KEY="${PAPERGUARD_SSH_KEY:-$HOME/.ssh/campus-mall-deploy-2026-07-18-codex}"
MARKER_PATH="${PAPERGUARD_MARKER:-/home/ubuntu/paperguard/current-release}"

die() {
  printf 'check-report-version input error (%s): %s\n' "$1" "$2" >&2
  exit 2
}

[[ -f "$REPORT" ]] || die "input.report" "report not found: $REPORT"
[[ -r "$REPORT" ]] || die "input.report" "report not readable: $REPORT"
[[ -f "$SSH_KEY" ]] || die "input.ssh_key" "ssh key not found: $SSH_KEY"

# 部署记录表最后一行形如：| 11 | 前端 dist（...） | `56eae5d-rc9` | ... |
# 取最后一个「前端 dist」行的版本号列。
# 末尾的 `|| true` 不能省：grep 无匹配时返回 1，在 pipefail 下会让整个
# 命令替换以 1 退出，set -e 会在下面那条「空值检查」之前就把脚本掐掉，
# 于是「报告里根本没有部署记录」被报成退出码 1（版本不一致），
# 而它其实是输入错误，应当是退出码 2。两者含义不同，自动化才分得清。
claimed="$( { grep -E '^\|[[:space:]]*[0-9]+[[:space:]]*\|.*前端 dist' "$REPORT" \
  | tail -n 1 \
  | awk -F'|' '{print $4}' \
  | sed 's/[ `]//g'; } || true )"

[[ -n "$claimed" ]] || die "parse.claimed" "could not find a frontend deploy row in $REPORT"

printf '报告最后一条前端部署记录声称的版本: %s\n' "$claimed"

actual="$(ssh -i "$SSH_KEY" -o ConnectTimeout=15 -o BatchMode=yes "$SSH_TARGET" \
  "cat '$MARKER_PATH' 2>/dev/null" || true)"
actual="$(printf '%s' "$actual" | tr -d '[:space:]')"

if [[ -z "$actual" ]]; then
  die "read.marker" "could not read $MARKER_PATH on $SSH_TARGET"
fi

printf '服务器 %s 的实际版本: %s\n' "$MARKER_PATH" "$actual"

if [[ "$claimed" == "$actual" ]]; then
  printf '一致 ✓\n'
  exit 0
fi

printf '不一致 ✗ —— 报告部署记录与生产实际版本不符。\n' >&2
printf '  报告: %s\n  生产: %s\n' "$claimed" "$actual" >&2
printf '  处理：以生产标记文件为准，并更新报告部署记录表的最后一行。\n' >&2
exit 1
