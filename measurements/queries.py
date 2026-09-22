"""Bounded analytics queries for one location and one optional robot scope."""

from __future__ import annotations

from collections import defaultdict
import calendar
from datetime import date
import math
import re

from .config import get_location_config
from .database import connect, initialize_analytics
from .settings import ANALYTICS_DB
from .summarizer import ALL_SOURCES, MISC_METRICS


def _arguments(filters: dict, client: str, aliases: list[str] | None = None):
    clauses = ["j.client=?"]
    params: list = [client]
    source = str(filters.get("source", "")).strip()
    if aliases is not None:
        if not aliases:
            clauses.append("1=0")
        else:
            placeholders = ",".join("?" for _ in aliases)
            clauses.append(f"s.source IN ({placeholders})")
            params.extend(aliases)
    else:
        clauses.append("s.source=?")
        params.append(source or ALL_SOURCES)
    for key, column in (
        ("start", "j.job_date>=?"),
        ("end", "j.job_date<=?"),
        ("color", "j.color=?"),
        ("car", "j.car_id=?"),
        ("body", "j.body_id=?"),
    ):
        value = str(filters.get(key, "")).strip()
        if value and value != "All":
            clauses.append(column)
            params.append(value)
    return " AND ".join(clauses), params


def options(client: str) -> dict:
    initialize_analytics()
    with connect(ANALYTICS_DB) as connection:
        date_row = connection.execute(
            "SELECT MIN(job_date),MAX(job_date),COUNT(*) FROM jobs WHERE client=?", (client,)
        ).fetchone()
        colors = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT color FROM jobs WHERE client=? ORDER BY color", (client,)
            )
        ]
        cars = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT car_id FROM jobs WHERE client=? ORDER BY car_id", (client,)
            )
        ]
        bodies = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT body_id FROM jobs WHERE client=? ORDER BY body_id", (client,)
            )
        ]
        sources = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT s.source
                FROM job_scopes s JOIN jobs j ON j.id=s.job_id
                WHERE j.client=? AND s.source<>? ORDER BY s.source
                """,
                (client, ALL_SOURCES),
            )
        ]
        metrics = [
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT m.metric_key
                FROM metric_stats m
                JOIN job_scopes s ON s.id=m.scope_id
                JOIN jobs j ON j.id=s.job_id
                WHERE j.client=? ORDER BY m.metric_key
                """,
                (client,),
            )
            if row[0] in MISC_METRICS or re.match(r"^Confidence_\d+$", row[0])
        ]
    maximum = date_row[1]
    default_start = None
    if maximum:
        latest = date.fromisoformat(maximum)
        month_index = latest.year * 12 + latest.month - 1 - 3
        year, zero_based_month = divmod(month_index, 12)
        month = zero_based_month + 1
        default_start = date(
            year, month, min(latest.day, calendar.monthrange(year, month)[1])
        ).isoformat()
    return {
        "minimum_date": date_row[0],
        "maximum_date": maximum,
        "default_start": default_start,
        "job_count": int(date_row[2]),
        "colors": colors,
        "cars": cars,
        "bodies": bodies,
        "sources": sources,
        "misc_metrics": metrics,
    }


def summary(client: str, filters: dict, aliases: list[str] | None = None) -> dict:
    where, params = _arguments(filters, client, aliases)
    with connect(ANALYTICS_DB) as connection:
        row = connection.execute(
            f"""
            SELECT COUNT(DISTINCT j.id) jobs,
                   COALESCE(SUM(s.raw_count),0) measurements,
                   COALESCE(SUM(s.aligned_count),0) aligned,
                   COALESCE(SUM(s.deduplicated_count),0) deduplicated,
                   COALESCE(SUM(s.valid_count),0) valid,
                   MIN(j.job_date) first_date,
                   MAX(j.job_date) latest_date,
                   MAX(j.imported_at) last_import
            FROM jobs j JOIN job_scopes s ON s.job_id=j.id
            WHERE {where}
            """,
            params,
        ).fetchone()
        color_rows = connection.execute(
            f"""
            SELECT j.color,COUNT(DISTINCT j.id) jobs
            FROM jobs j JOIN job_scopes s ON s.job_id=j.id
            WHERE {where}
            GROUP BY j.color ORDER BY jobs DESC,j.color LIMIT 4
            """,
            params,
        ).fetchall()
    measurements = int(row["measurements"] or 0)
    deduplicated = int(row["deduplicated"] or 0)
    return {
        "jobs": int(row["jobs"] or 0),
        "measurements": measurements,
        "alignment_percentage": 100 * int(row["aligned"] or 0) / measurements
        if measurements
        else 0,
        "valid_percentage": 100 * int(row["valid"] or 0) / deduplicated
        if deduplicated
        else 0,
        "first_date": row["first_date"],
        "latest_date": row["latest_date"],
        "last_import": row["last_import"],
        "colors": [dict(item) for item in color_rows],
    }


def operation(client: str, filters: dict, aliases: list[str] | None = None) -> dict:
    where, params = _arguments(filters, client, aliases)
    with connect(ANALYTICS_DB) as connection:
        rows = connection.execute(
            f"""
            SELECT j.job_date,j.color,COUNT(DISTINCT j.id) AS jobs,
                   SUM(s.raw_count) AS measurements
            FROM jobs j JOIN job_scopes s ON s.job_id=j.id
            WHERE {where}
            GROUP BY j.job_date,j.color
            ORDER BY j.job_date,j.color
            """,
            params,
        ).fetchall()
    return {"rows": [dict(row) for row in rows]}


def performance(client: str, filters: dict, aliases: list[str] | None = None) -> dict:
    where, params = _arguments(filters, client, aliases)
    with connect(ANALYTICS_DB) as connection:
        rows = connection.execute(
            f"""
            SELECT j.job_date,
                   SUM(s.raw_count) raw_count,
                   SUM(s.aligned_count) aligned_count,
                   SUM(s.deduplicated_count) deduplicated_count,
                   SUM(s.valid_count) valid_count
            FROM jobs j JOIN job_scopes s ON s.job_id=j.id
            WHERE {where}
            GROUP BY j.job_date ORDER BY j.job_date
            """,
            params,
        ).fetchall()
    result = []
    for row in rows:
        raw = int(row["raw_count"] or 0)
        deduplicated = int(row["deduplicated_count"] or 0)
        valid = int(row["valid_count"] or 0)
        aligned = int(row["aligned_count"] or 0)
        result.append(
            {
                "date": row["job_date"],
                "raw_count": raw,
                "deduplicated_count": deduplicated,
                "aligned_count": aligned,
                "valid_count": valid,
                "alignment_percentage": 100 * aligned / raw if raw else 0,
                "valid_percentage": 100 * valid / deduplicated if deduplicated else 0,
            }
        )
    return {"rows": result}


def _metric_label(metric_key: str, layer_count: int, config: dict) -> str:
    match = re.match(r"^(Thickness|Confidence)_(\d+)$", metric_key)
    if not match:
        return metric_key.replace("_", " ")
    index = int(match.group(2)) - 1
    names = config["layer_names"].get(str(layer_count), [])
    layer = names[index] if 0 <= index < len(names) else f"Layer {index + 1}"
    return layer if match.group(1) == "Thickness" else f"Confidence: {layer}"


def _combine(rows: list[dict]) -> dict:
    count = 0
    mean = 0.0
    m2 = 0.0
    minimum = None
    maximum = None
    for row in rows:
        sample_count = int(row["sample_count"])
        if not sample_count or row["mean"] is None:
            continue
        group_mean = float(row["mean"])
        group_m2 = (sample_count - 1) * float(row["stdev"] or 0) ** 2
        if count == 0:
            count, mean, m2 = sample_count, group_mean, group_m2
        else:
            delta = group_mean - mean
            total = count + sample_count
            m2 = m2 + group_m2 + delta * delta * count * sample_count / total
            mean = mean + delta * sample_count / total
            count = total
        minimum = row["minimum"] if minimum is None else min(minimum, row["minimum"])
        maximum = row["maximum"] if maximum is None else max(maximum, row["maximum"])
    return {
        "count": count,
        "mean": mean if count else None,
        "stdev": math.sqrt(m2 / (count - 1)) if count > 1 else None,
        "min": minimum,
        "max": maximum,
    }


def metric_series(
    client: str,
    filters: dict,
    group: str,
    view: str,
    metric: str | None = None,
    aliases: list[str] | None = None,
) -> dict:
    where, params = _arguments(filters, client, aliases)
    if group == "thickness":
        metric_clause = "m.metric_key LIKE 'Thickness\\_%' ESCAPE '\\'"
    elif group == "misc" and (
        metric in MISC_METRICS or (metric and re.match(r"^Confidence_\d+$", metric))
    ):
        metric_clause = "m.metric_key=?"
        params.append(metric)
    else:
        raise ValueError("Unknown metric group or metric")
    limit = 5000 if view == "car" else 20000
    with connect(ANALYTICS_DB) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                f"""
                SELECT j.id,j.job_date,j.job_time,j.car_id,j.body_id,j.color,j.layer_count,
                       m.metric_key,m.sample_count,m.mean,m.stdev,m.minimum,m.maximum
                FROM jobs j
                JOIN job_scopes s ON s.job_id=j.id
                JOIN metric_stats m ON m.scope_id=s.id
                WHERE {where} AND {metric_clause}
                ORDER BY j.job_date,j.job_time,j.id,m.metric_key LIMIT ?
                """,
                params + [limit + 1],
            ).fetchall()
        ]
    truncated = len(rows) > limit
    rows = rows[:limit]
    config = get_location_config(client)
    if view == "car":
        points = []
        for row in rows:
            points.append(
                {
                    "x": (
                        f"{row['job_date']} {row['job_time']} / "
                        f"{row['car_id']} / {row['body_id']}"
                    ),
                    "date": row["job_date"],
                    "car_id": row["car_id"],
                    "body_id": row["body_id"],
                    "color": row["color"],
                    "series": _metric_label(row["metric_key"], row["layer_count"], config),
                    "count": row["sample_count"],
                    "mean": row["mean"],
                    "stdev": row["stdev"],
                    "min": row["minimum"],
                    "max": row["maximum"],
                }
            )
        return {"points": points, "truncated": truncated}

    groups = defaultdict(list)
    labels = {}
    for row in rows:
        key = (row["job_date"], row["metric_key"], row["layer_count"])
        groups[key].append(row)
        labels[key] = _metric_label(row["metric_key"], row["layer_count"], config)
    points = []
    for key in sorted(groups):
        points.append(
            {
                "x": key[0],
                "date": key[0],
                "series": labels[key],
                **_combine(groups[key]),
            }
        )
    return {"points": points, "truncated": truncated}


def status_summary(client: str) -> dict:
    initialize_analytics()
    with connect(ANALYTICS_DB) as connection:
        row = connection.execute(
            "SELECT COUNT(*) jobs,MAX(imported_at) last_import FROM jobs WHERE client=?",
            (client,),
        ).fetchone()
    return {"jobs": int(row["jobs"] or 0), "last_import": row["last_import"]}


def discovered_sources(client: str) -> list[str]:
    return options(client)["sources"]
