"""岗位与密级权限。

密级：公开 < 内部 < 秘密 < 机密。
两条脱敏规则（针对高于本人密级、或不属于本岗位字段域的内容）：
  - 库位字段：仅库房岗（admin / keeper）可见；
  - 借阅字段（借阅人、用途、期限等）：仅借阅岗（admin / reader）与审计岗可见。
无密级权限时，标题也以占位符代替，但档号保留（编目/盘点需要核对档号）。
"""
import functools

LEVEL_ORDER = {"公开": 0, "内部": 1, "秘密": 2, "机密": 3}

ROLE_NAMES = {
    "admin": "管理员",
    "cataloger": "编目员",
    "keeper": "库房管理员",
    "reader": "借阅员",
    "auditor": "审计员",
}
# 各岗位默认可查看的最高密级
ROLE_CLEARANCE = {
    "admin": "机密",
    "cataloger": "秘密",
    "keeper": "秘密",
    "reader": "内部",
    "auditor": "机密",
}

LOCATION_ROLES = {"admin", "keeper"}      # 可见库位字段的岗位
LOAN_ROLES = {"admin", "reader", "auditor"}  # 可见借阅字段的岗位

MASK = "●●●●（无权查看）"


class PermissionError(Exception):
    """业务权限不足（HTTP 层映射为 403）。"""


def level_ok(clearance, level):
    return LEVEL_ORDER.get(clearance, 0) >= LEVEL_ORDER.get(level, 0)


def can_see_location(user, level):
    return user["role"] in LOCATION_ROLES and level_ok(user["clearance"], level)


def can_see_loan(user, level):
    return user["role"] in LOAN_ROLES and level_ok(user["clearance"], level)


def can_see_title(user, level):
    return level_ok(user["clearance"], level)


# ---- 操作级权限（角色矩阵） ---------------------------------------------------

ACTION_ROLES = {
    "catalog.create": {"admin", "cataloger"},
    "fonds.create": {"admin", "cataloger"},
    "packing.plan": {"admin", "cataloger"},
    "packing.commit": {"admin", "cataloger"},
    "inbound": {"admin", "keeper"},
    "move.create": {"admin", "keeper"},
    "move.handle": {"admin", "keeper"},
    "stocktake.create": {"admin", "keeper"},
    "stocktake.count": {"admin", "keeper"},
    "stocktake.confirm": {"admin", "auditor"},
    "loan.apply": {"admin", "reader"},
    "loan.approve": {"admin"},
    "loan.borrow": {"admin", "reader", "keeper"},
    "loan.return": {"admin", "reader", "keeper"},
    "discrepancy.resolve": {"admin", "keeper", "auditor"},
    "discrepancy.supplement": {"admin", "auditor"},
}


def require(action, user):
    if user["role"] not in ACTION_ROLES.get(action, {"admin"}):
        raise PermissionError(f"{ROLE_NAMES.get(user['role'], user['role'])}无权执行：{action}")
    return True


def mask_record(row, user):
    """对案卷行做视图脱敏，返回新 dict。row 为 sqlite3.Row 或 mapping。"""
    d = dict(row)
    lvl = d.get("security_level", "公开")
    if not can_see_title(user, lvl):
        d["title"] = MASK
    # 档号始终保留：盘点与编目必须按档号核对
    d["_title_masked"] = not can_see_title(user, lvl)
    return d


def mask_location_text(slot_text, level, user):
    if slot_text and not can_see_location(user, level):
        return MASK
    return slot_text


def mask_box(row, user, slot_text=None):
    """盒视图：按岗位/密级隐藏库位与标题性信息。"""
    d = dict(row)
    lvl = d.get("security_level", "公开")
    if not can_see_location(user, lvl):
        d["slot_code"] = None
        d["location_masked"] = True
    elif slot_text is not None:
        d["slot_code"] = slot_text
        d["location_masked"] = False
    return d


def mask_loan(row, user, level="公开"):
    """借阅视图：非借阅岗位隐藏借阅人/用途/期限。"""
    d = dict(row)
    if not can_see_loan(user, level):
        for f in ("borrower", "borrower_org", "purpose", "due_date"):
            if f in d:
                d[f] = MASK
        d["loan_masked"] = True
    else:
        d["loan_masked"] = False
    return d


def current_user(conn, username):
    if not username:
        return None
    row = conn.execute(
        "SELECT * FROM users WHERE username=? AND active=1", (username,)
    ).fetchone()
    return dict(row) if row else None


def login_user(conn, username):
    """演示环境无密码：选择岗位身份即登录。未知用户自动拒绝。"""
    return current_user(conn, username)
