#!/usr/bin/env python3
"""jobs 租约与重试机制直测（阶段 5，零覆盖域 #2）

覆盖 backend/app/db.py:1618-1729 claim_jobs / complete_job / fail_job，
以及 services/workflow_jobs.py 的失败重试策略。
该层此前无任何测试（既有用例只从 HTTP 入口间接触发）。

隔离：使用 /tmp 下独立临时库，不触碰 E2E 库与生产库。
用法：.venv/bin/python qa_jobs_lease.py
退出码：0 全通过 / 1 有失败
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "backend"))

tmp_dir = tempfile.mkdtemp(prefix="pg-jobs-")
os.environ["PAPERGUARD_DB_PATH"] = os.path.join(tmp_dir, "jobs.db")
os.environ.setdefault("PAPERGUARD_SECRET_KEY", "qa-jobs-lease-only-not-a-real-secret")

from app.db import Store  # noqa: E402
from app.services.workflow_jobs import consume_archive_jobs  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASSED if ok else FAILED).append(name) if ok else FAILED.append((name, detail))
    if ok:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name} — {detail}")
    return ok


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def now_text(offset_seconds: float = 0.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


def fresh_store() -> Store:
    return Store()


def enqueue(store: Store, job_id: str, *, job_type: str = "archive.generate",
            max_attempts: int = 5, available_at: str = "") -> str:
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, job_type, resource_type, resource_id, state, attempt_count, "
            "max_attempts, available_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, job_type, "archive", f"archive-{job_id}", "queued", 0, max_attempts,
             available_at or now_text(), now_text(), now_text()),
        )
        conn.commit()
    return job_id


def read_job(store: Store, job_id: str) -> dict:
    with store._connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else {}


print("临时库 =", os.environ["PAPERGUARD_DB_PATH"])

# --------------------------------------------------------------------------
section("J1 并发抢占原子性：两个 consumer 抢同一条 job，只能有一个拿到")
# --------------------------------------------------------------------------
s1 = fresh_store()
jid = enqueue(s1, "job-j1")
results: list[list[dict]] = []
barrier = threading.Barrier(2)


def grab(store: Store, tag: str) -> None:
    barrier.wait()
    results.append(store.claim_jobs(job_type="archive.generate", consumer_id=tag))


t1 = threading.Thread(target=grab, args=(s1, "consumer-1"))
t2 = threading.Thread(target=grab, args=(s1, "consumer-2"))
t1.start(); t2.start(); t1.join(); t2.join()

total_claimed = sum(len(r) for r in results)
check("J1 只有一个 consumer 抢到该 job（不得重复消费）",
      total_claimed == 1,
      f"两个 consumer 合计抢到 {total_claimed} 次（>1 即为重复消费：归档会重复生成 zip）")

# --------------------------------------------------------------------------
section("J2 租约过期可被重新抢占，未过期不可")
# --------------------------------------------------------------------------
s2 = fresh_store()
jid2 = enqueue(s2, "job-j2")
first = s2.claim_jobs(job_type="archive.generate", consumer_id="c-a", lease_seconds=60)
check("J2 首次抢占成功", len(first) == 1 and first[0]["id"] == jid2, f"{first}")

blocked = s2.claim_jobs(job_type="archive.generate", consumer_id="c-b", lease_seconds=60)
check("J2 租约未过期时他人抢不到", blocked == [], f"他人抢到 {blocked}")

# 把 lease_until 改到过去，模拟租约过期（等价 worker 崩溃）
with s2._connect() as conn:
    conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", (now_text(-1), jid2))
    conn.commit()
stolen = s2.claim_jobs(job_type="archive.generate", consumer_id="c-b", lease_seconds=60)
check("J2 租约过期后可被重新抢占（崩溃恢复）", len(stolen) == 1 and stolen[0]["id"] == jid2,
      f"未抢到：{stolen}")
check("J2 抢占后 attempt_count 递增", int(stolen[0]["attempt_count"]) == 2 if stolen else False,
      f"attempt_count={stolen[0]['attempt_count'] if stolen else 'N/A'}")

# --------------------------------------------------------------------------
section("J3 租约令牌防止过期 worker 回写（原 worker 静默失败）")
# --------------------------------------------------------------------------
s3 = fresh_store()
jid3 = enqueue(s3, "job-j3")
c1 = s3.claim_jobs(job_type="archive.generate", consumer_id="c-old", lease_seconds=60)
old_token = c1[0]["lease_token"]
with s3._connect() as conn:
    conn.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", (now_text(-1), jid3))
    conn.commit()
c2 = s3.claim_jobs(job_type="archive.generate", consumer_id="c-new", lease_seconds=60)
new_token = c2[0]["lease_token"] if c2 else ""

stale_result = s3.complete_job(jid3, lease_token=old_token)
check("J3 过期 worker 用旧令牌 complete 被拒（防重复回写）", stale_result is None,
      f"返回 {stale_result}（非 None 说明旧 worker 仍能覆盖结果）")

fresh_result = s3.complete_job(jid3, lease_token=new_token)
check("J3 新租约持有者 complete 成功", fresh_result is not None and fresh_result["state"] == "succeeded",
      f"{fresh_result}")

# --------------------------------------------------------------------------
section("J4 失败重试：未达 max_attempts 回到 queued，达到后停放 failed")
# --------------------------------------------------------------------------
s4 = fresh_store()
jid4 = enqueue(s4, "job-j4", max_attempts=3)
states = []
for i in range(4):
    claimed = s4.claim_jobs(job_type="archive.generate", consumer_id=f"c{i}", lease_seconds=60)
    if not claimed:
        states.append(("claim-miss", ""))
        break
    out = s4.fail_job(jid4, error=f"boom-{i}", lease_token=claimed[0]["lease_token"],
                      retry_delay_seconds=0)
    states.append((out["state"] if out else "None", f"attempt={out['attempt_count'] if out else 'N/A'}"))
check("J4 重试次数到达 max_attempts=3 后停放为 failed",
      [x[0] for x in states[:3]] == ["queued", "queued", "failed"],
      f"状态序列={states}")

final = read_job(s4, jid4)
check("J4 停放后不再被 claim 抢到", s4.claim_jobs(job_type="archive.generate", consumer_id="c-x") == [],
      f"停放记录仍可被抢：{read_job(s4, jid4)['state']}")
check("J4 失败原因已持久化", "boom-2" in (final.get("last_error") or ""), f"last_error={final.get('last_error')}")

# --------------------------------------------------------------------------
section("J5 重试退避策略（R5 修复后：应改为指数退避）")
# --------------------------------------------------------------------------
src_path = os.path.join(REPO, "backend", "app", "services", "workflow_jobs.py")
with open(src_path, encoding="utf-8") as fh:
    src = fh.read()
delays = set()
for line in src.splitlines():
    if "retry_delay_seconds=" in line and "def " not in line:
        delays.add(line.strip())
print("  源码中 retry_delay_seconds 传参：")
for d in sorted(delays):
    print(f"    {d}")

# 修复后：所有调用点都应走 _retry_delay_seconds(...)，不得再有字面量常量
literals = {d for d in delays if "retry_delay_seconds=_retry" not in d}
check("J5 退避不再使用字面量常量（全部走 _retry_delay_seconds）",
      not literals,
      f"仍有字面量传参：{literals}")

from app.services.workflow_jobs import _retry_delay_seconds  # noqa: E402

seq = [_retry_delay_seconds(i) for i in range(1, 6)]
print(f"  退避序列 attempt 1..5 = {seq}")
check("J5 退避为指数递增（5/10/20/40/80）", seq == [5, 10, 20, 40, 80], f"实际 {seq}")
check("J5 退避有上限封顶", _retry_delay_seconds(12) == 300 and _retry_delay_seconds(50) == 300,
      f"attempt12={_retry_delay_seconds(12)} attempt50={_retry_delay_seconds(50)}")
check("J5 非法入参安全回落到首次退避",
      _retry_delay_seconds(None) == 5 and _retry_delay_seconds("x") == 5 and _retry_delay_seconds(0) == 5,
      f"None={_retry_delay_seconds(None)} 'x'={_retry_delay_seconds('x')} 0={_retry_delay_seconds(0)}")

# --------------------------------------------------------------------------
section("J6 归档记录缺失时的重试延迟（BUG-03 修复后：应退避而非立即重排队）")
# --------------------------------------------------------------------------
s6 = fresh_store()
jid6 = enqueue(s6, "job-j6", max_attempts=5)
# 关联的 archive 记录不存在 —— consume_archive_jobs 走 fail_job 分支
res = consume_archive_jobs(s6, consumer_id="qa-consumer", limit=5)
job6 = read_job(s6, jid6)
print(f"  消费结果 = {res}")
print(f"  job 状态 = {job6.get('state')}, available_at = {job6.get('available_at')}, now = {now_text()}")
check("J6 记录缺失时 job 未被误判成功", job6.get("state") in {"queued", "failed"},
      f"state={job6.get('state')}")
immediate = job6.get("available_at", "") <= now_text(1)
check("J6 记录缺失时不再零延迟重排队（应有退避，消除热循环）",
      not immediate,
      f"available_at={job6.get('available_at')} 仍不晚于 {now_text(1)}，热循环未消除")

# --------------------------------------------------------------------------
print(f"\n临时库保留供核查：{os.environ['PAPERGUARD_DB_PATH']}")
print(f"\n通过 {len(PASSED)} / 失败 {len(FAILED)}")
if FAILED:
    print("\n失败明细：")
    for name, detail in FAILED:
        print(f"  - {name}\n      {detail}")
    sys.exit(1)
print("全部通过")
