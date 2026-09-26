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
