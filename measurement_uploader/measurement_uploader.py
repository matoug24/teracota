#!/usr/bin/env python3
"""Standard-library-only TeraCota Measurement History uploader."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = ("server_url", "api_token", "client", "data_directory")
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    if missing:
        raise ValueError("Missing uploader settings: " + ", ".join(missing))
    config["server_url"] = config["server_url"].rstrip("/")
    config.setdefault("source_id", socket.gethostname())
    config.setdefault("scan_mode", "year_month")
    config.setdefault("recursive", False)
    config.setdefault("initial_lookback_days", 2)
    config.setdefault("rescan_overlap_days", 1)
    config.setdefault("bootstrap_all_history", False)
    config.setdefault("stable_seconds", 30)
    config.setdefault("request_timeout_seconds", 3600)
    config.setdefault("max_attempts", 3)
    config.setdefault("retry_delay_seconds", 600)
    config.setdefault("verify_tls", True)
    config.setdefault("expected_daily_files", 50)
    config.setdefault("journal_path", str(path.with_name("measurement_upload_journal.sqlite3")))
    config.setdefault("log_path", str(path.with_name("measurement_uploader.log")))
    return config


def configure_logging(path: Path, verbose: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers = [RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def connect_journal(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS uploaded_files (
            path TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            status TEXT NOT NULL,
            batch_id TEXT,
            confirmed_at TEXT,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS uploader_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    return conn


def acquire_lock(journal_path: Path):
    lock_path = journal_path.with_suffix(journal_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise RuntimeError("Another measurement uploader instance is already running") from exc
    return handle


def ssl_context(config: dict):
    return ssl.create_default_context() if config["verify_tls"] else ssl._create_unverified_context()


def request_json(config: dict, method: str, route: str, payload=None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        config["server_url"] + route,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {config['api_token']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(
        request, timeout=int(config["request_timeout_seconds"]), context=ssl_context(config)
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_file(config: dict, path: Path, upload: dict) -> None:
    if upload.get("skip"):
        return
    headers = {str(key): str(value) for key, value in upload.get("headers", {}).items()}
    headers["Content-Length"] = str(path.stat().st_size)
    if upload["url"].startswith(config["server_url"]):
        headers["Authorization"] = f"Bearer {config['api_token']}"
    request = urllib.request.Request(upload["url"], method="PUT", headers=headers)
    with path.open("rb") as stream:
        request.data = stream
        with urllib.request.urlopen(
            request, timeout=int(config["request_timeout_seconds"]), context=ssl_context(config)
        ) as response:
            if response.status not in {200, 201, 204}:
                raise RuntimeError(f"Upload returned HTTP {response.status}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _state(journal, key: str):
    row = journal.execute("SELECT value FROM uploader_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _set_state(journal, key: str, value: str) -> None:
    journal.execute(
        "INSERT INTO uploader_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _months_between(start: date, end: date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def scan_directories(config: dict, journal, today: date | None = None) -> list[Path]:
    root = Path(config["data_directory"]).resolve()
    if not root.is_dir():
        raise ValueError(f"Data directory does not exist: {root}")
    if config["scan_mode"] != "year_month":
        return [root]
    today = today or date.today()
    last_text = _state(journal, "last_successful_scan_date")
    if not last_text and config["bootstrap_all_history"]:
        return sorted(
            month
            for year in root.iterdir()
            if year.is_dir() and len(year.name) == 4 and year.name.isdigit()
            for month in year.iterdir()
            if month.is_dir() and month.name.isdigit() and 1 <= int(month.name) <= 12
        )
    try:
        last = date.fromisoformat(last_text) if last_text else today - timedelta(days=int(config["initial_lookback_days"]))
    except ValueError:
        last = today - timedelta(days=int(config["initial_lookback_days"]))
    start = min(last - timedelta(days=max(0, int(config["rescan_overlap_days"]))), today)
    return [
        root / f"{year:04d}" / f"{month:02d}"
        for year, month in _months_between(start, today)
        if (root / f"{year:04d}" / f"{month:02d}").is_dir()
    ]


def discover(config: dict, journal) -> list[dict]:
    pattern = "**/*.csv" if config["recursive"] else "*.csv"
    candidates = sorted(
        path
        for directory in scan_directories(config, journal)
        for path in directory.glob(pattern)
        if path.is_file()
    )
    pending = []
    initial = {}
    for path in candidates:
        stat = path.stat()
        existing = journal.execute("SELECT * FROM uploaded_files WHERE path=?", (str(path),)).fetchone()
        if existing and existing["status"] in {"VERIFIED", "IMPORTED", "IMPORT_FAILED"} and existing["size_bytes"] == stat.st_size and existing["mtime_ns"] == stat.st_mtime_ns:
            continue
        pending.append(path)
        initial[path] = stat
    if pending and int(config["stable_seconds"]) > 0:
        logging.info("Waiting %s seconds to verify %s stable files", config["stable_seconds"], len(pending))
        time.sleep(int(config["stable_seconds"]))
    result = []
    for path in pending:
        try:
            current = path.stat()
        except FileNotFoundError:
            continue
        if current.st_size != initial[path].st_size or current.st_mtime_ns != initial[path].st_mtime_ns:
            logging.warning("Skipping file still being written: %s", path)
            continue
        digest = sha256_file(path)
        existing = journal.execute("SELECT * FROM uploaded_files WHERE path=?", (str(path),)).fetchone()
        if existing and existing["status"] in {"VERIFIED", "IMPORTED", "IMPORT_FAILED"} and existing["sha256"] == digest:
            continue
        result.append(
            {"path": path, "name": path.name, "size": current.st_size, "mtime_ns": current.st_mtime_ns, "mtime": current.st_mtime, "sha256": digest}
        )
    return result


def record(journal, item: dict, status: str, batch_id: str, error=None) -> None:
    journal.execute(
        """
        INSERT INTO uploaded_files(path,size_bytes,mtime_ns,sha256,status,batch_id,confirmed_at,error)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(path) DO UPDATE SET size_bytes=excluded.size_bytes,mtime_ns=excluded.mtime_ns,
            sha256=excluded.sha256,status=excluded.status,batch_id=excluded.batch_id,
            confirmed_at=excluded.confirmed_at,error=excluded.error
        """,
        (str(item["path"]), item["size"], item["mtime_ns"], item["sha256"], status, batch_id, datetime.now().isoformat(timespec="seconds"), error),
    )


def retry(config: dict, description: str, operation):
    attempts = max(1, int(config["max_attempts"]))
    delay = max(1, int(config["retry_delay_seconds"]))
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception:
            if attempt == attempts:
                raise
            logging.exception("%s failed (%s/%s); retrying in %s seconds", description, attempt, attempts, delay)
            time.sleep(delay)


def reconcile(config: dict, journal) -> None:
    batches = [row[0] for row in journal.execute("SELECT DISTINCT batch_id FROM uploaded_files WHERE batch_id IS NOT NULL AND status NOT IN ('IMPORTED','IMPORT_FAILED')")]
    for batch_id in batches:
        try:
            receipt = request_json(config, "GET", f"/api/uploads/{batch_id}")
        except Exception:
            logging.warning("Could not refresh receipt %s", batch_id)
            continue
        remote = {item["filename"]: item for item in receipt.get("items", [])}
        for row in journal.execute("SELECT path FROM uploaded_files WHERE batch_id=?", (batch_id,)).fetchall():
            path = Path(row["path"])
            item = remote.get(path.name)
            if item:
                journal.execute(
                    "UPDATE uploaded_files SET status=?,error=? WHERE path=?",
                    (item["status"], item.get("error"), str(path)),
                )


def run(config: dict) -> int:
    journal_path = Path(config["journal_path"])
    lock = acquire_lock(journal_path)
    try:
        with connect_journal(journal_path) as journal:
            reconcile(config, journal)
            files = discover(config, journal)
            if not files:
                logging.info("No new stable CSV files found")
                _set_state(journal, "last_successful_scan_date", date.today().isoformat())
                return 0
            logging.info("Preparing %s CSV files", len(files))
            expected = max(0, int(config["expected_daily_files"]))
            if expected and len(files) < expected:
                logging.warning("Found %s new files; approximately %s were expected", len(files), expected)
            prepared = retry(
                config,
                "batch preparation",
                lambda: request_json(
                    config,
                    "POST",
                    "/api/uploads/prepare",
                    {
                        "source_id": config["source_id"], "client": config["client"],
                        "expected_daily_files": expected,
                        "files": [{"name": item["name"], "size": item["size"], "sha256": item["sha256"], "mtime": item["mtime"]} for item in files],
                    },
                ),
            )
            batch_id = prepared["batch_id"]
            by_name = {item["name"]: item for item in files}
            for remote in prepared["items"]:
                item = by_name[remote["name"]]
                retry(config, f"upload {item['name']}", lambda item=item, remote=remote: upload_file(config, item["path"], remote["upload"]))
                record(journal, item, "STORED", batch_id)
            receipt = retry(config, "batch verification", lambda: request_json(config, "POST", f"/api/uploads/{batch_id}/complete", {}))
            if receipt.get("status") != "VERIFIED":
                raise RuntimeError(f"Server did not verify batch: {receipt.get('status')}")
            for item in files:
                record(journal, item, "VERIFIED", batch_id)
            _set_state(journal, "last_successful_scan_date", date.today().isoformat())
            logging.info("Batch %s is fully verified", batch_id)
        return 0
    finally:
        lock.close()


def doctor(config: dict) -> int:
    root = Path(config["data_directory"])
    if not root.is_dir():
        print(f"FAIL: data directory not found: {root}")
        return 1
    with connect_journal(Path(config["journal_path"])):
        pass
    remote = request_json(
        config,
        "GET",
        f"/api/uploads/configuration?client={urllib.parse.quote(config['client'])}",
    )
    if remote.get("client") != config["client"]:
        print(f"FAIL: server client is {remote.get('client')!r}, uploader uses {config['client']!r}")
        return 1
    print("OK: folder, journal, TLS, token, and server client are valid")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload vehicle CSVs to TeraCota Measurement History")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        configure_logging(Path(config["log_path"]), args.verbose)
        if args.doctor:
            return doctor(config)
        with connect_journal(Path(config["journal_path"])) as journal:
            if args.status:
                reconcile(config, journal)
                rows = journal.execute("SELECT status,COUNT(*) count FROM uploaded_files GROUP BY status ORDER BY status").fetchall()
                print("; ".join(f"{row['status']}: {row['count']}" for row in rows) or "No upload receipts")
                return 0
            if args.dry_run:
                for item in discover({**config, "stable_seconds": 0}, journal):
                    print(f"{item['size']:>9}  {item['path']}")
                return 0
        return run(config)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logging.error("HTTP %s: %s", exc.code, detail)
        return 1
    except Exception:
        logging.exception("Measurement uploader failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
