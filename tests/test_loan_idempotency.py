"""借阅：幂等键重复提交返回原单；同盒两人同时借出仅一人成功；超期差异与归还闭环。"""
import threading
import unittest
from datetime import date, timedelta

from app import db, services as svc
from app.services import BusinessError
from tests.base import BaseCase


class LoanIdempotencyTest(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin = self.users["admin"]
        self.keeper = self.users["keeper"]
        self.reader = self.users["reader"]
        _, self.locs = self.make_fonds_boxes([(2025, "永久", "公开", 12)])
        self.b1 = self.locs["T01-2025-Y-H001"][0]
        self.b2 = self.locs["T01-2025-Y-H002"][0]

    def _apply_approve(self, box_id, borrower, idem=None):
        lid, no, dup = svc.apply_loan(
            self.conn, self.reader, box_id, borrower, "虚构用途", days=14, idem_key=idem)
        self.conn.commit()
        svc.approve_loan(self.conn, self.admin, lid)
        self.conn.commit()
        return lid

    def test_idempotent_apply_returns_same_loan(self):
        id1, n1, d1 = svc.apply_loan(
            self.conn, self.reader, self.b1, "甲", "用途", idem_key="KEY-001")
        self.conn.commit()
        self.assertFalse(d1)
        id2, n2, d2 = svc.apply_loan(
            self.conn, self.reader, self.b1, "甲-重试", "用途2", idem_key="KEY-001")
        self.conn.commit()
        self.assertTrue(d2)
        self.assertEqual((id1, n1), (id2, n2))
        # 数据库中只有一条
        n = self.conn.execute("SELECT COUNT(*) c FROM loans WHERE idem_key='KEY-001'").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_application_after_borrowed_blocked(self):
        l1 = self._apply_approve(self.b1, "甲")
        # 已批准但未借出：他人仍可提交申请（裁决延后到出库）
        lid2, _, _ = svc.apply_loan(self.conn, self.reader, self.b1, "乙", "用途")
        self.conn.commit()
        svc.borrow_loan(self.conn, self.keeper, l1)
        self.conn.commit()
        # 盒已借出：新申请直接拒绝
        with self.assertRaises(BusinessError):
            svc.apply_loan(self.conn, self.reader, self.b1, "丙", "用途")

    def test_sequential_double_borrow_only_one(self):
        # 同盒两张申请先并存（待审批），先后批准，最终借出时只有一人成功
        l1, _, _ = svc.apply_loan(self.conn, self.reader, self.b1, "甲", "用途")
        self.conn.commit()
        lid3, _, _ = svc.apply_loan(self.conn, self.reader, self.b1, "丙", "用途")
        self.conn.commit()
        l2, _, _ = svc.apply_loan(self.conn, self.reader, self.b2, "乙", "用途")  # 另一盒
        self.conn.commit()
        svc.approve_loan(self.conn, self.admin, l1)
        svc.approve_loan(self.conn, self.admin, lid3)
        svc.approve_loan(self.conn, self.admin, l2)
        self.conn.commit()
        svc.borrow_loan(self.conn, self.keeper, l1)
        self.conn.commit()
        with self.assertRaises(BusinessError):
            svc.borrow_loan(self.conn, self.keeper, lid3)
        # b2 正常
        svc.borrow_loan(self.conn, self.keeper, l2)
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT status FROM boxes WHERE id=?", (self.b1,)).fetchone()["status"],
            "loaned")
        winner = self.conn.execute(
            "SELECT borrower FROM loans WHERE box_id=? AND status='borrowed'", (self.b1,)).fetchone()
        self.assertEqual(winner["borrower"], "甲")

    def test_concurrent_double_borrow_only_one_succeeds(self):
        """两名人员同时点“借出”：线程并发，条件更新保证只有一人成功。"""
        lid1, _, _ = svc.apply_loan(self.conn, self.reader, self.b1, "甲", "用途")
        lid2, _, _ = svc.apply_loan(self.conn, self.reader, self.b1, "乙", "用途")
        self.conn.commit()
        svc.approve_loan(self.conn, self.admin, lid1)
        svc.approve_loan(self.conn, self.admin, lid2)
        self.conn.commit()

        results = {}
        barrier = threading.Barrier(2)

        def worker(loan_id, name):
            c = db.connect(self.db_path)
            c.execute("PRAGMA busy_timeout=10000")
            try:
                barrier.wait(timeout=5)
                try:
                    svc.borrow_loan(
                        c, {"username": name, "display_name": name, "role": "keeper",
                            "clearance": "机密", "active": 1, "id": 1},
                        loan_id)
                    c.commit()
                    results[name] = "ok"
                except BusinessError:
                    results[name] = "rejected"
            finally:
                c.close()

        t1 = threading.Thread(target=worker, args=(lid1, "K1"))
        t2 = threading.Thread(target=worker, args=(lid2, "K2"))
        t1.start(); t2.start(); t1.join(15); t2.join(15)
        self.assertEqual(sorted(results.values()), ["ok", "rejected"])
        # 盒上只有一条 borrowed
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM loans WHERE box_id=? AND status='borrowed'", (self.b1,)).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_overdue_creates_discrepancy_and_return_closes_it(self):
        lid = self._apply_approve(self.b1, "甲")
        svc.borrow_loan(self.conn, self.keeper, lid)
        self.conn.execute(
            "UPDATE loans SET due_date=? WHERE id=?",
            ((date.today() - timedelta(days=1)).isoformat(), lid))
        self.conn.commit()
        added = svc.scan_reminders(self.conn)
        self.conn.commit()
        self.assertGreaterEqual(added, 1)
        d = self.conn.execute(
            "SELECT * FROM discrepancies WHERE type='loan_overdue' AND loan_id=?", (lid,)).fetchone()
        self.assertIsNotNone(d)
        # 再扫描不重复产生 open 差异
        svc.scan_reminders(self.conn)
        self.conn.commit()
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM discrepancies WHERE type='loan_overdue' AND loan_id=? AND status='open'",
            (lid,)).fetchone()["c"]
        self.assertEqual(n, 1)
        # 归还闭环
        svc.return_loan(self.conn, self.keeper, lid)
        self.conn.commit()
        d2 = self.conn.execute("SELECT status FROM discrepancies WHERE id=?", (d["id"],)).fetchone()
        self.assertEqual(d2["status"], "resolved")

    def test_due_soon_reminder_no_discrepancy(self):
        lid = self._apply_approve(self.b1, "甲")
        svc.borrow_loan(self.conn, self.keeper, lid)
        self.conn.execute(
            "UPDATE loans SET due_date=? WHERE id=?",
            ((date.today() + timedelta(days=2)).isoformat(), lid))
        self.conn.commit()
        svc.scan_reminders(self.conn)
        self.conn.commit()
        r = self.conn.execute(
            "SELECT kind FROM reminders WHERE ref_type='loan' AND ref_id=?", (lid,)).fetchone()
        self.assertEqual(r["kind"], "due_soon")
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM discrepancies WHERE loan_id=?", (lid,)).fetchone()["c"]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
