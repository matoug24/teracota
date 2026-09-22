"""Token-protected cloud upload endpoints for measurement CSV files."""

from __future__ import annotations

from datetime import datetime, timezone
import hmac
from pathlib import Path
import re
import uuid

from flask import Blueprint, jsonify, request, url_for

from .config import connect as connect_main, location_exists
from .database import UPLOAD_DB, connect, initialize_uploads
from .settings import MAX_UPLOAD_BYTES, UPLOAD_API_TOKEN
from .storage import ObjectStorage


blueprint = Blueprint("measurement_uploads", __name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_slug(value: str, maximum: int = 120) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return (slug[:maximum] or "item")


def _authorized() -> bool:
    supplied = request.headers.get("Authorization", "")
    expected = f"Bearer {UPLOAD_API_TOKEN}"
    return bool(UPLOAD_API_TOKEN) and hmac.compare_digest(supplied, expected)


def _deny():
    if not _authorized():
        return jsonify({"error": "Valid measurement uploader API token required"}), 401
    return None


def _batch_payload(batch_id: str):
    initialize_uploads()
    with connect(UPLOAD_DB) as connection:
        batch = connection.execute(
            "SELECT * FROM upload_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if not batch:
            return None
        items = connection.execute(
            """
            SELECT filename,size_bytes,sha256,status,error
            FROM upload_items WHERE batch_id=? ORDER BY filename
            """,
            (batch_id,),
        ).fetchall()
    return {**dict(batch), "items": [dict(item) for item in items]}


@blueprint.get("/api/uploads/configuration")
def configuration():
    denied = _deny()
    if denied:
        return denied
    requested = str(request.args.get("client", "")).strip()
    if requested:
        if not location_exists(requested):
            return jsonify({"error": "Client does not match an existing location"}), 404
        return jsonify({"client": requested})
    with connect_main() as connection:
        clients = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM locations ORDER BY display_rank,name"
            ).fetchall()
        ]
    return jsonify({"clients": clients})


@blueprint.post("/api/uploads/prepare")
def prepare():
    denied = _deny()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    client = str(payload.get("client", "")).strip()
    if not location_exists(client):
        return jsonify({"error": "Client must exactly match an existing location"}), 400
    source_id = str(payload.get("source_id", "")).strip()[:120]
    files = payload.get("files")
    if not source_id or not isinstance(files, list) or not files or len(files) > 1000:
        return jsonify({"error": "source_id and 1-1000 files are required"}), 400
    try:
        expected_files = max(0, int(payload.get("expected_daily_files", 0))) or None
    except (TypeError, ValueError):
        expected_files = None

    initialize_uploads()
    batch_id = uuid.uuid4().hex
    storage = ObjectStorage()
    records = []
    response_items = []
    with connect(UPLOAD_DB) as connection:
        for raw in files:
            filename = Path(str(raw.get("name", ""))).name
            if filename != str(raw.get("name", "")) or not filename.lower().endswith(".csv"):
                return jsonify({"error": f"Unsafe CSV filename: {raw.get('name')}"}), 400
            try:
                size = int(raw.get("size"))
            except (TypeError, ValueError):
                return jsonify({"error": f"Invalid size for {filename}"}), 400
            sha256 = str(raw.get("sha256", "")).lower()
            if size < 0 or size > MAX_UPLOAD_BYTES:
                return jsonify({"error": f"Invalid size for {filename}"}), 400
            if len(sha256) != 64 or any(
                character not in "0123456789abcdef" for character in sha256
            ):
                return jsonify({"error": f"Invalid SHA-256 for {filename}"}), 400
            reusable = connection.execute(
                """
                SELECT object_key FROM upload_items
                WHERE filename=? AND size_bytes=? AND sha256=?
                  AND status IN ('VERIFIED','IMPORTED')
                ORDER BY rowid DESC LIMIT 1
                """,
                (filename, size, sha256),
            ).fetchone()
            item_id = uuid.uuid4().hex
            if reusable:
                object_key = reusable["object_key"]
                item_status = "VERIFIED"
                upload = {"skip": True, "reason": "identical object already verified"}
            else:
                safe_client = _safe_slug(client, 80)
                safe_filename = _safe_slug(Path(filename).stem)
                object_key = f"incoming/{safe_client}/{sha256[:16]}/{safe_filename}.csv"
                item_status = "PENDING"
                upload = storage.prepare_upload(object_key, sha256)
                if upload.pop("filesystem", False):
                    upload["url"] = url_for(
                        "measurement_uploads.receive_content",
                        batch_id=batch_id,
                        item_id=item_id,
                        _external=True,
                    )
            records.append(
                (
                    item_id,
                    batch_id,
                    filename,
                    object_key,
                    size,
                    sha256,
                    raw.get("mtime"),
                    item_status,
                )
            )
            response_items.append(
                {"id": item_id, "name": filename, "deduplicated": bool(reusable), "upload": upload}
            )
        connection.execute(
            """
            INSERT INTO upload_batches(id,source_id,client,status,expected_files,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (batch_id, source_id, client, "UPLOADING", expected_files, _now()),
        )
        connection.executemany(
            """
            INSERT INTO upload_items(
                id,batch_id,filename,object_key,size_bytes,sha256,source_mtime,status
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            records,
        )
    return jsonify({"batch_id": batch_id, "items": response_items}), 201


@blueprint.put("/api/uploads/<batch_id>/<item_id>/content")
def receive_content(batch_id: str, item_id: str):
    denied = _deny()
    if denied:
        return denied
    initialize_uploads()
    with connect(UPLOAD_DB) as connection:
        item = connection.execute(
            "SELECT * FROM upload_items WHERE id=? AND batch_id=?", (item_id, batch_id)
        ).fetchone()
    if not item:
        return jsonify({"error": "Upload item not found"}), 404
    try:
        ObjectStorage().receive_filesystem(
            item["object_key"], request.stream, int(item["size_bytes"]), item["sha256"]
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 422
    return "", 204


@blueprint.post("/api/uploads/<batch_id>/complete")
def complete(batch_id: str):
    denied = _deny()
    if denied:
        return denied
    initialize_uploads()
    storage = ObjectStorage()
    failures = []
    with connect(UPLOAD_DB) as connection:
        items = connection.execute(
            "SELECT * FROM upload_items WHERE batch_id=?", (batch_id,)
        ).fetchall()
    if not items:
        return jsonify({"error": "Batch not found"}), 404
    for item in items:
        try:
            storage.verify(item["object_key"], int(item["size_bytes"]), item["sha256"])
            status, error = "VERIFIED", None
        except Exception as exc:
            status, error = "FAILED", str(exc)
            failures.append(f"{item['filename']}: {exc}")
        with connect(UPLOAD_DB) as connection:
            connection.execute(
                "UPDATE upload_items SET status=?,error=? WHERE id=?",
                (status, error, item["id"]),
            )
    with connect(UPLOAD_DB) as connection:
        connection.execute(
            "UPDATE upload_batches SET status=?,completed_at=?,error=? WHERE id=?",
            ("FAILED" if failures else "VERIFIED", _now(), "\n".join(failures) or None, batch_id),
        )
    return jsonify(_batch_payload(batch_id)), 422 if failures else 200


@blueprint.get("/api/uploads/<batch_id>")
def status(batch_id: str):
    denied = _deny()
    if denied:
        return denied
    payload = _batch_payload(batch_id)
    return (jsonify(payload), 200) if payload else (jsonify({"error": "Batch not found"}), 404)
