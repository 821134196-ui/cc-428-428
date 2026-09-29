"""SQLite 连接、建表与基础 DAO。

档号与库位全部持久化在 SQLite；盘点附件保存在本地目录（见 attachments 模块）。
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    role        TEXT NOT NULL,                 -- admin 管理员 / cataloger 编目员 / keeper 库房管理员 / reader 借阅员 / auditor 审计员
    clearance   TEXT NOT NULL DEFAULT '公开',  -- 可查看的最高密级
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS fonds (
    id     INTEGER PRIMARY KEY,
    code   TEXT UNIQUE NOT NULL,   -- 全宗号
    name   TEXT NOT NULL,
    note   TEXT DEFAULT ''
);

-- 案卷（档号最小管理单元）
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY,
    archive_no  TEXT UNIQUE NOT NULL,           -- 档号：全宗-年度-期限-序号
    fonds_id    INTEGER NOT NULL REFERENCES fonds(id),
    year        INTEGER NOT NULL,
    retention   TEXT NOT NULL,                  -- 保管期限：永久 Y / 定期30年 D30 / 定期10年 D10
    title       TEXT NOT NULL,
    security_level TEXT NOT NULL DEFAULT '公开', -- 公开 / 内部 / 秘密 / 机密
    seq         INTEGER NOT NULL,               -- 同全宗-年度-期限内的序号，装盒连续性依据
    box_id      INTEGER REFERENCES boxes(id),
    box_seq     INTEGER,                        -- 盒内顺序
    status      TEXT NOT NULL DEFAULT 'unboxed',-- unboxed / boxed / inbound / loaned / missing
    created_at  TEXT NOT NULL
);

-- 档案盒
CREATE TABLE IF NOT EXISTS boxes (
    id          INTEGER PRIMARY KEY,
    box_no      TEXT UNIQUE NOT NULL,
    fonds_id    INTEGER NOT NULL REFERENCES fonds(id),
    year        INTEGER NOT NULL,
    retention   TEXT NOT NULL,
    security_level TEXT NOT NULL DEFAULT '公开', -- 盒内最高密级
    status      TEXT NOT NULL DEFAULT 'packed', -- packed 已装盒 / inbound 已入库 / loaned 在借 / moving 移位中
    location_id INTEGER REFERENCES locations(id),
    inbound_at  TEXT,
    inbound_by  TEXT,
    packed_at   TEXT NOT NULL
);

-- 库位：库房-货架-层位（网格单元）
CREATE TABLE IF NOT EXISTS locations (
    id         INTEGER PRIMARY KEY,
    room_code  TEXT NOT NULL,        -- 库房
    shelf_code TEXT NOT NULL,        -- 货架
    layer_no   INTEGER NOT NULL,     -- 层
    slot_code  TEXT UNIQUE NOT NULL, -- R01-A-03-02
    capacity   INTEGER NOT NULL DEFAULT 1,
    note       TEXT DEFAULT ''
);

-- 移位单（调架必须先开单）
CREATE TABLE IF NOT EXISTS move_orders (
    id          INTEGER PRIMARY KEY,
    order_no    TEXT UNIQUE NOT NULL,
    box_id      INTEGER NOT NULL REFERENCES boxes(id),
    from_loc_id INTEGER REFERENCES locations(id),
    to_loc_id   INTEGER NOT NULL REFERENCES locations(id),
    status      TEXT NOT NULL DEFAULT 'draft', -- draft 待出库 / in_transit 在途 / done 已交接 / interrupted 中断 / cancelled 已撤销
    applicant   TEXT NOT NULL,
    handler     TEXT,                          -- 搬运人
    receiver    TEXT,                          -- 接收人（交接）
    created_at  TEXT NOT NULL,
    moved_at    TEXT,
    received_at TEXT,
    note        TEXT DEFAULT ''
);

-- 盘点任务
CREATE TABLE IF NOT EXISTS stock_takes (
    id         INTEGER PRIMARY KEY,
    task_no    TEXT UNIQUE NOT NULL,
    scope_room  TEXT,
    scope_shelf TEXT,                 -- NULL 表示整库或按库房
    status     TEXT NOT NULL DEFAULT 'open', -- open 进行中(已冻结) / confirmed 已确认 / cancelled
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    confirmed_by TEXT,
    note       TEXT DEFAULT ''
);

-- 盘点清点明细
CREATE TABLE IF NOT EXISTS stock_take_items (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER NOT NULL REFERENCES stock_takes(id),
    box_id      INTEGER NOT NULL REFERENCES boxes(id),
    expected_loc_id INTEGER REFERENCES locations(id),
    result      TEXT,                 -- present 相符 / missing 缺盒 / wrong_box 错盒 / label_damaged 标签损坏 / fewer_items 少件
    actual_loc_id INTEGER REFERENCES locations(id),
    actual_count INTEGER,             -- 实有件数（少件时小于盒内件数）
    counted_by  TEXT,
    counted_at  TEXT,
    note        TEXT DEFAULT '',
    UNIQUE(task_id, box_id)
);

-- 盘点差异（六类：缺卷 / 错盒 / 标签损坏 / 盘点少件 / 移位中断 / 借阅超期）
CREATE TABLE IF NOT EXISTS discrepancies (
    id          INTEGER PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    type        TEXT NOT NULL,        -- missing_record 缺卷 / wrong_box 错盒 / label_damaged 标签损坏 / fewer_items 盘点少件 / move_interrupted 移位中断 / loan_overdue 借阅超期
    task_id     INTEGER REFERENCES stock_takes(id),
    box_id      INTEGER REFERENCES boxes(id),
    record_id   INTEGER REFERENCES records(id),
    location_id INTEGER REFERENCES locations(id),
    move_order_id INTEGER REFERENCES move_orders(id),
    loan_id     INTEGER REFERENCES loans(id),
    title       TEXT NOT NULL,
    detail      TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open', -- open 待处理 / resolved 已处理 / supplemented 已补录
    basis       TEXT DEFAULT '',       -- 补录依据（补录必填）
    created_at  TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT,
    resolution  TEXT DEFAULT ''
);

-- 借阅单（幂等键防止重复提交；一盒同时只允许一人借出）
CREATE TABLE IF NOT EXISTS loans (
    id          INTEGER PRIMARY KEY,
    loan_no     TEXT UNIQUE NOT NULL,
    idem_key    TEXT UNIQUE,                       -- 借阅幂等键
    box_id      INTEGER NOT NULL REFERENCES boxes(id),
    borrower    TEXT NOT NULL,                     -- 借阅人
    borrower_org TEXT DEFAULT '',
    purpose     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending 待审批 / approved 已批准 / rejected 已驳回 / borrowed 已借出 / returned 已归还
    applied_at  TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    reject_reason TEXT DEFAULT '',
    borrowed_at TEXT,
    due_date    TEXT,                              -- 归还期限（主列展示）
    returned_at TEXT,
    renew_times INTEGER NOT NULL DEFAULT 0
);

-- 进程内任务生成的盘点提醒（落库便于页面展示）
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,   -- due_soon / overdue / stocktake_open
    ref_type    TEXT NOT NULL,   -- loan / stocktake
    ref_id      INTEGER NOT NULL,
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(kind, ref_type, ref_id)
);

CREATE INDEX IF NOT EXISTS idx_records_group ON records(fonds_id, year, retention, seq);
CREATE INDEX IF NOT EXISTS idx_boxes_location ON boxes(location_id);
CREATE INDEX IF NOT EXISTS idx_loans_box ON loans(box_id, status);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path=None):
    conn = sqlite3.connect(db_path or str(config.DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextmanager
def get_conn(db_path=None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None):
    config.ensure_dirs()
    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA)
