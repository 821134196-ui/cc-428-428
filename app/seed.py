"""样例数据：虚构编号，无真实文件。

一次生成：岗位用户、全宗、案卷编目、装盒、入库绑定库位、一张移位单、
一次进行中的盘点（含少件差异）、若干借阅（含一笔超期）。
"""
from datetime import date, timedelta

from . import services as svc
from .security import ROLE_CLEARANCE

USERS = [
    ("admin", "赵主管", "admin"),
    ("cataloger", "钱编目", "cataloger"),
    ("keeper", "孙库管", "keeper"),
    ("reader", "李借阅", "reader"),
    ("auditor", "周审计", "auditor"),
]


def _u(conn, username):
    return dict(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())


def seed(conn):
    # 岗位用户
    for uname, dname, role in USERS:
        conn.execute(
            "INSERT OR IGNORE INTO users(username, display_name, role, clearance) VALUES(?,?,?,?)",
            (uname, dname, role, ROLE_CLEARANCE[role]),
        )
    admin = _u(conn, "admin")
    keeper = _u(conn, "keeper")
    reader = _u(conn, "reader")

    # 全宗
    fid = svc.create_fonds(conn, admin, "X001", "虚构市城建局", "演示全宗")

    # 案卷：2024 永久 12 条（其中两条秘密/机密），2024 定期30年 6 条，
    # 2023 永久 3 条（序号 1,2,4 —— 制造一个断档以演示缺卷切盒提示）
    for i in range(1, 13):
        level = "机密" if i == 7 else ("秘密" if i == 10 else "公开")
        svc.create_record(conn, admin, fid, 2024, "永久",
                          f"虚构市政项目案卷 #{i:02d}（虚构编号，无真实内容）", level)
    for i in range(1, 7):
        svc.create_record(conn, admin, fid, 2024, "定期30年",
                          f"虚构规划许可案卷 #{i:02d}", "内部")
    for i in (1, 2, 4):
        svc.create_record(conn, admin, fid, 2023, "永久",
                          f"虚构年度综合案卷 #{i:02d}（序号 {i}，断档演示）", "公开", seq=i)

    # 装盒
    svc.commit_packing(conn, admin, fid, 2024, "永久")    # → 2 盒（每盒 10 件）
    svc.commit_packing(conn, admin, fid, 2024, "定期30年")  # → 1 盒
    svc.commit_packing(conn, admin, fid, 2023, "永久")    # 断档 → 2 盒

    # 库位：KF1 库房 A/B 架各 3 层，KF2 库房 A 架 2 层
    slots = {}
    for shelf in ("A", "B"):
        for layer in (1, 2, 3):
            _, slot = svc.add_location(conn, admin, "KF1", shelf, layer, 1)
            slots[slot] = conn.execute(
                "SELECT id FROM locations WHERE slot_code=?", (slot,)).fetchone()["id"]
    for layer in (1, 2):
        _, slot = svc.add_location(conn, admin, "KF2", "A", layer, 1)
        slots[slot] = conn.execute(
            "SELECT id FROM locations WHERE slot_code=?", (slot,)).fetchone()["id"]

    boxes = svc.list_boxes(conn, admin)
    by_no = {b["box_no"]: b for b in boxes}

    # 入库：2024 永久两盒、定期30年一盒 → KF1；2023 两盒 → KF2
    layout = [
        ("X001-2024-Y-H001", "KF1-A-01"),
        ("X001-2024-Y-H002", "KF1-A-02"),
        ("X001-2024-D30-H001", "KF1-B-01"),
        ("X001-2023-Y-H001", "KF2-A-01"),
        ("X001-2023-Y-H002", "KF2-A-02"),
    ]
    for box_no, slot in layout:
        svc.inbound_box(conn, keeper, by_no[box_no]["id"], slots[slot])

    # 移位单：H002 从 KF1-A-02 调到 KF1-A-03（开单 → 出库 → 交接完成）
    _, move_no = svc.create_move(conn, keeper, by_no["X001-2024-Y-H002"]["id"],
                                 slots["KF1-A-03"], "腾位调架")
    order = conn.execute("SELECT id FROM move_orders WHERE order_no=?", (move_no,)).fetchone()
    svc.start_move(conn, keeper, order["id"])
    svc.receive_move(conn, keeper, order["id"])

    # 借阅：H001 一笔正常在借（未到期）
    loan_id, loan_no, _ = svc.apply_loan(
        conn, reader, by_no["X001-2024-Y-H001"]["id"],
        "冯研究", "虚构课题查阅", days=14, idem_key="demo-loan-1",
        borrower_org="虚构市规划院")
    svc.approve_loan(conn, admin, loan_id)
    svc.borrow_loan(conn, keeper, loan_id)

    # 一笔已经超期的借阅（直接构造历史单：借出 D30 盒，期限昨天）
    lid2, lno2, _ = svc.apply_loan(
        conn, reader, by_no["X001-2024-D30-H001"]["id"],
        "陈编研", "虚构史志编纂", days=1, idem_key="demo-loan-2")
    svc.approve_loan(conn, admin, lid2)
    svc.borrow_loan(conn, keeper, lid2)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    conn.execute("UPDATE loans SET due_date=? WHERE id=?", (yesterday, lid2))

    # 盘点任务：KF2 库房（两盒），登记 H001(2023) 少件，形成差异
    tid, task_no, n = svc.create_stocktake(conn, keeper, room_code="KF2", note="年终盘点样例")
    box_2023_h1 = by_no["X001-2023-Y-H001"]["id"]
    svc.count_item(conn, keeper, tid, box_2023_h1, "fewer_items",
                   note="样例：盒内实有 0 件演示少件", actual_count=0)
    box_2023_h2 = by_no["X001-2023-Y-H002"]["id"]
    svc.count_item(conn, keeper, tid, box_2023_h2, "label_damaged", note="样例：边角磨损")

    # 触发一次提醒扫描（产生超期差异 + 盘点冻结提醒）
    svc.scan_reminders(conn)

    return {
        "fonds": "X001",
        "boxes": [b["box_no"] for b in boxes],
        "stocktake": task_no,
        "loan_overdue": lno2,
    }
