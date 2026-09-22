"""Stream production CSV files into compact whole-vehicle and per-source summaries."""

from __future__ import annotations

from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
import re


ALL_SOURCES = "__ALL__"
MISC_METRICS = (
    "Yaw",
    "Pitch",
    "Focus",
    "Sample Peak Position Range",
    "Ref_Peak_Height",
    "Laser Temp",
    "Heatsink Temp",
    "Emitter PD",
    "Receiver PD",
    "Align and Collect Time [s]",
)
THICKNESS_RE = re.compile(r"^Thickness_(\d+)$")
CONFIDENCE_RE = re.compile(r"^Confidence_(\d+)$")
FILENAME_TIME_RE = re.compile(r"\((\d{4}-\d{2}-\d{2}) (\d{2});(\d{2});(\d{2})\)")


@dataclass
class RunningStats:
    count: int = 0
    mean: float = 0.0
    m2: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def add(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (value - self.mean)
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)

    def result(self) -> dict:
        return {
            "count": self.count,
            "mean": self.mean if self.count else None,
            "stdev": math.sqrt(self.m2 / (self.count - 1)) if self.count > 1 else None,
            "min": self.minimum,
            "max": self.maximum,
        }


def _number(value) -> float | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _retry_identity(measurement: str, row_number: int) -> tuple[str, int, int]:
    text = str(measurement or "").strip().strip('"')
    match = re.match(r"^(.*)_([0-9]+)$", text)
    if match:
        return match.group(1), int(match.group(2)), row_number
    return text, -1, row_number


def _is_aligned(row: dict) -> bool:
    return str(row.get("Alignment_Status", "")).strip().casefold() == "aligned"


def _is_valid(row: dict) -> bool:
    return (
        _is_aligned(row)
        and str(row.get("Status", "")).strip().casefold() == "ok"
        and str(row.get("Measurement_Status", "")).strip().casefold() == "succeeded"
    )


def _select_latest(target: dict, row: dict, row_number: int) -> None:
    identity, retry, order = _retry_identity(row.get("Measurement", ""), row_number)
    current = target.get(identity)
    rank = (retry, order)
    if current is None or rank >= current[0]:
        target[identity] = (rank, row)


def _filename_metadata(filename: str, config: dict, fallback_timestamp: str):
    stem = Path(filename).stem
    parts = stem.split("_")
    try:
        car_id = parts[int(config["car_id_index"])]
        body_id = parts[int(config["body_id_index"])]
    except (IndexError, TypeError, ValueError) as exc:
        raise ValueError(
            f"Filename {filename!r} does not contain the configured car/body indexes"
        ) from exc

    match = FILENAME_TIME_RE.search(stem)
    if match:
        job_date = match.group(1)
        job_time = ":".join(match.groups()[1:])
    else:
        cleaned = str(fallback_timestamp or "").strip().strip('"')
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError as exc:
            raise ValueError(f"No usable timestamp in filename or CSV: {filename}") from exc
        job_date = parsed.date().isoformat()
        job_time = parsed.time().replace(microsecond=0).isoformat()
    return car_id, body_id, job_date, job_time


def _scope_result(
    source: str,
    raw_count: int,
    aligned_count: int,
    selected: dict,
    metrics: tuple[str, ...],
) -> dict:
    rows = [value[1] for value in selected.values()]
    valid_rows = [row for row in rows if _is_valid(row)]
    accumulators = {metric: RunningStats() for metric in metrics}
    for row in valid_rows:
        for metric, stats in accumulators.items():
            value = _number(row.get(metric))
            if value is not None:
                stats.add(value)
    deduplicated_count = len(rows)
    return {
        "source": source,
        "raw_count": raw_count,
        "deduplicated_count": deduplicated_count,
        "aligned_count": aligned_count,
        "valid_count": len(valid_rows),
        "alignment_percentage": round(100 * aligned_count / raw_count, 4) if raw_count else 0.0,
        "valid_percentage": round(100 * len(valid_rows) / deduplicated_count, 4)
        if deduplicated_count
        else 0.0,
        "metrics": {
            metric: stats.result() for metric, stats in accumulators.items() if stats.count
        },
    }


def summarize_csv(csv_path: str | Path, config: dict) -> dict:
    path = Path(csv_path)
    overall = {}
    by_source: dict[str, dict] = {}
    raw_counts = Counter()
    aligned_counts = Counter()
    colors = Counter()
    fieldnames = []
    active_layers = set()
    fallback_timestamp = ""

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames or []
        required = {
            "Measurement",
            "Calibration",
            "Source",
            "Alignment_Status",
            "Status",
            "Measurement_Status",
        }
        missing = sorted(required.difference(fieldnames))
        if missing:
            raise ValueError(f"{path.name} is missing required columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=1):
            source = str(row.get("Source") or "Unknown").strip() or "Unknown"
            raw_counts[ALL_SOURCES] += 1
            raw_counts[source] += 1
            if _is_aligned(row):
                aligned_counts[ALL_SOURCES] += 1
                aligned_counts[source] += 1
            calibration = str(row.get("Calibration") or "").strip().strip('"')
            if calibration:
                colors[calibration.split()[0]] += 1
            fallback_timestamp = fallback_timestamp or str(row.get("Timestamp") or "")
            for column in fieldnames:
                match = THICKNESS_RE.match(column)
                if match and _number(row.get(column)) is not None:
                    active_layers.add(int(match.group(1)))
            _select_latest(overall, row, row_number)
            _select_latest(by_source.setdefault(source, {}), row, row_number)

    if not raw_counts[ALL_SOURCES]:
        raise ValueError(f"{path.name} contains no measurement rows")
    layer_count = max(active_layers, default=0)
    if layer_count not in {3, 4, 5}:
        raise ValueError(f"{path.name} has unsupported detected layer count {layer_count}")
    dynamic_metrics = sorted(
        (
            name
            for name in fieldnames
            if THICKNESS_RE.match(name) or CONFIDENCE_RE.match(name)
        ),
        key=lambda value: (value.split("_")[0], int(value.rsplit("_", 1)[1])),
    )
    metrics = tuple(MISC_METRICS) + tuple(dynamic_metrics)
    car_id, body_id, job_date, job_time = _filename_metadata(
        path.name, config, fallback_timestamp
    )
    scopes = [
        _scope_result(
            ALL_SOURCES,
            raw_counts[ALL_SOURCES],
            aligned_counts[ALL_SOURCES],
            overall,
            metrics,
        )
    ]
    for source in sorted(by_source):
        scopes.append(
            _scope_result(
                source,
                raw_counts[source],
                aligned_counts[source],
                by_source[source],
                metrics,
            )
        )
    return {
        "client": config["client"],
        "filename": path.name,
        "car_id": car_id,
        "body_id": body_id,
        "job_date": job_date,
        "job_time": job_time,
        "color": colors.most_common(1)[0][0] if colors else "Unknown",
        "layer_count": layer_count,
        "scopes": scopes,
    }
