#!/usr/bin/env python3
"""Tests for the archive management system.

Tests:
1. 档号唯一 (archive number uniqueness)
2. 移位交接 (transfer handover)
3. 密级权限 (security-level permissions / desensitization)
4. 盘点冻结 (inventory freeze)
5. 借阅幂等 (borrowing idempotency)

Usage:
    python3 tests.py
"""
import os
import sys
import tempfile
import threading
import time
import traceback

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from archive import db, services


# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------

class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def record(self, name, passed, error=None):
        if passed:
            self.passed += 1
            print(f"  ✓ {name}")
        else:
            self.failed += 1
            self.errors.append((name, error))
            print(f"  ✗ {name}")
            if error:
                print(f"    {error}")


def assert_raises(exc_type, func, *args, **kwargs):
    """Assert that func raises exc_type."""
    try:
        func(*args, **kwargs)
    except exc_type:
        return True
    except Exception as e:
        raise AssertionError(f"Expected {exc_type.__name__}, got {type(e).__name__}: {e}")
    raise AssertionError(f"Expected {exc_type.__name__}, but no exception was raised")


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------

def setup_test_db():
    """Create a temporary database for testing."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test_archive.db")
    db.DB_PATH = db_path
    db.reset_db()
    return tmpdir


def teardown_test_db(tmpdir):
    """Clean up the temporary database."""
    import shutil
    try:
        shutil.rmtree(tmpdir, ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test 1: 档号唯一 (archive number uniqueness)
# ---------------------------------------------------------------------------

def test_archive_number_uniqueness(result):
    print("\n[测试1] 档号唯一 (archive number uniqueness)")
    tmpdir = setup_test_db()
    try:
        # Create a fonds
        fonds = services.create_fonds("T001", "测试全宗")

        # Create a file with a unique archive number
        f1 = services.create_file(
            fonds_id=fonds["id"],
            archive_number="T001-2024-001",
            title="测试案卷1",
            year=2024,
            retention_period="永久",
        )
        result.record("创建案卷成功", f1["archive_number"] == "T001-2024-001")

        # Try to create another file with the same archive number — should fail
        try:
            services.create_file(
                fonds_id=fonds["id"],
                archive_number="T001-2024-001",
                title="重复档号案卷",
                year=2024,
                retention_period="永久",
            )
            result.record("重复档号被拒绝", False, "未抛出异常")
        except services.ConflictError as e:
            result.record("重复档号被拒绝", "已存在" in str(e))

        # Different archive number should succeed
        f2 = services.create_file(
            fonds_id=fonds["id"],
            archive_number="T001-2024-002",
            title="测试案卷2",
            year=2024,
            retention_period="永久",
        )
        result.record("不同档号创建成功", f2["archive_number"] == "T001-2024-002")

        # Box continuity: f1(001) and f2(002) are continuous → should succeed
        try:
            services.create_box(
                box_number="BOX-TEST-001",
                year=2024,
                retention_period="永久",
                file_ids=[f1["id"], f2["id"]],
            )
            result.record("连续档号装盒成功", True)
        except Exception as e:
            result.record("连续档号装盒成功", False, str(e))

        # Create two files with a gap (050 → 060, discontinuous)
        f3 = services.create_file(
            fonds_id=fonds["id"],
            archive_number="T001-2024-050",
            title="测试案卷3",
            year=2024,
            retention_period="永久",
        )
        f4 = services.create_file(
            fonds_id=fonds["id"],
            archive_number="T001-2024-060",
            title="测试案卷4",
            year=2024,
            retention_period="永久",
        )
        # Try to box f3(050) and f4(060) — should fail due to discontinuity
        try:
            services.create_box(
                box_number="BOX-TEST-002",
                year=2024,
                retention_period="永久",
                file_ids=[f3["id"], f4["id"]],
            )
            result.record("不连续档号装盒被拒绝", False, "未抛出异常")
        except services.ValidationError as e:
            result.record("不连续档号装盒被拒绝", "不连续" in str(e))

    finally:
        teardown_test_db(tmpdir)


# ---------------------------------------------------------------------------
# Test 2: 移位交接 (transfer handover)
# ---------------------------------------------------------------------------

def test_transfer_handover(result):
    print("\n[测试2] 移位交接 (transfer handover)")
    tmpdir = setup_test_db()
    try:
        # Setup: fonds, files, box, warehouse, shelf
        fonds = services.create_fonds("T002", "测试全宗2")
        f1 = services.create_file(fonds["id"], "T002-2024-001", "案卷1", 2024, "永久")
        f2 = services.create_file(fonds["id"], "T002-2024-002", "案卷2", 2024, "永久")
        box = services.create_box("BOX-T002-001", 2024, "永久", [f1["id"], f2["id"]])

        wh1 = services.create_warehouse("WH-T1", "测试库房1")
        wh2 = services.create_warehouse("WH-T2", "测试库房2")
        shelf1 = services.create_shelf(wh1["id"], "S1", 5)
        shelf2 = services.create_shelf(wh2["id"], "S2", 5)

        # Enter warehouse
        services.enter_warehouse(box["id"], wh1["id"], shelf1["id"], 1, "admin")
        box_after = services.get_box(box["id"], "管理员")
        result.record("入库后盒在原库位", box_after["warehouse_id"] == wh1["id"] and box_after["shelf_id"] == shelf1["id"])

        # Create transfer order
        transfer = services.create_transfer(box["id"], wh2["id"], shelf2["id"], 3, "admin")
        result.record("移位单创建成功", transfer["status"] == "待执行")

        # Box should be in 移位中 status
        box_transferring = services.get_box(box["id"], "管理员")
        result.record("移位中盒状态为移位中", box_transferring["status"] == "移位中")

        # Confirm transfer — handover complete
        confirmed = services.confirm_transfer(transfer["id"], "admin")
        result.record("移位确认成功", confirmed["status"] == "已确认")

        # Box should now be at the new location
        box_final = services.get_box(box["id"], "管理员")
        result.record("移位后盒在新库位",
            box_final["warehouse_id"] == wh2["id"] and
            box_final["shelf_id"] == shelf2["id"] and
            box_final["layer"] == 3 and
            box_final["status"] == "在库")

        # Cannot confirm an already-confirmed transfer
        try:
            services.confirm_transfer(transfer["id"], "admin")
            result.record("重复确认被拒绝", False, "未抛出异常")
        except services.ConflictError:
            result.record("重复确认被拒绝", True)

        # Test interrupt: create another transfer and interrupt it
        transfer2 = services.create_transfer(box["id"], wh1["id"], shelf1["id"], 2, "admin")
        interrupted = services.interrupt_transfer(transfer2["id"], "admin", "测试中断")
        result.record("移位中断成功", interrupted["status"] == "中断")

        # After interrupt, box should be back at original location
        box_interrupted = services.get_box(box["id"], "管理员")
        result.record("中断后盒回到原库位",
            box_interrupted["warehouse_id"] == wh2["id"] and
            box_interrupted["shelf_id"] == shelf2["id"] and
            box_interrupted["status"] == "在库")

        # Interrupt should create a difference record
        diffs = services.list_differences(type_filter="移位中断")
        result.record("中断生成差异记录", len(diffs) > 0)

    finally:
        teardown_test_db(tmpdir)


# ---------------------------------------------------------------------------
# Test 3: 密级权限 (security-level permissions / desensitization)
# ---------------------------------------------------------------------------

def test_security_permissions(result):
    print("\n[测试3] 密级权限 (security-level permissions)")
    tmpdir = setup_test_db()
    try:
        fonds = services.create_fonds("T003", "测试全宗3")

        # Create files with different security levels
        f_public = services.create_file(fonds["id"], "T003-2024-001", "公开案卷", 2024, "永久", "公开")
        f_internal = services.create_file(fonds["id"], "T003-2024-002", "内部案卷", 2024, "永久", "内部")
        f_secret = services.create_file(fonds["id"], "T003-2024-003", "秘密案卷", 2024, "永久", "秘密")
        f_confidential = services.create_file(fonds["id"], "T003-2024-004", "机密案卷", 2024, "永久", "机密")

        # 管理员 can see all security levels
        admin_files = services.list_files("管理员")
        admin_levels = {f["archive_number"]: f["security_level"] for f in admin_files}
        result.record("管理员可见密级",
            admin_levels.get("T003-2024-003") == "秘密" and
            admin_levels.get("T003-2024-004") == "机密")

        # 档案员 can see all security levels
        archivist_files = services.list_files("档案员")
        archivist_levels = {f["archive_number"]: f["security_level"] for f in archivist_files}
        result.record("档案员可见密级",
            archivist_levels.get("T003-2024-003") == "秘密" and
            archivist_levels.get("T003-2024-004") == "机密")

        # 查阅者 cannot see 秘密/机密 — should be masked as ***
        reader_files = services.list_files("查阅者")
        reader_levels = {f["archive_number"]: f["security_level"] for f in reader_files}
        result.record("查阅者密级被脱敏",
            reader_levels.get("T003-2024-003") == "***" and
            reader_levels.get("T003-2024-004") == "***")
        # 公开 and 内部 should still be visible
        result.record("查阅者可见公开和内部",
            reader_levels.get("T003-2024-001") == "公开" and
            reader_levels.get("T003-2024-002") == "内部")

        # Test location desensitization for boxes with sensitive files
        box_secret = services.create_box("BOX-T003-S", 2024, "永久", [f_secret["id"]])
        wh = services.create_warehouse("WH-T3", "测试库房3")
        shelf = services.create_shelf(wh["id"], "S1", 5)
        services.enter_warehouse(box_secret["id"], wh["id"], shelf["id"], 1, "admin")

        # 管理员 sees the location
        admin_box = services.get_box(box_secret["id"], "管理员")
        result.record("管理员可见密级库位",
            admin_box["warehouse_id"] == wh["id"] and admin_box["shelf_id"] == shelf["id"])

        # 查阅者 should NOT see the location
        reader_box = services.get_box(box_secret["id"], "查阅者")
        result.record("查阅者密级库位被脱敏",
            reader_box["warehouse_id"] is None and reader_box["shelf_id"] is None)

        # 查阅者 cannot perform warehouse entry
        try:
            services.enter_warehouse(box_secret["id"], wh["id"], shelf["id"], 2, "reader")
            result.record("查阅者入库被拒绝", False, "未抛出异常")
        except services.PermissionError:
            result.record("查阅者入库被拒绝", True)

        # 查阅者 cannot create transfer orders
        try:
            services.create_transfer(box_secret["id"], wh["id"], shelf["id"], 3, "reader")
            result.record("查阅者移位被拒绝", False, "未抛出异常")
        except services.PermissionError:
            result.record("查阅者移位被拒绝", True)

    finally:
        teardown_test_db(tmpdir)


# ---------------------------------------------------------------------------
# Test 4: 盘点冻结 (inventory freeze)
# ---------------------------------------------------------------------------

def test_inventory_freeze(result):
    print("\n[测试4] 盘点冻结 (inventory freeze)")
    tmpdir = setup_test_db()
    try:
        # Setup
        fonds = services.create_fonds("T004", "测试全宗4")
        f1 = services.create_file(fonds["id"], "T004-2024-001", "案卷1", 2024, "永久")
        f2 = services.create_file(fonds["id"], "T004-2024-002", "案卷2", 2024, "永久")
        box1 = services.create_box("BOX-T004-001", 2024, "永久", [f1["id"]])
        box2 = services.create_box("BOX-T004-002", 2024, "永久", [f2["id"]])

        wh = services.create_warehouse("WH-T4", "测试库房4")
        shelf = services.create_shelf(wh["id"], "S1", 5)
        services.enter_warehouse(box1["id"], wh["id"], shelf["id"], 1, "admin")
        services.enter_warehouse(box2["id"], wh["id"], shelf["id"], 2, "admin")

        # Create and start inventory task
        task = services.create_inventory_task("测试盘点任务", "admin")
        result.record("盘点任务创建成功", task["status"] == "待开始")

        services.start_inventory_task(task["id"], "admin")
        task_started = services.get_inventory_task(task["id"])
        result.record("盘点任务开始成功", task_started["status"] == "进行中")

        # Record results during 进行中
        services.record_inventory_result(task["id"], box1["id"], "在库", username="admin")
        services.record_inventory_result(task["id"], box2["id"], "缺卷", notes="测试缺卷", username="admin")
        task_with_records = services.get_inventory_task(task["id"])
        result.record("盘点记录已录入", len(task_with_records["records"]) == 2)

        # Cannot record duplicate results for the same box
        try:
            services.record_inventory_result(task["id"], box1["id"], "在库", username="admin")
            result.record("重复盘点记录被拒绝", False, "未抛出异常")
        except services.ConflictError:
            result.record("重复盘点记录被拒绝", True)

        # Complete the task
        services.complete_inventory_task(task["id"], "admin")
        task_completed = services.get_inventory_task(task["id"])
        result.record("盘点任务完成成功", task_completed["status"] == "已完成")

        # Freeze the task
        services.freeze_inventory_task(task["id"], "admin")
        task_frozen = services.get_inventory_task(task["id"])
        result.record("盘点任务冻结成功", task_frozen["status"] == "已冻结")

        # After freezing, cannot add records directly
        try:
            services.record_inventory_result(task["id"], box1["id"], "在库", username="admin")
            result.record("冻结后直接录入被拒绝", False, "未抛出异常")
        except services.ConflictError:
            result.record("冻结后直接录入被拒绝", True)

        # Supplementary records require basis
        try:
            services.supplement_inventory_record(task["id"], box1["id"], "在库", basis="", username="admin")
            result.record("补录无依据被拒绝", False, "未抛出异常")
        except services.ValidationError:
            result.record("补录无依据被拒绝", True)

        # Supplementary record with basis should succeed
        supplement = services.supplement_inventory_record(
            task["id"], box1["id"], "在库",
            basis="经复核原始记录，该盒实际在库，此前记录有误",
            username="admin",
        )
        result.record("补录有依据成功", supplement is not None)
        result.record("补录记录标注依据", "补录" in supplement["notes"] and "依据" in supplement["notes"])

        # Cannot supplement a non-frozen task
        task2 = services.create_inventory_task("未冻结任务", "admin")
        services.start_inventory_task(task2["id"], "admin")
        try:
            services.supplement_inventory_record(task2["id"], box1["id"], "在库", basis="测试依据", username="admin")
            result.record("非冻结任务补录被拒绝", False, "未抛出异常")
        except services.ConflictError:
            result.record("非冻结任务补录被拒绝", True)

        # Inventory summary aggregates by location
        summary = services.get_inventory_summary(task["id"])
        result.record("盘点汇总按库位聚合", len(summary["by_location"]) > 0)
        result.record("盘点汇总统计正确", summary["total_records"] == 3)  # 2 original + 1 supplement

    finally:
        teardown_test_db(tmpdir)


# ---------------------------------------------------------------------------
# Test 5: 借阅幂等 (borrowing idempotency)
# ---------------------------------------------------------------------------

def test_borrowing_idempotency(result):
    print("\n[测试5] 借阅幂等 (borrowing idempotency)")
    tmpdir = setup_test_db()
    try:
        # Setup
        fonds = services.create_fonds("T005", "测试全宗5")
        f1 = services.create_file(fonds["id"], "T005-2024-001", "案卷1", 2024, "永久")
        box = services.create_box("BOX-T005-001", 2024, "永久", [f1["id"]])

        due_date = "2026-12-31"

        # First borrowing request should succeed
        br1 = services.create_borrowing(box["id"], "张三", due_date, "admin")
        result.record("首次借阅申请成功", br1["status"] == "待审批")

        # Second borrowing request for the same box should fail (idempotency)
        try:
            services.create_borrowing(box["id"], "李四", due_date, "admin")
            result.record("重复借阅被拒绝", False, "未抛出异常")
        except services.ConflictError as e:
            result.record("重复借阅被拒绝", "已有未完成的借阅" in str(e))

        # Approve the first borrowing
        services.approve_borrowing(br1["id"], "admin")
        br1_approved = services.list_borrowings("管理员", status_filter="已批准")
        result.record("借阅批准成功", len(br1_approved) == 1)

        # Box should be in 借出 status
        box_borrowed = services.get_box(box["id"], "管理员")
        result.record("借出盒状态为借出", box_borrowed["status"] == "借出")

        # Still cannot borrow the same box
        try:
            services.create_borrowing(box["id"], "王五", due_date, "admin")
            result.record("借出期间借阅被拒绝", False, "未抛出异常")
        except services.ConflictError:
            result.record("借出期间借阅被拒绝", True)

        # Return the borrowing
        services.return_borrowing(br1["id"], "admin")
        br1_returned = services.list_borrowings("管理员", status_filter="已归还")
        result.record("借阅归还成功", len(br1_returned) == 1)

        # Box should be back to 在库
        box_returned = services.get_box(box["id"], "管理员")
        result.record("归还后盒状态为在库", box_returned["status"] == "在库")

        # Now can borrow again
        br2 = services.create_borrowing(box["id"], "赵六", due_date, "admin")
        result.record("归还后再次借阅成功", br2["status"] == "待审批")

        # Test concurrent borrowing (simulate race condition)
        # Create a new box for this test
        f2 = services.create_file(fonds["id"], "T005-2024-002", "案卷2", 2024, "永久")
        box2 = services.create_box("BOX-T005-002", 2024, "永久", [f2["id"]])

        results = []
        errors = []

        def borrow(borrower):
            try:
                br = services.create_borrowing(box2["id"], borrower, due_date, "admin")
                results.append(br)
            except Exception as e:
                errors.append(str(e))

        # Simulate two people trying to borrow the same box simultaneously
        t1 = threading.Thread(target=borrow, args=("借款人A",))
        t2 = threading.Thread(target=borrow, args=("借款人B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Only one should succeed
        result.record("并发借阅只有一人成功", len(results) == 1 and len(errors) == 1)
        result.record("并发借阅失败者收到冲突错误", any("已有未完成的借阅" in e for e in errors))

    finally:
        teardown_test_db(tmpdir)


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

def run_all_tests():
    print("=" * 60)
    print("档案装盒编目与库位盘点系统 — 测试套件")
    print("=" * 60)

    result = TestResult()

    test_archive_number_uniqueness(result)
    test_transfer_handover(result)
    test_security_permissions(result)
    test_inventory_freeze(result)
    test_borrowing_idempotency(result)

    print("\n" + "=" * 60)
    print(f"测试结果: {result.passed} 通过, {result.failed} 失败")
    print("=" * 60)

    if result.failed > 0:
        print("\n失败的测试:")
        for name, error in result.errors:
            print(f"  ✗ {name}: {error}")
        sys.exit(1)
    else:
        print("\n所有测试通过！")
        sys.exit(0)


if __name__ == "__main__":
    run_all_tests()
