"""Environment settings for measurement upload and analytics data."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(
    os.environ.get("TERACOTA_MEASUREMENT_DATA_DIR", str(PROJECT_ROOT / "measurement_data"))
).resolve()
ANALYTICS_DB = Path(
    os.environ.get("TERACOTA_MEASUREMENT_ANALYTICS_DB", str(DATA_DIR / "vehicle_summaries.sqlite3"))
).resolve()
UPLOAD_DB = Path(
    os.environ.get("TERACOTA_MEASUREMENT_UPLOAD_DB", str(DATA_DIR / "upload_control.sqlite3"))
).resolve()
OBJECT_CACHE_DIR = DATA_DIR / "object_store"

OBJECT_STORAGE_BACKEND = os.environ.get(
    "TERACOTA_MEASUREMENT_STORAGE_BACKEND", "filesystem"
).strip().lower()
OBJECT_STORAGE_BUCKET = os.environ.get("TERACOTA_MEASUREMENT_STORAGE_BUCKET", "").strip()
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1").strip()
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "").strip() or None
UPLOAD_API_TOKEN = os.environ.get("TERACOTA_MEASUREMENT_UPLOAD_TOKEN", "").strip()
MAX_UPLOAD_BYTES = int(
    os.environ.get("TERACOTA_MEASUREMENT_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))
)
PRESIGNED_URL_SECONDS = int(
    os.environ.get("TERACOTA_MEASUREMENT_PRESIGNED_URL_SECONDS", "3600")
)

DEFAULT_LOCATION_CONFIG = {
    "car_id_index": 0,
    "body_id_index": 2,
    "layer_names": {
        "3": ["Clearcoat", "Basecoat", "Primer"],
        "4": ["Clearcoat", "Basecoat", "Primer", "E-coat"],
        "5": ["Clearcoat", "Basecoat", "Primer", "E-coat", "Substrate"],
    },
}


def validate_location_config(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Configuration must be a JSON object")
    try:
        car_index = int(payload.get("car_id_index", DEFAULT_LOCATION_CONFIG["car_id_index"]))
        body_index = int(payload.get("body_id_index", DEFAULT_LOCATION_CONFIG["body_id_index"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Car and body filename indexes must be integers") from exc
    if car_index < 0 or body_index < 0:
        raise ValueError("Car and body filename indexes cannot be negative")

    raw_layers = payload.get("layer_names", DEFAULT_LOCATION_CONFIG["layer_names"])
    if not isinstance(raw_layers, dict):
        raise ValueError("Layer names must contain the 3, 4, and 5 layer lists")
    layers = {}
    for count in (3, 4, 5):
        names = raw_layers.get(str(count))
        if not isinstance(names, list) or len(names) != count:
            raise ValueError(f"The {count}-layer configuration requires exactly {count} names")
        cleaned = [str(name).strip() for name in names]
        if any(not name for name in cleaned):
            raise ValueError("Layer names cannot be blank")
        layers[str(count)] = cleaned
    return {
        "car_id_index": car_index,
        "body_id_index": body_index,
        "layer_names": layers,
    }


def validate_environment() -> list[str]:
    problems = []
    if OBJECT_STORAGE_BACKEND not in {"filesystem", "s3"}:
        problems.append("TERACOTA_MEASUREMENT_STORAGE_BACKEND must be filesystem or s3")
    if OBJECT_STORAGE_BACKEND == "s3" and not OBJECT_STORAGE_BUCKET:
        problems.append("TERACOTA_MEASUREMENT_STORAGE_BUCKET is required for S3 storage")
    if len(UPLOAD_API_TOKEN) < 24:
        problems.append("TERACOTA_MEASUREMENT_UPLOAD_TOKEN must be at least 24 characters")
    return problems
