"""档号唯一性：重复档号、显式档号冲突、装盒连续切盒。"""
import sqlite3

from app import services as svc
from app.services import BusinessError
from tests.base import BaseCase


class ArchiveNoUniqueTest(BaseCase):
    def setUp(self):
        super().setUp()
        self.admin = self.users["admin"]
        self.fid = svc.create_fonds(self.conn, self.admin, "A01", "测试", "")

    def test_auto_archive_no_unique(self):
        nos = set()
        for i in range(25):
            _, no = svc.create_record(self.conn, self.admin, self.fid, 2025, "永久", f"r{i}")
            self.assertNotIn(no, nos)
            nos.add(no)

    def test_explicit_duplicate_rejected(self):
        _, no = svc.create_record(self.conn, self.admin, self.fid, 2025, "永久", "r1")
        with self.assertRaises(BusinessError):
            svc.create_record(self.conn, self.admin, self.fid, 2025, "永久",
                              "r2", archive_no=no)

    def test_unique_across_retention_same_year(self):
        n1 = svc.create_record(self.conn, self.admin, self.fid, 2025, "永久", "a")[1]
        n2 = svc.create_record(self.conn, self.admin, self.fid, 2025, "定期30年", "b")[1]
        self.assertNotEqual(n1, n2)

    def test_box_packing_continuous_and_no_dup(self):
        for i in range(1, 26):
            svc.create_record(self.conn, self.admin, self.fid, 2025, "永久", f"r{i}")
        created = svc.commit_packing(self.conn, self.admin, self.fid, 2025, "永久")
        self.assertEqual(len(created), 3)  # 10 + 10 + 5
        seen = set()
        for b in svc.list_boxes(self.conn, self.admin, with_records=True):
            seqs = [r["seq"] for r in b["records"]]
            # 盒内档号连续
            self.assertEqual(seqs, list(range(seqs[0], seqs[-1] + 1)))
            for r in b["records"]:
                self.assertNotIn(r["archive_no"], seen, "档号跨盒重复")
                seen.add(r["archive_no"])
        self.assertEqual(len(seen), 25)

    def test_gap_forces_box_split(self):
        for s in (1, 2, 5, 6):  # 2→5 断档
            svc.create_record(self.conn, self.admin, self.fid, 2025, "永久",
                              f"r{s}", seq=s)
        groups = svc.packing_preview(self.conn, self.admin, self.fid)
        g = groups[0]
        self.assertEqual(g["gaps"], [(2, 5)])
        self.assertEqual(len(g["boxes"]), 2)  # 断档处切盒

    def test_db_unique_constraint_is_real(self):
        svc.create_record(self.conn, self.admin, self.fid, 2025, "永久", "r1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO records(archive_no,fonds_id,year,retention,title,security_level,seq,status,created_at)"
                " VALUES('A01-2025-Y-0001',1,2025,'永久','x','公开',99,'unboxed','t')")
