"""HTTP API layer for the archive management system.

Uses http.server (standard library only). Provides JSON REST endpoints
for all operations.
"""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import db, services

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


class ArchiveHandler(BaseHTTPRequestHandler):
    """HTTP request handler with JSON API routing."""

    server_version = "ArchiveMS/1.0"

    def log_message(self, format, *args):
        """Suppress default logging to stderr."""
        pass

    # -- helpers ----------------------------------------------------------

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message, code="error"):
        self._send_json({"error": message, "code": code}, status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _get_username(self):
        """Get the current username from headers. Defaults to admin."""
        return self.headers.get("X-Username", "admin")

    def _get_role(self):
        """Get the current user's role."""
        username = self._get_username()
        conn = db.get_conn()
        row = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return "查阅者"
        return row["role"]

    # -- routing ----------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # API routes
        if path.startswith("/api/"):
            self._handle_api_get(path, query)
            return

        # Static files
        self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            self._handle_api_post(path)
            return
        self._send_error(404, "Not found", "not_found")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            self._handle_api_put(path)
            return
        self._send_error(404, "Not found", "not_found")

    # -- API GET ----------------------------------------------------------

    def _handle_api_get(self, path, query):
        username = self._get_username()
        role = self._get_role()

        try:
            # 全宗
            if path == "/api/fonds":
                self._send_json(services.list_fonds())
            # 案卷
            elif path == "/api/files":
                fonds_id = query.get("fonds_id", [None])[0]
                unboxed = query.get("unboxed", [None])[0]
                self._send_json(services.list_files(
                    role,
                    fonds_id=int(fonds_id) if fonds_id else None,
                    unboxed_only=unboxed == "1",
                ))
            elif re.match(r"^/api/files/\d+$", path):
                file_id = int(path.split("/")[-1])
                self._send_json(services.get_file(file_id, role))
            # 装盒建议
            elif path == "/api/boxing/suggest":
                year = int(query.get("year", [0])[0])
                retention = query.get("retention_period", [""])[0]
                if not year or not retention:
                    self._send_error(400, "缺少年份或保管期限", "validation_error")
                    return
                self._send_json(services.suggest_boxing(year, retention))
            # 装盒清单
            elif path == "/api/boxes":
                self._send_json(services.list_boxes(role))
            elif re.match(r"^/api/boxes/\d+$", path):
                box_id = int(path.split("/")[-1])
                self._send_json(services.get_box(box_id, role))
            # 库房
            elif path == "/api/warehouses":
                self._send_json(services.list_warehouses())
            # 货架
            elif path == "/api/shelves":
                wh_id = query.get("warehouse_id", [None])[0]
                self._send_json(services.list_shelves(
                    warehouse_id=int(wh_id) if wh_id else None,
                ))
            # 库位地图
            elif path == "/api/locations":
                wh_id = query.get("warehouse_id", [None])[0]
                self._send_json(services.get_location_map(
                    role,
                    warehouse_id=int(wh_id) if wh_id else None,
                ))
            # 移位单
            elif path == "/api/transfers":
                status = query.get("status", [None])[0]
                self._send_json(services.list_transfers(status=status))
            # 盘点任务
            elif path == "/api/inventory/tasks":
                self._send_json(services.list_inventory_tasks())
            elif re.match(r"^/api/inventory/tasks/\d+$", path):
                task_id = int(path.split("/")[-1])
                self._send_json(services.get_inventory_task(task_id))
            elif re.match(r"^/api/inventory/tasks/\d+/summary$", path):
                task_id = int(path.split("/")[-2])
                self._send_json(services.get_inventory_summary(task_id))
            elif re.match(r"^/api/inventory/tasks/\d+/attachments$", path):
                task_id = int(path.split("/")[-2])
                self._send_json(services.list_attachments(task_id))
            # 差异
            elif path == "/api/differences":
                type_filter = query.get("type", [None])[0]
                status_filter = query.get("status", [None])[0]
                self._send_json(services.list_differences(
                    type_filter=type_filter,
                    status_filter=status_filter,
                ))
            # 借阅
            elif path == "/api/borrowings":
                status_filter = query.get("status", [None])[0]
                self._send_json(services.list_borrowings(role, status_filter=status_filter))
            else:
                self._send_error(404, f"未知接口: {path}", "not_found")
        except services.ServiceError as e:
            status = 400
            if e.code == "not_found":
                status = 404
            elif e.code == "conflict":
                status = 409
            elif e.code == "permission_denied":
                status = 403
            self._send_error(status, str(e), e.code)
        except Exception as e:
            self._send_error(500, str(e), "internal_error")

    # -- API POST ---------------------------------------------------------

    def _handle_api_post(self, path):
        username = self._get_username()
        role = self._get_role()
        body = self._read_body()

        try:
            # 全宗
            if path == "/api/fonds":
                self._send_json(services.create_fonds(body.get("code", ""), body.get("name", "")), 201)
            # 案卷
            elif path == "/api/files":
                self._send_json(services.create_file(
                    fonds_id=int(body["fonds_id"]),
                    archive_number=body["archive_number"],
                    title=body["title"],
                    year=int(body["year"]),
                    retention_period=body["retention_period"],
                    security_level=body.get("security_level", "公开"),
                ), 201)
            # 装盒
            elif path == "/api/boxes":
                self._send_json(services.create_box(
                    box_number=body["box_number"],
                    year=int(body["year"]),
                    retention_period=body["retention_period"],
                    file_ids=body["file_ids"],
                ), 201)
            # 库房
            elif path == "/api/warehouses":
                self._send_json(services.create_warehouse(body.get("code", ""), body.get("name", "")), 201)
            # 货架
            elif path == "/api/shelves":
                self._send_json(services.create_shelf(
                    warehouse_id=int(body["warehouse_id"]),
                    code=body["code"],
                    layers_count=int(body.get("layers_count", 5)),
                ), 201)
            # 入库
            elif path == "/api/warehouse/enter":
                self._send_json(services.enter_warehouse(
                    box_id=int(body["box_id"]),
                    warehouse_id=int(body["warehouse_id"]),
                    shelf_id=int(body["shelf_id"]),
                    layer=int(body["layer"]),
                    username=username,
                ))
            # 移位
            elif path == "/api/transfers":
                self._send_json(services.create_transfer(
                    box_id=int(body["box_id"]),
                    to_warehouse_id=int(body["to_warehouse_id"]),
                    to_shelf_id=int(body["to_shelf_id"]),
                    to_layer=int(body["to_layer"]),
                    username=username,
                ), 201)
            elif re.match(r"^/api/transfers/\d+/confirm$", path):
                transfer_id = int(path.split("/")[-2])
                self._send_json(services.confirm_transfer(transfer_id, username))
            elif re.match(r"^/api/transfers/\d+/interrupt$", path):
                transfer_id = int(path.split("/")[-2])
                self._send_json(services.interrupt_transfer(
                    transfer_id, username, reason=body.get("reason", ""),
                ))
            # 盘点任务
            elif path == "/api/inventory/tasks":
                self._send_json(services.create_inventory_task(body.get("name", ""), username), 201)
            elif re.match(r"^/api/inventory/tasks/\d+/start$", path):
                task_id = int(path.split("/")[-2])
                self._send_json(services.start_inventory_task(task_id, username))
            elif re.match(r"^/api/inventory/tasks/\d+/complete$", path):
                task_id = int(path.split("/")[-2])
                self._send_json(services.complete_inventory_task(task_id, username))
            elif re.match(r"^/api/inventory/tasks/\d+/freeze$", path):
                task_id = int(path.split("/")[-2])
                self._send_json(services.freeze_inventory_task(task_id, username))
            elif re.match(r"^/api/inventory/tasks/\d+/records$", path):
                task_id = int(path.split("/")[-2])
                self._send_json(services.record_inventory_result(
                    task_id=task_id,
                    box_id=int(body["box_id"]),
                    result=body["result"],
                    actual_warehouse_id=body.get("actual_warehouse_id"),
                    actual_shelf_id=body.get("actual_shelf_id"),
                    actual_layer=body.get("actual_layer"),
                    notes=body.get("notes", ""),
                    username=username,
                ), 201)
            elif re.match(r"^/api/inventory/tasks/\d+/supplement$", path):
                task_id = int(path.split("/")[-2])
                self._send_json(services.supplement_inventory_record(
                    task_id=task_id,
                    box_id=int(body["box_id"]),
                    result=body["result"],
                    basis=body["basis"],
                    actual_warehouse_id=body.get("actual_warehouse_id"),
                    actual_shelf_id=body.get("actual_shelf_id"),
                    actual_layer=body.get("actual_layer"),
                    notes=body.get("notes", ""),
                    username=username,
                ), 201)
            # 借阅
            elif path == "/api/borrowings":
                self._send_json(services.create_borrowing(
                    box_id=int(body["box_id"]),
                    borrower=body["borrower"],
                    due_date=body["due_date"],
                    username=username,
                ), 201)
            elif re.match(r"^/api/borrowings/\d+/approve$", path):
                borrowing_id = int(path.split("/")[-2])
                self._send_json(services.approve_borrowing(borrowing_id, username))
            elif re.match(r"^/api/borrowings/\d+/reject$", path):
                borrowing_id = int(path.split("/")[-2])
                self._send_json(services.reject_borrowing(
                    borrowing_id, username, reason=body.get("reason", ""),
                ))
            elif re.match(r"^/api/borrowings/\d+/return$", path):
                borrowing_id = int(path.split("/")[-2])
                self._send_json(services.return_borrowing(borrowing_id, username))
            # 差异处理
            elif re.match(r"^/api/differences/\d+/resolve$", path):
                diff_id = int(path.split("/")[-2])
                self._send_json(services.resolve_difference(
                    diff_id, username, basis=body.get("basis", ""),
                ))
            else:
                self._send_error(404, f"未知接口: {path}", "not_found")
        except services.ServiceError as e:
            status = 400
            if e.code == "not_found":
                status = 404
            elif e.code == "conflict":
                status = 409
            elif e.code == "permission_denied":
                status = 403
            self._send_error(status, str(e), e.code)
        except KeyError as e:
            self._send_error(400, f"缺少必填字段: {e}", "validation_error")
        except Exception as e:
            self._send_error(500, str(e), "internal_error")

    # -- API PUT ----------------------------------------------------------

    def _handle_api_put(self, path):
        # Currently no PUT-only endpoints; delegate to POST handler pattern
        self._send_error(404, f"未知接口: {path}", "not_found")

    # -- static files ------------------------------------------------------

    def _serve_static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        # Prevent path traversal
        safe_path = os.path.normpath(path.lstrip("/"))
        if safe_path.startswith(".."):
            self._send_error(403, "Forbidden", "forbidden")
            return
        full_path = os.path.join(STATIC_DIR, safe_path)
        if not os.path.isfile(full_path):
            self._send_error(404, "Not found", "not_found")
            return
        # Determine content type
        ext = os.path.splitext(full_path)[1].lower()
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
        }
        content_type = content_types.get(ext, "application/octet-stream")
        try:
            with open(full_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self._send_error(500, str(e), "internal_error")


def create_server(host="0.0.0.0", port=8080):
    """Create and return the HTTP server."""
    db.init_db()
    server = ThreadingHTTPServer((host, port), ArchiveHandler)
    return server
