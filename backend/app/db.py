from __future__ import annotations
import sqlite3
from app.config import DATA_DIR, DB_PATH

def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # 并发写（如两路同时对账）时等待锁而不是立刻报 database is locked
    conn.execute("PRAGMA busy_timeout=8000")
    return conn
