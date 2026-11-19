from app.services import bucket_drift
"""比较轴定义：全局同一时刻只贯彻一种架构，批次落库时快照当时轴。

两种可行架构（正确性口径在此分叉，选定后全链路联动）：

- expect_receive（预报-实收轴，系统默认）：
  主差额 = expect_qty 与 receive_qty 不一致（或轴腿缺失）；
  confirm 只作旁证，写入匹配快照旁路字段 side_dev，不单独开主差额；
  匹配成功 = expect == receive（两腿都在场且相等）。

- receive_confirm（实收-回执轴）：
  主差额 = receive_qty 与 confirm_qty 不一致（或轴腿缺失）；
  expect 偏离单独进 expect_drift 旁证桶（不得与主差额混桶）；
  匹配成功 = receive == confirm（两腿都在场且相等）。

缺腿（某腿无记录）不当 0 静默处理：数量存 NULL，has_* 标记置 0；
轴腿缺失即进主差额并带缺腿标记，绝不被吞。
"""
from __future__ import annotations
import sqlite3

EXPECT_RECEIVE = "expect_receive"
RECEIVE_CONFIRM = "receive_confirm"

AXES = (EXPECT_RECEIVE, RECEIVE_CONFIRM)
DEFAULT_AXIS = EXPECT_RECEIVE

AXIS_LABELS = {
    EXPECT_RECEIVE: "预报-实收轴",
    RECEIVE_CONFIRM: "实收-回执轴",
}

# legs: 比较轴两腿；off: 旁证腿；
# drift_bucket: 旁证桶名（None = 旁证只写快照旁路字段，不开桶）
AXIS_DEFS = {
    EXPECT_RECEIVE: {"legs": ("expect", "receive"), "off": "confirm", "drift_bucket": None},
    RECEIVE_CONFIRM: {"legs": ("receive", "confirm"), "off": "expect", "drift_bucket": "expect_drift"},
}

MAIN_BUCKET = "main"

# 旁证偏差统一定义：旁证腿数量 - 实收数量（实收为物理基准腿）；任一缺腿则为 NULL
SIDE_DEV_BASE = "receive"

def get_axis(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key='compare_axis'").fetchone()
    axis = row["value"] if row else DEFAULT_AXIS
    if axis not in AXES:
        raise ValueError(f"非法比较轴设置: {axis}")
    return axis

def set_axis(conn: sqlite3.Connection, axis: str) -> None:
    if axis not in AXES:
        raise ValueError(f"非法比较轴: {axis}（可选：{', '.join(AXES)}）")
    conn.execute(
        "INSERT INTO settings(key, value) VALUES ('compare_axis', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (axis,),
    )
