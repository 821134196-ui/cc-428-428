"""Domain services for the archive management system.

All business logic lives here. The API layer (api.py) is a thin wrapper
around these functions.
"""
import os
import shutil
from datetime import datetime, timedelta
from typing import Optional

from . import db


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ServiceError(Exception):
    """Base error for service-layer violations."""
    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


class NotFoundError(ServiceError):
    def __init__(self, message: str):
        super().__init__(message, "not_found")


class ConflictError(ServiceError):
    def __init__(self, message: str):
        super().__init__(message, "conflict")


class PermissionError(ServiceError):
    def __init__(self, message: str):
        super().__init__(message, "permission_denied")


class ValidationError(ServiceError):
    def __init__(self, message: str):
        super().__init__(message, "validation_error")


# ---------------------------------------------------------------------------
# Role-based access / desensitization
# ---------------------------------------------------------------------------

# Fields that are desensitized for 查阅者 on secret-related records
_SENSITIVE_LEVELS = {"秘密", "机密"}


def _user_role(conn, username: str) -> str:
    row = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        raise PermissionError(f"用户 {username} 不存在")
    return row["role"]


def can_view_sensitive(role: str) -> bool:
    """管理员 and 档案员 can view sensitive fields; 查阅者 cannot."""
    return role in ("管理员", "档案员")


def desensitize_file(file_row: dict, role: str) -> dict:
    """Return a file dict with sensitive fields masked for 查阅者."""
    result = dict(file_row)
    if not can_view_sensitive(role) and result.get("security_level") in _SENSITIVE_LEVELS:
        result["security_level"] = "***"
        # Also mask location info if present
        for key in ("warehouse_id", "shelf_id", "layer"):
            if key in result and result[key] is not None:
                result[key] = None
    return result


def desensitize_box(box_row: dict, role: str) -> dict:
    """Return a box dict with sensitive location fields masked for 查阅者."""
    result = dict(box_row)
    if not can_view_sensitive(role):
        # Check if any file in this box is sensitive
        conn = db.get_conn()
        sensitive = conn.execute(
            "SELECT 1 FROM files WHERE box_id = ? AND security_level IN ('秘密','机密') LIMIT 1",
            (result["id"],),
        ).fetchone()
        if sensitive:
            for key in ("warehouse_id", "shelf_id", "layer"):
                if key in result and result[key] is not None:
                    result[key] = None
    return result


# ---------------------------------------------------------------------------
# 全宗 (fonds)
# ---------------------------------------------------------------------------

def create_fonds(code: str, name: str) -> dict:
    if not code or not name:
        raise ValidationError("全宗代码和名称不能为空")
    with db.transaction() as conn:
        try:
            cur = conn.execute("INSERT INTO fonds (code, name) VALUES (?, ?)", (code, name))
        except db.sqlite3.IntegrityError:
            raise ConflictError(f"全宗代码 {code} 已存在")
        row = conn.execute("SELECT * FROM fonds WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_fonds() -> list:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM fonds ORDER BY code").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 案卷 (files)
# ---------------------------------------------------------------------------

def create_file(fonds_id: int, archive_number: str, title: str, year: int,
                retention_period: str, security_level: str = "公开") -> dict:
    if retention_period not in ("永久", "30年", "10年"):
        raise ValidationError("保管期限必须是 永久/30年/10年")
    if security_level not in ("公开", "内部", "秘密", "机密"):
        raise ValidationError("密级必须是 公开/内部/秘密/机密")
    if not archive_number or not title:
        raise ValidationError("档号和题名不能为空")
    with db.transaction() as conn:
        # Verify fonds exists
        fonds = conn.execute("SELECT id FROM fonds WHERE id = ?", (fonds_id,)).fetchone()
        if not fonds:
            raise NotFoundError(f"全宗 {fonds_id} 不存在")
        try:
            cur = conn.execute(
                "INSERT INTO files (fonds_id, archive_number, title, year, retention_period, security_level) VALUES (?, ?, ?, ?, ?, ?)",
                (fonds_id, archive_number, title, year, retention_period, security_level),
            )
        except db.sqlite3.IntegrityError:
            raise ConflictError(f"档号 {archive_number} 已存在，档号不可重复")
        row = conn.execute("SELECT * FROM files WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_files(role: str, fonds_id: Optional[int] = None, unboxed_only: bool = False) -> list:
    conn = db.get_conn()
    sql = "SELECT * FROM files WHERE 1=1"
    params = []
    if fonds_id is not None:
        sql += " AND fonds_id = ?"
        params.append(fonds_id)
    if unboxed_only:
        sql += " AND box_id IS NULL"
    sql += " ORDER BY archive_number"
    rows = conn.execute(sql, params).fetchall()
    return [desensitize_file(dict(r), role) for r in rows]


def get_file(file_id: int, role: str) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    if not row:
        raise NotFoundError(f"案卷 {file_id} 不存在")
    return desensitize_file(dict(row), role)


# ---------------------------------------------------------------------------
# 装盒建议 (boxing suggestions)
# ---------------------------------------------------------------------------

def suggest_boxing(year: int, retention_period: str, max_per_box: int = 20) -> list:
    """Generate boxing suggestions for unboxed files of a given year and retention period.

    Rules:
    - Files are grouped by (year, retention_period).
    - Within each group, files are sorted by archive_number.
    - Archive numbers in a box must be continuous and non-repeating.
    - A new box starts when the sequence breaks or max_per_box is reached.
    """
    if retention_period not in ("永久", "30年", "10年"):
        raise ValidationError("保管期限必须是 永久/30年/10年")
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT * FROM files WHERE year = ? AND retention_period = ? AND box_id IS NULL ORDER BY archive_number",
        (year, retention_period),
    ).fetchall()
    files = [dict(r) for r in rows]

    suggestions = []
    current_box = []
    prev_num = None

    for f in files:
        num = f["archive_number"]
        if prev_num is not None and not _is_continuous(prev_num, num):
            # Sequence break — start a new box
            suggestions.append(current_box)
            current_box = []
        current_box.append(f)
        prev_num = num
        if len(current_box) >= max_per_box:
            suggestions.append(current_box)
            current_box = []

    if current_box:
        suggestions.append(current_box)

    return [
        {
            "box_index": i + 1,
            "year": year,
            "retention_period": retention_period,
            "file_count": len(group),
            "archive_numbers": [f["archive_number"] for f in group],
            "files": group,
        }
        for i, group in enumerate(suggestions)
    ]


def _is_continuous(prev: str, curr: str) -> bool:
    """Check if two archive numbers are continuous.

    Archive numbers may have various formats. We try to extract a trailing
    numeric sequence and check if curr = prev + 1.
    """
    import re
    prev_match = re.search(r"(\d+)$", prev)
    curr_match = re.search(r"(\d+)$", curr)
    if not prev_match or not curr_match:
        return False
    prev_num = int(prev_match.group(1))
    curr_num = int(curr_match.group(1))
    # Check that the prefixes match
    prev_prefix = prev[:prev_match.start()]
    curr_prefix = curr[:curr_match.start()]
    if prev_prefix != curr_prefix:
        return False
    # Zero-padding must match
    if len(prev_match.group(1)) != len(curr_match.group(1)):
        return False
    return curr_num == prev_num + 1


# ---------------------------------------------------------------------------
# 装盒 (boxing)
# ---------------------------------------------------------------------------

def create_box(box_number: str, year: int, retention_period: str, file_ids: list) -> dict:
    """Create a box and assign files to it.

    Validates:
    - All files exist and are unboxed.
    - All files have the same year and retention_period as the box.
    - Archive numbers are continuous and non-repeating.
    """
    if retention_period not in ("永久", "30年", "10年"):
        raise ValidationError("保管期限必须是 永久/30年/10年")
    if not file_ids:
        raise ValidationError("装盒清单不能为空")
    with db.transaction() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO boxes (box_number, year, retention_period) VALUES (?, ?, ?)",
                (box_number, year, retention_period),
            )
        except db.sqlite3.IntegrityError:
            raise ConflictError(f"盒号 {box_number} 已存在")
        box_id = cur.lastrowid

        # Fetch all files and validate
        files = []
        for fid in file_ids:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (fid,)).fetchone()
            if not row:
                raise NotFoundError(f"案卷 {fid} 不存在")
            if row["box_id"] is not None:
                raise ConflictError(f"案卷 {row['archive_number']} 已装盒")
            if row["year"] != year or row["retention_period"] != retention_period:
                raise ValidationError(
                    f"案卷 {row['archive_number']} 的年度/保管期限与盒不一致"
                )
            files.append(dict(row))

        # Sort by archive_number and validate continuity
        files.sort(key=lambda f: f["archive_number"])
        for i in range(1, len(files)):
            if not _is_continuous(files[i - 1]["archive_number"], files[i]["archive_number"]):
                raise ValidationError(
                    f"档号 {files[i-1]['archive_number']} 与 {files[i]['archive_number']} 不连续"
                )

        # Assign files to box
        for pos, f in enumerate(files, start=1):
            conn.execute(
                "INSERT INTO box_files (box_id, file_id, position) VALUES (?, ?, ?)",
                (box_id, f["id"], pos),
            )
            conn.execute("UPDATE files SET box_id = ? WHERE id = ?", (box_id, f["id"]))

        box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
        return dict(box)


def list_boxes(role: str) -> list:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM boxes ORDER BY box_number").fetchall()
    return [desensitize_box(dict(r), role) for r in rows]


def get_box(box_id: int, role: str) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
    if not row:
        raise NotFoundError(f"盒 {box_id} 不存在")
    box = desensitize_box(dict(row), role)
    # Include files in the box
    files = conn.execute(
        "SELECT f.* FROM files f JOIN box_files bf ON f.id = bf.file_id WHERE bf.box_id = ? ORDER BY bf.position",
        (box_id,),
    ).fetchall()
    box["files"] = [desensitize_file(dict(f), role) for f in files]
    return box


# ---------------------------------------------------------------------------
# 库房与货架 (warehouses & shelves)
# ---------------------------------------------------------------------------

def create_warehouse(code: str, name: str) -> dict:
    if not code or not name:
        raise ValidationError("库房代码和名称不能为空")
    with db.transaction() as conn:
        try:
            cur = conn.execute("INSERT INTO warehouses (code, name) VALUES (?, ?)", (code, name))
        except db.sqlite3.IntegrityError:
            raise ConflictError(f"库房代码 {code} 已存在")
        row = conn.execute("SELECT * FROM warehouses WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_warehouses() -> list:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM warehouses ORDER BY code").fetchall()
    return [dict(r) for r in rows]


def create_shelf(warehouse_id: int, code: str, layers_count: int = 5) -> dict:
    if not code:
        raise ValidationError("货架代码不能为空")
    if layers_count < 1:
        raise ValidationError("层数必须大于0")
    with db.transaction() as conn:
        wh = conn.execute("SELECT id FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
        if not wh:
            raise NotFoundError(f"库房 {warehouse_id} 不存在")
        try:
            cur = conn.execute(
                "INSERT INTO shelves (warehouse_id, code, layers_count) VALUES (?, ?, ?)",
                (warehouse_id, code, layers_count),
            )
        except db.sqlite3.IntegrityError:
            raise ConflictError(f"货架代码 {code} 已存在")
        row = conn.execute("SELECT * FROM shelves WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_shelves(warehouse_id: Optional[int] = None) -> list:
    conn = db.get_conn()
    if warehouse_id is not None:
        rows = conn.execute("SELECT * FROM shelves WHERE warehouse_id = ? ORDER BY code", (warehouse_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM shelves ORDER BY code").fetchall()
    return [dict(r) for r in rows]


def get_location_map(role: str, warehouse_id: Optional[int] = None) -> list:
    """Build a grid map of shelves with their occupied layers.

    Returns a list of shelves, each with a list of layers and the box
    (if any) occupying that layer.
    """
    conn = db.get_conn()
    if warehouse_id is not None:
        shelves = conn.execute(
            "SELECT * FROM shelves WHERE warehouse_id = ? ORDER BY code", (warehouse_id,)
        ).fetchall()
    else:
        shelves = conn.execute("SELECT * FROM shelves ORDER BY code").fetchall()

    result = []
    for shelf in shelves:
        shelf_dict = dict(shelf)
        layers = []
        for layer_num in range(1, shelf["layers_count"] + 1):
            box = conn.execute(
                "SELECT * FROM boxes WHERE warehouse_id = ? AND shelf_id = ? AND layer = ?",
                (shelf["warehouse_id"], shelf["id"], layer_num),
            ).fetchone()
            layer_info = {"layer": layer_num, "box": None}
            if box:
                layer_info["box"] = desensitize_box(dict(box), role)
            layers.append(layer_info)
        shelf_dict["layers"] = layers
        result.append(shelf_dict)
    return result


# ---------------------------------------------------------------------------
# 入库 (warehouse entry)
# ---------------------------------------------------------------------------

def enter_warehouse(box_id: int, warehouse_id: int, shelf_id: int, layer: int, username: str) -> dict:
    """Bind a box to a warehouse/shelf/layer location.

    Validates:
    - Box exists and is in 在库 status.
    - Warehouse, shelf, and layer exist.
    - The location is not already occupied.
    """
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以执行入库操作")

    with db.transaction() as conn:
        box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
        if not box:
            raise NotFoundError(f"盒 {box_id} 不存在")
        if box["status"] != "在库":
            raise ConflictError(f"盒 {box['box_number']} 当前状态为 {box['status']}，不能入库")

        wh = conn.execute("SELECT id FROM warehouses WHERE id = ?", (warehouse_id,)).fetchone()
        if not wh:
            raise NotFoundError(f"库房 {warehouse_id} 不存在")
        shelf = conn.execute(
            "SELECT * FROM shelves WHERE id = ? AND warehouse_id = ?", (shelf_id, warehouse_id)
        ).fetchone()
        if not shelf:
            raise NotFoundError(f"货架 {shelf_id} 不存在或不属于该库房")
        if layer < 1 or layer > shelf["layers_count"]:
            raise ValidationError(f"层位必须在 1 到 {shelf['layers_count']} 之间")

        # Check location is free
        occupied = conn.execute(
            "SELECT id FROM boxes WHERE warehouse_id = ? AND shelf_id = ? AND layer = ? AND id != ?",
            (warehouse_id, shelf_id, layer, box_id),
        ).fetchone()
        if occupied:
            raise ConflictError(f"库位 {warehouse_id}/{shelf_id}/{layer} 已被占用")

        conn.execute(
            "UPDATE boxes SET warehouse_id = ?, shelf_id = ?, layer = ? WHERE id = ?",
            (warehouse_id, shelf_id, layer, box_id),
        )
        row = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
        return dict(row)


# ---------------------------------------------------------------------------
# 移位 (transfer)
# ---------------------------------------------------------------------------

def create_transfer(box_id: int, to_warehouse_id: int, to_shelf_id: int, to_layer: int, username: str) -> dict:
    """Create a transfer order (移位单) for a box to move to a new location."""
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以创建移位单")

    with db.transaction() as conn:
        box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
        if not box:
            raise NotFoundError(f"盒 {box_id} 不存在")
        if box["status"] != "在库":
            raise ConflictError(f"盒 {box['box_number']} 当前状态为 {box['status']}，不能移位")

        wh = conn.execute("SELECT id FROM warehouses WHERE id = ?", (to_warehouse_id,)).fetchone()
        if not wh:
            raise NotFoundError(f"库房 {to_warehouse_id} 不存在")
        shelf = conn.execute(
            "SELECT * FROM shelves WHERE id = ? AND warehouse_id = ?", (to_shelf_id, to_warehouse_id)
        ).fetchone()
        if not shelf:
            raise NotFoundError(f"货架 {to_shelf_id} 不存在或不属于该库房")
        if to_layer < 1 or to_layer > shelf["layers_count"]:
            raise ValidationError(f"层位必须在 1 到 {shelf['layers_count']} 之间")

        # Check target location is free
        occupied = conn.execute(
            "SELECT id FROM boxes WHERE warehouse_id = ? AND shelf_id = ? AND layer = ? AND id != ?",
            (to_warehouse_id, to_shelf_id, to_layer, box_id),
        ).fetchone()
        if occupied:
            raise ConflictError(f"目标库位 {to_warehouse_id}/{to_shelf_id}/{to_layer} 已被占用")

        cur = conn.execute(
            """INSERT INTO transfer_orders
               (box_id, from_warehouse_id, from_shelf_id, from_layer, to_warehouse_id, to_shelf_id, to_layer, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (box_id, box["warehouse_id"], box["shelf_id"], box["layer"],
             to_warehouse_id, to_shelf_id, to_layer, username),
        )
        conn.execute("UPDATE boxes SET status = '移位中' WHERE id = ?", (box_id,))
        row = conn.execute("SELECT * FROM transfer_orders WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def confirm_transfer(transfer_id: int, username: str) -> dict:
    """Confirm a transfer — handover complete. Updates box location."""
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以确认移位")

    with db.transaction() as conn:
        order = conn.execute("SELECT * FROM transfer_orders WHERE id = ?", (transfer_id,)).fetchone()
        if not order:
            raise NotFoundError(f"移位单 {transfer_id} 不存在")
        if order["status"] != "待执行":
            raise ConflictError(f"移位单 {transfer_id} 状态为 {order['status']}，不能确认")

        conn.execute(
            "UPDATE transfer_orders SET status = '已确认', confirmed_by = ?, confirmed_at = datetime('now', 'localtime') WHERE id = ?",
            (username, transfer_id),
        )
        conn.execute(
            "UPDATE boxes SET warehouse_id = ?, shelf_id = ?, layer = ?, status = '在库' WHERE id = ?",
            (order["to_warehouse_id"], order["to_shelf_id"], order["to_layer"], order["box_id"]),
        )
        row = conn.execute("SELECT * FROM transfer_orders WHERE id = ?", (transfer_id,)).fetchone()
        return dict(row)


def interrupt_transfer(transfer_id: int, username: str, reason: str = "") -> dict:
    """Interrupt a transfer — creates a difference record for 移位中断."""
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以中断移位")

    with db.transaction() as conn:
        order = conn.execute("SELECT * FROM transfer_orders WHERE id = ?", (transfer_id,)).fetchone()
        if not order:
            raise NotFoundError(f"移位单 {transfer_id} 不存在")
        if order["status"] != "待执行":
            raise ConflictError(f"移位单 {transfer_id} 状态为 {order['status']}，不能中断")

        conn.execute(
            "UPDATE transfer_orders SET status = '中断' WHERE id = ?", (transfer_id,)
        )
        # Box returns to 在库 at original location
        conn.execute(
            "UPDATE boxes SET status = '在库' WHERE id = ?", (order["box_id"],)
        )
        # Create difference record
        desc = f"移位单 {order['id']} 中断"
        if reason:
            desc += f"：{reason}"
        conn.execute(
            "INSERT INTO differences (type, box_id, description, status) VALUES ('移位中断', ?, ?, '待处理')",
            (order["box_id"], desc),
        )
        row = conn.execute("SELECT * FROM transfer_orders WHERE id = ?", (transfer_id,)).fetchone()
        return dict(row)


def list_transfers(status: Optional[str] = None) -> list:
    conn = db.get_conn()
    if status:
        rows = conn.execute("SELECT * FROM transfer_orders WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM transfer_orders ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 盘点 (inventory)
# ---------------------------------------------------------------------------

def create_inventory_task(name: str, username: str) -> dict:
    """Create an inventory task."""
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以创建盘点任务")
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO inventory_tasks (name, created_by) VALUES (?, ?)",
            (name, username),
        )
        row = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_inventory_tasks() -> list:
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM inventory_tasks ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_inventory_task(task_id: int) -> dict:
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise NotFoundError(f"盘点任务 {task_id} 不存在")
    task = dict(row)
    records = conn.execute(
        "SELECT * FROM inventory_records WHERE task_id = ? ORDER BY id", (task_id,)
    ).fetchall()
    task["records"] = [dict(r) for r in records]
    return task


def start_inventory_task(task_id: int, username: str) -> dict:
    """Start an inventory task — begins the freeze window.

    Once started, boxes that are in 在库 status and have a location are
    expected to be counted. The task cannot be modified after completion.
    """
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以开始盘点")
    with db.transaction() as conn:
        task = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise NotFoundError(f"盘点任务 {task_id} 不存在")
        if task["status"] != "待开始":
            raise ConflictError(f"盘点任务 {task_id} 状态为 {task['status']}，不能开始")
        conn.execute("UPDATE inventory_tasks SET status = '进行中' WHERE id = ?", (task_id,))
        row = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row)


def record_inventory_result(task_id: int, box_id: int, result: str,
                            actual_warehouse_id: Optional[int] = None,
                            actual_shelf_id: Optional[int] = None,
                            actual_layer: Optional[int] = None,
                            notes: str = "",
                            username: str = "") -> dict:
    """Record an inventory result for a box.

    Validates:
    - Task is in 进行中 status (not frozen).
    - Box exists.
    - Result is valid.
    """
    if result not in ("在库", "缺卷", "错盒", "标签损坏", "少件"):
        raise ValidationError("盘点结果必须是 在库/缺卷/错盒/标签损坏/少件")

    with db.transaction() as conn:
        task = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise NotFoundError(f"盘点任务 {task_id} 不存在")
        if task["status"] != "进行中":
            raise ConflictError(f"盘点任务 {task_id} 状态为 {task['status']}，不能记录结果")

        box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
        if not box:
            raise NotFoundError(f"盒 {box_id} 不存在")

        # Check if already recorded for this task
        existing = conn.execute(
            "SELECT id FROM inventory_records WHERE task_id = ? AND box_id = ?",
            (task_id, box_id),
        ).fetchone()
        if existing:
            raise ConflictError(f"盒 {box['box_number']} 已在本盘点任务中记录")

        cur = conn.execute(
            """INSERT INTO inventory_records
               (task_id, box_id, expected_warehouse_id, expected_shelf_id, expected_layer,
                actual_warehouse_id, actual_shelf_id, actual_layer, result, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, box_id, box["warehouse_id"], box["shelf_id"], box["layer"],
             actual_warehouse_id, actual_shelf_id, actual_layer, result, notes),
        )

        # Auto-create difference records for non-在库 results
        if result != "在库":
            diff_type = {
                "缺卷": "缺卷",
                "错盒": "错盒",
                "标签损坏": "标签损坏",
                "少件": "盘点少件",
            }.get(result)
            if diff_type:
                desc = f"盘点任务 {task['name']} 中发现盒 {box['box_number']} {result}"
                if notes:
                    desc += f"：{notes}"
                conn.execute(
                    "INSERT INTO differences (type, box_id, description, status) VALUES (?, ?, ?, '待处理')",
                    (diff_type, box_id, desc),
                )

        row = conn.execute("SELECT * FROM inventory_records WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def complete_inventory_task(task_id: int, username: str) -> dict:
    """Complete an inventory task — freezes it.

    After freezing, records cannot be modified. Supplementary records
    (补录) must have a basis (依据).
    """
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以完成盘点")
    with db.transaction() as conn:
        task = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise NotFoundError(f"盘点任务 {task_id} 不存在")
        if task["status"] != "进行中":
            raise ConflictError(f"盘点任务 {task_id} 状态为 {task['status']}，不能完成")
        conn.execute(
            "UPDATE inventory_tasks SET status = '已完成', completed_at = datetime('now', 'localtime') WHERE id = ?",
            (task_id,),
        )
        row = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row)


def freeze_inventory_task(task_id: int, username: str) -> dict:
    """Freeze an inventory task. After freezing, records cannot be modified."""
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以冻结盘点")
    with db.transaction() as conn:
        task = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise NotFoundError(f"盘点任务 {task_id} 不存在")
        if task["status"] not in ("已完成",):
            raise ConflictError(f"盘点任务 {task_id} 状态为 {task['status']}，只有已完成的任务可以冻结")
        conn.execute(
            "UPDATE inventory_tasks SET status = '已冻结', frozen_at = datetime('now', 'localtime') WHERE id = ?",
            (task_id,),
        )
        row = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row)


def supplement_inventory_record(task_id: int, box_id: int, result: str, basis: str,
                                 actual_warehouse_id: Optional[int] = None,
                                 actual_shelf_id: Optional[int] = None,
                                 actual_layer: Optional[int] = None,
                                 notes: str = "",
                                 username: str = "") -> dict:
    """Add a supplementary record (补录) to a frozen inventory task.

    Requires a basis (依据). This is the only way to add records after
    a task is frozen.
    """
    if not basis or not basis.strip():
        raise ValidationError("补录必须注明依据")
    if result not in ("在库", "缺卷", "错盒", "标签损坏", "少件"):
        raise ValidationError("盘点结果必须是 在库/缺卷/错盒/标签损坏/少件")

    with db.transaction() as conn:
        task = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
        if not task:
            raise NotFoundError(f"盘点任务 {task_id} 不存在")
        if task["status"] != "已冻结":
            raise ConflictError(f"盘点任务 {task_id} 状态为 {task['status']}，只有已冻结的任务可以补录")

        box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
        if not box:
            raise NotFoundError(f"盒 {box_id} 不存在")

        cur = conn.execute(
            """INSERT INTO inventory_records
               (task_id, box_id, expected_warehouse_id, expected_shelf_id, expected_layer,
                actual_warehouse_id, actual_shelf_id, actual_layer, result, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, box_id, box["warehouse_id"], box["shelf_id"], box["layer"],
             actual_warehouse_id, actual_shelf_id, actual_layer, result,
             f"[补录] 依据：{basis}。{notes}"),
        )

        # Update or create difference record with basis
        if result != "在库":
            diff_type = {
                "缺卷": "缺卷",
                "错盒": "错盒",
                "标签损坏": "标签损坏",
                "少件": "盘点少件",
            }.get(result)
            if diff_type:
                desc = f"补录：盘点任务 {task['name']} 中发现盒 {box['box_number']} {result}。依据：{basis}"
                conn.execute(
                    "INSERT INTO differences (type, box_id, description, status, basis) VALUES (?, ?, ?, '已补录', ?)",
                    (diff_type, box_id, desc, basis),
                )

        row = conn.execute("SELECT * FROM inventory_records WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def get_inventory_summary(task_id: int) -> dict:
    """Get a summary of inventory results, aggregated by location."""
    conn = db.get_conn()
    task = conn.execute("SELECT * FROM inventory_tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        raise NotFoundError(f"盘点任务 {task_id} 不存在")

    records = conn.execute(
        """SELECT ir.*, b.box_number, w.code AS warehouse_code, s.code AS shelf_code
           FROM inventory_records ir
           JOIN boxes b ON ir.box_id = b.id
           LEFT JOIN warehouses w ON ir.expected_warehouse_id = w.id
           LEFT JOIN shelves s ON ir.expected_shelf_id = s.id
           WHERE ir.task_id = ?
           ORDER BY ir.expected_warehouse_id, ir.expected_shelf_id, ir.expected_layer""",
        (task_id,),
    ).fetchall()

    # Aggregate by location
    by_location = {}
    for r in records:
        key = f"{r['warehouse_code'] or '未分配'}/{r['shelf_code'] or '未分配'}/{r['expected_layer'] or '未分配'}"
        if key not in by_location:
            by_location[key] = {"location": key, "total": 0, "results": {}}
        by_location[key]["total"] += 1
        result = r["result"]
        by_location[key]["results"][result] = by_location[key]["results"].get(result, 0) + 1

    # Overall stats
    total = len(records)
    result_counts = {}
    for r in records:
        result_counts[r["result"]] = result_counts.get(r["result"], 0) + 1

    return {
        "task": dict(task),
        "total_records": total,
        "result_counts": result_counts,
        "by_location": list(by_location.values()),
    }


# ---------------------------------------------------------------------------
# 差异处理 (differences)
# ---------------------------------------------------------------------------

def list_differences(type_filter: Optional[str] = None, status_filter: Optional[str] = None) -> list:
    conn = db.get_conn()
    sql = "SELECT d.*, b.box_number FROM differences d LEFT JOIN boxes b ON d.box_id = b.id WHERE 1=1"
    params = []
    if type_filter:
        sql += " AND d.type = ?"
        params.append(type_filter)
    if status_filter:
        sql += " AND d.status = ?"
        params.append(status_filter)
    sql += " ORDER BY d.created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def resolve_difference(diff_id: int, username: str, basis: str = "") -> dict:
    """Resolve a difference. If it's a 补录 type, requires basis."""
    with db.transaction() as conn:
        diff = conn.execute("SELECT * FROM differences WHERE id = ?", (diff_id,)).fetchone()
        if not diff:
            raise NotFoundError(f"差异记录 {diff_id} 不存在")
        if diff["status"] == "已处理":
            raise ConflictError(f"差异记录 {diff_id} 已处理")
        if diff["status"] == "已补录":
            raise ConflictError(f"差异记录 {diff_id} 已补录，不能重复处理")

        new_status = "已处理"
        update_basis = diff["basis"]
        if basis and basis.strip():
            new_status = "已补录"
            update_basis = basis

        conn.execute(
            "UPDATE differences SET status = ?, basis = ?, resolved_at = datetime('now', 'localtime'), resolved_by = ? WHERE id = ?",
            (new_status, update_basis, username, diff_id),
        )
        row = conn.execute("SELECT * FROM differences WHERE id = ?", (diff_id,)).fetchone()
        return dict(row)


# ---------------------------------------------------------------------------
# 借阅 (borrowings)
# ---------------------------------------------------------------------------

def create_borrowing(box_id: int, borrower: str, due_date: str, username: str) -> dict:
    """Create a borrowing request.

    Enforces idempotency: only one active borrowing (待审批 or 已批准)
    per box. If two people try to borrow the same box simultaneously,
    only one succeeds.
    """
    if not due_date:
        raise ValidationError("归还期限不能为空")
    with db.transaction() as conn:
        box = conn.execute("SELECT * FROM boxes WHERE id = ?", (box_id,)).fetchone()
        if not box:
            raise NotFoundError(f"盒 {box_id} 不存在")

        # Check for existing active borrowing — this is the idempotency guard
        existing = conn.execute(
            "SELECT id FROM borrowings WHERE box_id = ? AND status IN ('待审批','已批准')",
            (box_id,),
        ).fetchone()
        if existing:
            raise ConflictError(f"盒 {box['box_number']} 已有未完成的借阅，不能重复借阅")

        try:
            cur = conn.execute(
                "INSERT INTO borrowings (box_id, borrower, due_date, status) VALUES (?, ?, ?, '待审批')",
                (box_id, borrower, due_date),
            )
        except db.sqlite3.IntegrityError:
            # Concurrent request won — the unique partial index on active
            # borrowings prevents two active borrowings for the same box.
            raise ConflictError(f"盒 {box['box_number']} 已有未完成的借阅，不能重复借阅")
        row = conn.execute("SELECT * FROM borrowings WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def approve_borrowing(borrowing_id: int, username: str) -> dict:
    """Approve a borrowing request."""
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以审批借阅")
    with db.transaction() as conn:
        br = conn.execute("SELECT * FROM borrowings WHERE id = ?", (borrowing_id,)).fetchone()
        if not br:
            raise NotFoundError(f"借阅记录 {borrowing_id} 不存在")
        if br["status"] != "待审批":
            raise ConflictError(f"借阅记录 {borrowing_id} 状态为 {br['status']}，不能审批")

        conn.execute(
            "UPDATE borrowings SET status = '已批准', approved_by = ?, borrowed_at = datetime('now', 'localtime') WHERE id = ?",
            (username, borrowing_id),
        )
        # Update box status to 借出
        conn.execute("UPDATE boxes SET status = '借出' WHERE id = ?", (br["box_id"],))
        row = conn.execute("SELECT * FROM borrowings WHERE id = ?", (borrowing_id,)).fetchone()
        return dict(row)


def reject_borrowing(borrowing_id: int, username: str, reason: str = "") -> dict:
    """Reject a borrowing request."""
    role = _user_role(db.get_conn(), username)
    if not can_view_sensitive(role):
        raise PermissionError("只有管理员或档案员可以拒绝借阅")
    with db.transaction() as conn:
        br = conn.execute("SELECT * FROM borrowings WHERE id = ?", (borrowing_id,)).fetchone()
        if not br:
            raise NotFoundError(f"借阅记录 {borrowing_id} 不存在")
        if br["status"] != "待审批":
            raise ConflictError(f"借阅记录 {borrowing_id} 状态为 {br['status']}，不能拒绝")

        conn.execute(
            "UPDATE borrowings SET status = '已拒绝' WHERE id = ?", (borrowing_id,)
        )
        row = conn.execute("SELECT * FROM borrowings WHERE id = ?", (borrowing_id,)).fetchone()
        return dict(row)


def return_borrowing(borrowing_id: int, username: str) -> dict:
    """Return a borrowed box."""
    with db.transaction() as conn:
        br = conn.execute("SELECT * FROM borrowings WHERE id = ?", (borrowing_id,)).fetchone()
        if not br:
            raise NotFoundError(f"借阅记录 {borrowing_id} 不存在")
        if br["status"] not in ("已批准", "超期"):
            raise ConflictError(f"借阅记录 {borrowing_id} 状态为 {br['status']}，不能归还")

        conn.execute(
            "UPDATE borrowings SET status = '已归还', returned_at = datetime('now', 'localtime') WHERE id = ?",
            (borrowing_id,),
        )
        # Update box status back to 在库
        conn.execute("UPDATE boxes SET status = '在库' WHERE id = ?", (br["box_id"],))
        row = conn.execute("SELECT * FROM borrowings WHERE id = ?", (borrowing_id,)).fetchone()
        return dict(row)


def list_borrowings(role: str, status_filter: Optional[str] = None) -> list:
    conn = db.get_conn()
    if status_filter:
        rows = conn.execute(
            "SELECT br.*, b.box_number FROM borrowings br JOIN boxes b ON br.box_id = b.id WHERE br.status = ? ORDER BY br.created_at DESC",
            (status_filter,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT br.*, b.box_number FROM borrowings br JOIN boxes b ON br.box_id = b.id ORDER BY br.created_at DESC"
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # Desensitize for 查阅者
        if not can_view_sensitive(role):
            box = conn.execute("SELECT * FROM boxes WHERE id = ?", (d["box_id"],)).fetchone()
            if box:
                sensitive = conn.execute(
                    "SELECT 1 FROM files WHERE box_id = ? AND security_level IN ('秘密','机密') LIMIT 1",
                    (d["box_id"],),
                ).fetchone()
                if sensitive:
                    d["borrower"] = "***"
                    d["approved_by"] = "***"
        result.append(d)
    return result


def check_overdue_borrowings() -> list:
    """Check for overdue borrowings and create difference records.

    This is called by the background reminder task.
    """
    conn = db.get_conn()
    now = datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT br.*, b.box_number FROM borrowings br
           JOIN boxes b ON br.box_id = b.id
           WHERE br.status = '已批准' AND br.due_date < ?""",
        (now,),
    ).fetchall()

    created = []
    with db.transaction() as trans_conn:
        for br in rows:
            # Update status to 超期
            trans_conn.execute(
                "UPDATE borrowings SET status = '超期' WHERE id = ?", (br["id"],)
            )
            # Check if difference already exists
            existing = trans_conn.execute(
                "SELECT id FROM differences WHERE type = '借阅超期' AND box_id = ? AND status = '待处理'",
                (br["box_id"],),
            ).fetchone()
            if not existing:
                desc = f"借阅超期：盒 {br['box_number']} 应于 {br['due_date']} 归还，借阅人 {br['borrower']}"
                trans_conn.execute(
                    "INSERT INTO differences (type, box_id, description, status) VALUES ('借阅超期', ?, ?, '待处理')",
                    (br["box_id"], desc),
                )
                created.append(dict(br))
    return created


# ---------------------------------------------------------------------------
# 盘点附件 (inventory attachments)
# ---------------------------------------------------------------------------

def save_attachment(task_id: int, filename: str, content: bytes) -> str:
    """Save an inventory attachment to the local filesystem.

    Returns the saved file path.
    """
    attach_dir = db.get_attachment_dir()
    task_dir = os.path.join(attach_dir, f"task_{task_id}")
    os.makedirs(task_dir, exist_ok=True)
    # Sanitize filename
    safe_name = os.path.basename(filename)
    if not safe_name:
        safe_name = "attachment"
    path = os.path.join(task_dir, safe_name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def list_attachments(task_id: int) -> list:
    """List attachments for an inventory task."""
    attach_dir = db.get_attachment_dir()
    task_dir = os.path.join(attach_dir, f"task_{task_id}")
    if not os.path.isdir(task_dir):
        return []
    result = []
    for fname in os.listdir(task_dir):
        fpath = os.path.join(task_dir, fname)
        if os.path.isfile(fpath):
            result.append({
                "filename": fname,
                "size": os.path.getsize(fpath),
                "path": fpath,
            })
    return result
