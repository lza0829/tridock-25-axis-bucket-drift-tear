from app.services import bucket_drift
from __future__ import annotations
from datetime import datetime, timezone
import sqlite3

from app.services.axis import AXIS_DEFS, AXIS_LABELS, MAIN_BUCKET, SIDE_DEV_BASE, get_axis
from app.services.gate import begin_write

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _sum_map(conn: sqlite3.Connection, table: str, dock_day: str) -> dict[tuple[str, str], float]:
    rows = conn.execute(
        f"SELECT supplier_code, sku, SUM(qty) q FROM {table} WHERE dock_day=? GROUP BY supplier_code, sku",
        (dock_day,),
    ).fetchall()
    return {(r["supplier_code"], r["sku"]): float(r["q"]) for r in rows}

def run_reconcile(conn: sqlite3.Connection, dock_day: str) -> dict:
    """按当前设置的比较轴跑三联对账，批次快照落库当时轴。

    口径（两架构共用骨架，由 AXIS_DEFS 分叉）：
    - 匹配成功 = 比较轴两腿都在场且数量相等；轴腿缺失或不等 -> 主差额（bucket='main'）。
    - 旁证腿只写快照旁路字段 side_dev（旁证腿-实收）；receive_confirm 轴下
      expect 漂移另进 expect_drift 旁证桶，绝不与主差额混桶。
    - 缺腿数量存 NULL 且 has_* 置 0，不当 0 静默对齐。
    """
    begin_write(conn)
    try:
        sealed = conn.execute(
            "SELECT 1 FROM reconcile_batches WHERE dock_day=? AND status='sealed'",
            (dock_day,),
        ).fetchone()
        if sealed:
            raise ValueError(f"到货日 {dock_day} 已结案，不能重跑对账")

        # 替换同一 dock_day 的旧 open 批（同事务内删旧建新，配合部分唯一索引，
        # 并发两路对账也不会落下两套互相矛盾的 open 主差额）
        old = conn.execute(
            "SELECT id FROM reconcile_batches WHERE dock_day=? AND status='open'",
            (dock_day,),
        ).fetchall()
        for r in old:
            conn.execute("DELETE FROM match_rows WHERE batch_id=?", (r["id"],))
            conn.execute("DELETE FROM variance_rows WHERE batch_id=?", (r["id"],))
            conn.execute("DELETE FROM reconcile_batches WHERE id=?", (r["id"],))

        axis = get_axis(conn)
        spec = AXIS_DEFS[axis]
        leg_a, leg_b = spec["legs"]
        off_leg = spec["off"]
        drift_bucket = spec["drift_bucket"]

        expect_m = _sum_map(conn, "expect_lines", dock_day)
        receive_m = _sum_map(conn, "receive_lines", dock_day)
        confirm_m = _sum_map(conn, "confirm_lines", dock_day)
        keys = set(expect_m) | set(receive_m) | set(confirm_m)

        # 批次号全局单调（不被删旧批回退），保证快照可回溯
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('batch_seq', '0') ON CONFLICT(key) DO NOTHING"
        )
        seq = int(conn.execute("SELECT value FROM settings WHERE key='batch_seq'").fetchone()["value"]) + 1
        conn.execute("UPDATE settings SET value=? WHERE key='batch_seq'", (str(seq),))
        batch_no = f"RB-{dock_day.replace('-', '')}-{seq:03d}"
        cur = conn.execute(
            '''INSERT INTO reconcile_batches(batch_no, dock_day, axis, matched_count, variance_count, sidecar_count, status, sealed_at, note)
               VALUES (?,?,?,0,0,0,'open',NULL,?)''',
            (batch_no, dock_day, axis, f"三联对齐·{AXIS_LABELS[axis]}"),
        )
        batch_id = int(cur.lastrowid)

        matched = 0
        variance = 0
        sidecar = 0
        now = _now()
        for supplier_code, sku in sorted(keys):
            k = (supplier_code, sku)
            qty = {
                "expect": expect_m.get(k),
                "receive": receive_m.get(k),
                "confirm": confirm_m.get(k),
            }
            has = {leg: qty[leg] is not None for leg in qty}
            # 匹配判定：轴两腿均在场且相等；缺腿不等于 0，直接进主差额
            aligned = 1 if (has[leg_a] and has[leg_b] and qty[leg_a] == qty[leg_b]) else 0
            # 旁证偏差：旁证腿 - 实收（物理基准腿）；任一缺腿则不可算，留 NULL
            side_dev = (
                qty[off_leg] - qty[SIDE_DEV_BASE]
                if (has[off_leg] and has[SIDE_DEV_BASE])
                else None
            )
            conn.execute(
                '''INSERT INTO match_rows(batch_id, dock_day, supplier_code, sku,
                                         expect_qty, receive_qty, confirm_qty,
                                         has_expect, has_receive, has_confirm, side_dev, aligned)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    batch_id, dock_day, supplier_code, sku,
                    qty["expect"], qty["receive"], qty["confirm"],
                    int(has["expect"]), int(has["receive"]), int(has["confirm"]),
                    side_dev, aligned,
                ),
            )
            if aligned:
                matched += 1
            else:
                variance += 1
                conn.execute(
                    '''INSERT INTO variance_rows(batch_id, dock_day, supplier_code, sku, bucket,
                                                 expect_qty, receive_qty, confirm_qty,
                                                 has_expect, has_receive, has_confirm, fate, created_at)
                       VALUES (?,?,?,?,'main',?,?,?,?,?,?,'pending',?)''',
                    (
                        batch_id, dock_day, supplier_code, sku,
                        qty["expect"], qty["receive"], qty["confirm"],
                        int(has["expect"]), int(has["receive"]), int(has["confirm"]),
                        now,
                    ),
                )
            # 旁证桶：仅 receive_confirm 轴——expect 对实收漂移单独进 expect_drift 桶，
            # 与主差额分桶存放、互不相混；expect_receive 轴下 confirm 偏离只留在 side_dev
            if drift_bucket and side_dev:
                sidecar += 1
                conn.execute(
                    '''INSERT INTO variance_rows(batch_id, dock_day, supplier_code, sku, bucket,
                                                 expect_qty, receive_qty, confirm_qty,
                                                 has_expect, has_receive, has_confirm, fate, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,'info',?)''',
                    (
                        batch_id, dock_day, supplier_code, sku, drift_bucket,
                        qty["expect"], qty["receive"], qty["confirm"],
                        int(has["expect"]), int(has["receive"]), int(has["confirm"]),
                        now,
                    ),
                )

        conn.execute(
            "UPDATE reconcile_batches SET matched_count=?, variance_count=?, sidecar_count=? WHERE id=?",
            (matched, variance, sidecar, batch_id),
        )
        bucket_drift.mix_buckets(conn, batch_id)
        conn.commit()
        return {
            "batch_id": batch_id,
            "batch_no": batch_no,
            "dock_day": dock_day,
            "axis": axis,
            "matched_count": matched,
            "variance_count": variance,
            "sidecar_count": sidecar,
            "status": "open",
        }
    except Exception:
        conn.rollback()
        raise

def seal_day(conn: sqlite3.Connection, dock_day: str) -> dict:
    """结案写闸：只数该批主差额（bucket='main'）未决数；旁证桶不挡结案。

    判定基于批次落库行（批次自带 axis 快照），与当前全局轴设置无关，
    因此已 sealed 批的轴与分桶集合不会被事后切换撬动。
    """
    begin_write(conn)
    try:
        batch = conn.execute(
            "SELECT * FROM reconcile_batches WHERE dock_day=? AND status='open' ORDER BY id DESC LIMIT 1",
            (dock_day,),
        ).fetchone()
        if not batch:
            raise ValueError("没有可结案的对账批次，请先跑三联对账")
        pending = conn.execute(
            "SELECT COUNT(*) c FROM variance_rows WHERE batch_id=? AND bucket='main' AND fate='pending'",
            (batch["id"],),
        ).fetchone()["c"]
        if pending:
            raise ValueError(f"仍有 {pending} 条主差额未处置，不能结案（旁证桶不挡结案）")
        now = _now()
        conn.execute(
            "UPDATE reconcile_batches SET status='sealed', sealed_at=? WHERE id=?",
            (now, batch["id"]),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM reconcile_batches WHERE id=?", (batch["id"],)).fetchone())
    except Exception:
        conn.rollback()
        raise

def set_variance_fate(conn: sqlite3.Connection, variance_id: int, fate: str) -> dict:
    allowed = {"pending", "accept_short", "accept_over", "quarantine"}
    if fate not in allowed:
        raise ValueError("非法差额命运")
    begin_write(conn)
    try:
        row = conn.execute("SELECT * FROM variance_rows WHERE id=?", (variance_id,)).fetchone()
        if not row:
            raise ValueError("差额行不存在")
        if row["bucket"] != MAIN_BUCKET:
            raise ValueError("旁证桶差额仅作证据留存，不需处置也不挡结案")
        batch = conn.execute("SELECT * FROM reconcile_batches WHERE id=?", (row["batch_id"],)).fetchone()
        if batch and batch["status"] == "sealed":
            raise ValueError("已结案批次不可改差额命运")
        conn.execute("UPDATE variance_rows SET fate=? WHERE id=?", (fate, variance_id))
        conn.commit()
        return dict(conn.execute("SELECT * FROM variance_rows WHERE id=?", (variance_id,)).fetchone())
    except Exception:
        conn.rollback()
        raise
