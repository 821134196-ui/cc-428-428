"""测试前置：每个用例使用独立临时数据目录，互不污染。"""
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="archive-test-")
os.environ["ARCHIVE_HOME"] = _TMP

import unittest  # noqa: E402

from app import config, db, services as svc  # noqa: E402
from app.security import ROLE_CLEARANCE  # noqa: E402

USERS = [
    ("admin", "赵主管", "admin"),
    ("cataloger", "钱编目", "cataloger"),
    ("keeper", "孙库管", "keeper"),
    ("reader", "李借阅", "reader"),
    ("auditor", "周审计", "auditor"),
]


class BaseCase(unittest.TestCase):
    db_path = None

    def setUp(self):
        # 每个用例独立 SQLite 文件
        fd, self.db_path = tempfile.mkstemp(suffix=".db", dir=_TMP)
        os.close(fd)
        config.DB_PATH = self.db_path
        db.init_db(self.db_path)
        self.conn = db.connect(self.db_path)
        self.addCleanup(self._cleanup)
        for uname, dname, role in USERS:
            self.conn.execute(
                "INSERT INTO users(username, display_name, role, clearance) VALUES(?,?,?,?)",
                (uname, dname, role, ROLE_CLEARANCE[role]))
        self.conn.commit()
        self.users = {u: self._user(u) for u, _, _ in USERS}

    def _cleanup(self):
        self.conn.close()
        os.unlink(self.db_path)

    def _user(self, name):
        return dict(self.conn.execute(
            "SELECT * FROM users WHERE username=?", (name,)).fetchone())

    # ---- 构造辅助 ----
    def make_fonds_boxes(self, records_spec):
        """records_spec: [(year, retention, level, n)] → 装盒入库一条龙。"""
        admin, keeper = self.users["admin"], self.users["keeper"]
        fid = svc.create_fonds(self.conn, admin, "T01", "测试全宗", "")
        for year, ret, level, n in records_spec:
            for i in range(1, n + 1):
                svc.create_record(self.conn, admin, fid, year, ret,
                                  f"案卷-{year}-{i}", level)
            svc.commit_packing(self.conn, admin, fid, year, ret)
        boxes = svc.list_boxes(self.conn, admin)
        locs = {}
        for i, b in enumerate(boxes):
            _, slot = svc.add_location(self.conn, admin, "KF1", "A", i + 1, 1)
            loc_id = self.conn.execute(
                "SELECT id FROM locations WHERE slot_code=?", (slot,)).fetchone()["id"]
            svc.inbound_box(self.conn, keeper, b["id"], loc_id)
            locs[b["box_no"]] = (b["id"], loc_id, slot)
        return fid, locs
