"""盘点冻结与差异：建单即冻结、确认后不改库存、补录必须注明依据、六类差异聚合。"""
from app import services as svc
from app.services import BusinessError
from tests.base import BaseCase


class StocktakeFreezeTest(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin, self.keeper = self.users["admin"], self.users["keeper"]
        self.auditor = self.users["auditor"]
        self.reader = self.users["reader"]
        _, self.locs = self.make_fonds_boxes([(2025, "永久", "公开", 12)])
        self.h1 = self.locs["T01-2025-Y-H001"]
        self.h2 = self.locs["T01-2025-Y-H002"]
        self.free_slot = self._new_location("KF2", "A", 1)
        self.tid, self.tno, _ = svc.create_stocktake(
            self.conn, self.keeper, room_code="KF1", note="冻结测试")

    def _new_location(self, room, shelf, layer):
        _, slot = svc.add_location(self.conn, self.admin, room, shelf, layer, 1)
        return self.conn.execute(
            "SELECT id FROM locations WHERE slot_code=?", (slot,)).fetchone()["id"]

    def _box(self, bid):
        return dict(self.conn.execute("SELECT * FROM boxes WHERE id=?", (bid,)).fetchone())

    # ---- 冻结 ----
    def test_inbound_blocked_while_frozen(self):
        # 再造一个已装盒未入库的盒，试图入到 KF1（冻结范围）→ 拒绝；入到 KF2 也因盒在范围?
        # h1/h2 都在 KF1 且已入库，直接验证移位与借出被冻结
        with self.assertRaises(BusinessError):
            svc.create_move(self.conn, self.keeper, self.h1[0], self.free_slot)

    def test_borrow_blocked_while_frozen(self):
        # 冻结期间申请与审批可以走（不改变库存），出库借出被冻结拦截
        lid, _, _ = svc.apply_loan(self.conn, self.reader, self.h1[0], "冯研究", "课题")
        svc.approve_loan(self.conn, self.admin, lid)
        with self.assertRaises(BusinessError):
            svc.borrow_loan(self.conn, self.keeper, lid)
        # 盒仍在库
        self.assertEqual(self._box(self.h1[0])["status"], "inbound")

    def test_freeze_scope_is_limited(self):
        # KF2 不在盘点范围：新盒可以入库到 KF2
        admin = self.admin
        fid2 = svc.create_fonds(self.conn, admin, "T02", "他处全宗", "")
        svc.create_record(self.conn, admin, fid2, 2025, "永久", "x")
        svc.commit_packing(self.conn, admin, fid2, 2025, "永久")
        b = svc.list_boxes(self.conn, admin)[-1]
        svc.inbound_box(self.conn, self.keeper, b["id"], self.free_slot)  # 不抛异常

    # ---- 清点与差异 ----
    def test_fewer_items_creates_discrepancy(self):
        svc.count_item(self.conn, self.keeper, self.tid, self.h1[0],
                       "fewer_items", actual_count=8)
        d = self.conn.execute(
            "SELECT * FROM discrepancies WHERE type='fewer_items' AND box_id=?",
            (self.h1[0],)).fetchone()
        self.assertIsNotNone(d)
        self.assertEqual(d["location_id"], self.h1[1])

    def test_fewer_items_count_validation(self):
        with self.assertRaises(BusinessError):
            svc.count_item(self.conn, self.keeper, self.tid, self.h1[0],
                           "fewer_items", actual_count=10)  # 登记 10 件，不能报"少件"

    def test_missing_record_creates_discrepancy_and_marks_record(self):
        rec = self.conn.execute(
            "SELECT archive_no FROM records WHERE box_id=? ORDER BY box_seq LIMIT 1",
            (self.h1[0],)).fetchone()
        svc.count_item(self.conn, self.keeper, self.tid, self.h1[0],
                       "present", missing_archive_nos=rec["archive_no"])
        d = self.conn.execute(
            "SELECT * FROM discrepancies WHERE type='missing_record'").fetchone()
        self.assertIsNotNone(d)
        st = self.conn.execute(
            "SELECT status FROM records WHERE archive_no=?", (rec["archive_no"],)).fetchone()
        self.assertEqual(st["status"], "missing")

    def test_whole_box_missing_marks_every_record(self):
        svc.count_item(self.conn, self.keeper, self.tid, self.h1[0], "missing",
                       note="库位无实物")
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM discrepancies WHERE type='missing_record' AND box_id=?",
            (self.h1[0],)).fetchone()["c"]
        self.assertEqual(n, 10)  # 盒内 10 件全部按缺卷登记
        miss = self.conn.execute(
            "SELECT COUNT(*) c FROM records WHERE box_id=? AND status='missing'",
            (self.h1[0],)).fetchone()["c"]
        self.assertEqual(miss, 10)

    def test_wrong_box_and_label_damaged(self):
        svc.count_item(self.conn, self.keeper, self.tid, self.h1[0], "wrong_box", note="实物不符")
        svc.count_item(self.conn, self.keeper, self.tid, self.h2[0], "label_damaged")
        types = {r["type"] for r in self.conn.execute(
            "SELECT type FROM discrepancies WHERE task_id=?", (self.tid,))}
        self.assertIn("wrong_box", types)
        self.assertIn("label_damaged", types)

    def test_confirm_requires_all_counted(self):
        with self.assertRaises(BusinessError):
            svc.confirm_stocktake(self.conn, self.auditor, self.tid)

    def test_confirm_does_not_change_stock(self):
        for it in self.conn.execute(
            "SELECT box_id FROM stock_take_items WHERE task_id=?", (self.tid,)):
            svc.count_item(self.conn, self.keeper, self.tid, it["box_id"], "fewer_items",
                           actual_count=1)
        svc.confirm_stocktake(self.conn, self.auditor, self.tid)
        # 库存不变：盒仍在原库位、在库、10 件仍登记在册
        b = self._box(self.h1[0])
        self.assertEqual(b["status"], "inbound")
        self.assertEqual(b["location_id"], self.h1[1])
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM records WHERE box_id=?", (self.h1[0],)).fetchone()["c"]
        self.assertEqual(n, 10)

    def test_freeze_released_after_confirm(self):
        for it in self.conn.execute(
            "SELECT box_id FROM stock_take_items WHERE task_id=?", (self.tid,)):
            svc.count_item(self.conn, self.keeper, self.tid, it["box_id"], "present")
        svc.confirm_stocktake(self.conn, self.auditor, self.tid)
        # 冻结解除：移位可以开单
        _, no = svc.create_move(self.conn, self.keeper, self.h1[0], self.free_slot)
        self.assertTrue(no.startswith("YW-"))

    def test_count_after_confirm_rejected(self):
        for it in self.conn.execute(
            "SELECT box_id FROM stock_take_items WHERE task_id=?", (self.tid,)):
            svc.count_item(self.conn, self.keeper, self.tid, it["box_id"], "present")
        svc.confirm_stocktake(self.conn, self.auditor, self.tid)
        with self.assertRaises(BusinessError):
            svc.count_item(self.conn, self.keeper, self.tid, self.h1[0], "present")

    # ---- 补录依据 ----
    def test_supplement_requires_basis(self):
        svc.count_item(self.conn, self.keeper, self.tid, self.h1[0],
                       "fewer_items", actual_count=1)
        d = self.conn.execute(
            "SELECT id FROM discrepancies WHERE type='fewer_items'").fetchone()
        with self.assertRaises(BusinessError):
            svc.supplement_discrepancy(self.conn, self.auditor, d["id"], "  ")
        svc.supplement_discrepancy(self.conn, self.auditor, d["id"],
                                   "凭档案移交清册 QX-2025-009 补登")
        row = self.conn.execute("SELECT * FROM discrepancies WHERE id=?", (d["id"],)).fetchone()
        self.assertEqual(row["status"], "supplemented")
        self.assertIn("QX-2025-009", row["basis"])
        # 补录不改库存
        self.assertEqual(self._box(self.h1[0])["location_id"], self.h1[1])

    def test_resolve_idempotent(self):
        svc.count_item(self.conn, self.keeper, self.tid, self.h1[0],
                       "fewer_items", actual_count=1)
        d = self.conn.execute(
            "SELECT id FROM discrepancies WHERE type='fewer_items'").fetchone()
        svc.resolve_discrepancy(self.conn, self.keeper, d["id"], "已找回")
        with self.assertRaises(BusinessError):
            svc.resolve_discrepancy(self.conn, self.keeper, d["id"], "再次处理")

    def test_diff_grouped_by_location(self):
        svc.count_item(self.conn, self.keeper, self.tid, self.h1[0], "label_damaged")
        svc.count_item(self.conn, self.keeper, self.tid, self.h2[0], "wrong_box")
        grouped = svc.list_discrepancies(self.conn, self.admin, group_by_location=True)
        slots = {g["slot_code"] for g in grouped}
        self.assertIn("KF1-A-01", slots)
        self.assertIn("KF1-A-02", slots)
