"""移位交接：调架先开单、在途不动库位、交接后落位、中断退原位并出差异、在途不能重复开单。"""
from app import services as svc
from app.services import BusinessError
from tests.base import BaseCase


class MoveHandoverTest(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin, self.keeper = self.users["admin"], self.users["keeper"]
        _, self.locs = self.make_fonds_boxes([(2025, "永久", "公开", 12)])
        self.b1 = self.locs["T01-2025-Y-H001"]  # (box_id, loc_id, slot)
        self.b2 = self.locs["T01-2025-Y-H002"]
        # 目标空库位
        _, to_slot = svc.add_location(self.conn, self.admin, "KF2", "A", 1, 1)
        self.to_loc = self.conn.execute(
            "SELECT id FROM locations WHERE slot_code=?", (to_slot,)).fetchone()["id"]

    def _box(self, box_id):
        return dict(self.conn.execute(
            "SELECT * FROM boxes WHERE id=?", (box_id,)).fetchone())

    def test_move_order_first_no_location_change(self):
        _, no = svc.create_move(self.conn, self.keeper, self.b1[0], self.to_loc)
        box = self._box(self.b1[0])
        self.assertEqual(box["location_id"], self.b1[1])       # 库位未变
        self.assertEqual(box["status"], "inbound")
        order = self.conn.execute("SELECT * FROM move_orders WHERE order_no=?", (no,)).fetchone()
        self.assertEqual(order["status"], "draft")

    def test_full_handover_flow(self):
        _, no = svc.create_move(self.conn, self.keeper, self.b1[0], self.to_loc)
        oid = self.conn.execute("SELECT id FROM move_orders WHERE order_no=?", (no,)).fetchone()["id"]
        svc.start_move(self.conn, self.keeper, oid)
        self.assertEqual(self._box(self.b1[0])["status"], "moving")
        # 在途库位仍指向原位
        self.assertEqual(self._box(self.b1[0])["location_id"], self.b1[1])
        svc.receive_move(self.conn, self.keeper, oid)
        box = self._box(self.b1[0])
        self.assertEqual(box["location_id"], self.to_loc)
        self.assertEqual(box["status"], "inbound")
        order = self.conn.execute("SELECT * FROM move_orders WHERE id=?", (oid,)).fetchone()
        self.assertEqual(order["status"], "done")
        self.assertTrue(order["receiver"])

    def test_cannot_start_non_draft(self):
        _, no = svc.create_move(self.conn, self.keeper, self.b1[0], self.to_loc)
        oid = self.conn.execute("SELECT id FROM move_orders WHERE order_no=?", (no,)).fetchone()["id"]
        svc.start_move(self.conn, self.keeper, oid)
        with self.assertRaises(BusinessError):
            svc.start_move(self.conn, self.keeper, oid)

    def test_duplicate_active_move_rejected(self):
        svc.create_move(self.conn, self.keeper, self.b1[0], self.to_loc)
        with self.assertRaises(BusinessError):
            svc.create_move(self.conn, self.keeper, self.b1[0], self.to_loc)

    def test_interrupt_returns_box_and_creates_discrepancy(self):
        _, no = svc.create_move(self.conn, self.keeper, self.b1[0], self.to_loc, "测试调架")
        oid = self.conn.execute("SELECT id FROM move_orders WHERE order_no=?", (no,)).fetchone()["id"]
        svc.start_move(self.conn, self.keeper, oid)
        svc.interrupt_move(self.conn, self.keeper, oid, "搬运车故障")
        box = self._box(self.b1[0])
        self.assertEqual(box["location_id"], self.b1[1])
        self.assertEqual(box["status"], "inbound")
        order = self.conn.execute("SELECT * FROM move_orders WHERE id=?", (oid,)).fetchone()
        self.assertEqual(order["status"], "interrupted")
        d = self.conn.execute(
            "SELECT * FROM discrepancies WHERE type='move_interrupted' AND box_id=?",
            (self.b1[0],)).fetchone()
        self.assertIsNotNone(d)
        self.assertEqual(d["status"], "open")

    def test_receive_into_full_location_rejected(self):
        # 开单时目标尚空；出库后、交接前另一盒占满目标（容量 1），交接必须拒绝
        _, no = svc.create_move(self.conn, self.keeper, self.b1[0], self.to_loc)
        oid = self.conn.execute("SELECT id FROM move_orders WHERE order_no=?", (no,)).fetchone()["id"]
        svc.start_move(self.conn, self.keeper, oid)
        self.conn.execute(
            "INSERT INTO boxes(box_no,fonds_id,year,retention,status,location_id,packed_at)"
            " VALUES('OCC-1',1,2025,'永久','inbound',?, 't')", (self.to_loc,))
        with self.assertRaises(BusinessError):
            svc.receive_move(self.conn, self.keeper, oid)
        # 交接失败：盒仍是移位中、库位仍是原位
        self.assertEqual(self._box(self.b1[0])["status"], "moving")
        self.assertEqual(self._box(self.b1[0])["location_id"], self.b1[1])
