#!/usr/bin/env python3
"""Seed script — creates sample data for cataloging, warehouse entry, and inventory.

Usage:
    python3 seed.py

This will:
1. Reset the database
2. Create a fonds (全宗)
3. Create sample files (案卷) with various years, retention periods, and security levels
4. Create warehouses and shelves
5. Create boxes with files (装盒)
6. Enter boxes into warehouse locations (入库)
7. Create an inventory task and record results (盘点)
8. Create a borrowing (借阅)
9. Create differences (差异记录)
"""
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from archive import db, services


def seed():
    print("=" * 60)
    print("档案装盒编目与库位盘点系统 — 样例数据初始化")
    print("=" * 60)

    # Reset database
    print("\n[1/8] 重置数据库...")
    db.reset_db()
    print("  ✓ 数据库已重置")

    # Create fonds
    print("\n[2/8] 创建全宗...")
    fonds = services.create_fonds("A001", "中共中央办公厅")
    print(f"  ✓ 全宗: {fonds['code']} — {fonds['name']}")

    # Create warehouses
    print("\n[3/8] 创建库房与货架...")
    wh1 = services.create_warehouse("WH-A", "第一库房")
    wh2 = services.create_warehouse("WH-B", "第二库房（密级）")
    print(f"  ✓ 库房: {wh1['code']} — {wh1['name']}")
    print(f"  ✓ 库房: {wh2['code']} — {wh2['name']}")

    shelf_a1 = services.create_shelf(wh1["id"], "A1", 5)
    shelf_a2 = services.create_shelf(wh1["id"], "A2", 5)
    shelf_b1 = services.create_shelf(wh2["id"], "B1", 3)
    print(f"  ✓ 货架: {shelf_a1['code']} ({shelf_a1['layers_count']}层)")
    print(f"  ✓ 货架: {shelf_a2['code']} ({shelf_a2['layers_count']}层)")
    print(f"  ✓ 货架: {shelf_b1['code']} ({shelf_b1['layers_count']}层)")

    # Create sample files
    print("\n[4/8] 创建案卷...")
    files_data = [
        # 2024年 永久
        ("A001-2024-001", "关于2024年度工作要点的通知", 2024, "永久", "公开"),
        ("A001-2024-002", "关于加强党的建设的意见", 2024, "永久", "内部"),
        ("A001-2024-003", "关于推进深化改革的决定", 2024, "永久", "秘密"),
        ("A001-2024-004", "关于2024年经济工作的部署", 2024, "永久", "机密"),
        ("A001-2024-005", "关于民生保障工作的安排", 2024, "永久", "公开"),
        # 2024年 30年
        ("A001-2024-006", "关于2024年上半年工作的总结", 2024, "30年", "公开"),
        ("A001-2024-007", "关于2024年下半年工作的计划", 2024, "30年", "内部"),
        ("A001-2024-008", "关于专项工作的报告", 2024, "30年", "秘密"),
        ("A001-2024-009", "关于人事任免的通知", 2024, "30年", "内部"),
        ("A001-2024-010", "关于财务预算的批复", 2024, "30年", "公开"),
        # 2023年 永久
        ("A001-2023-001", "关于2023年度工作的总结", 2023, "永久", "公开"),
        ("A001-2023-002", "关于2023年重要会议的纪要", 2023, "永久", "内部"),
        ("A001-2023-003", "关于2023年重大事项的决定", 2023, "永久", "秘密"),
        # 2023年 30年
        ("A001-2023-004", "关于2023年日常工作的记录", 2023, "30年", "公开"),
        ("A001-2023-005", "关于2023年专项工作的报告", 2023, "30年", "内部"),
        # 2024年 10年
        ("A001-2024-011", "关于2024年一般性工作的通知", 2024, "10年", "公开"),
        ("A001-2024-012", "关于2024年日常事务的安排", 2024, "10年", "公开"),
    ]

    created_files = []
    for archive_number, title, year, retention, security in files_data:
        f = services.create_file(
            fonds_id=fonds["id"],
            archive_number=archive_number,
            title=title,
            year=year,
            retention_period=retention,
            security_level=security,
        )
        created_files.append(f)
    print(f"  ✓ 创建了 {len(created_files)} 件案卷")

    # Create boxes and assign files
    print("\n[5/8] 装盒...")
    # Box 1: 2024年 永久 (files 001-005)
    box1 = services.create_box(
        box_number="BOX-2024-YJ-001",
        year=2024,
        retention_period="永久",
        file_ids=[f["id"] for f in created_files[:5]],
    )
    print(f"  ✓ 盒 {box1['box_number']} (2024年·永久, 5件)")

    # Box 2: 2024年 30年 (files 006-010)
    box2 = services.create_box(
        box_number="BOX-2024-30-001",
        year=2024,
        retention_period="30年",
        file_ids=[f["id"] for f in created_files[5:10]],
    )
    print(f"  ✓ 盒 {box2['box_number']} (2024年·30年, 5件)")

    # Box 3: 2023年 永久 (files 011-013)
    box3 = services.create_box(
        box_number="BOX-2023-YJ-001",
        year=2023,
        retention_period="永久",
        file_ids=[f["id"] for f in created_files[10:13]],
    )
    print(f"  ✓ 盒 {box3['box_number']} (2023年·永久, 3件)")

    # Box 4: 2023年 30年 (files 014-015)
    box4 = services.create_box(
        box_number="BOX-2023-30-001",
        year=2023,
        retention_period="30年",
        file_ids=[f["id"] for f in created_files[13:15]],
    )
    print(f"  ✓ 盒 {box4['box_number']} (2023年·30年, 2件)")

    # Box 5: 2024年 10年 (files 016-017)
    box5 = services.create_box(
        box_number="BOX-2024-10-001",
        year=2024,
        retention_period="10年",
        file_ids=[f["id"] for f in created_files[15:17]],
    )
    print(f"  ✓ 盒 {box5['box_number']} (2024年·10年, 2件)")

    # Enter boxes into warehouse locations
    print("\n[6/8] 入库绑定库位...")
    services.enter_warehouse(box1["id"], wh1["id"], shelf_a1["id"], 1, "admin")
    services.enter_warehouse(box2["id"], wh1["id"], shelf_a1["id"], 2, "admin")
    services.enter_warehouse(box3["id"], wh1["id"], shelf_a2["id"], 1, "admin")
    services.enter_warehouse(box4["id"], wh1["id"], shelf_a2["id"], 2, "admin")
    services.enter_warehouse(box5["id"], wh2["id"], shelf_b1["id"], 1, "admin")
    print(f"  ✓ 5 个盒已入库")

    # Create inventory task and record results
    print("\n[7/8] 创建盘点任务...")
    task = services.create_inventory_task("2024年度第一季度盘点", "admin")
    services.start_inventory_task(task["id"], "admin")
    print(f"  ✓ 盘点任务: {task['name']} (已开始)")

    # Record results for boxes
    services.record_inventory_result(task["id"], box1["id"], "在库", username="admin")
    services.record_inventory_result(task["id"], box2["id"], "在库", username="admin")
    services.record_inventory_result(task["id"], box3["id"], "缺卷", notes="缺 A001-2023-003", username="admin")
    services.record_inventory_result(task["id"], box4["id"], "标签损坏", notes="标签模糊不清", username="admin")
    services.record_inventory_result(task["id"], box5["id"], "在库", username="admin")
    print(f"  ✓ 已记录 5 条盘点结果")

    # Complete and freeze
    services.complete_inventory_task(task["id"], "admin")
    services.freeze_inventory_task(task["id"], "admin")
    print(f"  ✓ 盘点任务已完成并冻结")

    # Create a borrowing
    print("\n[8/8] 创建借阅记录...")
    from datetime import datetime, timedelta
    due = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    borrowing = services.create_borrowing(box1["id"], "张三", due, "admin")
    services.approve_borrowing(borrowing["id"], "admin")
    print(f"  ✓ 借阅: 盒 {box1['box_number']} → 张三 (应还 {due})")

    # Create an overdue borrowing for demonstration
    past_due = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    borrowing2 = services.create_borrowing(box2["id"], "李四", past_due, "admin")
    services.approve_borrowing(borrowing2["id"], "admin")
    # Manually trigger overdue check
    services.check_overdue_borrowings()
    print(f"  ✓ 借阅: 盒 {box2['box_number']} → 李四 (已超期)")

    # Create a transfer order for demonstration
    print("\n[额外] 创建移位单...")
    transfer = services.create_transfer(box5["id"], wh1["id"], shelf_a1["id"], 3, "admin")
    print(f"  ✓ 移位单: 盒 {box5['box_number']} → {wh1['code']}/{shelf_a1['code']}/3")

    print("\n" + "=" * 60)
    print("样例数据初始化完成！")
    print("=" * 60)
    print(f"\n启动服务器: python3 server.py")
    print(f"访问地址: http://localhost:8080")
    print(f"\n用户切换:")
    print(f"  admin     — 管理员 (全部权限)")
    print(f"  archivist — 档案员 (可编目、入库、盘点)")
    print(f"  reader    — 查阅者 (密级档案脱敏)")


if __name__ == "__main__":
    seed()
