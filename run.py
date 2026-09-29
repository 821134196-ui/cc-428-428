#!/usr/bin/env python3
"""单条本地启动命令：

    python3 run.py

首次启动自动建库并生成编目、入库、移位与盘点样例；随后启动 Web 服务，
进程内后台线程按周期扫描借阅到期/超期并生成盘点提醒。

可选参数：
    --reset      清空本地数据目录后重新播种
    --no-serve   只初始化与播种，不启动服务（用于冒烟/测试准备）
    --port PORT  覆盖监听端口
环境变量：ARCHIVE_HOME（数据目录）、ARCHIVE_HOST、ARCHIVE_PORT。
"""
import shutil
import sys
import threading
import time

from app import config, db, services as svc
from app.seed import seed
from app.web import make_server


def reset_data():
    if config.HOME.exists():
        shutil.rmtree(config.HOME)


def init_and_seed(force_reset=False):
    if force_reset:
        reset_data()
    config.ensure_dirs()
    db.init_db()
    with db.get_conn() as conn:
        has = conn.execute("SELECT COUNT(*) c FROM records").fetchone()["c"]
        if has:
            print(f"[init] 数据已存在（{has} 条案卷），跳过播种。")
            return False
        info = seed(conn)
    print(f"[init] 样例已生成：全宗 {info['fonds']}，盒 {len(info['boxes'])} 个，"
          f"盘点单 {info['stocktake']}，超期借阅 {info['loan_overdue']}")
    return True


class ReminderThread(threading.Thread):
    """进程内任务：周期生成盘点/借阅提醒。守护线程，随进程退出。"""

    def __init__(self, interval):
        super().__init__(daemon=True, name="reminder")
        self.interval = interval
        self.stop_evt = threading.Event()

    def run(self):
        while not self.stop_evt.wait(self.interval):
            try:
                with db.get_conn() as conn:
                    n = svc.scan_reminders(conn)
                if n:
                    print(f"[reminder] 新增提醒 {n} 条")
            except Exception as e:  # 提醒任务不应拖垮进程
                print(f"[reminder] 扫描失败：{e}")

    def stop(self):
        self.stop_evt.set()


def main(argv):
    force_reset = "--reset" in argv
    no_serve = "--no-serve" in argv
    port = config.PORT
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])

    init_and_seed(force_reset)

    # 启动即扫描一次，确保超期差异可见
    with db.get_conn() as conn:
        svc.scan_reminders(conn)

    if no_serve:
        return 0

    reminder = ReminderThread(config.REMINDER_INTERVAL)
    reminder.start()
    server = make_server(config.HOST, port)
    print(f"[serve] 档案装盒编目与库位盘点系统已启动：http://{config.HOST}:{port}")
    print(f"[serve] 数据库：{config.DB_PATH}    附件目录：{config.ATTACH_DIR}")
    print(f"[serve] 提醒扫描间隔 {config.REMINDER_INTERVAL}s；Ctrl+C 退出。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[serve] 正在退出…")
    finally:
        reminder.stop()
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
