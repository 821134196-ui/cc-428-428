"""Database layer for the archive management system.

Uses SQLite (standard library only). All档号 (archive numbers) and库位
(locations) are persisted here. Inventory attachments live on the local
filesystem under data/inventory_attachments/.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager

DB_PATH = os.environ.get("ARCHIVE_DB_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "archive.db"))
ATTACHMENT_DIR = os.environ.get("ARCHIVE_ATTACHMENT_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "inventory_attachments"))

_local = threading.local()


def get_db_path():
    return DB_PATH


def get_attachment_dir():
    os.makedirs(ATTACHMENT_DIR, exist_ok=True)
    return ATTACHMENT_DIR


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn():
    """Return a thread-local connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = _connect()
    return _local.conn


@contextmanager
def transaction():
    """Context manager that commits on success, rolls back on error."""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


SCHEMA = """
-- 全宗 (fonds)
CREATE TABLE IF NOT EXISTS fonds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 案卷 (files) — 档号 must be unique
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fonds_id INTEGER NOT NULL REFERENCES fonds(id),
    archive_number TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    year INTEGER NOT NULL,
    retention_period TEXT NOT NULL CHECK (retention_period IN ('永久','30年','10年')),
    security_level TEXT NOT NULL DEFAULT '公开' CHECK (security_level IN ('公开','内部','秘密','机密')),
    box_id INTEGER REFERENCES boxes(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_files_fonds ON files(fonds_id);
CREATE INDEX IF NOT EXISTS idx_files_box ON files(box_id);
CREATE INDEX IF NOT EXISTS idx_files_year_retention ON files(year, retention_period);

-- 盒 (boxes)
CREATE TABLE IF NOT EXISTS boxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_number TEXT NOT NULL UNIQUE,
    year INTEGER NOT NULL,
    retention_period TEXT NOT NULL CHECK (retention_period IN ('永久','30年','10年')),
    status TEXT NOT NULL DEFAULT '在库' CHECK (status IN ('在库','借出','移位中')),
    warehouse_id INTEGER REFERENCES warehouses(id),
    shelf_id INTEGER REFERENCES shelves(id),
    layer INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_boxes_location ON boxes(warehouse_id, shelf_id, layer);

-- 库房 (warehouses)
CREATE TABLE IF NOT EXISTS warehouses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 货架 (shelves)
CREATE TABLE IF NOT EXISTS shelves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    code TEXT NOT NULL,
    layers_count INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(warehouse_id, code)
);

-- 装盒清单 (box_files) — ordered list of files in a box
CREATE TABLE IF NOT EXISTS box_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL REFERENCES boxes(id),
    file_id INTEGER NOT NULL REFERENCES files(id),
    position INTEGER NOT NULL,
    UNIQUE(box_id, file_id),
    UNIQUE(box_id, position)
);
CREATE INDEX IF NOT EXISTS idx_box_files_box ON box_files(box_id);

-- 移位单 (transfer_orders)
CREATE TABLE IF NOT EXISTS transfer_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL REFERENCES boxes(id),
    from_warehouse_id INTEGER REFERENCES warehouses(id),
    from_shelf_id INTEGER REFERENCES shelves(id),
    from_layer INTEGER,
    to_warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    to_shelf_id INTEGER NOT NULL REFERENCES shelves(id),
    to_layer INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT '待执行' CHECK (status IN ('待执行','已确认','中断')),
    created_by TEXT NOT NULL,
    confirmed_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_transfer_box ON transfer_orders(box_id);

-- 盘点任务 (inventory_tasks)
CREATE TABLE IF NOT EXISTS inventory_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待开始' CHECK (status IN ('待开始','进行中','已完成','已冻结')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    completed_at TEXT,
    frozen_at TEXT
);

-- 盘点记录 (inventory_records)
CREATE TABLE IF NOT EXISTS inventory_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES inventory_tasks(id),
    box_id INTEGER NOT NULL REFERENCES boxes(id),
    expected_warehouse_id INTEGER REFERENCES warehouses(id),
    expected_shelf_id INTEGER REFERENCES shelves(id),
    expected_layer INTEGER,
    actual_warehouse_id INTEGER REFERENCES warehouses(id),
    actual_shelf_id INTEGER REFERENCES shelves(id),
    actual_layer INTEGER,
    result TEXT NOT NULL CHECK (result IN ('在库','缺卷','错盒','标签损坏','少件')),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_inventory_records_task ON inventory_records(task_id);
CREATE INDEX IF NOT EXISTS idx_inventory_records_box ON inventory_records(box_id);

-- 差异记录 (differences)
CREATE TABLE IF NOT EXISTS differences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('缺卷','错盒','标签损坏','盘点少件','移位中断','借阅超期')),
    box_id INTEGER REFERENCES boxes(id),
    file_id INTEGER REFERENCES files(id),
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待处理' CHECK (status IN ('待处理','已处理','已补录')),
    basis TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    resolved_at TEXT,
    resolved_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_differences_type ON differences(type);
CREATE INDEX IF NOT EXISTS idx_differences_status ON differences(status);

-- 借阅 (borrowings)
CREATE TABLE IF NOT EXISTS borrowings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL REFERENCES boxes(id),
    borrower TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '待审批' CHECK (status IN ('待审批','已批准','已拒绝','已归还','超期')),
    approved_by TEXT,
    borrowed_at TEXT,
    due_date TEXT,
    returned_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_borrowings_box ON borrowings(box_id);
-- Only one active borrowing per box (待审批 or 已批准)
CREATE UNIQUE INDEX IF NOT EXISTS idx_borrowings_active ON borrowings(box_id) WHERE status IN ('待审批','已批准');

-- 用户 (users) — role-based access
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('管理员','档案员','查阅者')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


def init_db():
    """Create the database and all tables if they don't exist."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _connect()
    conn.executescript(SCHEMA)
    conn.commit()
    # Seed default users if none exist
    cur = conn.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO users (username, role) VALUES (?, ?)",
            [
                ("admin", "管理员"),
                ("archivist", "档案员"),
                ("reader", "查阅者"),
            ],
        )
        conn.commit()
    conn.close()


def reset_db():
    """Drop and recreate all tables. Used by seed script."""
    # Close any existing thread-local connection first
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None
    conn = _connect()
    conn.executescript("""
        PRAGMA foreign_keys=OFF;
        DROP TABLE IF EXISTS borrowings;
        DROP TABLE IF EXISTS differences;
        DROP TABLE IF EXISTS inventory_records;
        DROP TABLE IF EXISTS inventory_tasks;
        DROP TABLE IF EXISTS transfer_orders;
        DROP TABLE IF EXISTS box_files;
        DROP TABLE IF EXISTS boxes;
        DROP TABLE IF EXISTS shelves;
        DROP TABLE IF EXISTS warehouses;
        DROP TABLE IF EXISTS files;
        DROP TABLE IF EXISTS fonds;
        DROP TABLE IF EXISTS users;
        PRAGMA foreign_keys=ON;
    """)
    conn.commit()
    conn.close()
    init_db()
