from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import connect
from app.services import axis as axis_svc

router = APIRouter(tags=["settings"])

class DayIn(BaseModel):
    dock_day: str

class AxisIn(BaseModel):
    axis: str

@router.get("/settings")
def get_settings():
    conn = connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()

@router.get("/settings/axes")
def list_axes():
    """可选比较轴及口径说明；全局同一时刻只贯彻一种。"""
    return [
        {
            "value": axis_svc.EXPECT_RECEIVE,
            "label": axis_svc.AXIS_LABELS[axis_svc.EXPECT_RECEIVE],
            "main": "expect_qty ≠ receive_qty 进主差额（轴腿缺失也算）",
            "sidecar": "confirm 仅作旁证写入快照 side_dev，不单独开主差额",
            "match": "匹配成功 = expect == receive",
        },
        {
            "value": axis_svc.RECEIVE_CONFIRM,
            "label": axis_svc.AXIS_LABELS[axis_svc.RECEIVE_CONFIRM],
            "main": "receive_qty ≠ confirm_qty 进主差额（轴腿缺失也算）",
            "sidecar": "expect 偏离单独进 expect_drift 旁证桶，不与主差额混桶",
            "match": "匹配成功 = receive == confirm",
        },
    ]

@router.post("/settings/dock-day")
def set_dock_day(body: DayIn):
    if len(body.dock_day) != 10:
        raise HTTPException(400, "dock_day 格式应为 YYYY-MM-DD")
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('dock_day', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (body.dock_day,),
        )
        conn.commit()
        return {"dock_day": body.dock_day}
    finally:
        conn.close()

@router.post("/settings/axis")
def set_compare_axis(body: AxisIn):
    """切换全局比较轴。只影响之后新跑的批次；已落库（尤其已结案）批次的
    axis 快照与分桶集合封存不变，不会被事后切换撬动。"""
    conn = connect()
    try:
        axis_svc.set_axis(conn, body.axis)
        conn.commit()
        return {"compare_axis": body.axis, "label": axis_svc.AXIS_LABELS[body.axis]}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        conn.close()
