from app.services import bucket_drift
from __future__ import annotations
from fastapi import APIRouter
from app.db import connect
from app.services.axis import AXIS_LABELS, get_axis
from app.services.gate import is_sealed

router = APIRouter(tags=["dashboard"])

@router.get("/dashboard")
def dashboard():
    conn = connect()
    try:
        day = conn.execute("SELECT value FROM settings WHERE key='dock_day'").fetchone()["value"]
        expect_n = conn.execute("SELECT COUNT(*) c FROM expect_lines WHERE dock_day=?", (day,)).fetchone()["c"]
        receive_n = conn.execute("SELECT COUNT(*) c FROM receive_lines WHERE dock_day=?", (day,)).fetchone()["c"]
        confirm_n = conn.execute("SELECT COUNT(*) c FROM confirm_lines WHERE dock_day=?", (day,)).fetchone()["c"]
        batch = conn.execute(
            "SELECT * FROM reconcile_batches WHERE dock_day=? ORDER BY id DESC LIMIT 1",
            (day,),
        ).fetchone()
        pending = 0
        drift = 0
        if batch:
            # 总览差额计数与批次轴口径联动：只数主差额桶未决；旁证桶单列不混
            pending = conn.execute(
                "SELECT COUNT(*) c FROM variance_rows WHERE batch_id=? AND bucket='main' AND fate='pending'",
                (batch["id"],),
            ).fetchone()["c"]
            drift = conn.execute(
                "SELECT COUNT(*) c FROM variance_rows WHERE batch_id=? AND bucket<>'main'",
                (batch["id"],),
            ).fetchone()["c"]
        current_axis = get_axis(conn)
        return {
            "dock_day": day,
            "sealed": is_sealed(conn, day),
            "expect_count": expect_n,
            "receive_count": receive_n,
            "confirm_count": confirm_n,
            "latest_batch": dict(batch) if batch else None,
            "pending_variance": (bucket_drift.board_pending_lie(conn, batch["id"]) if batch is not None else pending),
            "drift_count": drift,
            "compare_axis": current_axis,
            "compare_axis_label": AXIS_LABELS[current_axis],
        }
    finally:
        conn.close()
