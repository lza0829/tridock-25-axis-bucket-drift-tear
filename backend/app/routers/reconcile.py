from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import connect
from app.services import reconcile_service as svc

router = APIRouter(tags=["reconcile"])

class RunIn(BaseModel):
    dock_day: str

class SealIn(BaseModel):
    dock_day: str

class FateIn(BaseModel):
    fate: str

@router.post("/reconcile/run")
def run_reconcile(body: RunIn):
    conn = connect()
    try:
        return svc.run_reconcile(conn, body.dock_day)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        conn.close()

@router.post("/reconcile/seal")
def seal(body: SealIn):
    conn = connect()
    try:
        return svc.seal_day(conn, body.dock_day)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        conn.close()

@router.get("/reconcile/batches")
def batches(dock_day: str | None = None):
    conn = connect()
    try:
        if dock_day:
            rows = conn.execute(
                "SELECT * FROM reconcile_batches WHERE dock_day=? ORDER BY id DESC", (dock_day,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM reconcile_batches ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.get("/reconcile/matches")
def matches(batch_id: int):
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM match_rows WHERE batch_id=? ORDER BY supplier_code, sku", (batch_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.get("/reconcile/variances")
def variances(batch_id: int | None = None, dock_day: str | None = None, bucket: str | None = None):
    """差额列表：bucket 过滤主差额/旁证桶（main / expect_drift），两桶不混。"""
    conn = connect()
    try:
        clauses = []
        params: list = []
        if batch_id is not None:
            clauses.append("batch_id=?")
            params.append(batch_id)
        if dock_day:
            clauses.append("dock_day=?")
            params.append(dock_day)
        if bucket:
            clauses.append("bucket=?")
            params.append(bucket)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM variance_rows {where} ORDER BY id", params
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/reconcile/variances/{variance_id}/fate")
def set_fate(variance_id: int, body: FateIn):
    conn = connect()
    try:
        return svc.set_variance_fate(conn, variance_id, body.fate)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        conn.close()
