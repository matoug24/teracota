"""Authenticated pages and downloads for raw measurement CSV files."""

from __future__ import annotations

import calendar
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import zipfile

from flask import Blueprint, abort, jsonify, render_template, request, send_file, url_for

from .config import location_exists
from .file_catalog import archive_records, file_record, month_catalog, month_files
from .settings import DATA_DIR, MAX_ARCHIVE_BYTES
from .storage import ObjectStorage


_archive_lock = threading.Lock()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.") or "measurements"


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _send_record(record: dict):
    materialized = ObjectStorage().materialize(
        record["object_key"], int(record["size_bytes"]), record["sha256"]
    )
    try:
        path = materialized.__enter__()
        response = send_file(
            path,
            mimetype="text/csv",
            as_attachment=True,
            download_name=Path(record["filename"]).name,
            conditional=True,
        )
    except Exception:
        materialized.__exit__(*sys.exc_info())
        raise
    response.headers["Cache-Control"] = "private, no-store"
    response.call_on_close(lambda: materialized.__exit__(None, None, None))
    return response


def create_file_blueprint(theme_getter):
    blueprint = Blueprint("measurement_files", __name__, template_folder="templates")

    @blueprint.get("/measurements/files/<path:location>")
    def raw_files_page(location: str):
        if not location_exists(location):
            abort(404)
        catalog = month_catalog(location)
        for year in catalog["years"]:
            year["size_label"] = _human_size(year["size_bytes"])
            for month in year["months"]:
                month["size_label"] = _human_size(month["size_bytes"])
                month["archive_url"] = url_for(
                    "measurement_files.download_month",
                    location=location,
                    year=month["year"],
                    month=month["month"],
                )
        return render_template(
            "measurements/files.html",
            theme_css=theme_getter(),
            location=location,
            catalog=catalog,
            total_size_label=_human_size(catalog["size_bytes"]),
            back_url=url_for("measurements.location_history", location=location),
        )

    @blueprint.get("/api/measurements/files")
    def api_month_files():
        location = str(request.args.get("location", "")).strip()
        if not location or not location_exists(location):
            abort(404)
        try:
            year = int(request.args.get("year", ""))
            month = int(request.args.get("month", ""))
            page = int(request.args.get("page", 1))
            payload = month_files(location, year, month, page)
        except (TypeError, ValueError):
            return jsonify({"error": "Select a valid year and month"}), 400
        for item in payload["files"]:
            item["size_label"] = _human_size(int(item["size_bytes"]))
            item["download_url"] = url_for(
                "measurement_files.download_file", file_id=item["id"]
            )
        return jsonify(payload)

    @blueprint.get("/measurements/files/file/<int:file_id>")
    def download_file(file_id: int):
        record = file_record(file_id)
        if not record:
            abort(404)
        try:
            return _send_record(record)
        except (OSError, RuntimeError, ValueError):
            abort(404, "The stored CSV file is unavailable or failed verification")

    @blueprint.get("/measurements/files/<path:location>/archive/<int:year>/<int:month>")
    def download_month(location: str, year: int, month: int):
        if not location_exists(location):
            abort(404)
        try:
            records = archive_records(location, year, month)
        except ValueError:
            abort(400, "Select a valid year and month")
        if not records:
            abort(404, "No imported CSV files were found for this month")
        source_bytes = sum(int(record["size_bytes"]) for record in records)
        if source_bytes > MAX_ARCHIVE_BYTES:
            abort(
                413,
                f"This month contains {_human_size(source_bytes)}; the archive limit is "
                f"{_human_size(MAX_ARCHIVE_BYTES)}",
            )
        if not _archive_lock.acquire(blocking=False):
            abort(429, "Another monthly archive is being prepared. Try again shortly.")

        temp_dir = DATA_DIR / "tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        archive = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b", dir=temp_dir)
        try:
            storage = ObjectStorage()
            root = _safe_name(location)
            with zipfile.ZipFile(
                archive,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as bundle:
                for record in records:
                    with storage.materialize(
                        record["object_key"], int(record["size_bytes"]), record["sha256"]
                    ) as source:
                        archive_name = (
                            f"{root}/{year:04d}/{month:02d}/{Path(record['filename']).name}"
                        )
                        bundle.write(source, archive_name)
            archive.seek(0, os.SEEK_END)
            archive_size = archive.tell()
            archive.seek(0)
        except (OSError, RuntimeError, ValueError):
            archive.close()
            abort(404, "One or more stored CSV files are unavailable or failed verification")
        except Exception:
            archive.close()
            raise
        finally:
            _archive_lock.release()

        try:
            response = send_file(
                archive,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"{root}-{year:04d}-{month:02d}-csv.zip",
                conditional=False,
            )
        except Exception:
            archive.close()
            raise
        response.content_length = archive_size
        response.headers["Cache-Control"] = "private, no-store"
        response.call_on_close(archive.close)
        return response

    return blueprint
