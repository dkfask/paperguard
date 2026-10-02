"""租约令牌的**运行时**防线（补 §22.4.1 登记的「租约无运行时防线」缺口）。

为什么需要这个文件
------------------
`test_backup_restore.py::test_durable_job_lease_retry_and_completion` 已经测了
租约的**顺利路径**（claim → fail → re-claim → complete），
`test_workflow_api.py` 也测了 outbox「不带令牌 ack ⇒ 409」。
所以「租约完全没有运行时测试」是**不准确**的陈述——本文件的前提。

但那两处都只覆盖「**没带令牌**」或「**用有效令牌**」，
**没有一处断言「用别人的令牌必须被拒」**。

实测（2026-10-03，INC-95 当时）：在干净克隆里把 `Store.complete_job` 的令牌校验
**整段删除**（等价于任何人可以完成任何人的任务），`backend/tests/` 全部
**798 passed / 2 skipped、exit=0** ⇒ 那个方向确实没有任何防线。
（**该数字是那一刻的实测值，不复述**——本文件此后新增了用例，套件总数已变，
现值以 `--self-test` 之外的 `pytest -q` 实跑为准。）

本文件把判据钉成**负向**的：**被他人持有时，错误令牌必须被拒，且状态不得变。**

已由规格裁决并实现（2026-10-03，INC-105）
------------------------------------
本文件首版把「该行 `lease_token` 为空时是否放行」登记为**产品裁决**、不断言其对错。
**这个保留是多余的**：W-09 **LE-6 早已明文禁止**此通配，并写明取舍——
「旧代码的 NULL 分支不是为『未领取』设计的，是**为了省事**，
  代价是任何人都能提交别人的任务。**便利性不应由安全性买单。**」
**当时没读到那条规则。** 于是本轮把它当规格冲突处理：实现照 LE-6 修，
并补上最后两项测试。

两处通配**性质不同**（实测分离，不是推断）
------------------------------------------
· `complete_job` 的条件含 `state = 'running'`，而 `claim_jobs` 置 running 时
  **必定**写入随机 token ⇒ 该态**正常路径不可达**，通配实为死代码。
· `mark_workflow_event_delivered_with_lease` 的条件只有
  `delivery_status != 'delivered'`，**不含「已被领取」**
  ⇒ 任何 `pending` 事件都能被无令牌 ack 成 `delivered`，**这是真实的越权面**。

**「不可达」不是「安全」**：第一处虽正常路径造不出，迁移脚本/历史数据/手工修复
都可能进入该状态，判据必须自己站得住。
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


def _pending_event(store: Store, *, dedupe_key: str) -> str:
    """造一条 `delivery_status='pending'`、**从未被领取**的 outbox 事件。"""
    with store.workflow_transaction() as conn:
        assert store.save_workflow_event_conn(
            conn, event_type="test.lease", recipient_id="user-1",
            resource_type="test", resource_id="resource-1",
            dedupe_key=dedupe_key, data={"x": 1},
        )
        row = conn.execute(
            "SELECT id FROM workflow_events WHERE dedupe_key = ?", (dedupe_key,)
        ).fetchone()
    return row["id"]


def test_a_never_claimed_event_cannot_be_acked_without_a_lease(tmp_path):
    """★ 从未被领取的 outbox 事件**不得**被无令牌 ack 成 `delivered`（W-09 LE-6）。

    这是本轮实测抓到的**真实越权面**（INC-105），与
    `test_complete_job_rejects_a_foreign_lease_token` 覆盖的状态**不同**：

    · `complete_job` 的条件是 `state = 'running'`，而 `claim_jobs` 在置 running
      的同时**必定**写入一个随机 token ⇒「running 且无租约」正常路径不可达。
    · `mark_workflow_event_delivered_with_lease` 的条件只有
      `delivery_status != 'delivered'` —— **不含「已被领取」**，
      所以任何 `pending` 的事件都落进通配分支。

    实测（修前）：`mark_workflow_event_delivered(event_id)` 返回 `True`，
    该行被改成 `delivery_status='delivered'`、`lease_token=None`。

    规格 W-09 LE-6 已明文禁止此通配，并写明取舍：
    「旧代码的 NULL 分支不是为『未领取』设计的，是**为了省事**，
      代价是任何人都能提交别人的任务。**便利性不应由安全性买单。**」
    """
    store = Store(tmp_path / "lease.db")
    event_id = _pending_event(store, dedupe_key="dk-unclaimed")

    assert store.mark_workflow_event_delivered(event_id) is False, \
        "从未被领取的事件被无令牌 ack 了——W-09 LE-6 的通配分支仍在生效"

    row = next((e for e in store.list_workflow_events("user-1")
                if e["id"] == event_id), None)
    assert row is not None, "事件行不见了（ack 失败不应删行）"
    assert row["delivery_status"] == "pending", \
        f"被拒绝后状态却变成了 {row['delivery_status']!r}"

    # **证明本测试不是「一律拒绝」**：合法领取后，持令牌者必须仍能 ack。
    claimed = store.claim_workflow_events([event_id], consumer_id="worker-a")
    assert len(claimed) == 1 and claimed[0]["lease_token"], "未能合法领取"
    assert store.mark_workflow_event_delivered_with_lease(
        event_id, lease_token=claimed[0]["lease_token"]) is True, \
        "合法令牌 ack 失败——本测试把门禁锁死成「永远拒绝」了"


def test_a_running_job_without_a_lease_is_not_completable(tmp_path):
    """`complete_job` 侧的同一条规则（W-09 LE-6），覆盖**不可达也要钉住**的状态。

    为什么连不可达的状态也要测：该状态正常路径造不出来，
    于是它**只能靠直接写库进入**——而一旦真发生（迁移脚本、历史数据、
    手工修复），通配分支就会放行。**「不可达」不是「安全」**，
    判据必须自己站得住，否则它是靠外部假设支撑的。
    """
    store = Store(tmp_path / "lease.db")
    job_id = new_id("job")
    with store.workflow_transaction() as conn:
        store.create_job_conn(
            conn, job_id=job_id, job_type="test.lease", resource_type="test",
            resource_id="resource-1", max_attempts=3,
        )
        # 直接把状态推成 running 但**不写 lease_token**——正常路径造不出的组合
        conn.execute(
            "UPDATE jobs SET state = 'running', lease_until = NULL, lease_token = NULL "
            "WHERE id = ?", (job_id,))

    assert store.complete_job(job_id) is None, \
        "running 且无租约的任务被无令牌完成了——W-09 LE-6 的通配分支仍在生效"
    row = store.get_job(job_id)
    assert row["state"] == "running", f"被拒绝后状态却变成了 {row['state']!r}"
