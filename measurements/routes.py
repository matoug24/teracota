"""Measurement History pages, analytics APIs, and admin configuration APIs."""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, render_template, request, url_for

from .config import (
    aliases_for_system,
    delete_source_mapping,
    get_location_config,
    location_exists,
    location_systems,
    save_location_config,
    save_source_mapping,
    source_mappings,
    system_context,
)
from .database import ANALYTICS_DB, UPLOAD_DB, quick_check
from .queries import (
    discovered_sources,
    metric_series,
    operation,
    options,
    performance,
    status_summary,
    summary,
)
from .settings import validate_environment


def create_blueprint(theme_getter):
    blueprint = Blueprint(
        "measurements",
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/assets/measurements",
    )

    def filters() -> dict:
        return {
            key: request.args.get(key, "")
            for key in ("start", "end", "source", "color", "car", "body")
        }

    def query_context():
        location = str(request.args.get("location", "")).strip()
        if not location or not location_exists(location):
            abort(404)
        system_id = request.args.get("system_id", type=int)
        aliases = None
        if system_id:
            system = system_context(system_id)
            if not system or system["location"] != location:
                abort(400, "System does not belong to the selected location")
            aliases = aliases_for_system(system_id, location)
        return location, aliases

    @blueprint.get("/measurements/location/<path:location>")
    def location_history(location: str):
        if not location_exists(location):
            abort(404)
        return render_template(
            "measurements/history.html",
            theme_css=theme_getter(),
            location=location,
            system_id="",
            system_name="",
            back_url=url_for("location_detail", location=location),
        )

    @blueprint.get("/measurements/system/<int:system_id>")
    def system_history(system_id: int):
        system = system_context(system_id)
        if not system:
            abort(404)
        return render_template(
            "measurements/history.html",
            theme_css=theme_getter(),
            location=system["location"],
            system_id=system_id,
            system_name=system["name"],
            back_url=url_for("system_detail", system_id=system_id),
        )

    @blueprint.get("/api/measurements/options")
    def api_options():
        location, _ = query_context()
        payload = options(location)
        systems = location_systems(location)
        mappings = source_mappings(location)
        aliases_by_system = {}
        for mapping in mappings:
            aliases_by_system.setdefault(mapping["system_id"], []).append(mapping["source_name"])
        payload["systems"] = [
            {**system, "source_aliases": aliases_by_system.get(system["id"], [])}
            for system in systems
        ]
        mapped_sources = {mapping["source_name"] for mapping in mappings}
        payload["unmapped_sources"] = [
            source for source in payload["sources"] if source not in mapped_sources
        ]
        return jsonify(payload)

    @blueprint.get("/api/measurements/summary")
    def api_summary():
        location, aliases = query_context()
        return jsonify(summary(location, filters(), aliases))

    @blueprint.get("/api/measurements/operation")
    def api_operation():
        location, aliases = query_context()
        return jsonify(operation(location, filters(), aliases))

    @blueprint.get("/api/measurements/performance")
    def api_performance():
        location, aliases = query_context()
        return jsonify(performance(location, filters(), aliases))

    @blueprint.get("/api/measurements/metrics/<group>")
    def api_metrics(group: str):
        location, aliases = query_context()
        try:
            payload = metric_series(
                location,
                filters(),
                group,
                request.args.get("view", "daily"),
                request.args.get("metric"),
                aliases,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(payload)

    @blueprint.get("/api/measurements/status")
    def api_status():
        location, _ = query_context()
        return jsonify(status_summary(location))

    @blueprint.get("/api/measurements/admin/config")
    def admin_config():
        locations = []
        for location in _active_locations():
            locations.append(
                {
                    "name": location,
                    "config": get_location_config(location),
                    "systems": location_systems(location),
                    "mappings": source_mappings(location),
                    "discovered_sources": discovered_sources(location),
                }
            )
        return jsonify(
            {
                "locations": locations,
                "environment_problems": validate_environment(),
                "databases": {
                    "analytics": quick_check(ANALYTICS_DB),
                    "uploads": quick_check(UPLOAD_DB),
                },
            }
        )

    @blueprint.put("/api/measurements/admin/locations/<path:location>")
    def update_location_config(location: str):
        try:
            return jsonify(save_location_config(location, request.get_json(silent=True) or {}))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @blueprint.post("/api/measurements/admin/mappings")
    def create_mapping():
        payload = request.get_json(silent=True) or {}
        try:
            mapping = save_source_mapping(
                str(payload.get("location", "")).strip(),
                int(payload.get("system_id")),
                payload.get("source_name"),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(mapping), 201

    @blueprint.delete("/api/measurements/admin/mappings/<int:mapping_id>")
    def remove_mapping(mapping_id: int):
        if not delete_source_mapping(mapping_id):
            abort(404)
        return "", 204

    def _active_locations() -> list[str]:
        from .config import connect

        with connect() as connection:
            return [
                row["name"]
                for row in connection.execute(
                    """
                    SELECT locations.name
                    FROM locations
                    WHERE EXISTS(SELECT 1 FROM systems WHERE systems.location=locations.name)
                    ORDER BY locations.display_rank,locations.name
                    """
                ).fetchall()
            ]

    return blueprint
