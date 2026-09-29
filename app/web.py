"""零依赖 Web 层：http.server + 服务端渲染 HTML。

页面：总览 / 案卷编目 / 装盒清单 / 库位地图(含移位单) / 盘点任务 / 借阅审批 / 差异处理。
身份通过 cookie uid 选择（演示环境无密码），岗位不同看到的字段不同（密级脱敏）。
"""
import html
import json
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db, services as svc
from .security import current_user, PermissionError, ROLE_NAMES, ROLE_CLEARANCE, LEVEL_ORDER

# ---- 主题：纸张米色 / 炭黑 / 青绿 -------------------------------------------
BG = "#f4ecd8"      # 纸张米色
BG2 = "#efe6cd"
INK = "#2b2722"     # 炭黑
TEAL = "#1f8a7a"    # 青绿
TEAL_D = "#176e61"
TEAL_L = "#d9ece7"
WARN = "#b5552d"
LINE = "#d8ccb0"

CSS = f"""
* {{ box-sizing: border-box; }}
body {{ margin:0; background:{BG}; color:{INK};
  font-family:"Songti SC","Noto Serif CJK SC","Source Han Serif SC",Georgia,serif;
  font-size:15px; line-height:1.55; }}
header {{ background:{INK}; color:{BG}; padding:10px 22px; display:flex;
  align-items:center; gap:18px; flex-wrap:wrap; }}
header .brand {{ font-size:18px; font-weight:700; letter-spacing:2px; }}
header a {{ color:{BG}; text-decoration:none; opacity:.82; font-size:14px; }}
header a:hover, header a.active {{ opacity:1; text-decoration:underline; text-underline-offset:5px; }}
.who {{ margin-left:auto; font-size:13px; display:flex; align-items:center; gap:8px; }}
.who select {{ background:{BG}; color:{INK}; border:none; border-radius:3px; padding:3px 6px; }}
main {{ max-width:1180px; margin:22px auto 60px; padding:0 20px; }}
h1 {{ font-size:22px; margin:4px 0 14px; }}
h2 {{ font-size:17px; margin:26px 0 10px; border-left:4px solid {TEAL}; padding-left:9px; }}
.card {{ background:#fbf6e9; border:1px solid {LINE}; border-radius:8px; padding:16px 18px; margin:14px 0;
  box-shadow:0 1px 0 rgba(43,39,34,.05); }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:12px; }}
.stat {{ background:#fbf6e9; border:1px solid {LINE}; border-radius:8px; padding:12px 14px; }}
.stat b {{ display:block; font-size:26px; color:{TEAL_D}; }}
.stat span {{ font-size:13px; color:#6d6557; }}
table {{ border-collapse:collapse; width:100%; background:#fbf6e9; }}
th, td {{ border:1px solid {LINE}; padding:7px 9px; text-align:left; vertical-align:top; font-size:14px; }}
th {{ background:{BG2}; font-weight:700; white-space:nowrap; }}
tr:nth-child(even) td {{ background:#f7f0df; }}
.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  font-size:12.8px; letter-spacing:.2px; user-select:all; -webkit-user-select:all;
  word-break:break-all; white-space:normal; }}
.copy-hint {{ color:#9a8f76; font-size:11.5px; }}
button, .btn {{ background:{TEAL}; color:#fff; border:0; border-radius:5px; padding:6px 14px;
  font-size:14px; cursor:pointer; font-family:inherit; text-decoration:none; display:inline-block; }}
button:hover, .btn:hover {{ background:{TEAL_D}; }}
button.ghost {{ background:transparent; color:{TEAL_D}; border:1px solid {TEAL}; }}
button.danger {{ background:{WARN}; }}
button.mini {{ padding:2px 9px; font-size:12.5px; }}
input, select, textarea {{ font-family:inherit; font-size:14px; padding:5px 8px; border:1px solid #b9ac8c;
  border-radius:4px; background:#fffdf6; color:{INK}; }}
.form-row {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin:8px 0; }}
.form-row label {{ font-size:13.5px; color:#5c5446; }}
.tag {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; white-space:nowrap; }}
.tg-open {{ background:{TEAL_L}; color:{TEAL_D}; }}
.tg-warn {{ background:#f6e0d2; color:{WARN}; }}
.tg-mute {{ background:#e7e0cd; color:#6d6557; }}
.tg-secret {{ background:#efe3c2; color:#7a5b12; }}
.muted {{ color:#8a8067; }}
.flash {{ background:#f6e0d2; border:1px solid {WARN}; color:{WARN}; padding:10px 14px;
  border-radius:6px; margin:12px 0; }}
.ok {{ background:{TEAL_L}; border-color:{TEAL}; color:{TEAL_D}; }}
/* 货架网格 */
.grid-room {{ margin:18px 0; }}
.shelf-grid {{ display:grid; gap:7px; margin-top:8px;
  grid-template-columns:96px repeat(var(--layers), minmax(132px,1fr)); }}
.shelf-grid .gh {{ background:{TEAL_D}; color:{BG}; border-radius:5px; padding:5px 8px;
  font-size:13px; text-align:center; align-self:center; }}
.shelf-grid .gl {{ background:{BG2}; border:1px solid {LINE}; border-radius:5px; padding:6px 8px;
  font-size:13px; font-weight:700; text-align:center; align-self:center; }}
.cell {{ border:1.5px solid {LINE}; border-radius:6px; padding:7px 8px; min-height:64px; background:#fbf6e9; }}
.cell.full {{ border-color:{TEAL}; background:{TEAL_L}; }}
.cell.loan {{ border-color:{WARN}; }}
.cell.move {{ border-style:dashed; border-color:{TEAL_D}; }}
.cell .slot {{ font-size:11.5px; color:#8a8067; }}
.small {{ font-size:12.5px; }}
.col-main {{ background:{BG2}!important; }}
a {{ color:{TEAL_D}; }}
.pillset {{ display:flex; gap:6px; flex-wrap:wrap; }}
footer {{ text-align:center; color:#9a8f76; font-size:12px; margin:30px 0 10px; }}
"""

NAV = [
    ("/", "总览"),
    ("/catalog", "案卷编目"),
    ("/boxes", "装盒清单"),
    ("/locations", "库位地图"),
    ("/stocktake", "盘点任务"),
    ("/loans", "借阅审批"),
    ("/discrepancies", "差异处理"),
]

STATUS_TAG = {
    "unboxed": ("未装盒", "tg-mute"), "boxed": ("已装盒", "tg-open"),
    "inbound": ("在库", "tg-open"), "loaned": ("在借", "tg-warn"),
    "missing": ("缺卷", "tg-warn"), "moving": ("移位中", "tg-warn"),
    "packed": ("已装盒", "tg-open"),
    "draft": ("待出库", "tg-warn"), "in_transit": ("在途", "tg-warn"),
    "done": ("已交接", "tg-open"), "interrupted": ("中断", "tg-warn"),
    "cancelled": ("已撤销", "tg-mute"),
    "open": ("进行中·已冻结", "tg-warn"), "confirmed": ("已确认", "tg-open"),
    "pending": ("待审批", "tg-warn"), "approved": ("已批准", "tg-open"),
    "rejected": ("已驳回", "tg-mute"), "borrowed": ("已借出", "tg-warn"),
    "returned": ("已归还", "tg-mute"),
    "resolved": ("已处理", "tg-open"), "supplemented": ("已补录", "tg-open"),
    "present": ("相符", "tg-open"), "fewer_items": ("少件", "tg-warn"),
    "wrong_box": ("错盒", "tg-warn"), "label_damaged": ("标签损坏", "tg-warn"),
}
SECRET_TAG = {"公开": "tg-open", "内部": "tg-open", "秘密": "tg-secret", "机密": "tg-secret"}


def esc(s):
    return html.escape(str(s if s is not None else ""))


def tag(status):
    name, cls = STATUS_TAG.get(status, (status, "tg-mute"))
    return f'<span class="tag {cls}">{esc(name)}</span>'


def secret_tag(level):
    return f'<span class="tag {SECRET_TAG.get(level, "tg-mute")}">{esc(level)}</span>'


def page(title, body, user, active="/"):
    with db.get_conn() as conn:
        urows = conn.execute(
            "SELECT username, display_name, role FROM users WHERE active=1 ORDER BY id"
        ).fetchall()
    opts = []
    for u in urows:
        sel = " selected" if user and user["username"] == u["username"] else ""
        opts.append(
            f'<option value="{esc(u["username"])}"{sel}>'
            f'{esc(u["display_name"])}（{ROLE_NAMES.get(u["role"], u["role"])}）</option>')
    nav = "".join(
        f'<a href="{u}"{" class=active" if u == active else ""}>{n}</a>' for u, n in NAV
    )
    clr = ROLE_NAMES.get(user["role"], "") if user else ""
    return f"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{esc(title)} · 档案装盒编目与库位盘点</title><style>{CSS}</style></head>
<body><header>
<span class=brand>档·案·库</span>{nav}
<form class=who method=post action="/switch-user">
<span>{esc(clr)}</span><select name=username onchange="this.form.submit()">{''.join(opts)}</select>
<noscript><button class=mini type=submit>切换</button></noscript></form>
</header><main>{body}</main>
<footer>虚构演示数据 · SQLite 保存档号与库位 · 附件存于本地目录 · 进程内任务生成盘点提醒</footer>
</body></html>"""


def flash(msg, ok=False):
    return f'<div class="flash {"ok" if ok else ""}">{esc(msg)}</div>'


# ---------------------------------------------------------------- 页面渲染

def _fonds_options(conn, selected=None):
    rows = svc.list_fonds(conn)
    return "".join(
        f'<option value="{r["id"]}"{" selected" if str(r["id"])==str(selected) else ""}>'
        f'{esc(r["code"])} {esc(r["name"])}</option>' for r in rows)


def view_dashboard(conn, user):
    def c(sql, *a):
        return conn.execute(sql, a).fetchone()[0]
    stats = [
        (c("SELECT COUNT(*) FROM records"), "案卷条目"),
        (c("SELECT COUNT(*) FROM boxes"), "档案盒"),
        (c("SELECT COUNT(*) FROM boxes WHERE status='inbound'"), "在库盒"),
        (c("SELECT COUNT(*) FROM boxes WHERE status='loaned'"), "借出盒"),
        (c("SELECT COUNT(*) FROM stock_takes WHERE status='open'"), "冻结盘点"),
        (c("SELECT COUNT(*) FROM discrepancies WHERE status='open'"), "待处理差异"),
        (c("SELECT COUNT(*) FROM move_orders WHERE status='in_transit'"), "移位在途"),
        (c("SELECT COUNT(*) FROM loans WHERE status='borrowed' AND due_date<date('now')"), "超期借阅"),
    ]
    cards = "".join(f'<div class=stat><b>{n}</b><span>{esc(l)}</span></div>' for n, l in stats)
    rems = svc.list_reminders(conn)
    rem_html = "".join(
        f'<tr><td>{esc(r["created_at"])}</td><td>{esc(r["kind"])}</td><td>{esc(r["message"])}</td></tr>'
        for r in rems) or '<tr><td colspan=3 class=muted>暂无提醒</td></tr>'
    diffs = svc.list_discrepancies(conn, user, status="open")[:8]
    d_html = "".join(
        f'<tr><td>{esc(svc.DIFF_TYPE_NAMES[d["type"]])}</td><td>{esc(d["title"])}</td>'
        f'<td>{esc(d["slot_code"] or "—")}</td></tr>' for d in diffs
    ) or '<tr><td colspan=3 class=muted>暂无待处理差异</td></tr>'
    body = f"""
<h1>总览</h1>
<div class=cards>{cards}</div>
<h2>盘点提醒（进程内任务）</h2>
<div class=card><table><tr><th>时间</th><th>类型</th><th>内容</th></tr>{rem_html}</table></div>
<h2>待处理差异</h2>
<div class=card><table><tr><th>类型</th><th>事项</th><th>库位</th></tr>{d_html}</table>
<p class=small><a href="/discrepancies">前往差异处理 →</a></p></div>
<p class=small muted>密级脱敏规则：库位字段仅库房岗可见；借阅字段仅借阅岗与审计岗可见；标题按本人密级显示，档号始终保留以便核对。</p>
"""
    return page("总览", body, user, "/")


def view_catalog(conn, user):
    fonds = svc.list_fonds(conn)
    f_html = "".join(
        f'<tr><td class=mono>{esc(f["code"])}</td><td>{esc(f["name"])}</td><td>{esc(f["note"])}</td></tr>'
        for f in fonds) or '<tr><td colspan=3 class=muted>暂无全宗</td></tr>'
    recs = svc.list_records(conn, user)
    r_html = "".join(
        f'<tr><td class=mono>{esc(r["archive_no"])}<div class=copy-hint>点击可全选复制</div></td>'
        f'<td>{esc(r["fonds_code"])}</td><td>{r["year"]}</td><td>{esc(r["retention"])}</td>'
        f'<td>{esc(r["title"])}{" <span class=muted>(标题已脱敏)</span>" if r.get("_title_masked") else ""}</td>'
        f'<td>{secret_tag(r["security_level"])}</td><td>{tag(r["status"])}</td></tr>'
        for r in recs)
    plan_groups = svc.packing_preview(conn, user)
    plan_html = ""
    for g in plan_groups:
        gap = ""
        if g["gaps"]:
            gap = ('<p class="small" style="color:' + WARN + '">⚠ 序号断档（疑似缺卷）：' +
                   esc("，".join(f"{a}→{b}" for a, b in g["gaps"])) + "，断档处已自动切盒</p>")
        box_h = "".join(
            "<div class=small>第 %d 盒（%d 件）：<span class=mono>%s … %s</span></div>"
            % (i + 1, len(b), esc(b[0]["archive_no"]), esc(b[-1]["archive_no"]))
            for i, b in enumerate(g["boxes"]))
        plan_html += f"""
<div class=card><b>{esc(g['fonds_code'])} · {g['year']} 年度 · {esc(g['retention'])}</b>
（{len(g['records'])} 条未装盒，建议 {len(g['boxes'])} 盒）
{box_h}{gap}
<form method=post action="/packing/commit" class=form-row>
<input type=hidden name=fonds_id value={g['fonds_id']}>
<input type=hidden name=year value={g['year']}>
<input type=hidden name=retention value="{esc(g['retention'])}">
<button class=mini type=submit>按建议装盒（档号连续切盒）</button></form></div>"""
    body = f"""
<h1>案卷编目</h1>
<h2>新建全宗</h2>
<div class=card><form method=post action="/fonds/new" class=form-row>
<label>全宗号<input name=code required placeholder="X001"></label>
<label>名称<input name=name required placeholder="××局"></label>
<label>备注<input name=note></label><button type=submit>新建</button></form></div>
<table><tr><th>全宗号</th><th>名称</th><th>备注</th></tr>{f_html}</table>
<h2>案卷登记（虚构编号，不上传真实文件）</h2>
<div class=card><form method=post action="/records/new" class=form-row>
<label>全宗<select name=fonds_id required>{_fonds_options(conn)}</select></label>
<label>年度<input name=year type=number value=2025 required></label>
<label>保管期限<select name=retention><option>永久</option><option>定期30年</option><option>定期10年</option></select></label>
<label>密级<select name=security_level>{"".join(f"<option>{l}</option>" for l in LEVEL_ORDER)}</select></label>
<label>题名<input name=title required placeholder="虚构案卷题名"></label>
<button type=submit>登记并生成档号</button></form>
<p class=small muted>档号规则：全宗号-年度-期限码-序号，全局唯一；序号由系统在同全宗/年度/期限内递增。</p></div>
<div class=card style="max-height:340px;overflow:auto">
<table><tr><th>档号（等宽可复制）</th><th>全宗</th><th>年度</th><th>期限</th><th>题名</th><th>密级</th><th>状态</th></tr>{r_html}</table></div>
<h2>装盒建议（按年度与保管期限）</h2>
{plan_html or '<p class=muted>暂无未装盒案卷</p>'}
"""
    return page("案卷编目", body, user, "/catalog")


def view_boxes(conn, user):
    boxes = svc.list_boxes(conn, user, with_records=True)
    locs = svc.list_locations(conn)
    loc_opts = "".join(
        f'<option value="{l["id"]}">{esc(l["slot_code"])}</option>' for l in locs if l["used"] < l["capacity"])
    rows = ""
    for b in boxes:
        recs = "".join(
            f'<div class=small>{r["box_seq"]:02d}. <span class=mono>{esc(r["archive_no"])}</span>'
            f' {esc(r["title"])}{" 🔒" if r.get("_title_masked") else ""}</div>'
            for r in b["records"])
        slot = esc(b["slot_code"]) if b.get("slot_code") else ("●●●●（无权查看）" if b.get("location_masked") and b["status"] in ("inbound", "loaned", "moving") else "—")
        action = ""
        if b["status"] == "packed":
            action = (f'<form method=post action="/boxes/inbound" class=form-row>'
                      f'<input type=hidden name=box_id value={b["id"]}>'
                      f'<select name=location_id required>{loc_opts}</select>'
                      f'<button class=mini type=submit>入库绑定库位</button></form>')
        rows += f"""<tr><td class=mono>{esc(b['box_no'])}</td><td>{secret_tag(b['security_level'])}</td>
<td>{b['year']}·{esc(b['retention'])}</td><td>{tag(b['status'])}</td><td>{slot}</td>
<td>{len(b['records'])} 件<div class=copy-hint>盒内档号连续</div>{recs}</td><td>{action}</td></tr>"""
    body = f"""<h1>装盒清单</h1>
<div class=card><table><tr><th>盒号</th><th>盒密级</th><th>年度/期限</th><th>状态</th><th>库位</th><th>盒内档号清单（连续不可重复）</th><th>操作</th></tr>{rows}</table></div>
<p class=small muted>入库时才绑定库房/货架/层位；盘点冻结或在借/移位中的盒不能重复入库。</p>
"""
    return page("装盒清单", body, user, "/boxes")


def view_locations(conn, user):
    locs = svc.list_locations(conn)
    boxes = svc.list_boxes(conn, user)
    slot_boxes = {}
    for b in boxes:
        if b.get("slot_code"):
            slot_boxes.setdefault(b["slot_code"], []).append(b)
    rooms = sorted({l["room_code"] for l in locs})
    grids = ""
    for room in rooms:
        rlocs = [l for l in locs if l["room_code"] == room]
        shelves = sorted({l["shelf_code"] for l in rlocs})
        max_layer = max((l["layer_no"] for l in rlocs), default=1)
        head = "".join(f'<div class=gh>{i} 层</div>' for i in range(1, max_layer + 1))
        rows_ = ""
        for sh in shelves:
            cells = ""
            for i in range(1, max_layer + 1):
                loc = next((x for x in rlocs if x["shelf_code"] == sh and x["layer_no"] == i), None)
                if not loc:
                    cells += '<div class=cell><span class=slot>—</span></div>'
                    continue
                bs = slot_boxes.get(loc["slot_code"], [])
                cls = "cell full" + (" loan" if any(x["status"] == "loaned" for x in bs) else
                                     "") + (" move" if any(x["status"] == "moving" for x in bs) else "")
                inner = "".join(
                    f'<div class=small><span class=mono>{esc(x["box_no"])}</span> {tag(x["status"])}</div>'
                    for x in bs) or '<span class=small muted>空位</span>'
                cells += (f'<div class="{cls}"><span class=slot>{esc(loc["slot_code"])} '
                          f'({loc["used"]}/{loc["capacity"]})</span>{inner}</div>')
            rows_ += f'<div class=gl>{esc(sh)} 架</div>{cells}'
        grids += f"""<div class=grid-room><h2>库房 {esc(room)}</h2>
<div class=shelf-grid style="--layers:{max_layer}"><div class=gh>货架 ＼ 层位</div>{head}{rows_}</div></div>"""

    # 移位单
    moves = svc.list_moves(conn)
    can_loc = user["role"] in ("admin", "keeper")
    box_opts = "".join(f'<option value="{b["id"]}">{esc(b["box_no"])}</option>'
                       for b in boxes if b["status"] == "inbound" and b.get("slot_code"))
    to_opts = "".join(f'<option value="{l["id"]}">{esc(l["slot_code"])}</option>'
                      for l in locs if l["used"] < l["capacity"])
    m_rows = ""
    for m in moves:
        acts = ""
        if m["status"] == "draft":
            acts = (f'<button class=mini formaction="/moves/start" name=order_id value={m["id"]}>出库搬运</button> '
                    f'<button class="mini danger" formaction="/moves/interrupt" name=order_id value={m["id"]}>中断</button>')
        elif m["status"] == "in_transit":
            acts = (f'<button class=mini formaction="/moves/receive" name=order_id value={m["id"]}>接收交接</button> '
                    f'<button class="mini danger" formaction="/moves/interrupt" name=order_id value={m["id"]}>中断</button>')
        m_rows += (f'<tr><td class=mono>{esc(m["order_no"])}</td><td class=mono>{esc(m["box_no"])}</td>'
                   f'<td>{esc(m["from_slot"] or "—")} → {esc(m["to_slot"])}</td><td>{tag(m["status"])}</td>'
                   f'<td>{esc(m["applicant"])} / {esc(m["handler"] or "—")} / {esc(m["receiver"] or "—")}</td>'
                   f'<td class=small>{esc(m["note"])}</td><td><form method=post class=pillset>{acts}</form></td></tr>')
    new_loc = ""
    if can_loc:
        new_loc = f"""<div class=card><form method=post action="/locations/new" class=form-row>
<label>库房<input name=room_code required placeholder="KF1"></label>
<label>货架<input name=shelf_code required placeholder="A"></label>
<label>层<input name=layer_no type=number value=1 min=1></label>
<label>容量<input name=capacity type=number value=1 min=1></label>
<button type=submit>新增库位</button></form></div>"""
    new_move = ""
    if can_loc:
        new_move = f"""<div class=card><form method=post action="/moves/new" class=form-row>
<label>盒<select name=box_id required>{box_opts}</select></label>
<label>目标库位<select name=to_loc_id required>{to_opts}</select></label>
<label>事由<input name=note placeholder="调架事由"></label>
<button type=submit>先生成移位单（不动库位）</button></form></div>"""
    body = f"""<h1>库位地图</h1>{new_loc}{grids or '<p class=muted>暂无库位</p>'}
<h2>调架移位</h2>{new_move}
<div class=card><table><tr><th>移位单号</th><th>盒号</th><th>库位变化</th><th>状态</th>
<th>申请人/搬运人/接收人</th><th>备注</th><th>交接操作</th></tr>{m_rows or '<tr><td colspan=7 class=muted>暂无移位单</td></tr>'}</table>
<p class=small muted>调架必须先开移位单；出库后盒为“移位中”，接收交接完成库位才变更；在途中止会退回原位并登记“移位中断”差异。</p></div>"""
    return page("库位地图", body, user, "/locations")


def view_stocktake(conn, user, task_id=None, err=""):
    tasks = svc.list_stocktakes(conn)
    locs = svc.list_locations(conn)
    rooms = sorted({l["room_code"] for l in locs})
    room_opts = "".join(f'<option>{r}</option>' for r in rooms)
    t_rows = "".join(
        f'<tr><td class=mono>{esc(t["task_no"])}</td><td>{esc(t["scope_room"])}/{esc(t["scope_shelf"] or "")}</td>'
        f'<td>{tag(t["status"])}</td><td>{t["pending"] or 0}/{t["total"]}</td><td>{t["diffs"] or 0}</td>'
        f'<td>{esc(t["created_by"])}<div class=small muted>{esc(t["created_at"])}</div></td>'
        f'<td><a class="btn mini" href="/stocktake?task_id={t["id"]}">打开</a></td></tr>'
        for t in tasks)
    detail = ""
    if task_id:
        task, items = svc.stocktake_detail(conn, user, int(task_id))
        i_rows = ""
        for it in items:
            res = it["result"]
            count_field = ""
            miss_field = ""
            attach = ""
            box = conn.execute("SELECT * FROM boxes WHERE id=?", (it["box_id"],)).fetchone()
            n_recs = conn.execute("SELECT COUNT(*) c FROM records WHERE box_id=?", (it["box_id"],)).fetchone()["c"]
            if task["status"] == "open":
                count_field = ('<label class=small>实有件数<input name=actual_count type=number min=0 '
                               f'value={n_recs} style="width:70px"></label>')
                miss_field = ('<label class=small>缺卷档号(逗号分隔)<input name=missing_archive_nos '
                               'placeholder="档号…" class=mono style="width:200px"></label>')
                attach = (f'<button class=mini ghost formaction="/stocktake/attachment" name=box_id '
                          f'value={it["box_id"]} title="在本地附件目录登记一个虚构编号照片占位">登记照片(虚构编号)</button>')
            i_rows += f"""<tr><td class=mono>{esc(it['box_no'])}</td>
<td>{esc(it['expected_slot'] or '—')}</td><td>{tag(res) if res else '<span class=muted>未清点</span>'}</td>
<td>{it['actual_count'] if it['actual_count'] is not None else '—'}</td>
<td class=small>{esc(it['note'])}<div class=muted>{esc(it['counted_by'] or '')} {esc(it['counted_at'] or '')}</div></td>
<td>{('<form method=post action="/stocktake/count" class=pillset>'
      '<input type=hidden name=task_id value=' + str(task["id"]) + '>'
      '<input type=hidden name=box_id value=' + str(it["box_id"]) + '>'
      '<select name=result>'
      '<option value=present>相符</option><option value=missing>缺盒</option>'
      '<option value=wrong_box>错盒</option><option value=label_damaged>标签损坏</option>'
      f'<option value=fewer_items>少件</option></select>{count_field}{miss_field}'
      '<input name=note placeholder="备注" style="width:130px">'
      f'<button class=mini>提交清点</button>{attach}</form>') if task["status"] == "open" else ""}</td></tr>"""
        confirm = ""
        if task["status"] == "open":
            confirm = (f'<form method=post action="/stocktake/confirm" class=form-row>'
                       f'<input type=hidden name=task_id value={task["id"]}>'
                       '<button type=submit>确认盘点结果（冻结解除，不直接改库存）</button></form>')
        detail = f"""
<h2>盘点单 <span class=mono>{esc(task['task_no'])}</span> {tag(task['status'])}</h2>
<div class=card>{flash(err) if err else ''}
<table><tr><th>盒号</th><th>应有库位</th><th>结果</th><th>实有件数</th><th>备注</th><th>清点登记</th></tr>{i_rows}</table>
{confirm}<p class=small muted>确认后只固化盘点结论并解除冻结；补录/改库存必须到差异处理页注明依据。</p></div>"""
    body = f"""<h1>盘点任务</h1>
<div class=card><form method=post action="/stocktake/new" class=form-row>
<label>库房<select name=room_code>{room_opts}</select></label>
<label>货架(可空)<input name=shelf_code placeholder="整库房"></label>
<label>说明<input name=note></label><button type=submit>创建盘点任务并冻结范围</button></form>
<p class=small muted>创建后范围内盒的入库、调架、借出全部冻结，直至确认。</p></div>
<div class=card><table><tr><th>任务号</th><th>范围</th><th>状态</th><th>清点进度</th><th>差异</th><th>创建人</th><th></th></tr>{t_rows}</table></div>
{detail}"""
    return page("盘点任务", body, user, "/stocktake")


def view_loans(conn, user):
    loans = svc.list_loans(conn, user)
    boxes = svc.list_boxes(conn, user)
    box_opts = "".join(f'<option value="{b["id"]}">{esc(b["box_no"])}</option>'
                       for b in boxes if b["status"] == "inbound")
    rows = ""
    for ln in loans:
        acts = ""
        if ln["status"] == "pending":
            acts = (f'<button class=mini formaction="/loans/approve" name=loan_id value={ln["id"]}>批准</button> '
                    f'<button class=mini danger formaction="/loans/reject" name=loan_id value={ln["id"]}">驳回</button>')
        elif ln["status"] == "approved":
            acts = f'<button class=mini formaction="/loans/borrow" name=loan_id value={ln["id"]}>出库借出</button>'
        elif ln["status"] == "borrowed":
            acts = f'<button class=mini formaction="/loans/return" name=loan_id value={ln["id"]}>归还</button>'
        # 审批状态与归还期限置于主列（col-main 高亮）
        rows += f"""<tr><td class=mono>{esc(ln['loan_no'])}</td>
<td class=mono>{esc(ln['box_no'])}</td>
<td class=col-main>{tag(ln['status'])}{('<div class=small muted>'+esc(ln['reject_reason'])+'</div>') if ln['reject_reason'] else ''}</td>
<td class="col-main mono">{esc(ln['due_date'])}</td>
<td>{esc(ln['borrower'])}{('<div class=small muted>'+esc(ln['borrower_org'])+'</div>') if ln.get('borrower_org') and ln.get('borrower_org')!='●●●●（无权查看）' else ''}</td>
<td>{esc(ln['purpose'])}</td>
<td class=small>{esc(ln['approved_by'] or '—')}</td>
<td><form method=post class=pillset>{acts}</form></td></tr>"""
    body = f"""<h1>借阅审批</h1>
<div class=card><form method=post action="/loans/apply" class=form-row>
<label>盒<select name=box_id required>{box_opts}</select></label>
<label>借阅人<input name=borrower required></label>
<label>单位<input name=borrower_org></label>
<label>用途<input name=purpose required></label>
<label>天数<input name=days type=number value=14 min=1 style="width:64px"></label>
<label class=small>幂等键<input name=idem_key placeholder="重复提交返回原单" class=mono></label>
<button type=submit>提交申请</button></form>
<p class=small muted>同一盒同时只允许一人借出；带相同幂等键的重复提交直接返回原借阅单。</p></div>
<div class=card><table><tr><th>借阅单号</th><th>盒号</th><th class=col-main>审批状态（主列）</th>
<th class=col-main>归还期限（主列）</th><th>借阅人/单位</th><th>用途</th><th>审批人</th><th>操作</th></tr>{rows}</table></div>"""
    return page("借阅审批", body, user, "/loans")


def view_discrepancies(conn, user, err=""):
    grouped = svc.list_discrepancies(conn, user, group_by_location=True)
    blocks = ""
    for g in grouped:
        rows = ""
        for d in g["items"]:
            act = ""
            if d["status"] == "open":
                act = (f'<form method=post action="/discrepancies/resolve" class=form-row>'
                       f'<input type=hidden name=diff_id value={d["id"]}>'
                       '<input name=resolution placeholder="处理说明" required style="width:170px">'
                       '<button class=mini>登记处理</button></form>'
                       f'<form method=post action="/discrepancies/supplement" class=form-row>'
                       f'<input type=hidden name=diff_id value={d["id"]}>'
                       '<input name=basis placeholder="补录依据（必填）" required style="width:170px">'
                       '<button class=mini ghost>补录(注明依据)</button></form>')
            extra = esc(d["archive_no"] or "")
            rows += (f'<tr><td>{esc(svc.DIFF_TYPE_NAMES[d["type"]])}</td>'
                     f'<td>{esc(d["title"])}<div class=small muted>{esc(d["detail"])}</div>'
                     f'{("<span class=mono>" + esc(extra) + "</span>") if extra else ""}</td>'
                     f'<td>{tag(d["status"])}<div class=small muted>{esc(d["resolution"] or esc(d["basis"]))}</div></td>'
                     f'<td>{act}</td></tr>')
        blocks += (f'<div class=card><h2 style="margin-top:4px">库位 <span class=mono>{esc(g["slot_code"])}</span>'
                   f' <span class="tag tg-mute">{len(g["items"])} 条</span></h2>'
                   f'<table><tr><th>类型</th><th>事项 / 档号</th><th>状态</th><th>处理 / 补录</th></tr>{rows}</table></div>')
    body = f"""<h1>差异处理（按库位聚合）</h1>
{flash(err) if err else ''}
{blocks or '<p class=muted>暂无差异记录</p>'}
<p class=small muted>六类差异：缺卷、错盒、标签损坏、盘点少件、移位中断、借阅超期。盘点确认后不会直接改库存；
补录必须填写依据（凭证/审批单号）。</p>"""
    return page("差异处理", body, user, "/discrepancies")


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "Archive/1.0"

    def log_message(self, fmt, *args):
        pass

    # -- 身份 --
    def _user(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        uname = cookie["uid"].value if "uid" in cookie else "admin"
        with db.get_conn() as conn:
            return current_user(conn, uname)

    def _render(self, html_text, code=200):
        data = html_text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, path):
        self.send_response(303)
        self.send_header("Location", path)
        self.end_headers()

    def _form(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def _error(self, user, msg, code=409):
        self._render(page("操作未完成",
                          f'<h1>操作未完成</h1>{flash(msg)}<p><a class=btn href="javascript:history.back()">返回</a></p>',
                          user), code)

    # -- GET --
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        user = self._user()
        try:
            with db.get_conn() as conn:
                if path == "/":
                    out = view_dashboard(conn, user)
                elif path == "/catalog":
                    out = view_catalog(conn, user)
                elif path == "/boxes":
                    out = view_boxes(conn, user)
                elif path == "/locations":
                    out = view_locations(conn, user)
                elif path == "/stocktake":
                    err = qs.get("err", [""])[0]
                    out = view_stocktake(conn, user, qs.get("task_id", [None])[0], err)
                elif path == "/loans":
                    out = view_loans(conn, user)
                elif path == "/discrepancies":
                    out = view_discrepancies(conn, user, qs.get("err", [""])[0])
                else:
                    self._render(page("未找到", "<h1>404</h1>", user), 404)
                    return
            self._render(out)
        except (svc.BusinessError, PermissionError) as e:
            self._error(user, str(e), 403 if isinstance(e, PermissionError) else 409)

    # -- POST --
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        user = self._user()
        form = self._form()
        try:
            with db.get_conn() as conn:
                def f(k, default=""):
                    return form.get(k, default)

                if path == "/switch-user":
                    self.send_response(303)
                    self.send_header("Set-Cookie", f"uid={f('username')}; Path=/")
                    self.send_header("Location", "/")
                    self.end_headers()
                    return
                elif path == "/fonds/new":
                    svc.create_fonds(conn, user, f("code"), f("name"), f("note"))
                    redir = "/catalog"
                elif path == "/records/new":
                    svc.create_record(conn, user, int(f("fonds_id")), int(f("year")),
                                      f("retention"), f("title"), f("security_level"))
                    redir = "/catalog"
                elif path == "/packing/commit":
                    svc.commit_packing(conn, user, int(f("fonds_id")), int(f("year")), f("retention"))
                    redir = "/boxes"
                elif path == "/locations/new":
                    svc.add_location(conn, user, f("room_code"), f("shelf_code"),
                                     int(f("layer_no")), int(f("capacity") or 1), f("note"))
                    redir = "/locations"
                elif path == "/boxes/inbound":
                    svc.inbound_box(conn, user, int(f("box_id")), int(f("location_id")))
                    redir = "/boxes"
                elif path == "/moves/new":
                    svc.create_move(conn, user, int(f("box_id")), int(f("to_loc_id")), f("note"))
                    redir = "/locations"
                elif path == "/moves/start":
                    svc.start_move(conn, user, int(f("order_id")))
                    redir = "/locations"
                elif path == "/moves/receive":
                    svc.receive_move(conn, user, int(f("order_id")))
                    redir = "/locations"
                elif path == "/moves/interrupt":
                    svc.interrupt_move(conn, user, int(f("order_id")), f("reason", "页面登记中断"))
                    redir = "/locations"
                elif path == "/stocktake/new":
                    _, no, n = svc.create_stocktake(conn, user, f("room_code") or None,
                                                    f("shelf_code") or None, f("note"))
                    redir = "/stocktake"
                elif path == "/stocktake/count":
                    svc.count_item(conn, user, int(f("task_id")), int(f("box_id")), f("result"),
                                   f("note"),
                                   int(f("actual_count")) if f("actual_count") else None,
                                   None, f("missing_archive_nos"))
                    redir = f"/stocktake?task_id={f('task_id')}"
                elif path == "/stocktake/attachment":
                    from . import attachments
                    tid, bid = int(f("task_id")), int(f("box_id"))
                    box = conn.execute("SELECT box_no FROM boxes WHERE id=?", (bid,)).fetchone()
                    task = conn.execute("SELECT task_no FROM stock_takes WHERE id=?", (tid,)).fetchone()
                    existing = len(attachments.list_for_box(tid, bid)) + 1
                    name = f"PHOTO-{task['task_no']}-{box['box_no']}-{existing:02d}.txt"
                    attachments.save(tid, bid, name,
                                     f"虚构盘点照片编号 {name}\n不含真实档案内容。\n".encode())
                    redir = f"/stocktake?task_id={tid}"
                elif path == "/stocktake/confirm":
                    svc.confirm_stocktake(conn, user, int(f("task_id")))
                    redir = f"/stocktake?task_id={f('task_id')}"
                elif path == "/loans/apply":
                    svc.apply_loan(conn, user, int(f("box_id")), f("borrower"), f("purpose"),
                                   int(f("days") or 14), f("idem_key") or None, f("borrower_org"))
                    redir = "/loans"
                elif path == "/loans/approve":
                    svc.approve_loan(conn, user, int(f("loan_id")))
                    redir = "/loans"
                elif path == "/loans/reject":
                    svc.reject_loan(conn, user, int(f("loan_id")), "页面驳回")
                    redir = "/loans"
                elif path == "/loans/borrow":
                    svc.borrow_loan(conn, user, int(f("loan_id")))
                    redir = "/loans"
                elif path == "/loans/return":
                    svc.return_loan(conn, user, int(f("loan_id")))
                    redir = "/loans"
                elif path == "/discrepancies/resolve":
                    svc.resolve_discrepancy(conn, user, int(f("diff_id")), f("resolution"))
                    redir = "/discrepancies"
                elif path == "/discrepancies/supplement":
                    svc.supplement_discrepancy(conn, user, int(f("diff_id")), f("basis"))
                    redir = "/discrepancies"
                else:
                    self._error(user, "未知操作", 404)
                    return
            self._redirect(redir)
        except (svc.BusinessError, PermissionError) as e:
            self._error(user, str(e), 403 if isinstance(e, PermissionError) else 409)


def make_server(host=None, port=None):
    return ThreadingHTTPServer((host or config.HOST, port or config.PORT), Handler)
