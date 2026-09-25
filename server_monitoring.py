"""Server health, failed-import diagnostics, and bounded application-log access."""

from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import time

from flask import Blueprint, abort, current_app, g, jsonify, request, session

from measurements.settings import ANALYTICS_DB, DATA_DIR, UPLOAD_DB


PROCESS_STARTED_AT = time.time()
DEFAULT_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_LOG_BACKUPS = 5
MAX_LOG_ENTRIES = 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _bytes_for_files(path: Path) -> int:
    total = 0
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            total += candidate.stat().st_size
        except OSError:
            pass
    return total


def _percent(used: int | float, total: int | float) -> float | None:
    if not total:
        return None
    return round(float(used) * 100 / float(total), 1)


def _linux_memory() -> dict | None:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return None
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": max(0, total - available),
        "used_percent": _percent(total - available, total),
        "swap_total_bytes": swap_total,
        "swap_used_bytes": max(0, swap_total - swap_free),
        "swap_used_percent": _percent(swap_total - swap_free, swap_total),
    }


def _windows_memory() -> dict | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong),
            ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except (AttributeError, OSError):
        return None
    used = status.total_physical - status.available_physical
    return {
        "total_bytes": status.total_physical,
        "available_bytes": status.available_physical,
        "used_bytes": used,
        "used_percent": _percent(used, status.total_physical),
        "swap_total_bytes": None,
        "swap_used_bytes": None,
        "swap_used_percent": None,
    }


def _system_uptime() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        pass
    if os.name == "nt":
        try:
            return ctypes.windll.kernel32.GetTickCount64() / 1000
        except (AttributeError, OSError):
            pass
    return None


def _process_memory() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _disk_usage(path: Path) -> dict:
    candidate = path.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    usage = shutil.disk_usage(candidate)
    return {
        "path": str(candidate),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "used_percent": _percent(usage.used, usage.total),
    }


def _readonly_row(path: Path, query: str, parameters: tuple = ()):
    if not path.is_file():
        return None
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(query, parameters).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def _measurement_status() -> dict:
    jobs = _readonly_row(
        ANALYTICS_DB,
        "SELECT COUNT(*) AS count,COALESCE(SUM(size_bytes),0) AS bytes,MAX(imported_at) AS last_imported_at FROM jobs",
    )
    batches = _readonly_row(
        UPLOAD_DB,
        """
        SELECT
          SUM(CASE WHEN status IN ('PREPARING','UPLOADING','VERIFIED','IMPORTING') THEN 1 ELSE 0 END) AS pending,
          SUM(CASE WHEN status='IMPORT_FAILED' THEN 1 ELSE 0 END) AS failed
        FROM upload_batches
        """,
    )
    return {
        "imported_files": int(jobs["count"] or 0) if jobs else 0,
        "imported_bytes": int(jobs["bytes"] or 0) if jobs else 0,
        "last_imported_at": jobs["last_imported_at"] if jobs else None,
        "pending_batches": int(batches["pending"] or 0) if batches else 0,
        "failed_batches": int(batches["failed"] or 0) if batches else 0,
    }


def _failed_measurement_batches(limit: int = 100) -> list[dict]:
    if not UPLOAD_DB.is_file():
        return []
    connection = sqlite3.connect(str(UPLOAD_DB), timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        batches = connection.execute(
            """
            SELECT id,source_id,client,expected_files,created_at,completed_at,
                   import_attempts,error
            FROM upload_batches
            WHERE status='IMPORT_FAILED'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        results = []
        for batch in batches:
            failed_items = connection.execute(
                """
                SELECT filename,error FROM upload_items
                WHERE batch_id=? AND status='IMPORT_FAILED'
                ORDER BY filename LIMIT 50
                """,
                (batch["id"],),
            ).fetchall()
            item_count = connection.execute(
                "SELECT COUNT(*) AS total FROM upload_items WHERE batch_id=? AND status='IMPORT_FAILED'",
                (batch["id"],),
            ).fetchone()["total"]
            result = dict(batch)
            result["error"] = str(result.get("error") or "")[:12000]
            result["failed_file_count"] = int(item_count or 0)
            result["failed_files"] = [
                {
                    "filename": row["filename"],
                    "error": str(row["error"] or "")[:2000],
                }
                for row in failed_items
            ]
            results.append(result)
        return results
    finally:
        connection.close()


def _queue_failed_batch_retry(batch_id: str) -> bool:
    if not UPLOAD_DB.is_file():
        return False
    connection = sqlite3.connect(str(UPLOAD_DB), timeout=10)
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE upload_batches
            SET status='VERIFIED',import_started_at=NULL,imported_at=NULL,error=NULL
            WHERE id=? AND status='IMPORT_FAILED'
            """,
            (batch_id,),
        )
        if cursor.rowcount:
            connection.execute(
                """
                UPDATE upload_items SET status='VERIFIED',error=NULL
                WHERE batch_id=? AND status='IMPORT_FAILED'
                """,
                (batch_id,),
            )
        connection.commit()
        return bool(cursor.rowcount)
    finally:
        connection.close()


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def _log_path(database_path: str | Path) -> Path:
    configured = os.environ.get("TERACOTA_APP_LOG_PATH", "").strip()
    if configured:
        return Path(configured).resolve()
    return (Path(database_path).resolve().parent / "logs" / "teracota.log").resolve()


def configure_application_logging(app, database_path: str | Path) -> Path:
    path = _log_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = int(os.environ.get("TERACOTA_APP_LOG_MAX_BYTES", str(DEFAULT_LOG_MAX_BYTES)))
    backups = int(os.environ.get("TERACOTA_APP_LOG_BACKUPS", str(DEFAULT_LOG_BACKUPS)))
    existing = next(
        (
            handler
            for handler in app.logger.handlers
            if isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename).resolve() == path
        ),
        None,
    )
    if existing is None:
        handler = RotatingFileHandler(
            path,
            maxBytes=max(1024 * 1024, max_bytes),
            backupCount=max(1, min(20, backups)),
            encoding="utf-8",
        )
        handler.setFormatter(JsonLineFormatter())
        handler.setLevel(logging.INFO)
        app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    app.config["TERACOTA_APP_LOG_PATH"] = str(path)
    app.config["TERACOTA_APP_LOG_BACKUPS"] = max(1, min(20, backups))
    app.logger.info("Application logging initialized")
    return path


def _tail_lines(path: Path, limit: int) -> list[str]:
    if limit <= 0 or not path.is_file():
        return []
    block_size = 8192
    chunks = []
    remaining = b""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        position = stream.tell()
        while position > 0 and len(remaining.splitlines()) <= limit:
            read_size = min(block_size, position)
            position -= read_size
            stream.seek(position)
            chunks.append(stream.read(read_size))
            remaining = b"".join(reversed(chunks))
    return remaining.decode("utf-8", errors="replace").splitlines()[-limit:]


def read_log_entries(path: Path, backups: int, limit: int, level: str) -> list[dict]:
    candidates = [Path(f"{path}.{index}") for index in range(backups, 0, -1)] + [path]
    lines = []
    for candidate in candidates:
        lines.extend(_tail_lines(candidate, limit))
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            entry = {
                "timestamp": None,
                "level": "INFO",
                "logger": "legacy",
                "message": line,
            }
        entry_level = str(entry.get("level", "INFO")).upper()
        if level == "ERROR" and entry_level not in {"ERROR", "CRITICAL"}:
            continue
        if level not in {"ALL", "ERROR"} and entry_level != level:
            continue
        entries.append(entry)
    return entries[-limit:]


def collect_server_health(database_path: str | Path, log_path: Path, backups: int) -> dict:
    database_path = Path(database_path).resolve()
    memory = _linux_memory() or _windows_memory()
    try:
        loads = os.getloadavg()
    except (AttributeError, OSError):
        loads = (None, None, None)
    cpu_count = os.cpu_count() or 1
    log_bytes = _bytes_for_files(log_path)
    for index in range(1, backups + 1):
        log_bytes += _bytes_for_files(Path(f"{log_path}.{index}"))
    return {
        "generated_at": _utc_now(),
        "application": {
            "status": "online",
            "process_uptime_seconds": max(0, time.time() - PROCESS_STARTED_AT),
            "process_memory_bytes": _process_memory(),
            "log_bytes": log_bytes,
        },
        "host": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "system_uptime_seconds": _system_uptime(),
        },
        "cpu": {
            "logical_processors": cpu_count,
            "load_1m": round(loads[0], 2) if loads[0] is not None else None,
            "load_5m": round(loads[1], 2) if loads[1] is not None else None,
            "load_15m": round(loads[2], 2) if loads[2] is not None else None,
        },
        "memory": memory,
        "disk": {
            "root": _disk_usage(Path(database_path.anchor or "/")),
            "measurement": _disk_usage(DATA_DIR),
        },
        "storage": {
            "operations_database_bytes": _bytes_for_files(database_path),
            "analytics_database_bytes": _bytes_for_files(ANALYTICS_DB),
            "upload_database_bytes": _bytes_for_files(UPLOAD_DB),
        },
        "measurements": _measurement_status(),
    }


def create_monitoring_blueprint(database_path: str | Path) -> Blueprint:
    blueprint = Blueprint("server_monitoring", __name__)

    @blueprint.get("/api/admin/server-health")
    def server_health():
        path = Path(current_app.config["TERACOTA_APP_LOG_PATH"])
        backups = int(current_app.config["TERACOTA_APP_LOG_BACKUPS"])
        return jsonify(collect_server_health(database_path, path, backups))

    @blueprint.get("/api/admin/application-logs")
    def application_logs():
        try:
            limit = max(25, min(MAX_LOG_ENTRIES, int(request.args.get("limit", 200))))
        except (TypeError, ValueError):
            limit = 200
        level = str(request.args.get("level", "ALL")).strip().upper()
        if level not in {"ALL", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            level = "ALL"
        path = Path(current_app.config["TERACOTA_APP_LOG_PATH"])
        backups = int(current_app.config["TERACOTA_APP_LOG_BACKUPS"])
        return jsonify(
            {
                "entries": read_log_entries(path, backups, limit, level),
                "level": level,
                "limit": limit,
            }
        )

    @blueprint.get("/api/admin/measurement-import-failures")
    def measurement_import_failures():
        return jsonify({"batches": _failed_measurement_batches()})

    @blueprint.post("/api/admin/measurement-import-failures/<batch_id>/retry")
    def retry_measurement_import(batch_id: str):
        if not _queue_failed_batch_retry(batch_id):
            abort(404)
        current_app.logger.info("Measurement import batch %s queued for retry", batch_id)
        return jsonify({"queued": True, "batch_id": batch_id})

    return blueprint


def register_request_logging(app) -> None:
    @app.before_request
    def start_request_timer():
        g.teracota_request_started = time.perf_counter()

    @app.after_request
    def write_request_log(response):
        if session.get("logged_in") is not True:
            return response
        if (
            request.path.startswith("/assets/")
            or request.path == "/healthz"
            or request.path in {
                "/api/admin/server-health",
                "/api/admin/application-logs",
                "/api/admin/measurement-import-failures",
            }
        ):
            return response
        started = getattr(g, "teracota_request_started", time.perf_counter())
        elapsed_ms = (time.perf_counter() - started) * 1000
        current_app.logger.info(
            "%s %s -> %s in %.1f ms from %s",
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
            request.remote_addr or "unknown",
        )
        return response
