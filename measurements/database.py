"""Separate SQLite databases for compact measurement summaries and upload receipts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .settings import ANALYTICS_DB, DATA_DIR, UPLOAD_DB


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path: str | Path) -> ClosingConnection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute("PRAGMA cache_size=-4096")
    return connection


def initialize_analytics() -> None:
    with connect(ANALYTICS_DB) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                object_key TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                client TEXT NOT NULL,
                car_id TEXT NOT NULL,
                body_id TEXT NOT NULL,
                job_date TEXT NOT NULL,
                job_time TEXT NOT NULL,
                color TEXT NOT NULL,
                layer_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL,
                UNIQUE(client, filename)
            );
            CREATE TABLE IF NOT EXISTS job_scopes (
                id INTEGER PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                raw_count INTEGER NOT NULL,
                deduplicated_count INTEGER NOT NULL,
                aligned_count INTEGER NOT NULL,
                valid_count INTEGER NOT NULL,
                alignment_percentage REAL NOT NULL,
                valid_percentage REAL NOT NULL,
                UNIQUE(job_id, source)
            );
            CREATE TABLE IF NOT EXISTS metric_stats (
                scope_id INTEGER NOT NULL REFERENCES job_scopes(id) ON DELETE CASCADE,
                metric_key TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                mean REAL,
                stdev REAL,
                minimum REAL,
                maximum REAL,
                PRIMARY KEY(scope_id, metric_key)
            );
            CREATE INDEX IF NOT EXISTS idx_measurement_jobs_client_date
                ON jobs(client, job_date);
            CREATE INDEX IF NOT EXISTS idx_measurement_jobs_color_date
                ON jobs(client, color, job_date);
            CREATE INDEX IF NOT EXISTS idx_measurement_jobs_car_date
                ON jobs(client, car_id, job_date);
            CREATE INDEX IF NOT EXISTS idx_measurement_scopes_source_job
                ON job_scopes(source, job_id);
            CREATE INDEX IF NOT EXISTS idx_measurement_metrics_key_scope
                ON metric_stats(metric_key, scope_id);
            """
        )


def initialize_uploads() -> None:
    with connect(UPLOAD_DB) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS upload_batches (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                client TEXT NOT NULL,
                status TEXT NOT NULL,
                expected_files INTEGER,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                import_started_at TEXT,
                imported_at TEXT,
                import_attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS upload_items (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL REFERENCES upload_batches(id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                object_key TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                source_mtime TEXT,
                status TEXT NOT NULL,
                error TEXT,
                UNIQUE(batch_id, filename)
            );
            CREATE INDEX IF NOT EXISTS idx_measurement_batches_status_created
                ON upload_batches(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_measurement_items_batch ON upload_items(batch_id);
            CREATE INDEX IF NOT EXISTS idx_measurement_items_identity
                ON upload_items(filename, size_bytes, sha256, status);
            """
        )


def initialize_all() -> None:
    initialize_analytics()
    initialize_uploads()


def replace_job_summary(summary: dict, object_meta: dict) -> int:
    initialize_analytics()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(ANALYTICS_DB) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id FROM jobs WHERE client=? AND filename=?",
            (summary["client"], object_meta["filename"]),
        ).fetchone()
        if existing:
            connection.execute("DELETE FROM jobs WHERE id=?", (existing["id"],))
        cursor = connection.execute(
            """
            INSERT INTO jobs(
                filename,object_key,sha256,size_bytes,client,car_id,body_id,
                job_date,job_time,color,layer_count,imported_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                object_meta["filename"], object_meta["object_key"], object_meta["sha256"],
                int(object_meta["size_bytes"]), summary["client"], summary["car_id"],
                summary["body_id"], summary["job_date"], summary["job_time"],
                summary["color"], summary["layer_count"], now,
            ),
        )
        job_id = int(cursor.lastrowid)
        for scope in summary["scopes"]:
            scope_cursor = connection.execute(
                """
                INSERT INTO job_scopes(
                    job_id,source,raw_count,deduplicated_count,aligned_count,
                    valid_count,alignment_percentage,valid_percentage
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    job_id, scope["source"], scope["raw_count"],
                    scope["deduplicated_count"], scope["aligned_count"],
                    scope["valid_count"], scope["alignment_percentage"],
                    scope["valid_percentage"],
                ),
            )
            scope_id = int(scope_cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO metric_stats(
                    scope_id,metric_key,sample_count,mean,stdev,minimum,maximum
                ) VALUES(?,?,?,?,?,?,?)
                """,
                [
                    (
                        scope_id, key, values["count"], values["mean"], values["stdev"],
                        values["min"], values["max"],
                    )
                    for key, values in scope["metrics"].items()
                ],
            )
        connection.commit()
        return job_id


def rename_client(previous: str, replacement: str) -> None:
    initialize_all()
    with connect(ANALYTICS_DB) as connection:
        connection.execute("UPDATE jobs SET client=? WHERE client=?", (replacement, previous))
    with connect(UPLOAD_DB) as connection:
        connection.execute(
            "UPDATE upload_batches SET client=? WHERE client=?", (replacement, previous)
        )


def quick_check(path: Path) -> str:
    if not path.is_file():
        return "missing"
    with connect(path) as connection:
        return str(connection.execute("PRAGMA quick_check").fetchone()[0])


def readiness_check(path: Path, required_table: str) -> str:
    """Confirm that a measurement database and required table are readable."""
    if required_table not in {"jobs", "upload_batches"}:
        raise ValueError("Unsupported readiness table")
    if not path.is_file():
        return "missing"
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2)
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (required_table,),
        ).fetchone()
        return "ok" if row else f"missing table: {required_table}"
    finally:
        connection.close()
