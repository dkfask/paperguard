"""租约令牌的**运行时**防线（补 §22.4.1 登记的「租约无运行时防线」缺口）。

为什么需要这个文件
------------------
`test_backup_restore.py::test_durable_job_lease_retry_and_completion` 已经测了
租约的**顺利路径**（claim → fail → re-claim → complete），
`test_workflow_api.py` 也测了 outbox「不带令牌 ack ⇒ 409」。
所以「租约完全没有运行时测试」是**不准确**的陈述——本文件的前提。

但那两处都只覆盖「**没带令牌**」或「**用有效令牌**」，
**没有一处断言「用别人的令牌必须被拒」**。

实测（2026-10-03）：在干净克隆里把 `Store.complete_job` 的令牌校验
**整段删除**（等价于任何人可以完成任何人的任务），`backend/tests/` 全部
**798 passed / 2 skipped、exit=0** ⇒ 那个方向确实没有任何防线。

本文件把判据钉成**负向**的：**被他人持有时，错误令牌必须被拒，且状态不得变。**

刻意**不**覆盖的边界（如实登记）
------------------------------
`complete_job` / `mark_workflow_event_delivered_with_lease` 的 SQL 里都带着
`lease_token IS NULL OR lease_token = ''` 这个通配（INC-30 / INC-47）。
**当该行的 `lease_token` 本身为空时，通配会跳过校验。**
那是「任务当前没有被任何人租用」的状态——此时允许投递/完成
**可能是刻意设计**，属产品裁决，**本文件不断言其对错**。
"""
from __future__ import annotations

from app.db import Store, new_id


def _leased_job(store: Store) -> tuple[str, dict]:
    """建一个处于 `running`、并被某个 worker 真实持有的任务。"""
    job_id = new_id("job")
    with store.workflow_transaction() as conn:
        store.create_job_conn(
            conn, job_id=job_id, job_type="test.lease", resource_type="test",
            resource_id="resource-1", max_attempts=3,
        )
    claimed = store.claim_jobs(job_type="test.lease", consumer_id="worker-a",
                              lease_seconds=30)
    assert len(claimed) == 1, f"应当恰好领到 1 个任务，实际 {len(claimed)}"
    return job_id, claimed[0]


def test_complete_job_rejects_a_foreign_lease_token(tmp_path):
    """★ 正被 `worker-a` 持有时，伪造令牌不得完成任务（INC-30/47 的直接对偶）。"""
    store = Store(tmp_path / "lease.db")
    job_id, held = _leased_job(store)

    assert store.complete_job(job_id, lease_token="not-a-real-token") is None, \
        "伪造令牌被 complete_job 接受了——租约保护已失效"

    row = store.get_job(job_id)
    assert row is not None and row["state"] == "running", \
        f"被拒绝后状态却变成了 {(row or {}).get('state')!r}"
    assert row["lease_token"] == held["lease_token"], "持有者被换掉了"
    assert row["leased_by"] == held["leased_by"], "持有者被换掉了"

    # 真正的持有者仍然可以完成——**证明本测试不是「一律拒绝」**
    done = store.complete_job(job_id, lease_token=held["lease_token"])
    assert done and done["state"] == "succeeded", \
        "合法令牌完成失败——本测试把门禁锁死成「永远拒绝」了"


def test_fail_job_rejects_a_foreign_lease_token(tmp_path):
    """`fail_job` 同样不得被伪造令牌驱动，否则可强制重排他人的任务。"""
    store = Store(tmp_path / "lease.db")
    job_id, held = _leased_job(store)

    assert store.fail_job(job_id, error="forged",
                          lease_token="not-a-real-token") is None, \
        "伪造令牌驱动了 fail_job"

    row = store.get_job(job_id)
    assert row["state"] == "running", \
        f"被拒绝后状态却变成了 {row['state']!r}"

    # 真正的持有者仍可标记失败
    failed = store.fail_job(job_id, error="real",
                            lease_token=held["lease_token"])
    assert failed and failed["state"] == "queued", "合法令牌 fail 失败"


def test_lease_token_is_not_accepted_after_the_task_left_running(tmp_path):
    """任务被重新领取后，**旧的**令牌必须失效（否则旧 worker 能覆盖新 worker）。

    这是长任务重复执行/丢失的典型形态，也是 §22.4 第 4 条关心的问题。
    """
    store = Store(tmp_path / "lease.db")
    job_id, first = _leased_job(store)

    failed = store.fail_job(job_id, error="retry me",
                            lease_token=first["lease_token"])
    assert failed and failed["state"] == "queued"

    reclaimed = store.claim_jobs(job_type="test.lease", consumer_id="worker-b",
                                 lease_seconds=30)
    assert len(reclaimed) == 1 and reclaimed[0]["id"] == job_id
    assert reclaimed[0]["lease_token"] != first["lease_token"], \
        "重新领取后令牌竟然没变——本测试的后半段因此没有意义"

    assert store.complete_job(job_id, lease_token=first["lease_token"]) is None, \
        "**旧**令牌在新一轮租约里仍然有效——旧 worker 能覆盖新 worker"

    done = store.complete_job(job_id, lease_token=reclaimed[0]["lease_token"])
    assert done and done["state"] == "succeeded"
