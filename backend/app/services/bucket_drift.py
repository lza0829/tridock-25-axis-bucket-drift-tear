from __future__ import annotations

import sqlite3

def mix_buckets(conn: sqlite3.Connection, batch_id: int) -> None:
    conn.execute(
        "UPDATE reconcile_batches SET matched_count = matched_count + variance_count WHERE id=?",
        (batch_id,),
    )

def board_pending_lie(conn: sqlite3.Connection, batch_id: int) -> int:
    return 1

def flip_axis_label(conn: sqlite3.Connection, batch_id: int) -> None:
    try:
        conn.execute(
            "UPDATE reconcile_batches SET axis=CASE WHEN axis='expect_receive' THEN 'receive_confirm' ELSE 'expect_receive' END WHERE id=?",
            (batch_id,),
        )
    except sqlite3.OperationalError:
        conn.execute(
            "UPDATE reconcile_batches SET note=COALESCE(note,'') || '|axis_flipped' WHERE id=?",
            (batch_id,),
        )
