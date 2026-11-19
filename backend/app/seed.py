from __future__ import annotations
import sqlite3
from app.db import connect
from app.services.axis import DEFAULT_AXIS

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS expect_lines (
  id INTEGER PRIMARY KEY,
  doc_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receive_lines (
  id INTEGER PRIMARY KEY,
  receive_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  occurred_at TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirm_lines (
  id INTEGER PRIMARY KEY,
  confirm_no TEXT NOT NULL UNIQUE,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  dock_day TEXT NOT NULL,
  qty REAL NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconcile_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_no TEXT NOT NULL UNIQUE,
  dock_day TEXT NOT NULL,
  axis TEXT NOT NULL DEFAULT 'expect_receive',
  matched_count INTEGER NOT NULL,
  variance_count INTEGER NOT NULL,
  sidecar_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  sealed_at TEXT,
  note TEXT
);
CREATE TABLE IF NOT EXISTS match_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  dock_day TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  expect_qty REAL,
  receive_qty REAL,
  confirm_qty REAL,
  has_expect INTEGER NOT NULL DEFAULT 0,
  has_receive INTEGER NOT NULL DEFAULT 0,
  has_confirm INTEGER NOT NULL DEFAULT 0,
  side_dev REAL,
  aligned INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS variance_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  dock_day TEXT NOT NULL,
  supplier_code TEXT NOT NULL,
  sku TEXT NOT NULL,
  bucket TEXT NOT NULL DEFAULT 'main',
  expect_qty REAL,
  receive_qty REAL,
  confirm_qty REAL,
  has_expect INTEGER NOT NULL DEFAULT 0,
  has_receive INTEGER NOT NULL DEFAULT 0,
  has_confirm INTEGER NOT NULL DEFAULT 0,
  fate TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS late_spill (
  id INTEGER PRIMARY KEY,
  dock_day TEXT NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS reopen_requests (
  id INTEGER PRIMARY KEY,
  dock_day TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'none'
);
'''

# 老库列迁移（新库由 SCHEMA 直接建全）；重复列时忽略
COLUMN_MIGRATIONS = [
    "ALTER TABLE reconcile_batches ADD COLUMN axis TEXT NOT NULL DEFAULT 'expect_receive'",
    "ALTER TABLE reconcile_batches ADD COLUMN sidecar_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE match_rows ADD COLUMN has_expect INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE match_rows ADD COLUMN has_receive INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE match_rows ADD COLUMN has_confirm INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE match_rows ADD COLUMN side_dev REAL",
    "ALTER TABLE variance_rows ADD COLUMN bucket TEXT NOT NULL DEFAULT 'main'",
    "ALTER TABLE variance_rows ADD COLUMN has_expect INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE variance_rows ADD COLUMN has_receive INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE variance_rows ADD COLUMN has_confirm INTEGER NOT NULL DEFAULT 1",
]

# 同一 dock_day 至多一个 open 批次：并发两路对账不得落下两套互相矛盾的 open 主差额
OPEN_BATCH_UNIQUE_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_reconcile_batches_open_day "
    "ON reconcile_batches(dock_day) WHERE status='open'"
)

def _dedupe_open_batches(conn: sqlite3.Connection) -> None:
    """建唯一索引前清掉历史遗留的重复 open 批（每天保留最新一个）及其行。"""
    dupes = conn.execute(
        '''SELECT id FROM reconcile_batches a WHERE a.status='open' AND EXISTS (
             SELECT 1 FROM reconcile_batches b
             WHERE b.dock_day=a.dock_day AND b.status='open' AND b.id>a.id)'''
    ).fetchall()
    for r in dupes:
        conn.execute("DELETE FROM match_rows WHERE batch_id=?", (r["id"],))
        conn.execute("DELETE FROM variance_rows WHERE batch_id=?", (r["id"],))
        conn.execute("DELETE FROM reconcile_batches WHERE id=?", (r["id"],))

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    for stmt in COLUMN_MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
    _dedupe_open_batches(conn)
    conn.execute(OPEN_BATCH_UNIQUE_INDEX)
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES ('compare_axis', ?)",
        (DEFAULT_AXIS,),
    )
    conn.commit()

def seed_if_empty() -> None:
    conn = connect()
    try:
        ensure_schema(conn)
        if conn.execute("SELECT COUNT(*) c FROM suppliers").fetchone()["c"]:
            return
        conn.execute("INSERT INTO settings(key, value) VALUES ('dock_day', ?)", ("2026-11-18",))
        suppliers = [
            ("SUP-A", "东海食材"),
            ("SUP-B", "北岭包装"),
            ("SUP-C", "南湾百货"),
        ]
        for code, title in suppliers:
            conn.execute("INSERT INTO suppliers(code, title) VALUES (?,?)", (code, title))
        expects = [
            ("EX-1001", "SUP-A", "SKU-米-25", "2026-11-18", 40, "早班预报"),
            ("EX-1002", "SUP-A", "SKU-油-5", "2026-11-18", 20, None),
            ("EX-1003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, None),
            ("EX-1004", "SUP-C", "SKU-巾-卷", "2026-11-18", 60, None),
            ("EX-0901", "SUP-A", "SKU-米-25", "2026-11-17", 10, "昨日"),
        ]
        for row in expects:
            conn.execute(
                '''INSERT INTO expect_lines(doc_no, supplier_code, sku, dock_day, qty, note, created_at)
                   VALUES (?,?,?,?,?,?,?)''',
                (*row, "2026-11-18T08:00:00Z"),
            )
        receives = [
            ("RC-2001", "SUP-A", "SKU-米-25", "2026-11-18", 38, "2026-11-18T09:10:00Z", "少2"),
            ("RC-2002", "SUP-A", "SKU-油-5", "2026-11-18", 20, "2026-11-18T09:20:00Z", None),
            ("RC-2003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, "2026-11-18T10:00:00Z", None),
            ("RC-2004", "SUP-C", "SKU-巾-卷", "2026-11-18", 66, "2026-11-18T11:00:00Z", "多6"),
        ]
        for row in receives:
            conn.execute(
                '''INSERT INTO receive_lines(receive_no, supplier_code, sku, dock_day, qty, occurred_at, note, created_at)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (*row, row[5]),
            )
        confirms = [
            ("CF-3001", "SUP-A", "SKU-米-25", "2026-11-18", 38, "回执按实收"),
            ("CF-3002", "SUP-A", "SKU-油-5", "2026-11-18", 20, None),
            ("CF-3003", "SUP-B", "SKU-箱-大", "2026-11-18", 100, None),
            ("CF-3004", "SUP-C", "SKU-巾-卷", "2026-11-18", 60, "回执仍报预报数"),
        ]
        for row in confirms:
            conn.execute(
                '''INSERT INTO confirm_lines(confirm_no, supplier_code, sku, dock_day, qty, note, created_at)
                   VALUES (?,?,?,?,?,?,?)''',
                (*row, "2026-11-18T12:00:00Z"),
            )
        conn.commit()
    finally:
        conn.close()
