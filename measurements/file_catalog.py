"""Catalog queries for authenticated raw measurement file downloads."""

from __future__ import annotations

import calendar
from collections import OrderedDict

from .database import ANALYTICS_DB, connect, initialize_analytics


FILES_PER_PAGE = 100


def _period(year: int, month: int) -> tuple[str, str]:
    if year < 2000 or year > 2200 or month < 1 or month > 12:
        raise ValueError("Invalid measurement archive period")
    return f"{year:04d}-{month:02d}-01", f"{year + (month == 12):04d}-{month % 12 + 1:02d}-01"


def month_catalog(client: str) -> dict:
    initialize_analytics()
    with connect(ANALYTICS_DB) as connection:
        rows = connection.execute(
            """
            SELECT CAST(substr(job_date,1,4) AS INTEGER) AS year,
                   CAST(substr(job_date,6,2) AS INTEGER) AS month,
                   COUNT(*) AS file_count,
                   COALESCE(SUM(size_bytes),0) AS size_bytes
            FROM jobs
            WHERE client=? AND length(job_date)>=7
            GROUP BY substr(job_date,1,4),substr(job_date,6,2)
            ORDER BY year DESC,month DESC
            """,
            (client,),
        ).fetchall()
    years: OrderedDict[int, dict] = OrderedDict()
    total_files = 0
    total_bytes = 0
    for row in rows:
        year = int(row["year"])
        month = int(row["month"])
        group = years.setdefault(
            year,
            {"year": year, "file_count": 0, "size_bytes": 0, "months": []},
        )
        entry = {
            "year": year,
            "month": month,
            "month_name": calendar.month_name[month],
            "file_count": int(row["file_count"]),
            "size_bytes": int(row["size_bytes"]),
        }
        group["months"].append(entry)
        group["file_count"] += entry["file_count"]
        group["size_bytes"] += entry["size_bytes"]
        total_files += entry["file_count"]
        total_bytes += entry["size_bytes"]
    return {"years": list(years.values()), "file_count": total_files, "size_bytes": total_bytes}


def month_files(client: str, year: int, month: int, page: int = 1) -> dict:
    start, end = _period(year, month)
    page = max(1, int(page))
    initialize_analytics()
    with connect(ANALYTICS_DB) as connection:
        total = int(
            connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE client=? AND job_date>=? AND job_date<?",
                (client, start, end),
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT id,filename,size_bytes,job_date,job_time,color,car_id,body_id
            FROM jobs
            WHERE client=? AND job_date>=? AND job_date<?
            ORDER BY job_date DESC,job_time DESC,filename
            LIMIT ? OFFSET ?
            """,
            (client, start, end, FILES_PER_PAGE, (page - 1) * FILES_PER_PAGE),
        ).fetchall()
    pages = max(1, (total + FILES_PER_PAGE - 1) // FILES_PER_PAGE)
    if page > pages:
        page = pages
        return month_files(client, year, month, page)
    return {
        "files": [dict(row) for row in rows],
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": FILES_PER_PAGE,
    }


def file_record(file_id: int) -> dict | None:
    initialize_analytics()
    with connect(ANALYTICS_DB) as connection:
        row = connection.execute(
            """
            SELECT id,client,filename,object_key,sha256,size_bytes,job_date,job_time
            FROM jobs WHERE id=?
            """,
            (file_id,),
        ).fetchone()
    return dict(row) if row else None


def archive_records(client: str, year: int, month: int) -> list[dict]:
    start, end = _period(year, month)
    initialize_analytics()
    with connect(ANALYTICS_DB) as connection:
        rows = connection.execute(
            """
            SELECT id,client,filename,object_key,sha256,size_bytes,job_date,job_time
            FROM jobs
            WHERE client=? AND job_date>=? AND job_date<?
            ORDER BY job_date,job_time,filename
            """,
            (client, start, end),
        ).fetchall()
    return [dict(row) for row in rows]
