#!/usr/bin/env python3
"""Main entry point for the archive management system.

Starts the HTTP server and a background thread that periodically checks
for overdue borrowings and creates difference records (盘点提醒).

Usage:
    python3 server.py [--host HOST] [--port PORT]
"""
import argparse
import sys
import threading
import time

from archive import db, services
from archive.api import create_server


def reminder_loop(interval_seconds=300):
    """Background loop that checks for overdue borrowings.

    Runs every 5 minutes by default. Creates 借阅超期 difference records
    for any approved borrowings past their due date.
    """
    while True:
        try:
            created = services.check_overdue_borrowings()
            if created:
                print(f"[提醒] 发现 {len(created)} 条借阅超期记录", flush=True)
        except Exception as e:
            print(f"[提醒] 检查借阅超期时出错: {e}", flush=True)
        time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(description="档案装盒编目与库位盘点系统")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--no-reminder", action="store_true", help="禁用后台盘点提醒")
    args = parser.parse_args()

    # Initialize database
    db.init_db()
    print(f"[系统] 数据库已初始化: {db.get_db_path()}", flush=True)

    # Start background reminder thread
    if not args.no_reminder:
        t = threading.Thread(target=reminder_loop, daemon=True, name="inventory-reminder")
        t.start()
        print("[系统] 后台盘点提醒任务已启动 (每5分钟检查一次借阅超期)", flush=True)

    # Start HTTP server
    server = create_server(host=args.host, port=args.port)
    print(f"[系统] 服务器已启动: http://{args.host}:{args.port}", flush=True)
    print("[系统] 按 Ctrl+C 停止服务器", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[系统] 正在停止服务器...", flush=True)
        server.shutdown()
        print("[系统] 服务器已停止", flush=True)


if __name__ == "__main__":
    main()
