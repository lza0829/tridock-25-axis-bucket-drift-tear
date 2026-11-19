"""比较轴口径核对（两架构分别验证）：

1. 三腿互不相同时，两轴分叉出不同主差额集合；
2. 旁证（confirm 偏离 / expect_drift）不进主差额桶，两桶不混；
3. 缺腿不当 0 静默对齐：数量存 NULL、has_* 置 0、进主差额带标记；
4. 结案写闸只数主差额未决，旁证桶不挡结案；
5. 已 sealed 批的 axis 与分桶集合不被事后轴切换撬动，快照 axis 可回溯；
6. 并发两路对账不对同一 dock_day 落下两套互相矛盾的 open 主差额。

可直接运行：python -m tests.test_reconcile_axis
也可用 pytest 收集（测试函数均为 test_*）。
"""
from __future__ import annotations
import sqlite3
import threading
import tempfile
from pathlib import Path

from app.seed import ensure_schema
from app.services import reconcile_service as svc
from app.services.axis import EXPECT_RECEIVE, RECEIVE_CONFIRM, set_axis

DAY = "2026-11-19"
SUP = "SUP-T"

# 三腿夹具：sku 后缀 -> (expect, receive, confirm)，None = 缺腿
FIXTURE = {
    "ALIGN":   (10, 10, 10),   # 三腿一致
    "ESHORT":  (10, 8, 8),     # 预报≠实收，回执跟实收
    "CDEV":    (10, 10, 7),    # 仅回执偏离
    "ALLDIFF": (10, 8, 6),     # 三腿互不相同
    "ONLYR":   (None, 5, None),
    "ONLYC":   (None, None, 5),
    "ONLYE":   (5, None, None),
}

MAIN_A = {"ESHORT", "ALLDIFF", "ONLYR", "ONLYC", "ONLYE"}   # expect≠receive 或轴腿缺失
MAIN_B = {"CDEV", "ALLDIFF", "ONLYR", "ONLYC", "ONLYE"}     # receive≠confirm 或轴腿缺失
ALIGNED_A = {"ALIGN", "CDEV"}                                # expect==receive
ALIGNED_B = {"ALIGN", "ESHORT"}                              # receive==confirm
DRIFT_B = {"ESHORT", "ALLDIFF"}                              # expect 对实收漂移 -> expect_drift 旁证桶

def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    return conn

def make_db(tmp: str) -> Path:
    path = Path(tmp) / "t.db"
    conn = _connect(path)
    ensure_schema(conn)
    conn.close()
    return path

def load_fixture(path: Path, day: str = DAY, fixture: dict | None = None) -> None:
    conn = _connect(path)
    n = 0
    for sku, (e, r, c) in (fixture or FIXTURE).items():
        n += 1
        tag = f"{sku}-{day}-{n}"
        if e is not None:
            conn.execute(
                "INSERT INTO expect_lines(doc_no,supplier_code,sku,dock_day,qty,note,created_at) VALUES (?,?,?,?,?,NULL,'t')",
                (f"EX-{tag}", SUP, sku, day, e),
            )
        if r is not None:
            conn.execute(
                "INSERT INTO receive_lines(receive_no,supplier_code,sku,dock_day,qty,occurred_at,note,created_at) VALUES (?,?,?,?,?,'t',NULL,'t')",
                (f"RC-{tag}", SUP, sku, day, r),
            )
        if c is not None:
            conn.execute(
                "INSERT INTO confirm_lines(confirm_no,supplier_code,sku,dock_day,qty,note,created_at) VALUES (?,?,?,?,?,NULL,'t')",
                (f"CF-{tag}", SUP, sku, day, c),
            )
    conn.commit()
    conn.close()

def run(path: Path, day: str = DAY) -> dict:
    conn = _connect(path)
    try:
        return svc.run_reconcile(conn, day)
    finally:
        conn.close()

def switch_axis(path: Path, axis: str) -> None:
    conn = _connect(path)
    set_axis(conn, axis)
    conn.commit()
    conn.close()

def rows(path: Path, sql: str, params: tuple = ()) -> list[dict]:
    conn = _connect(path)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

def main_skus(path: Path, batch_id: int) -> set:
    return {r["sku"] for r in rows(path, "SELECT * FROM variance_rows WHERE batch_id=? AND bucket='main'", (batch_id,))}

def bucket_skus(path: Path, batch_id: int, bucket: str) -> set:
    return {r["sku"] for r in rows(path, "SELECT * FROM variance_rows WHERE batch_id=? AND bucket=?", (batch_id, bucket))}

def aligned_skus(path: Path, batch_id: int) -> set:
    return {r["sku"] for r in rows(path, "SELECT * FROM match_rows WHERE batch_id=? AND aligned=1", (batch_id,))}

def expect_raises(fn, needle: str) -> None:
    try:
        fn()
    except ValueError as e:
        assert needle in str(e), f"异常信息不含 {needle!r}: {e}"
        return
    raise AssertionError(f"应抛出含 {needle!r} 的 ValueError")

# ---------- 1. 两架构分桶/匹配口径 ----------

def test_axis_a_expect_receive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        load_fixture(path)
        res = run(path)  # 默认轴 expect_receive
        bid = res["batch_id"]
        assert res["axis"] == EXPECT_RECEIVE
        assert main_skus(path, bid) == MAIN_A, main_skus(path, bid)
        assert aligned_skus(path, bid) == ALIGNED_A
        # 仅回执偏离（CDEV）不开主差额：confirm 只作旁证
        assert "CDEV" not in main_skus(path, bid)
        # expect_receive 轴不开旁证桶
        assert res["sidecar_count"] == 0
        assert rows(path, "SELECT * FROM variance_rows WHERE batch_id=? AND bucket<>'main'", (bid,)) == []
        # 旁证写进快照旁路字段 side_dev = confirm - receive
        dev = {r["sku"]: r["side_dev"] for r in rows(path, "SELECT * FROM match_rows WHERE batch_id=?", (bid,))}
        assert dev["CDEV"] == -3 and dev["ESHORT"] == 0 and dev["ALLDIFF"] == -2
        assert dev["ALIGN"] == 0
        # 批次快照带 axis
        batch = rows(path, "SELECT * FROM reconcile_batches WHERE id=?", (bid,))[0]
        assert batch["axis"] == EXPECT_RECEIVE
        assert batch["variance_count"] == len(MAIN_A)
        assert batch["matched_count"] == len(ALIGNED_A)

def test_axis_b_receive_confirm() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        load_fixture(path)
        switch_axis(path, RECEIVE_CONFIRM)
        res = run(path)
        bid = res["batch_id"]
        assert res["axis"] == RECEIVE_CONFIRM
        assert main_skus(path, bid) == MAIN_B, main_skus(path, bid)
        assert aligned_skus(path, bid) == ALIGNED_B
        # expect 偏离单独进 expect_drift 旁证桶，不与主差额混桶
        assert bucket_skus(path, bid, "expect_drift") == DRIFT_B
        assert res["sidecar_count"] == len(DRIFT_B)
        drift_rows = rows(path, "SELECT * FROM variance_rows WHERE batch_id=? AND bucket='expect_drift'", (bid,))
        assert all(r["fate"] == "info" for r in drift_rows)
        # 两桶分离：主桶查询不带旁证，旁证查询不带主桶
        assert not (main_skus(path, bid) & {"ESHORT"})  # ESHORT 只在旁证桶
        assert "ESHORT" in bucket_skus(path, bid, "expect_drift")
        # ALLDIFF 同时有主差额（receive≠confirm）与旁证漂移，但分两行两桶
        assert "ALLDIFF" in main_skus(path, bid) and "ALLDIFF" in bucket_skus(path, bid, "expect_drift")
        kinds = {(r["sku"], r["bucket"]) for r in rows(path, "SELECT * FROM variance_rows WHERE batch_id=?", (bid,))}
        assert ("ALLDIFF", "main") in kinds and ("ALLDIFF", "expect_drift") in kinds
        # 旁证偏差字段：expect - receive
        dev = {r["sku"]: r["side_dev"] for r in rows(path, "SELECT * FROM match_rows WHERE batch_id=?", (bid,))}
        assert dev["ESHORT"] == 2 and dev["ALLDIFF"] == 2 and dev["CDEV"] == 0

def test_axes_diverge_on_three_way_mismatch() -> None:
    """同一组数据，两轴必须分叉出不同主差额集合。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        load_fixture(path)
        a = run(path)
        main_a = main_skus(path, a["batch_id"])
        switch_axis(path, RECEIVE_CONFIRM)
        b = run(path)
        main_b = main_skus(path, b["batch_id"])
        assert main_a != main_b
        assert "ESHORT" in main_a and "ESHORT" not in main_b
        assert "CDEV" in main_b and "CDEV" not in main_a
        # 重跑替换旧 open 批：同一 dock_day 任意时刻只有一套 open 口径
        open_batches = rows(path, "SELECT * FROM reconcile_batches WHERE dock_day=? AND status='open'", (DAY,))
        assert len(open_batches) == 1 and open_batches[0]["id"] == b["batch_id"]

# ---------- 2. 缺腿标记 ----------

def test_missing_legs_flagged_not_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        load_fixture(path)
        res = run(path)
        bid = res["batch_id"]
        m = {r["sku"]: r for r in rows(path, "SELECT * FROM match_rows WHERE batch_id=?", (bid,))}
        # 缺腿数量存 NULL 而非 0，且 has_* 置 0
        assert m["ONLYR"]["expect_qty"] is None and m["ONLYR"]["has_expect"] == 0
        assert m["ONLYR"]["confirm_qty"] is None and m["ONLYR"]["has_confirm"] == 0
        assert m["ONLYR"]["receive_qty"] == 5 and m["ONLYR"]["has_receive"] == 1
        # 缺腿键不被静默吞掉：进主差额并带缺腿标记
        v = {r["sku"]: r for r in rows(path, "SELECT * FROM variance_rows WHERE batch_id=? AND bucket='main'", (bid,))}
        assert v["ONLYC"]["has_expect"] == 0 and v["ONLYC"]["has_receive"] == 0
        assert v["ONLYC"]["expect_qty"] is None and v["ONLYC"]["receive_qty"] is None
        assert v["ONLYE"]["has_receive"] == 0 and v["ONLYE"]["receive_qty"] is None
        # 缺腿键绝不算匹配成功（缺≠0，0==0 的假对齐不存在）
        for sku in ("ONLYR", "ONLYC", "ONLYE"):
            assert m[sku]["aligned"] == 0

# ---------- 3. 结案写闸与轴联动 ----------

def test_seal_gate_counts_only_main_bucket() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        # receive_confirm 轴下只有 expect 漂移：旁证桶不挡结案
        load_fixture(path, fixture={"ESHORT": (10, 8, 8)})
        switch_axis(path, RECEIVE_CONFIRM)
        res = run(path)
        assert res["variance_count"] == 0 and res["sidecar_count"] == 1
        conn = _connect(path)
        sealed = svc.seal_day(conn, DAY)
        conn.close()
        assert sealed["status"] == "sealed" and sealed["axis"] == RECEIVE_CONFIRM

def test_seal_blocked_by_pending_main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        load_fixture(path, fixture={"ESHORT": (10, 8, 8)})  # 默认轴：1 条主差额
        res = run(path)
        assert res["variance_count"] == 1
        conn = _connect(path)
        expect_raises(lambda: svc.seal_day(conn, DAY), "主差额未处置")
        vid = rows(path, "SELECT * FROM variance_rows WHERE batch_id=? AND bucket='main'", (res["batch_id"],))[0]["id"]
        svc.set_variance_fate(conn, vid, "accept_short")
        sealed = svc.seal_day(conn, DAY)
        conn.close()
        assert sealed["status"] == "sealed"

# ---------- 4. sealed 批不可被轴切换撬动 / 快照可回溯 ----------

def test_sealed_batch_immune_to_axis_switch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        load_fixture(path)
        res = run(path)  # expect_receive
        bid = res["batch_id"]
        for r in rows(path, "SELECT * FROM variance_rows WHERE batch_id=? AND bucket='main'", (bid,)):
            conn = _connect(path)
            svc.set_variance_fate(conn, r["id"], "accept_short")
            conn.close()
        conn = _connect(path)
        svc.seal_day(conn, DAY)
        conn.close()
        before_batch = rows(path, "SELECT * FROM reconcile_batches WHERE id=?", (bid,))[0]
        before_main = main_skus(path, bid)
        before_aligned = aligned_skus(path, bid)

        switch_axis(path, RECEIVE_CONFIRM)  # 事后切换全局轴
        # 已结案日不能重跑
        expect_raises(lambda: run(path), "已结案")
        # 已结案批不可改命运
        vid = rows(path, "SELECT * FROM variance_rows WHERE batch_id=? AND bucket='main'", (bid,))[0]["id"]
        conn = _connect(path)
        expect_raises(lambda: svc.set_variance_fate(conn, vid, "quarantine"), "已结案")
        conn.close()
        # 快照 axis 与分桶集合原样可回溯
        after_batch = rows(path, "SELECT * FROM reconcile_batches WHERE id=?", (bid,))[0]
        assert after_batch == before_batch
        assert after_batch["axis"] == EXPECT_RECEIVE
        assert main_skus(path, bid) == before_main
        assert aligned_skus(path, bid) == before_aligned
        # 新轴只影响新批次：另一天跑出 receive_confirm 口径，老批不受牵连
        load_fixture(path, day="2026-11-20")
        res2 = run(path, day="2026-11-20")
        assert res2["axis"] == RECEIVE_CONFIRM
        assert rows(path, "SELECT * FROM reconcile_batches WHERE id=?", (bid,))[0]["axis"] == EXPECT_RECEIVE

# ---------- 5. 旁证桶不可处置 ----------

def test_sidecar_rows_reject_fate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        load_fixture(path, fixture={"ESHORT": (10, 8, 8)})
        switch_axis(path, RECEIVE_CONFIRM)
        res = run(path)
        drift = rows(path, "SELECT * FROM variance_rows WHERE batch_id=? AND bucket='expect_drift'", (res["batch_id"],))[0]
        conn = _connect(path)
        expect_raises(lambda: svc.set_variance_fate(conn, drift["id"], "accept_short"), "旁证桶")
        conn.close()

# ---------- 6. 并发两路对账：同一 dock_day 只有一套 open 主差额 ----------

def test_concurrent_runs_leave_single_open_batch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        load_fixture(path)
        barrier = threading.Barrier(4)
        errors: list[BaseException] = []

        def worker() -> None:
            conn = _connect(path)
            try:
                barrier.wait(timeout=10)
                svc.run_reconcile(conn, DAY)
            except BaseException as e:  # noqa: BLE001
                errors.append(e)
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, errors
        open_batches = rows(path, "SELECT * FROM reconcile_batches WHERE dock_day=? AND status='open'", (DAY,))
        assert len(open_batches) == 1, open_batches
        survivor = open_batches[0]["id"]
        # 不留孤儿行：所有匹配/差额行都挂在唯一幸存 open 批上
        orphans_m = rows(path, "SELECT * FROM match_rows WHERE batch_id<>?", (survivor,))
        orphans_v = rows(path, "SELECT * FROM variance_rows WHERE batch_id<>?", (survivor,))
        assert orphans_m == [] and orphans_v == []
        # 幸存批口径自洽：主差额集合 == 该轴应得集合
        assert main_skus(path, survivor) == MAIN_A
        batch = rows(path, "SELECT * FROM reconcile_batches WHERE id=?", (survivor,))[0]
        assert batch["variance_count"] == len(MAIN_A)

def test_rerun_replaces_open_batch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = make_db(tmp)
        load_fixture(path)
        first = run(path)
        second = run(path)
        assert first["batch_no"] != second["batch_no"]
        open_batches = rows(path, "SELECT * FROM reconcile_batches WHERE dock_day=? AND status='open'", (DAY,))
        assert len(open_batches) == 1 and open_batches[0]["id"] == second["batch_id"]
        assert rows(path, "SELECT * FROM variance_rows WHERE batch_id=?", (first["batch_id"],)) == []

ALL_TESTS = [
    test_axis_a_expect_receive,
    test_axis_b_receive_confirm,
    test_axes_diverge_on_three_way_mismatch,
    test_missing_legs_flagged_not_zero,
    test_seal_gate_counts_only_main_bucket,
    test_seal_blocked_by_pending_main,
    test_sealed_batch_immune_to_axis_switch,
    test_sidecar_rows_reject_fate,
    test_concurrent_runs_leave_single_open_batch,
    test_rerun_replaces_open_batch,
]

def main() -> None:
    for fn in ALL_TESTS:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(ALL_TESTS)} 项核对全部通过")

if __name__ == "__main__":
    main()
