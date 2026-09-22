"""Measurement configuration stored alongside the main operations records."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sqlite3

from .settings import DEFAULT_LOCATION_CONFIG, validate_location_config


_database_path = os.environ.get("TERACOTA_DB_PATH", "teracota.sqlite3")


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def configure(database_path: str) -> None:
    global _database_path
    _database_path = os.path.abspath(database_path)


def connect() -> ClosingConnection:
    path = os.path.abspath(_database_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def initialize_schema() -> None:
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS measurement_location_settings (
                location TEXT PRIMARY KEY,
                car_id_index INTEGER NOT NULL DEFAULT 0,
                body_id_index INTEGER NOT NULL DEFAULT 2,
                layer_names TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS measurement_source_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                system_id INTEGER NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
                location TEXT NOT NULL,
                source_name TEXT NOT NULL,
                UNIQUE(location, source_name)
            );
            CREATE INDEX IF NOT EXISTS idx_measurement_mappings_system
                ON measurement_source_mappings(system_id, source_name);
            """
        )


def location_exists(location: str) -> bool:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT 1 FROM locations WHERE name=?
            UNION SELECT 1 FROM systems WHERE location=? LIMIT 1
            """,
            (location, location),
        ).fetchone()
    return row is not None


def get_location_config(location: str) -> dict:
    initialize_schema()
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM measurement_location_settings WHERE location=?", (location,)
        ).fetchone()
    if not row:
        return {"client": location, **validate_location_config(DEFAULT_LOCATION_CONFIG)}
    return {
        "client": location,
        "car_id_index": int(row["car_id_index"]),
        "body_id_index": int(row["body_id_index"]),
        "layer_names": json.loads(row["layer_names"]),
    }


def save_location_config(location: str, payload: dict) -> dict:
    if not location_exists(location):
        raise ValueError("Client name must exactly match an existing location")
    validated = validate_location_config(payload)
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO measurement_location_settings(
                location,car_id_index,body_id_index,layer_names,updated_at
            ) VALUES(?,?,?,?,?)
            ON CONFLICT(location) DO UPDATE SET
                car_id_index=excluded.car_id_index,
                body_id_index=excluded.body_id_index,
                layer_names=excluded.layer_names,
                updated_at=excluded.updated_at
            """,
            (
                location,
                validated["car_id_index"],
                validated["body_id_index"],
                json.dumps(validated["layer_names"], separators=(",", ":")),
                updated_at,
            ),
        )
    return {"client": location, **validated}


def location_systems(location: str) -> list[dict]:
    with connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT id,name FROM systems WHERE location=? ORDER BY name,id", (location,)
            ).fetchall()
        ]


def source_mappings(location: str) -> list[dict]:
    initialize_schema()
    with connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT measurement_source_mappings.id,
                       measurement_source_mappings.system_id,
                       measurement_source_mappings.location,
                       measurement_source_mappings.source_name,
                       systems.name AS system_name
                FROM measurement_source_mappings
                JOIN systems ON systems.id=measurement_source_mappings.system_id
                WHERE measurement_source_mappings.location=?
                ORDER BY systems.name,measurement_source_mappings.source_name
                """,
                (location,),
            ).fetchall()
        ]


def aliases_for_system(system_id: int, location: str | None = None) -> list[str]:
    initialize_schema()
    statement = "SELECT source_name FROM measurement_source_mappings WHERE system_id=?"
    parameters: list = [system_id]
    if location:
        statement += " AND location=?"
        parameters.append(location)
    statement += " ORDER BY source_name"
    with connect() as connection:
        return [
            row["source_name"]
            for row in connection.execute(statement, parameters).fetchall()
        ]


def system_context(system_id: int) -> dict | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id,name,location FROM systems WHERE id=?", (system_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["source_aliases"] = aliases_for_system(system_id, result["location"])
    return result


def save_source_mapping(location: str, system_id: int, source_name: str) -> dict:
    source_name = str(source_name or "").strip()
    if not source_name:
        raise ValueError("Source name is required")
    if source_name == "__ALL__":
        raise ValueError("The whole-vehicle source cannot be assigned to one system")
    with connect() as connection:
        system = connection.execute(
            "SELECT id,name,location FROM systems WHERE id=?", (system_id,)
        ).fetchone()
        if not system or system["location"] != location:
            raise ValueError("The selected system must belong to this location")
        existing = connection.execute(
            "SELECT system_id FROM measurement_source_mappings WHERE location=? AND source_name=?",
            (location, source_name),
        ).fetchone()
        if existing and int(existing["system_id"]) != system_id:
            raise ValueError("This source name is already assigned to another system")
        cursor = connection.execute(
            """
            INSERT INTO measurement_source_mappings(system_id,location,source_name)
            VALUES(?,?,?)
            ON CONFLICT(location,source_name) DO UPDATE SET system_id=excluded.system_id
            """,
            (system_id, location, source_name),
        )
        mapping_id = cursor.lastrowid
        if not mapping_id:
            mapping_id = connection.execute(
                "SELECT id FROM measurement_source_mappings WHERE location=? AND source_name=?",
                (location, source_name),
            ).fetchone()["id"]
    return {
        "id": int(mapping_id),
        "system_id": system_id,
        "system_name": system["name"],
        "location": location,
        "source_name": source_name,
    }


def delete_source_mapping(mapping_id: int) -> bool:
    with connect() as connection:
        cursor = connection.execute(
            "DELETE FROM measurement_source_mappings WHERE id=?", (mapping_id,)
        )
    return cursor.rowcount > 0


def rename_location(previous: str, replacement: str) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE measurement_location_settings SET location=? WHERE location=?",
            (replacement, previous),
        )
        connection.execute(
            "UPDATE measurement_source_mappings SET location=? WHERE location=?",
            (replacement, previous),
        )


def move_system_mappings(system_id: int, replacement_location: str) -> None:
    with connect() as connection:
        aliases = connection.execute(
            "SELECT source_name FROM measurement_source_mappings WHERE system_id=?",
            (system_id,),
        ).fetchall()
        for alias in aliases:
            conflict = connection.execute(
                """
                SELECT system_id FROM measurement_source_mappings
                WHERE location=? AND source_name=? AND system_id<>?
                """,
                (replacement_location, alias["source_name"], system_id),
            ).fetchone()
            if conflict:
                raise ValueError(
                    f"Source alias {alias['source_name']} already belongs to a system at the target location"
                )
        connection.execute(
            "UPDATE measurement_source_mappings SET location=? WHERE system_id=?",
            (replacement_location, system_id),
        )
