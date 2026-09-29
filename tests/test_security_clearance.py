"""密级权限：岗位操作矩阵、库位/借阅/标题按岗位与密级脱敏，档号始终保留。"""
from app import services as svc
from app.security import PermissionError, can_see_location, can_see_loan, mask_record
from app.services import BusinessError
from tests.base import BaseCase


class ClearanceTest(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin = self.users["admin"]
        self.cataloger = self.users["cataloger"]   # 秘密
        self.keeper = self.users["keeper"]         # 秘密
        self.reader = self.users["reader"]         # 内部
        self.auditor = self.users["auditor"]       # 机密
        self.fid = svc.create_fonds(self.conn, self.admin, "S01", "密级测试", "")
        # 一条机密案卷并装盒入库
        svc.create_record(self.conn, self.admin, self.fid, 2025, "永久", "机密案卷标题", "机密")
        svc.commit_packing(self.conn, self.admin, self.fid, 2025, "永久")
        box = svc.list_boxes(self.conn, self.admin)[0]
        _, slot = svc.add_location(self.conn, self.admin, "KF1", "A", 1, 1)
        loc_id = self.conn.execute(
            "SELECT id FROM locations WHERE slot_code=?", (slot,)).fetchone()["id"]
        svc.inbound_box(self.conn, self.keeper, box["id"], loc_id)
        self.box_id = box["id"]
        self.rec = dict(self.conn.execute(
            "SELECT * FROM records WHERE box_id=?", (self.box_id,)).fetchone())

    # ---- 操作级 ----
    def test_reader_cannot_catalog(self):
        with self.assertRaises(PermissionError):
            svc.create_record(self.conn, self.reader, self.fid, 2025, "永久", "x")

    def test_cataloger_cannot_approve_loan(self):
        with self.assertRaises(PermissionError):
            svc.apply_loan(self.conn, self.cataloger, self.box_id, "x", "用途")

    def test_keeper_cannot_confirm_stocktake(self):
        # 构造一个已清点完的盘点任务
        tid, _, _ = svc.create_stocktake(self.conn, self.keeper, room_code="KF1")
        item = self.conn.execute(
            "SELECT box_id FROM stock_take_items WHERE task_id=?", (tid,)).fetchone()
        svc.count_item(self.conn, self.keeper, tid, item["box_id"], "present")
        with self.assertRaises(PermissionError):
            svc.confirm_stocktake(self.conn, self.keeper, tid)
        # 审计可以确认
        svc.confirm_stocktake(self.conn, self.auditor, tid)

    def test_clearance_blocks_high_level_catalog(self):
        # 借阅员 clearance=内部，即便给其编目动作也会在服务层被密级拦截（角色先拦）
        with self.assertRaises(PermissionError):
            svc.create_record(self.conn, self.reader, self.fid, 2025, "永久", "y", "机密")
        # 编目员（秘密）不能编目机密
        with self.assertRaises(BusinessError):
            svc.create_record(self.conn, self.cataloger, self.fid, 2025, "永久", "y", "机密")

    # ---- 字段脱敏 ----
    def test_location_masked_for_non_keeper(self):
        # keeper 在岗位允许名单内，但密级 秘密 < 机密 → 仍不可见
        self.assertFalse(can_see_location(self.keeper, "机密"))
        self.assertTrue(can_see_location(self.keeper, "秘密"))
        self.assertTrue(can_see_location(self.admin, "机密"))
        # 借阅员即便对公开盒也不能看库位字段（岗位限制）
        self.assertFalse(can_see_location(self.reader, "公开"))

    def test_loan_field_masked_for_cataloger(self):
        lid, no, _ = svc.apply_loan(self.conn, self.reader, self.box_id, "冯研究", "课题")
        svc.approve_loan(self.conn, self.admin, lid)
        svc.borrow_loan(self.conn, self.keeper, lid)
        loans_cat = svc.list_loans(self.conn, self.cataloger)
        self.assertEqual(loans_cat[0]["borrower"], "●●●●（无权查看）")
        self.assertTrue(loans_cat[0]["loan_masked"])
        loans_aud = svc.list_loans(self.conn, self.auditor)
        self.assertEqual(loans_aud[0]["borrower"], "冯研究")

    def test_title_masked_but_archive_no_kept(self):
        masked = mask_record(self.rec, self.reader)  # 内部 < 机密
        self.assertEqual(masked["title"], "●●●●（无权查看）")
        self.assertTrue(masked["_title_masked"])
        self.assertTrue(masked["archive_no"].startswith("S01-2025-Y-"))  # 档号保留
        shown = mask_record(self.rec, self.admin)
        self.assertEqual(shown["title"], "机密案卷标题")

    def test_box_view_masks_slot(self):
        # 机密盒：库管（秘密）在盒清单中也看不到库位
        boxes = svc.list_boxes(self.conn, self.keeper)
        b = next(x for x in boxes if x["id"] == self.box_id)
        self.assertIsNone(b["slot_code"])
        self.assertTrue(b["location_masked"])
