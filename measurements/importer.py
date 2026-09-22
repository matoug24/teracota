#!/usr/bin/env python3
"""Import verified cloud measurement objects into the compact analytics database."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import sys

from .config import configure, get_location_config, location_exists
from .database import UPLOAD_DB, connect, initialize_all, replace_job_summary
from .settings import validate_environment
from .storage import ObjectStorage
from .summarizer import summarize_csv


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _claim_batch(include_failed: bool = False):
    initialize_all()
    statuses = ["VERIFIED"] + (["IMPORT_FAILED"] if include_failed else [])
    placeholders = ",".join("?" for _ in statuses)
    stale_before = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(timespec="seconds")
    with connect(UPLOAD_DB) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE upload_batches SET status='VERIFIED',import_started_at=NULL
            WHERE status='IMPORTING' AND import_started_at<?
            """,
            (stale_before,),
        )
        row = connection.execute(
            f"""
            SELECT * FROM upload_batches WHERE status IN ({placeholders})
            ORDER BY created_at LIMIT 1
            """,
            statuses,
        ).fetchone()
        if row:
            connection.execute(
                """
                UPDATE upload_batches SET status='IMPORTING',import_started_at=?,
                    import_attempts=import_attempts+1,error=NULL WHERE id=?
                """,
                (_now(), row["id"]),
            )
        return dict(row) if row else None


def import_batch(batch: dict) -> tuple[int, list[str]]:
    if not location_exists(batch["client"]):
        message = f"Upload client no longer matches a location: {batch['client']}"
        with connect(UPLOAD_DB) as connection:
            connection.execute(
                "UPDATE upload_batches SET status='IMPORT_FAILED',error=? WHERE id=?",
                (message, batch["id"]),
            )
        return 0, [message]
    config = get_location_config(batch["client"])
    storage = ObjectStorage()
    imported = 0
    failures = []
    with connect(UPLOAD_DB) as connection:
        items = connection.execute(
            "SELECT * FROM upload_items WHERE batch_id=? ORDER BY filename", (batch["id"],)
        ).fetchall()
    for item in items:
        if item["status"] == "IMPORTED":
            imported += 1
            continue
        try:
            with storage.materialize(
                item["object_key"], int(item["size_bytes"]), item["sha256"]
            ) as csv_path:
                summary = summarize_csv(csv_path, config)
            replace_job_summary(
                summary,
                {
                    "filename": item["filename"],
                    "object_key": item["object_key"],
                    "sha256": item["sha256"],
                    "size_bytes": item["size_bytes"],
                },
            )
            with connect(UPLOAD_DB) as connection:
                connection.execute(
                    "UPDATE upload_items SET status='IMPORTED',error=NULL WHERE id=?",
                    (item["id"],),
                )
            imported += 1
        except Exception as exc:
            failures.append(f"{item['filename']}: {exc}")
            with connect(UPLOAD_DB) as connection:
                connection.execute(
                    "UPDATE upload_items SET status='IMPORT_FAILED',error=? WHERE id=?",
                    (str(exc), item["id"]),
                )
    with connect(UPLOAD_DB) as connection:
        connection.execute(
            """
            UPDATE upload_batches SET status=?,imported_at=?,import_started_at=NULL,error=?
            WHERE id=?
            """,
            (
                "IMPORT_FAILED" if failures else "IMPORTED",
                None if failures else _now(),
                "\n".join(failures) or None,
                batch["id"],
            ),
        )
    return imported, failures


def import_pending(include_failed: bool = False) -> int:
    total = 0
    failed_batches = 0
    while batch := _claim_batch(include_failed=include_failed):
        imported, failures = import_batch(batch)
        total += imported
        if failures:
            failed_batches += 1
            print(f"Batch {batch['id']} failed: {'; '.join(failures)}", file=sys.stderr)
            if include_failed:
                break
    print(f"Imported {total} measurement CSV summaries; failed batches: {failed_batches}")
    return 1 if failed_batches else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Import verified TeraCota measurement uploads")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--database", help="Override the main TeraCota database path")
    args = parser.parse_args()
    if args.database:
        configure(args.database)
    problems = validate_environment()
    if problems:
        print("Invalid measurement environment: " + "; ".join(problems), file=sys.stderr)
        return 2
    return import_pending(include_failed=args.retry_failed)


if __name__ == "__main__":
    raise SystemExit(main())
