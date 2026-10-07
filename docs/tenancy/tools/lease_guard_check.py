#!/usr/bin/env python3
"""租约栅栏静态检查：检出**空租约通配**。

为什么这个门禁存在
------------------
INC-47：`db.py` 里「凭租约 token 确认一次操作」这个语义有**三处独立实现、
三种严格度**：

| 位置 | 方法 | 写法 |
|---|---|---|
| `db.py:1676` | `complete_job` | `(lease_token IS NULL OR lease_token = '' OR lease_token = ?)` |
| `db.py:1791` | `mark_workflow_event_delivered_with_lease` | **同一串通配** |
| `db.py:1855` | `fail_workflow_event` | `if row["lease_token"] and row["lease_token"] != lease_token` |

前两处的后果是**任何持有 `id` 的调用方都能提交别人的任务/确认别人的事件**——
`lease_token` 参数默认值就是 `""`，而 `""` 恰好满足通配条件。

**为什么这个门禁与其余门禁不同，值得单独存在**
------------------------------------------------
其余门禁的静态判据只能证明「表建了、列有了、策略开了」，
**证明不了运行时行为**——加了就是虚假安全感。
但「空租约通配」是**可以用模式匹配精确检出、且已实际发生过两次**的形态。
静态检出它是有意义的。

**本门禁的边界（如实声明，不假装覆盖更多）**
--------------------------------------------
1. 只保证「带租约的 UPDATE 上不出现空租约通配」。
2. **不保证**该处用了正确的 CAS 条件——`fail_workflow_event` 那种
   「先 SELECT 再 UPDATE 且 UPDATE 不带 token 条件」的写法**不在本门禁视野内**，
   它同样是缺陷，但需要人工/运行时判据。
3. **不检查**租约列是否被正确索引、租约是否会被错误续期。

退出码：`0` 通过 / `1` 检出通配 / `2` 工具不可运行
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend/app"

# 判定为「空租约通配」的模式。
#
# 意图：允许「持有正确 token」，**禁止**「没有 token 也放行」。
# 两种历史写法都要抓：
#   (lease_token IS NULL OR lease_token = '' OR lease_token = ?)
#   (lease_token IS NULL OR lease_token = ?)
WILDCARD_PATTERNS = [
    re.compile(r"lease_token\s+IS\s+NULL\s+OR", re.I),
    re.compile(r"lease_token\s*=\s*'\s*'\s+OR", re.I),
    re.compile(r"lease_token\s*=\s*\"\s*\"\s+OR", re.I),
]

# 判定为「此处确实在处理租约」的标志。
#
# 为什么需要它：不能对全仓任何 `lease_token IS NULL` 都报警——
# `SELECT` 里读列、`SET lease_token = NULL`（**释放**租约，正确写法）
# 都不是缺陷。只有**用通配做条件判定**才是。
LEASED_CTX = re.compile(r"lease_token", re.I)


def scan_text(text: str) -> list[tuple[int, str]]:
    """返回 [(行号, 命中片段)]，只报**含通配且同处处理租约**的行。"""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not LEASED_CTX.search(line):
            continue
        for pat in WILDCARD_PATTERNS:
            m = pat.search(line)
            if m:
                hits.append((lineno, m.group(0)))
                break
    return hits


def verdict(total_files: int, hits: list[tuple[pathlib.Path, int, str]], err: str) -> int:
    if err:
        print(f"工具不可运行：{err}", file=sys.stderr)
        return 2
    if not total_files:
        print("✗ 未找到可扫描的源码文件 → 报 2（提取层空输入不得判绿）")
        return 2
    if hits:
        print("检出空租约通配：\n")
        for fname, lineno, frag in hits:
            rel = fname.relative_to(REPO)
            print(f"  ✗ {rel}:{lineno}  {frag}")
        print(
            "\n空租约通配 = 没有正确租约也能提交/确认。"
            "\n修法：精确匹配 lease_token，禁止任何 IS NULL / = '' 的放行分支"
            "（W-09 LE-5/LE-6）。"
        )
        return 1
    print(f"✓ 通过：{total_files} 个源文件内未检出空租约通配")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if not BACKEND.is_dir():
        return verdict(0, [], f"源码目录不存在：{BACKEND}")
    files = [p for p in BACKEND.rglob("*.py") if "__pycache__" not in str(p)]
    if not files:
        return verdict(0, [], "源码目录下无 .py 文件")
    hits: list[tuple[pathlib.Path, int, str]] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            return verdict(0, [], f"读取失败 {p}: {exc}")
        for lineno, frag in scan_text(text):
            hits.append((p, lineno, frag))
    return verdict(len(files), hits, "")


def self_test() -> int:
    """阳性对照 + 阴性对照 + 变异。

    三者缺一不可：
    - 阳性：真缺陷必须被抓到
    - 阴性：**正确**写法（释放租约、严格比较）不得被误报
    - 变异：把阴性样本改成阳性，判定必须跟着变
    """
    print("=" * 68)
    print("lease_guard_check 自检")
    print("=" * 68)
    results: list[tuple[str, bool, str]] = []

    def run(src: str) -> list[tuple[int, str]]:
        return scan_text(src)

    # --- 阳性：INC-30 / INC-47 的真实两处 ---
    real1 = (
        'cur = conn.execute(\n'
        '    "UPDATE jobs SET state = \'succeeded\' WHERE id = ? AND state = \'running\' "\n'
        '    "AND (lease_token IS NULL OR lease_token = \'\' OR lease_token = ?)"\n'
        ')\n'
    )
    real2 = (
        'cur = conn.execute(\n'
        '    "UPDATE workflow_events SET delivery_status = \'delivered\' WHERE id = ? "\n'
        '    "AND (lease_token IS NULL OR lease_token = \'\' OR lease_token = ?)"\n'
        ')\n'
    )
    results.append(("ST-1 db.py:1676 形态（complete_job）被抓",
                    bool(run(real1)), "未抓到 complete_job 形态"))
    results.append(("ST-2 db.py:1791 形态（outbox ack）被抓",
                    bool(run(real2)), "未抓到 outbox ack 形态"))

    # --- 阳性：只有 IS NULL 分支也必须抓 ---
    half = ('"UPDATE x SET a=1 WHERE id=? AND (lease_token IS NULL OR lease_token = ?)"')
    results.append(("ST-3 去掉 `= ''` 的半通配也被抓",
                    bool(run(half)), "半通配未被抓"))

    # --- 阴性：这些是**正确**写法，绝不能误报 ---
    good_release = ("SET lease_token = NULL, lease_until = NULL "
                    "WHERE id = ? AND state = 'running'")
    good_strict = ('if row["lease_token"] and row["lease_token"] != lease_token:')
    good_exact = ('"WHERE id = ? AND lease_token = ?"')
    good_read = ('row = conn.execute("SELECT lease_token FROM jobs").fetchone()')
    for name, src in (
        ("ST-4 释放租约 SET lease_token = NULL 不误报", good_release),
        ("ST-5 严格比较不误报", good_strict),
        ("ST-6 精确匹配 lease_token = ? 不误报", good_exact),
        ("ST-7 只读取 lease_token 不误报", good_read),
    ):
        hit = bool(run(src))
        results.append((name, not hit, "正确写法被误报"))

    # --- 变异：把严格写法换成通配，判定必须翻面 ---
    mutated = ('"WHERE id = ? AND (lease_token IS NULL OR lease_token = ?)"')
    flipped = bool(run(mutated)) and not bool(run(good_strict))
    results.append(("ST-8 变异：严格→通配，判定跟着翻面",
                    flipped, "变异未改变判定"))

    passed = 0
    for name, ok, why in results:
        mark = "✓" if ok else "✗"
        if ok:
            passed += 1
        else:
            print(f"  {mark} {name} —— {why}")
    print(f"\n自检 {passed}/{len(results)}")
    if passed == len(results):
        print("自检通过")
        return 0
    print("自检失败")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
