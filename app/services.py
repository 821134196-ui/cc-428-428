"""业务服务层：全部领域规则集中于此，Web 层与测试共用。

关键规则：
  * 档号全局唯一，装盒按 全宗/年度/保管期限 分组、按序号连续切盒；
  * 入库才绑定库位；调架先开移位单，交接完成后库位才变更，中断产生差异；
  * 盘点任务一旦创建即冻结范围内库存（入库/移位/借出被拦截），确认后也不直接改库存；
  * 缺卷/错盒/标签损坏/盘点少件/移位中断/借阅超期 六类差异；
  * 借阅带幂等键，同一盒同时只允许一人借出成功。
"""
import sqlite3
import uuid
from datetime import datetime, date, timedelta

from . import config
from .security import (
    require, level_ok, LEVEL_ORDER, can_see_location, can_see_loan,
    mask_record,
)

RETENTION_CODES = {"永久": "Y", "定期30年": "D30", "定期10年": "D10"}
RETENTION_NAMES = {v: k for k, v in RETENTION_CODES.items()}


class BusinessError(Exception):
    """业务规则冲突（HTTP 层映射为 409/400）。"""


def _code(prefix):
    return f"{prefix}-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def _today():
    return date.today()


# ---------------------------------------------------------------- 基础档案

def create_fonds(conn, user, code, name, note=""):
    require("fonds.create", user)
    try:
        cur = conn.execute(
            "INSERT INTO fonds(code, name, note) VALUES(?,?,?)", (code, name, note)
        )
    except sqlite3.IntegrityError:
        raise BusinessError(f"全宗号 {code} 已存在")
    return cur.lastrowid


def list_fonds(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM fonds ORDER BY code")]


def create_record(conn, user, fonds_id, year, retention, title,
                  security_level="公开", archive_no=None, seq=None):
    """编目一条案卷。档号默认按 全宗-年度-期限-序号 规则生成，全局唯一。

    seq 通常由系统在同全宗/年度/期限内递增；显式传入仅用于补登历史断号。"""
    require("catalog.create", user)
    if retention not in RETENTION_CODES:
        raise BusinessError(f"保管期限非法：{retention}")
    if security_level not in LEVEL_ORDER:
        raise BusinessError(f"密级非法：{security_level}")
    fonds = conn.execute("SELECT * FROM fonds WHERE id=?", (fonds_id,)).fetchone()
    if not fonds:
        raise BusinessError("全宗不存在")
    if not level_ok(user["clearance"], security_level):
        raise BusinessError("本人密级权限不足以编目该密级档案")

    if seq is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq),0)+1 AS next_seq FROM records "
            "WHERE fonds_id=? AND year=? AND retention=?",
            (fonds_id, year, retention),
        ).fetchone()
        seq = row["next_seq"]
    else:
        seq = int(seq)
        dup = conn.execute(
            "SELECT 1 FROM records WHERE fonds_id=? AND year=? AND retention=? AND seq=?",
            (fonds_id, year, retention, seq),
        ).fetchone()
        if dup:
            raise BusinessError(f"序号 {seq} 在该全宗/年度/期限内已存在")
    no = archive_no or f"{fonds['code']}-{year}-{RETENTION_CODES[retention]}-{seq:04d}"
    try:
        cur = conn.execute(
            """INSERT INTO records(archive_no, fonds_id, year, retention, title,
                                   security_level, seq, status, created_at)
               VALUES(?,?,?,?,?,?,?, 'unboxed', ?)""",
            (no, fonds_id, year, retention, title, security_level, seq, datetime.now().isoformat(timespec="seconds")),
        )
    except sqlite3.IntegrityError:
        raise BusinessError(f"档号已存在：{no}（档号必须全局唯一）")
    return cur.lastrowid, no


def list_records(conn, user, unboxed_only=False):
    sql = (
        "SELECT r.*, f.code AS fonds_code FROM records r JOIN fonds f ON f.id=r.fonds_id"
    )
    if unboxed_only:
        sql += " WHERE r.box_id IS NULL"
    sql += " ORDER BY r.fonds_id, r.year, r.retention, r.seq"
    return [mask_record(r, user) for r in conn.execute(sql)]


# ---------------------------------------------------------------- 装盒

def packing_preview(conn, user, fonds_id=None):
    """按年度+保管期限生成装盒建议；只取未装盒案卷，盒内档号必须连续。

    返回 [{group, records, boxes:[{records:[...]}], gaps:[(a,b)...]}]。
    gaps 表示序号断档（疑似缺卷），断档处强制切盒。
    """
    rows = list_records(conn, user, unboxed_only=True)
    if fonds_id:
        rows = [r for r in rows if r["fonds_id"] == int(fonds_id)]
    groups = {}
    for r in rows:
        groups.setdefault((r["fonds_id"], r["year"], r["retention"]), []).append(r)

    result = []
    for (fid, year, ret), items in sorted(groups.items()):
        items.sort(key=lambda x: x["seq"])
        # 在断档处切分连续段
        segments, cur_seg, gaps = [], [items[0]], []
        for prev, nxt in zip(items, items[1:]):
            if nxt["seq"] == prev["seq"] + 1:
                cur_seg.append(nxt)
            else:
                gaps.append((prev["seq"], nxt["seq"]))
                segments.append(cur_seg)
                cur_seg = [nxt]
        segments.append(cur_seg)
        boxes = []
        for seg in segments:
            for i in range(0, len(seg), config.BOX_CAPACITY):
                boxes.append(seg[i:i + config.BOX_CAPACITY])
        f = conn.execute("SELECT code FROM fonds WHERE id=?", (fid,)).fetchone()
        result.append({
            "fonds_id": fid,
            "fonds_code": f["code"],
            "year": year,
            "retention": ret,
            "records": items,
            "boxes": boxes,
            "gaps": gaps,
        })
    return result


def commit_packing(conn, user, fonds_id, year, retention):
    """把某 全宗/年度/期限 分组的建议落盒：创建 boxes 并回写 records.box_id。"""
    require("packing.commit", user)
    plan = [g for g in packing_preview(conn, user, fonds_id)
            if g["year"] == int(year) and g["retention"] == retention]
    if not plan:
        raise BusinessError("该分组没有可装盒的案卷")
    g = plan[0]
    f = conn.execute("SELECT code FROM fonds WHERE id=?", (fonds_id,)).fetchone()
    existing = conn.execute(
        "SELECT COUNT(*) c FROM boxes WHERE fonds_id=? AND year=? AND retention=?",
        (fonds_id, year, retention),
    ).fetchone()["c"]
    created = []
    for idx, recs in enumerate(g["boxes"], start=existing + 1):
        box_no = f"{f['code']}-{year}-{RETENTION_CODES[retention]}-H{idx:03d}"
        level = max((r["security_level"] for r in recs),
                    key=lambda x: LEVEL_ORDER[x])
        cur = conn.execute(
            """INSERT INTO boxes(box_no, fonds_id, year, retention, security_level,
                                 status, packed_at)
               VALUES(?,?,?,?,?, 'packed', ?)""",
            (box_no, fonds_id, year, retention, level, datetime.now().isoformat(timespec="seconds")),
        )
        box_id = cur.lastrowid
        for box_seq, r in enumerate(recs, start=1):
            conn.execute(
                "UPDATE records SET box_id=?, box_seq=?, status='boxed' WHERE id=?",
                (box_id, box_seq, r["id"]),
            )
        created.append({"id": box_id, "box_no": box_no, "count": len(recs)})
    return created


def list_boxes(conn, user, with_records=False):
    rows = conn.execute(
        """SELECT b.*, l.slot_code, f.code AS fonds_code
           FROM boxes b JOIN fonds f ON f.id=b.fonds_id
           LEFT JOIN locations l ON l.id=b.location_id
           ORDER BY b.box_no"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if not can_see_location(user, d["security_level"]):
            d["slot_code"] = None
            d["location_masked"] = True
        else:
            d["location_masked"] = False
        if with_records:
            recs = conn.execute(
                "SELECT * FROM records WHERE box_id=? ORDER BY box_seq", (d["id"],)
            )
            d["records"] = [mask_record(x, user) for x in recs]
        out.append(d)
    return out


# ---------------------------------------------------------------- 库位

def add_location(conn, user, room_code, shelf_code, layer_no, capacity=1, note=""):
    require("inbound", user)
    slot = f"{room_code}-{shelf_code}-{layer_no:02d}"
    try:
        cur = conn.execute(
            "INSERT INTO locations(room_code, shelf_code, layer_no, slot_code, capacity, note)"
            " VALUES(?,?,?,?,?,?)",
            (room_code, shelf_code, int(layer_no), slot, int(capacity), note),
        )
    except sqlite3.IntegrityError:
        raise BusinessError(f"库位已存在：{slot}")
    return cur.lastrowid, slot


def list_locations(conn):
    return [dict(r) for r in conn.execute(
        """SELECT l.*, COUNT(b.id) AS used
           FROM locations l LEFT JOIN boxes b ON b.location_id=l.id
           GROUP BY l.id ORDER BY l.room_code, l.shelf_code, l.layer_no""")]


def inbound_box(conn, user, box_id, location_id):
    """入库：绑定库房/货架/层位。已冻结或库位满载时拒绝。"""
    require("inbound", user)
    box = conn.execute("SELECT * FROM boxes WHERE id=?", (box_id,)).fetchone()
    if not box:
        raise BusinessError("盒不存在")
    if box["status"] not in ("packed",):
        raise BusinessError(f"盒当前状态 {box['status']} 不能入库（已入库/在借/移位中的盒请走对应流程）")
    _assert_not_frozen(conn, box_id)
    loc = conn.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()
    if not loc:
        raise BusinessError("库位不存在")
    used = conn.execute(
        "SELECT COUNT(*) c FROM boxes WHERE location_id=?", (location_id,)
    ).fetchone()["c"]
    if used >= loc["capacity"]:
        raise BusinessError(f"库位 {loc['slot_code']} 已满（容量 {loc['capacity']}）")
    conn.execute(
        "UPDATE boxes SET status='inbound', location_id=?, inbound_at=?, inbound_by=? WHERE id=?",
        (location_id, datetime.now().isoformat(timespec="seconds"), user["display_name"], box_id),
    )
    conn.execute(
        "UPDATE records SET status='inbound' WHERE box_id=? AND status='boxed'", (box_id,)
    )
    return loc["slot_code"]


# ---------------------------------------------------------------- 移位（调架）

def _assert_not_frozen(conn, box_id):
    row = conn.execute(
        """SELECT st.task_no FROM stock_take_items sti
           JOIN stock_takes st ON st.id=sti.task_id
           WHERE sti.box_id=? AND st.status='open'""",
        (box_id,),
    ).fetchone()
    if row:
        raise BusinessError(f"盘点冻结中（任务 {row['task_no']}），禁止改变该盒库存")


def create_move(conn, user, box_id, to_loc_id, note=""):
    """调架先开单：仅生成移位单，不动库位。"""
    require("move.create", user)
    box = conn.execute("SELECT * FROM boxes WHERE id=?", (box_id,)).fetchone()
    if not box:
        raise BusinessError("盒不存在")
    if not box["location_id"] and box["status"] != "moving":
        raise BusinessError("盒尚未入库，不能调架")
    _assert_not_frozen(conn, box_id)
    active = conn.execute(
        "SELECT id FROM move_orders WHERE box_id=? AND status IN ('draft','in_transit')",
        (box_id,),
    ).fetchone()
    if active:
        raise BusinessError("该盒已有未完成的移位单")
    loc = conn.execute("SELECT * FROM locations WHERE id=?", (to_loc_id,)).fetchone()
    if not loc:
        raise BusinessError("目标库位不存在")
    used = conn.execute(
        "SELECT COUNT(*) c FROM boxes WHERE location_id=? AND id<>?",
        (to_loc_id, box_id),
    ).fetchone()["c"]
    if used >= loc["capacity"]:
        raise BusinessError(f"目标库位 {loc['slot_code']} 已满")
    no = _code("YW")
    cur = conn.execute(
        """INSERT INTO move_orders(order_no, box_id, from_loc_id, to_loc_id,
                                   status, applicant, created_at, note)
           VALUES(?,?,?,?, 'draft', ?, ?, ?)""",
        (no, box_id, box["location_id"], to_loc_id, user["display_name"],
         datetime.now().isoformat(timespec="seconds"), note),
    )
    return cur.lastrowid, no


def start_move(conn, user, order_id, handler=None):
    """出库搬运：draft → in_transit，盒标记 moving。"""
    require("move.handle", user)
    o = conn.execute("SELECT * FROM move_orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        raise BusinessError("移位单不存在")
    if o["status"] != "draft":
        raise BusinessError(f"移位单状态 {o['status']}，不能出库")
    _assert_not_frozen(conn, o["box_id"])
    conn.execute(
        "UPDATE move_orders SET status='in_transit', handler=?, moved_at=? WHERE id=?",
        (handler or user["display_name"], datetime.now().isoformat(timespec="seconds"), order_id),
    )
    conn.execute("UPDATE boxes SET status='moving' WHERE id=?", (o["box_id"],))


def receive_move(conn, user, order_id):
    """接收交接：in_transit → done，库位此刻才变更；关联的中断差异自动闭环。"""
    require("move.handle", user)
    o = conn.execute("SELECT * FROM move_orders WHERE id=?", (order_id,)).fetchone()
    if not o:
        raise BusinessError("移位单不存在")
    if o["status"] != "in_transit":
        raise BusinessError(f"移位单状态 {o['status']}，不能交接")
    loc = conn.execute("SELECT * FROM locations WHERE id=?", (o["to_loc_id"],)).fetchone()
    used = conn.execute(
        "SELECT COUNT(*) c FROM boxes WHERE location_id=? AND id<>?",
        (o["to_loc_id"], o["box_id"]),
    ).fetchone()["c"]
    if used >= loc["capacity"]:
        raise BusinessError(f"目标库位 {loc['slot_code']} 已满，无法交接")
    conn.execute(
        """UPDATE move_orders SET status='done', receiver=?, received_at=? WHERE id=?""",
        (user["display_name"], datetime.now().isoformat(timespec="seconds"), order_id),
    )
    conn.execute(
        "UPDATE boxes SET status='inbound', location_id=? WHERE id=?",
        (o["to_loc_id"], o["box_id"]),
    )
    conn.execute(
        """UPDATE discrepancies SET status='resolved', resolved_at=?, resolved_by=?,
                  resolution='后续移位单 '||?||' 已完成交接，盒落位 '||?
           WHERE type='move_interrupted' AND box_id=? AND status='open'""",
        (datetime.now().isoformat(timespec="seconds"), user["display_name"],
         o["order_no"], loc["slot_code"], o["box_id"]),
    )


def interrupt_move(conn, user, order_id, reason=""):
    """移位中断：in_transit → interrupted，盒留在原库位，生成差异记录。"""
    require("move.handle", user)
    o = conn.execute("SELECT * FROM move_orders WHERE id=?", (order_id,)).fetchone()
    if not o or o["status"] != "in_transit":
        raise BusinessError("仅在途移位单可以中断")
    conn.execute(
        "UPDATE move_orders SET status='interrupted', note=? WHERE id=?",
        ((o["note"] + " | 中断原因：" + reason).strip(" |"), order_id),
    )
    # 盒仍在原库位，恢复 inbound；若原库位为空（首次入库途中）保持 moving 等待新移位单
    if o["from_loc_id"]:
        conn.execute(
            "UPDATE boxes SET status='inbound', location_id=? WHERE id=?",
            (o["from_loc_id"], o["box_id"]),
        )
    _add_discrepancy(
        conn, "move_interrupted", box_id=o["box_id"], location_id=o["from_loc_id"],
        move_order_id=o["id"],
        title=f"移位中断：{o['order_no']}",
        detail=reason or "搬运过程中断，盒未到达目标库位，已退回原库位",
    )


def list_moves(conn):
    return [dict(r) for r in conn.execute(
        """SELECT mo.*, b.box_no, l1.slot_code AS from_slot, l2.slot_code AS to_slot
           FROM move_orders mo
           JOIN boxes b ON b.id=mo.box_id
           LEFT JOIN locations l1 ON l1.id=mo.from_loc_id
           LEFT JOIN locations l2 ON l2.id=mo.to_loc_id
           ORDER BY mo.id DESC""")]


# ---------------------------------------------------------------- 盘点

def create_stocktake(conn, user, room_code=None, shelf_code=None, note=""):
    """创建盘点任务并按范围快照盒清单；任务 open 期间范围内库存冻结。"""
    require("stocktake.create", user)
    q = (
        """SELECT b.id FROM boxes b JOIN locations l ON l.id=b.location_id
           WHERE b.status='inbound'"""
    )
    args = []
    if room_code:
        q += " AND l.room_code=?"
        args.append(room_code)
    if shelf_code:
        q += " AND l.shelf_code=?"
        args.append(shelf_code)
    box_ids = [r["id"] for r in conn.execute(q, args)]
    if not box_ids:
        raise BusinessError("盘点范围内没有在库盒")
    no = _code("PD")
    cur = conn.execute(
        """INSERT INTO stock_takes(task_no, scope_room, scope_shelf, status,
                                   created_by, created_at, note)
           VALUES(?,?,?, 'open', ?, ?, ?)""",
        (no, room_code or "全部库房", shelf_code, user["display_name"],
         datetime.now().isoformat(timespec="seconds"), note),
    )
    tid = cur.lastrowid
    for bid in box_ids:
        loc = conn.execute("SELECT location_id FROM boxes WHERE id=?", (bid,)).fetchone()
        conn.execute(
            "INSERT INTO stock_take_items(task_id, box_id, expected_loc_id) VALUES(?,?,?)",
            (tid, bid, loc["location_id"]),
        )
    return tid, no, len(box_ids)


def _stocktake_box(conn, tid, box_id):
    row = conn.execute(
        "SELECT b.*, l.slot_code FROM boxes b LEFT JOIN locations l ON l.id=b.location_id "
        "WHERE b.id=?", (box_id,)).fetchone()
    recs = conn.execute(
        "SELECT * FROM records WHERE box_id=? ORDER BY box_seq", (box_id,)
    )
    return dict(row), [dict(r) for r in recs]


def stocktake_detail(conn, user, task_id):
    task = conn.execute("SELECT * FROM stock_takes WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise BusinessError("盘点任务不存在")
    items = []
    rows = conn.execute(
        """SELECT sti.*, b.box_no, b.security_level, le.slot_code AS expected_slot,
                  la.slot_code AS actual_slot
           FROM stock_take_items sti
           JOIN boxes b ON b.id=sti.box_id
           LEFT JOIN locations le ON le.id=sti.expected_loc_id
           LEFT JOIN locations la ON la.id=sti.actual_loc_id
           WHERE sti.task_id=?
           ORDER BY le.slot_code, b.box_no""", (task_id,)).fetchall()
    for r in rows:
        d = dict(r)
        if not can_see_location(user, d["security_level"]):
            d["expected_slot"] = "●●●●（无权查看）"
            d["actual_slot"] = "●●●●（无权查看）"
        items.append(d)
    return dict(task), items


def count_item(conn, user, task_id, box_id, result, note="",
               actual_count=None, actual_loc_id=None, missing_archive_nos=""):
    """登记一盒的清点结果，并按结果生成差异（缺卷/错盒/标签损坏/少件）。"""
    require("stocktake.count", user)
    task = conn.execute("SELECT * FROM stock_takes WHERE id=?", (task_id,)).fetchone()
    if not task or task["status"] != "open":
        raise BusinessError("盘点任务不存在或已结束（冻结解除后不能补录清点）")
    it = conn.execute(
        "SELECT * FROM stock_take_items WHERE task_id=? AND box_id=?",
        (task_id, box_id),
    ).fetchone()
    if not it:
        raise BusinessError("该盒不在本盘点任务范围内")
    valid = {"present", "missing", "wrong_box", "label_damaged", "fewer_items"}
    if result not in valid:
        raise BusinessError("清点结果非法")

    box, recs = _stocktake_box(conn, task_id, box_id)
    conn.execute(
        """UPDATE stock_take_items SET result=?, actual_loc_id=?, actual_count=?,
                  counted_by=?, counted_at=?, note=?
           WHERE id=?""",
        (result, actual_loc_id, actual_count, user["display_name"],
         datetime.now().isoformat(timespec="seconds"), note, it["id"]),
    )

    expected_count = len(recs)
    if result == "missing":
        # 整盒不在：盒内每件档号均按缺卷登记
        for rec in recs:
            conn.execute("UPDATE records SET status='missing' WHERE id=?", (rec["id"],))
            _add_discrepancy(
                conn, "missing_record", task_id=task_id, box_id=box_id, record_id=rec["id"],
                location_id=it["expected_loc_id"],
                title=f"缺卷（整盒缺失）：{rec['archive_no']}",
                detail=f"盒 {box['box_no']} 在库位未找到，盒内档号 {rec['archive_no']} 按缺卷登记。{note}",
            )
    elif result == "wrong_box":
        _add_discrepancy(
            conn, "wrong_box", task_id=task_id, box_id=box_id,
            location_id=actual_loc_id or it["expected_loc_id"],
            title=f"错盒：{box['box_no']}",
            detail=f"库位实物与登记盒号不符。{note}",
        )
    elif result == "label_damaged":
        _add_discrepancy(
            conn, "label_damaged", task_id=task_id, box_id=box_id,
            location_id=it["expected_loc_id"],
            title=f"标签损坏：{box['box_no']}",
            detail=f"盒/档号标签损坏需重制。{note}",
        )
    elif result == "fewer_items":
        n = int(actual_count) if actual_count is not None else expected_count - 1
        if n >= expected_count:
            raise BusinessError("少件件数必须小于盒内登记件数")
        _add_discrepancy(
            conn, "fewer_items", task_id=task_id, box_id=box_id,
            location_id=it["expected_loc_id"],
            title=f"盘点少件：{box['box_no']}",
            detail=f"登记 {expected_count} 件，实有 {n} 件，少 {expected_count - n} 件。{note}",
        )
    # 缺卷：逐档号登记（档号在盒内应存在但实物缺失）
    for raw in [x.strip() for x in (missing_archive_nos or "").replace("，", ",").split(",") if x.strip()]:
        rec = conn.execute("SELECT * FROM records WHERE archive_no=?", (raw,)).fetchone()
        if not rec:
            raise BusinessError(f"缺卷档号未编目：{raw}")
        if rec["box_id"] != box_id:
            raise BusinessError(f"档号 {raw} 不属于盒 {box['box_no']}")
        conn.execute("UPDATE records SET status='missing' WHERE id=?", (rec["id"],))
        _add_discrepancy(
            conn, "missing_record", task_id=task_id, box_id=box_id, record_id=rec["id"],
            location_id=it["expected_loc_id"],
            title=f"缺卷：{raw}",
            detail=f"盒 {box['box_no']} 内档号 {raw} 实物缺失。{note}",
        )


def confirm_stocktake(conn, user, task_id):
    """确认盘点结果：只解除冻结、固化结论，绝不直接修改库存。"""
    require("stocktake.confirm", user)
    task = conn.execute("SELECT * FROM stock_takes WHERE id=?", (task_id,)).fetchone()
    if not task or task["status"] != "open":
        raise BusinessError("盘点任务状态不允许确认")
    uncounted = conn.execute(
        "SELECT COUNT(*) c FROM stock_take_items WHERE task_id=? AND result IS NULL",
        (task_id,),
    ).fetchone()["c"]
    if uncounted:
        raise BusinessError(f"还有 {uncounted} 盒未清点，不能确认")
    conn.execute(
        "UPDATE stock_takes SET status='confirmed', confirmed_at=?, confirmed_by=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), user["display_name"], task_id),
    )


def list_stocktakes(conn):
    rows = conn.execute(
        """SELECT st.*,
                  (SELECT COUNT(*) FROM stock_take_items WHERE task_id=st.id AND result IS NULL) AS pending,
                  (SELECT COUNT(*) FROM stock_take_items WHERE task_id=st.id) AS total,
                  (SELECT COUNT(*) FROM discrepancies WHERE task_id=st.id) AS diffs
           FROM stock_takes st
           ORDER BY st.id DESC""").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 差异

def _add_discrepancy(conn, dtype, title, detail="", task_id=None, box_id=None,
                     record_id=None, location_id=None, move_order_id=None, loan_id=None):
    code = _code("CY")
    cur = conn.execute(
        """INSERT INTO discrepancies(code, type, task_id, box_id, record_id, location_id,
                                     move_order_id, loan_id, title, detail, status, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,'open',?)""",
        (code, dtype, task_id, box_id, record_id, location_id, move_order_id,
         loan_id, title, detail, datetime.now().isoformat(timespec="seconds")),
    )
    return cur.lastrowid, code


DIFF_TYPE_NAMES = {
    "missing_record": "缺卷",
    "wrong_box": "错盒",
    "label_damaged": "标签损坏",
    "fewer_items": "盘点少件",
    "move_interrupted": "移位中断",
    "loan_overdue": "借阅超期",
}


def list_discrepancies(conn, user, group_by_location=False, status=None):
    q = (
        """SELECT d.*, l.slot_code, b.box_no, r.archive_no, st.task_no
           FROM discrepancies d
           LEFT JOIN locations l ON l.id=d.location_id
           LEFT JOIN boxes b ON b.id=d.box_id
           LEFT JOIN records r ON r.id=d.record_id
           LEFT JOIN stock_takes st ON st.id=d.task_id"""
    )
    where, args = [], []
    if status:
        where.append("d.status=?")
        args.append(status)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY l.slot_code IS NULL, l.slot_code, d.id"
    rows = []
    for r in conn.execute(q, args):
        d = dict(r)
        if d.get("security_level") is None and d["box_id"]:
            lv = conn.execute("SELECT security_level FROM boxes WHERE id=?", (d["box_id"],)).fetchone()
            d["security_level"] = lv["security_level"] if lv else "公开"
        else:
            d.setdefault("security_level", "公开")
        if not can_see_location(user, d["security_level"]):
            d["slot_code"] = "●●●●（无权查看）"
        rows.append(d)
    if group_by_location:
        grouped = {}
        for d in rows:
            grouped.setdefault(d["slot_code"] or "无库位", []).append(d)
        return [{"slot_code": k, "items": v} for k, v in grouped.items()]
    return rows


def resolve_discrepancy(conn, user, diff_id, resolution):
    """普通处理：登记处理结论。"""
    require("discrepancy.resolve", user)
    if not resolution.strip():
        raise BusinessError("处理说明不能为空")
    d = conn.execute("SELECT * FROM discrepancies WHERE id=?", (diff_id,)).fetchone()
    if not d:
        raise BusinessError("差异不存在")
    if d["status"] != "open":
        raise BusinessError("差异已处理，不能重复处理")
    conn.execute(
        "UPDATE discrepancies SET status='resolved', resolution=?, resolved_at=?, resolved_by=? WHERE id=?",
        (resolution, datetime.now().isoformat(timespec="seconds"), user["display_name"], diff_id),
    )


def supplement_discrepancy(conn, user, diff_id, basis):
    """补录：必须注明依据，且不直接改动库存库位，仅形成可审计记录。"""
    require("discrepancy.supplement", user)
    if not basis.strip():
        raise BusinessError("补录必须注明依据（来源凭证/审批单号等）")
    d = conn.execute("SELECT * FROM discrepancies WHERE id=?", (diff_id,)).fetchone()
    if not d:
        raise BusinessError("差异不存在")
    if d["status"] != "open":
        raise BusinessError("差异已处理，不能重复补录")
    conn.execute(
        "UPDATE discrepancies SET status='supplemented', basis=?, resolved_at=?, resolved_by=? WHERE id=?",
        (basis.strip(), datetime.now().isoformat(timespec="seconds"), user["display_name"], diff_id),
    )


# ---------------------------------------------------------------- 借阅

def apply_loan(conn, user, box_id, borrower, purpose, days=14,
               idem_key=None, borrower_org=""):
    """申请借阅。idem_key 相同的重复提交返回原单（幂等）。"""
    require("loan.apply", user)
    if idem_key:
        old = conn.execute("SELECT * FROM loans WHERE idem_key=?", (idem_key,)).fetchone()
        if old:
            return old["id"], old["loan_no"], True
    box = conn.execute("SELECT * FROM boxes WHERE id=?", (box_id,)).fetchone()
    if not box:
        raise BusinessError("盒不存在")
    if box["status"] == "moving":
        raise BusinessError("盒在移位途中，不能借阅")
    # 已被借出的盒不能重复申请；待审批/已批准申请允许多人并存，
    # 真正的“同一盒只允许一人借出”在出库时以原子条件更新裁决
    held = conn.execute(
        "SELECT id FROM loans WHERE box_id=? AND status='borrowed'",
        (box_id,),
    ).fetchone()
    if held:
        raise BusinessError("该盒已被借出，尚未归还，不能重复申请")
    no = _code("JY")
    due = (_today() + timedelta(days=int(days))).isoformat()
    cur = conn.execute(
        """INSERT INTO loans(loan_no, idem_key, box_id, borrower, borrower_org, purpose,
                             status, applied_at, due_date)
           VALUES(?,?,?,?,?,?,'pending',?,?)""",
        (no, idem_key, box_id, borrower, borrower_org, purpose,
         datetime.now().isoformat(timespec="seconds"), due),
    )
    return cur.lastrowid, no, False


def approve_loan(conn, user, loan_id):
    require("loan.approve", user)
    n = conn.execute(
        """UPDATE loans SET status='approved', approved_by=?, approved_at=?
           WHERE id=? AND status='pending'""",
        (user["display_name"], datetime.now().isoformat(timespec="seconds"), loan_id),
    ).rowcount
    if not n:
        raise BusinessError("借阅单不存在或已审批")


def reject_loan(conn, user, loan_id, reason):
    require("loan.approve", user)
    n = conn.execute(
        "UPDATE loans SET status='rejected', reject_reason=? WHERE id=? AND status='pending'",
        (reason, loan_id),
    ).rowcount
    if not n:
        raise BusinessError("借阅单不存在或已审批")


def borrow_loan(conn, user, loan_id):
    """出库借出：条件更新保证同一盒两人同时借出只有一人成功。"""
    require("loan.borrow", user)
    loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    if not loan:
        raise BusinessError("借阅单不存在")
    if loan["status"] != "approved":
        raise BusinessError(f"借阅单状态 {loan['status']}，不能借出")
    _assert_not_frozen(conn, loan["box_id"])
    # 条件更新：不存在其他已借出该盒的借阅时才能成功（原子）。
    # SQLite 写锁使并发 UPDATE 串行：先执行者把自己置为 borrowed，
    # 后执行者的 NOT EXISTS 立即不成立 → 0 行，同一盒只有一人成功。
    cur = conn.execute(
        """UPDATE loans SET status='borrowed', borrowed_at=?
           WHERE id=? AND status='approved'
             AND NOT EXISTS (
                 SELECT 1 FROM loans l2
                 WHERE l2.box_id=loans.box_id
                   AND l2.id<>loans.id
                   AND l2.status='borrowed'
             )""",
        (datetime.now().isoformat(timespec="seconds"), loan_id),
    )
    if not cur.rowcount:
        raise BusinessError("借出失败：该盒已被另一人借出（同一盒只允许一人成功）")
    conn.execute("UPDATE boxes SET status='loaned' WHERE id=?", (loan["box_id"],))
    conn.execute("UPDATE records SET status='loaned' WHERE box_id=?", (loan["box_id"],))


def return_loan(conn, user, loan_id):
    require("loan.return", user)
    loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    if not loan or loan["status"] != "borrowed":
        raise BusinessError("该借阅单未在借出状态")
    conn.execute(
        "UPDATE loans SET status='returned', returned_at=? WHERE id=?",
        (datetime.now().isoformat(timespec="seconds"), loan_id),
    )
    conn.execute("UPDATE boxes SET status='inbound' WHERE id=?", (loan["box_id"],))
    conn.execute(
        "UPDATE records SET status='inbound' WHERE box_id=? AND status='loaned'",
        (loan["box_id"],),
    )
    # 归还即闭环超期差异
    conn.execute(
        "UPDATE discrepancies SET status='resolved', resolved_at=?, resolved_by=?,"
        " resolution='已归还' WHERE loan_id=? AND status='open'",
        (datetime.now().isoformat(timespec="seconds"), user["display_name"], loan_id),
    )


def list_loans(conn, user):
    rows = conn.execute(
        """SELECT ln.*, b.box_no, b.security_level FROM loans ln
           JOIN boxes b ON b.id=ln.box_id ORDER BY ln.id DESC"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if not can_see_loan(user, d["security_level"]):
            for f in ("borrower", "borrower_org", "purpose", "due_date"):
                d[f] = "●●●●（无权查看）"
            d["loan_masked"] = True
        else:
            d["loan_masked"] = False
        out.append(d)
    return out


# ---------------------------------------------------------------- 提醒 / 超期扫描

def scan_reminders(conn):
    """进程内周期任务：生成到期提醒与借阅超期差异。返回本次新增条数。"""
    today = _today().isoformat()
    soon = (_today() + timedelta(days=config.DUE_SOON_DAYS)).isoformat()
    added = 0
    for loan in conn.execute(
        "SELECT * FROM loans WHERE status='borrowed'"
    ).fetchall():
        overdue = loan["due_date"] < today
        due_soon = (not overdue) and loan["due_date"] <= soon
        if overdue:
            # 超期差异（幂等：一盒一在借单至多一条 open 超期差异）
            exists = conn.execute(
                "SELECT 1 FROM discrepancies WHERE type='loan_overdue' AND loan_id=? AND status='open'",
                (loan["id"],),
            ).fetchone()
            if not exists:
                box_loc = conn.execute(
                    "SELECT location_id FROM boxes WHERE id=?", (loan["box_id"],)
                ).fetchone()
                _add_discrepancy(
                    conn, "loan_overdue", box_id=loan["box_id"],
                    location_id=box_loc["location_id"] if box_loc else None,
                    loan_id=loan["id"],
                    title=f"借阅超期：{loan['loan_no']}",
                    detail=f"借阅人 {loan['borrower']}，归还期限 {loan['due_date']}，已超期",
                )
            msg = f"借阅单 {loan['loan_no']} 已超期（应于 {loan['due_date']} 归还）"
            kind = "overdue"
        elif due_soon:
            msg = f"借阅单 {loan['loan_no']} 将于 {loan['due_date']} 到期"
            kind = "due_soon"
        else:
            continue
        try:
            conn.execute(
                "INSERT INTO reminders(kind, ref_type, ref_id, message, created_at)"
                " VALUES(?,?,?,?,?)",
                (kind, "loan", loan["id"], msg, datetime.now().isoformat(timespec="seconds")),
            )
            added += 1
        except sqlite3.IntegrityError:
            pass
    for st in conn.execute("SELECT * FROM stock_takes WHERE status='open'").fetchall():
        try:
            conn.execute(
                "INSERT INTO reminders(kind, ref_type, ref_id, message, created_at)"
                " VALUES('stocktake_open','stocktake',?, ?, ?)",
                (st["id"], f"盘点任务 {st['task_no']} 进行中，范围内库存已冻结",
                 datetime.now().isoformat(timespec="seconds")),
            )
            added += 1
        except sqlite3.IntegrityError:
            pass
    return added


def list_reminders(conn, limit=20):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM reminders ORDER BY id DESC LIMIT ?", (limit,))]
