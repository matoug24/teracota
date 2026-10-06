"""Previous-day measurement metrics for location notification emails."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from .database import initialize_analytics
from .queries import summary


def robot_daily_metrics(
    operations_database: str | Path,
    location: str,
    summary_date: str,
) -> list[dict]:
    """Return dashboard-equivalent measurement metrics for every system at a location."""
    connection = sqlite3.connect(str(Path(operations_database).resolve()), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        systems = connection.execute(
            "SELECT id,name FROM systems WHERE location=? ORDER BY name COLLATE NOCASE,id",
            (location,),
        ).fetchall()
        mappings = connection.execute(
            """
            SELECT system_id,source_name
            FROM measurement_source_mappings
            WHERE location=?
            ORDER BY source_name COLLATE NOCASE
            """,
            (location,),
        ).fetchall()
    finally:
        connection.close()

    aliases_by_system: dict[int, list[str]] = {}
    for mapping in mappings:
        aliases_by_system.setdefault(int(mapping["system_id"]), []).append(
            mapping["source_name"]
        )

    initialize_analytics()
    filters = {"start": summary_date, "end": summary_date}
    result = []
    for system in systems:
        aliases = aliases_by_system.get(int(system["id"]), [])
        metrics = summary(location, filters, aliases=aliases)
        result.append(
            {
                "system_id": int(system["id"]),
                "system_name": system["name"],
                "source_aliases": aliases,
                "jobs": metrics["jobs"],
                "measurements": metrics["measurements"],
                "alignment_percentage": metrics["alignment_percentage"],
                "valid_percentage": metrics["valid_percentage"],
            }
        )
    return result


def robot_metrics_for_dates(
    operations_database: str | Path,
    location: str,
    summary_dates: list[str],
) -> list[dict]:
    """Return one ordered metric row per requested date for every robot."""
    robots_by_id: dict[int, dict] = {}
    for summary_date in summary_dates:
        for robot in robot_daily_metrics(operations_database, location, summary_date):
            entry = robots_by_id.setdefault(
                robot["system_id"],
                {
                    "system_id": robot["system_id"],
                    "system_name": robot["system_name"],
                    "source_aliases": robot["source_aliases"],
                    "days": [],
                },
            )
            entry["days"].append(
                {
                    "date": summary_date,
                    "jobs": robot["jobs"],
                    "measurements": robot["measurements"],
                    "alignment_percentage": robot["alignment_percentage"],
                    "valid_percentage": robot["valid_percentage"],
                }
            )
    return sorted(robots_by_id.values(), key=lambda item: item["system_name"].casefold())
