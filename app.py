from datetime import date
import os
import sqlite3

from flask import Flask, abort, jsonify, render_template, request, session


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.environ.get(
    "TERACOTA_DB_PATH",
    os.path.join(BASE_DIR, "teracota.sqlite3"),
)

app = Flask(__name__, template_folder=BASE_DIR, static_folder=BASE_DIR, static_url_path="/assets")
app.secret_key = os.environ.get("TERACOTA_SECRET_KEY", "teracota-local-session-key")

LOGIN_USERNAME = "teraview"
LOGIN_PASSWORD = "pythagorus"
THEME_FILES = {
    "standard": "flask_styles.css",
    "control-room": "flask_styles_control_room.css",
    "instrument": "flask_styles_instrument.css",
}


def get_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def execute(statement, parameters=()):
    with get_db() as db:
        cursor = db.execute(statement, parameters)
        db.commit()
        return cursor


def query_all(statement, parameters=()):
    with get_db() as db:
        return [dict(row) for row in db.execute(statement, parameters).fetchall()]


def query_one(statement, parameters=()):
    with get_db() as db:
        row = db.execute(statement, parameters).fetchone()
        return dict(row) if row else None


def ensure_column(table, column, definition):
    columns = {row["name"] for row in query_all(f"PRAGMA table_info({table})")}
    if column not in columns:
        execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    execute(
        """
        CREATE TABLE IF NOT EXISTS systems (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            status TEXT NOT NULL,
            owner TEXT NOT NULL,
            last_visit TEXT,
            next_visit TEXT,
            notes TEXT
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS site_visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            date TEXT NOT NULL,
            engineer TEXT NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS maintenance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id INTEGER,
            system_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            engineer TEXT NOT NULL,
            type TEXT NOT NULL,
            summary TEXT,
            FOREIGN KEY (visit_id) REFERENCES site_visits (id),
            FOREIGN KEY (system_id) REFERENCES systems (id)
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS system_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            severity TEXT NOT NULL,
            opened TEXT NOT NULL,
            status TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (system_id) REFERENCES systems (id)
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS development_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            expected_time TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    ensure_column("system_issues", "reported_by", "TEXT NOT NULL DEFAULT ''")
    ensure_column("system_issues", "resolution_notes", "TEXT NOT NULL DEFAULT ''")
    ensure_column("system_issues", "closed_date", "TEXT NOT NULL DEFAULT ''")
    ensure_column("maintenance_records", "visit_id", "INTEGER")
    execute(
        """
        CREATE TABLE IF NOT EXISTS system_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            FOREIGN KEY (system_id) REFERENCES systems (id)
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS locations (
            name TEXT PRIMARY KEY,
            contacts TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT ''
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    seed_db()
    sync_locations()
    backfill_status_history()
    execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('theme', 'standard')")


def seed_db():
    if query_one("SELECT id FROM systems LIMIT 1"):
        return

    systems = [
        (
            "TeraCota Alpha",
            "North Plant",
            "Operational",
            "Operations",
            "2026-07-05",
            "2026-08-02",
            "Running within expected limits. Monitor inlet pressure trend.",
        ),
        (
            "TeraCota Beta",
            "South Site",
            "Needs Maintenance",
            "Field Service",
            "2026-06-28",
            "2026-07-15",
            "Service visit scheduled for flow sensor replacement.",
        ),
        (
            "TeraCota Gamma",
            "Pilot Lab",
            "Commissioning",
            "Engineering",
            "2026-07-09",
            "2026-07-18",
            "Commissioning checklist in progress.",
        ),
    ]

    system_ids = []
    for system in systems:
        cursor = execute(
            """
            INSERT INTO systems
                (name, location, status, owner, last_visit, next_visit, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            system,
        )
        system_ids.append(cursor.lastrowid)

    maintenance = [
        (system_ids[0], "2026-07-05", "M. Matoug", "Preventive", "Checked seals, cleaned filters, verified PLC logs."),
        (system_ids[0], "2026-06-14", "A. Khan", "Inspection", "No alarms. Recalibrated temperature sensor."),
        (system_ids[1], "2026-06-28", "R. Chen", "Corrective", "Diagnosed intermittent flow readings. Sensor likely degraded."),
        (system_ids[2], "2026-07-09", "M. Matoug", "Commissioning", "Verified wiring, network connection, and baseline alarms."),
    ]
    for record in maintenance:
        system = query_one("SELECT location FROM systems WHERE id = ?", (record[0],))
        visit = execute(
            "INSERT INTO site_visits (location, date, engineer) VALUES (?, ?, ?)",
            (system["location"], record[1], record[2]),
        )
        execute(
            """
            INSERT INTO maintenance_records
                (visit_id, system_id, date, engineer, type, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (visit.lastrowid, *record),
        )

    issues = [
        (
            system_ids[0],
            "Inlet pressure drifting high",
            "Medium",
            "2026-07-05",
            "Open",
            "Trend is slow but visible. Recheck during next visit.",
        ),
        (
            system_ids[1],
            "Flow sensor intermittent",
            "High",
            "2026-06-28",
            "Open",
            "Replacement part ordered.",
        ),
    ]
    for issue in issues:
        execute(
            """
            INSERT INTO system_issues
                (system_id, title, severity, opened, status, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            issue,
        )

    tasks = [
        (
            "Improve cabinet condensation guidance",
            "Hardware",
            "2 weeks",
            "Create a field checklist for gasket inspection and ventilation review.",
            "2026-07-12",
        ),
        (
            "Add PLC clock sync diagnostic",
            "Software",
            "1 week",
            "Surface NTP configuration and clock drift in the maintenance screen.",
            "2026-07-12",
        ),
    ]
    for task in tasks:
        execute(
            """
            INSERT INTO development_tasks
                (title, category, expected_time, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            task,
        )


def load_state():
    systems = query_all("SELECT * FROM systems ORDER BY location, name")
    for system in systems:
        system["maintenance"] = query_all(
            "SELECT * FROM maintenance_records WHERE system_id = ? ORDER BY date DESC, id DESC",
            (system["id"],),
        )
        system["issues"] = query_all(
            "SELECT * FROM system_issues WHERE system_id = ? ORDER BY opened DESC, id DESC",
            (system["id"],),
        )
        for issue in system["issues"]:
            issue["severity"] = clean_severity(issue["severity"])
        system["status_history"] = query_all(
            "SELECT * FROM system_status_history WHERE system_id = ? ORDER BY started_at, id",
            (system["id"],),
        )

    tasks = query_all("SELECT * FROM development_tasks ORDER BY category, created_at DESC, id DESC")
    locations = query_all("SELECT * FROM locations ORDER BY name")
    return {
        "authenticated": is_authenticated(),
        "systems": systems,
        "tasks": tasks,
        "locations": locations,
        "theme": get_active_theme(),
    }


def clean(value, default=""):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def today_iso():
    return date.today().isoformat()


def is_authenticated():
    return session.get("logged_in") is True


def require_login():
    if not is_authenticated():
        abort(401)


def clean_categories(value):
    allowed = ["Calibration", "Optics", "Commissioning", "Electronics"]
    if isinstance(value, list):
        selected = [item for item in value if item in allowed]
    else:
        selected = [item.strip() for item in str(value or "").split(",") if item.strip() in allowed]
    return ", ".join(selected) if selected else "Calibration"


def clean_system_ids(value):
    values = value if isinstance(value, list) else [value]
    system_ids = []
    for item in values:
        try:
            system_id = int(item)
        except (TypeError, ValueError):
            continue
        if system_id not in system_ids:
            system_ids.append(system_id)
    return system_ids


def refresh_last_visit(system_id):
    latest = query_one(
        "SELECT MAX(date) AS latest_date FROM maintenance_records WHERE system_id = ?",
        (system_id,),
    )
    execute(
        "UPDATE systems SET last_visit = ? WHERE id = ?",
        (latest["latest_date"] if latest and latest["latest_date"] else "", system_id),
    )


def delete_site_visit_if_empty(visit_id):
    if not visit_id:
        return
    remaining = query_one("SELECT id FROM maintenance_records WHERE visit_id = ? LIMIT 1", (visit_id,))
    if not remaining:
        execute("DELETE FROM site_visits WHERE id = ?", (visit_id,))


def create_grouped_site_visit(payload, fallback_system_id=None):
    system_ids = clean_system_ids(payload.get("system_ids"))
    if not system_ids and fallback_system_id:
        system_ids = [fallback_system_id]
    if not system_ids:
        return None, "Select at least one system"

    placeholders = ",".join("?" for _ in system_ids)
    systems = query_all(
        f"SELECT id, location FROM systems WHERE id IN ({placeholders})",
        tuple(system_ids),
    )
    location = clean(payload.get("location"), systems[0]["location"] if systems else "")
    valid_ids = [system["id"] for system in systems if system["location"] == location]
    if len(valid_ids) != len(system_ids):
        return None, "All selected systems must belong to the same location"

    visit_date = clean(payload.get("date"), today_iso())
    engineer = clean(payload.get("engineer"), "Unknown engineer")
    visit = execute(
        "INSERT INTO site_visits (location, date, engineer) VALUES (?, ?, ?)",
        (location, visit_date, engineer),
    )
    for system_id in valid_ids:
        execute(
            """
            INSERT INTO maintenance_records
                (visit_id, system_id, date, engineer, type, summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                visit.lastrowid,
                system_id,
                visit_date,
                engineer,
                clean_categories(payload.get("type")),
                clean(payload.get("summary")),
            ),
        )
        refresh_last_visit(system_id)
    return visit.lastrowid, None


def clean_severity(value):
    severity = clean(value, "Medium").title()
    if severity == "Critical":
        severity = "High"
    return severity if severity in ("Low", "Medium", "High") else "Medium"


def clean_system_status(value):
    status = clean(value, "Operational")
    allowed = ("Operational", "Needs Maintenance", "Offline", "Commissioning")
    return status if status in allowed else "Operational"


def get_active_theme():
    setting = query_one("SELECT value FROM app_settings WHERE key = 'theme'")
    theme = setting["value"] if setting else "standard"
    return theme if theme in THEME_FILES else "standard"


def render_page(page, system_id="", location=""):
    theme = get_active_theme()
    return render_template(
        "index.html",
        initial_page=page,
        initial_system_id=system_id,
        initial_location=location,
        theme_css=THEME_FILES[theme],
    )


def sync_locations():
    for row in query_all("SELECT DISTINCT location FROM systems WHERE location <> ''"):
        execute("INSERT OR IGNORE INTO locations (name, contacts, notes) VALUES (?, '', '')", (row["location"],))


def backfill_status_history():
    for system in query_all("SELECT id, status FROM systems"):
        if query_one("SELECT id FROM system_status_history WHERE system_id = ? LIMIT 1", (system["id"],)):
            continue
        first_activity = query_one(
            """
            SELECT MIN(activity_date) AS first_date
            FROM (
                SELECT date AS activity_date FROM maintenance_records WHERE system_id = ?
                UNION ALL
                SELECT opened AS activity_date FROM system_issues WHERE system_id = ?
            )
            """,
            (system["id"], system["id"]),
        )
        started_at = first_activity["first_date"] if first_activity and first_activity["first_date"] else today_iso()
        execute(
            "INSERT INTO system_status_history (system_id, status, started_at) VALUES (?, ?, ?)",
            (system["id"], system["status"], started_at),
        )


def set_system_status(system_id, status, started_at):
    current = query_one("SELECT status FROM systems WHERE id = ?", (system_id,))
    if not current or current["status"] == status:
        return
    execute("UPDATE systems SET status = ? WHERE id = ?", (status, system_id))
    execute(
        "INSERT INTO system_status_history (system_id, status, started_at) VALUES (?, ?, ?)",
        (system_id, status, clean(started_at, today_iso())),
    )


def sync_current_status(system_id):
    latest = query_one(
        """
        SELECT status FROM system_status_history
        WHERE system_id = ?
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        (system_id,),
    )
    if latest:
        execute("UPDATE systems SET status = ? WHERE id = ?", (latest["status"], system_id))


@app.route("/")
@app.route("/index.html")
def index():
    return render_page("home")


@app.route("/systems/<int:system_id>")
def system_detail(system_id):
    return render_page("system", system_id=system_id)


@app.route("/locations/<path:location>")
def location_detail(location):
    return render_page("location", location=location)


@app.route("/admin")
def admin_page():
    return render_page("admin")


@app.route("/api/state")
def api_state():
    return jsonify(load_state())


@app.route("/api/login", methods=["POST"])
def login():
    payload = request.get_json(force=True)
    username = clean(payload.get("username"))
    password = clean(payload.get("password"))
    if username == LOGIN_USERNAME and password == LOGIN_PASSWORD:
        session["logged_in"] = True
        return jsonify(load_state())
    return jsonify({"message": "Invalid username or password"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify(load_state())


@app.route("/api/systems", methods=["POST"])
def create_system():
    require_login()
    payload = request.get_json(force=True)
    cursor = execute(
        """
        INSERT INTO systems
            (name, location, status, owner, last_visit, next_visit, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean(payload.get("name"), "Untitled System"),
            clean(payload.get("location"), "Unknown Location"),
            clean(payload.get("status"), "Operational"),
            "Unassigned",
            "",
            "",
            clean(payload.get("notes")),
        ),
    )
    execute(
        "INSERT INTO system_status_history (system_id, status, started_at) VALUES (?, ?, ?)",
        (
            cursor.lastrowid,
            clean(payload.get("status"), "Operational"),
            clean(payload.get("status_start"), today_iso()),
        ),
    )
    sync_locations()
    return jsonify({"id": cursor.lastrowid, **load_state()})


@app.route("/api/systems/<int:system_id>", methods=["PUT"])
def update_system(system_id):
    require_login()
    payload = request.get_json(force=True)
    previous = query_one("SELECT status FROM systems WHERE id = ?", (system_id,))
    new_status = clean(payload.get("status"), "Operational")
    execute(
        """
        UPDATE systems
        SET name = ?, location = ?, status = ?, owner = ?, last_visit = ?, next_visit = ?, notes = ?
        WHERE id = ?
        """,
        (
            clean(payload.get("name"), "Untitled System"),
            clean(payload.get("location"), "Unknown Location"),
            previous["status"] if previous else new_status,
            "Unassigned",
            clean(payload.get("last_visit")),
            "",
            clean(payload.get("notes")),
            system_id,
        ),
    )
    set_system_status(system_id, new_status, payload.get("status_start"))
    sync_locations()
    return jsonify(load_state())


@app.route("/api/systems/<int:system_id>", methods=["DELETE"])
def delete_system(system_id):
    require_login()
    visit_ids = [
        row["visit_id"]
        for row in query_all(
            "SELECT DISTINCT visit_id FROM maintenance_records WHERE system_id = ? AND visit_id IS NOT NULL",
            (system_id,),
        )
    ]
    execute("DELETE FROM maintenance_records WHERE system_id = ?", (system_id,))
    execute("DELETE FROM system_issues WHERE system_id = ?", (system_id,))
    execute("DELETE FROM system_status_history WHERE system_id = ?", (system_id,))
    execute("DELETE FROM systems WHERE id = ?", (system_id,))
    for visit_id in visit_ids:
        delete_site_visit_if_empty(visit_id)
    return jsonify(load_state())


@app.route("/api/status-history/<int:history_id>", methods=["PUT"])
def update_status_history(history_id):
    require_login()
    payload = request.get_json(force=True)
    history = query_one("SELECT system_id FROM system_status_history WHERE id = ?", (history_id,))
    if not history:
        abort(404)
    execute(
        "UPDATE system_status_history SET status = ?, started_at = ? WHERE id = ?",
        (
            clean_system_status(payload.get("status")),
            clean(payload.get("started_at"), today_iso()),
            history_id,
        ),
    )
    sync_current_status(history["system_id"])
    return jsonify(load_state())


@app.route("/api/status-history/<int:history_id>", methods=["DELETE"])
def delete_status_history(history_id):
    require_login()
    history = query_one("SELECT system_id FROM system_status_history WHERE id = ?", (history_id,))
    if not history:
        abort(404)
    count = query_one(
        "SELECT COUNT(*) AS total FROM system_status_history WHERE system_id = ?",
        (history["system_id"],),
    )
    if count["total"] <= 1:
        return jsonify({"message": "A system must keep at least one status event"}), 400
    execute("DELETE FROM system_status_history WHERE id = ?", (history_id,))
    sync_current_status(history["system_id"])
    return jsonify(load_state())


@app.route("/api/systems/<int:system_id>/maintenance", methods=["POST"])
def create_maintenance(system_id):
    require_login()
    payload = request.get_json(force=True)
    system = query_one("SELECT location FROM systems WHERE id = ?", (system_id,))
    if not system:
        abort(404)
    payload["location"] = system["location"]
    _, error = create_grouped_site_visit(payload, fallback_system_id=system_id)
    if error:
        return jsonify({"message": error}), 400
    return jsonify(load_state())


@app.route("/api/site-visits", methods=["POST"])
def create_site_visit():
    require_login()
    payload = request.get_json(force=True)
    visit_id, error = create_grouped_site_visit(payload)
    if error:
        return jsonify({"message": error}), 400
    return jsonify({"id": visit_id, **load_state()})


@app.route("/api/site-visits/<int:visit_id>", methods=["PUT"])
def update_site_visit(visit_id):
    require_login()
    payload = request.get_json(force=True)
    if not query_one("SELECT id FROM site_visits WHERE id = ?", (visit_id,)):
        abort(404)
    visit_date = clean(payload.get("date"), today_iso())
    engineer = clean(payload.get("engineer"), "Unknown engineer")
    system_ids = [
        row["system_id"]
        for row in query_all("SELECT DISTINCT system_id FROM maintenance_records WHERE visit_id = ?", (visit_id,))
    ]
    execute("UPDATE site_visits SET date = ?, engineer = ? WHERE id = ?", (visit_date, engineer, visit_id))
    execute(
        "UPDATE maintenance_records SET date = ?, engineer = ? WHERE visit_id = ?",
        (visit_date, engineer, visit_id),
    )
    for system_id in system_ids:
        refresh_last_visit(system_id)
    return jsonify(load_state())


@app.route("/api/site-visits/<int:visit_id>", methods=["DELETE"])
def delete_site_visit(visit_id):
    require_login()
    system_ids = [
        row["system_id"]
        for row in query_all("SELECT DISTINCT system_id FROM maintenance_records WHERE visit_id = ?", (visit_id,))
    ]
    execute("DELETE FROM maintenance_records WHERE visit_id = ?", (visit_id,))
    execute("DELETE FROM site_visits WHERE id = ?", (visit_id,))
    for system_id in system_ids:
        refresh_last_visit(system_id)
    return jsonify(load_state())


@app.route("/api/maintenance/<int:record_id>", methods=["PUT"])
def update_maintenance(record_id):
    require_login()
    payload = request.get_json(force=True)
    visit_date = clean(payload.get("date"), today_iso())
    engineer = clean(payload.get("engineer"), "Unknown engineer")
    record = query_one("SELECT system_id, visit_id FROM maintenance_records WHERE id = ?", (record_id,))
    if not record:
        abort(404)
    if record["visit_id"]:
        execute(
            "UPDATE site_visits SET date = ?, engineer = ? WHERE id = ?",
            (visit_date, engineer, record["visit_id"]),
        )
        execute(
            "UPDATE maintenance_records SET date = ?, engineer = ? WHERE visit_id = ?",
            (visit_date, engineer, record["visit_id"]),
        )
    execute(
        """
        UPDATE maintenance_records
        SET date = ?, engineer = ?, type = ?, summary = ?
        WHERE id = ?
        """,
        (
            visit_date,
            engineer,
            clean_categories(payload.get("type")),
            clean(payload.get("summary")),
            record_id,
        ),
    )
    affected_systems = [record["system_id"]]
    if record["visit_id"]:
        affected_systems = [
            row["system_id"]
            for row in query_all(
                "SELECT DISTINCT system_id FROM maintenance_records WHERE visit_id = ?",
                (record["visit_id"],),
            )
        ]
    for system_id in affected_systems:
        refresh_last_visit(system_id)
    return jsonify(load_state())


@app.route("/api/maintenance/<int:record_id>", methods=["DELETE"])
def delete_maintenance(record_id):
    require_login()
    record = query_one("SELECT system_id, visit_id FROM maintenance_records WHERE id = ?", (record_id,))
    execute("DELETE FROM maintenance_records WHERE id = ?", (record_id,))
    if record:
        refresh_last_visit(record["system_id"])
        delete_site_visit_if_empty(record["visit_id"])
    return jsonify(load_state())


@app.route("/api/systems/<int:system_id>/issues", methods=["POST"])
def create_issue(system_id):
    require_login()
    payload = request.get_json(force=True)
    severity = clean_severity(payload.get("severity"))
    issue_status = clean(payload.get("status"), "Open")
    if issue_status not in ("Open", "Closed"):
        issue_status = "Open"
    closed_date = clean(payload.get("closed_date"), today_iso()) if issue_status == "Closed" else ""
    execute(
        """
        INSERT INTO system_issues
            (system_id, title, severity, opened, status, notes, reported_by, resolution_notes, closed_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            system_id,
            clean(payload.get("title"), "Untitled issue"),
            severity,
            clean(payload.get("opened"), today_iso()),
            issue_status,
            clean(payload.get("notes")),
            clean(payload.get("reported_by"), "Unknown"),
            clean(payload.get("resolution_notes")),
            closed_date,
        ),
    )
    if severity == "High":
        current = query_one("SELECT status FROM systems WHERE id = ?", (system_id,))
        if current and current["status"] == "Operational":
            set_system_status(system_id, "Needs Maintenance", payload.get("opened"))
    return jsonify(load_state())


@app.route("/api/issues/<int:issue_id>", methods=["PUT"])
def update_issue(issue_id):
    require_login()
    payload = request.get_json(force=True)
    issue_status = clean(payload.get("status"), "Open")
    if issue_status not in ("Open", "Closed"):
        issue_status = "Open"
    closed_date = clean(payload.get("closed_date"), today_iso()) if issue_status == "Closed" else ""
    execute(
        """
        UPDATE system_issues
        SET title = ?, severity = ?, opened = ?, status = ?, notes = ?,
            reported_by = ?, resolution_notes = ?, closed_date = ?
        WHERE id = ?
        """,
        (
            clean(payload.get("title"), "Untitled issue"),
            clean_severity(payload.get("severity")),
            clean(payload.get("opened"), today_iso()),
            issue_status,
            clean(payload.get("notes")),
            clean(payload.get("reported_by"), "Unknown"),
            clean(payload.get("resolution_notes")),
            closed_date,
            issue_id,
        ),
    )
    return jsonify(load_state())


@app.route("/api/locations/<path:location>", methods=["PUT"])
def update_location(location):
    require_login()
    payload = request.get_json(force=True)
    execute(
        """
        INSERT INTO locations (name, contacts, notes) VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET contacts = excluded.contacts, notes = excluded.notes
        """,
        (location, clean(payload.get("contacts")), clean(payload.get("notes"))),
    )
    return jsonify(load_state())


@app.route("/api/settings/theme", methods=["PUT"])
def update_theme():
    require_login()
    payload = request.get_json(force=True)
    theme = clean(payload.get("theme"), "standard")
    if theme not in THEME_FILES:
        return jsonify({"message": "Unknown theme"}), 400
    execute(
        """
        INSERT INTO app_settings (key, value) VALUES ('theme', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (theme,),
    )
    return jsonify(load_state())


@app.route("/api/issues/<int:issue_id>", methods=["DELETE"])
def delete_issue(issue_id):
    require_login()
    execute("DELETE FROM system_issues WHERE id = ?", (issue_id,))
    return jsonify(load_state())


@app.route("/api/tasks", methods=["POST"])
def create_task():
    require_login()
    payload = request.get_json(force=True)
    execute(
        """
        INSERT INTO development_tasks
            (title, category, expected_time, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            clean(payload.get("title"), "Untitled task"),
            clean(payload.get("category"), "Hardware"),
            clean(payload.get("expected_time"), "Not estimated"),
            clean(payload.get("notes")),
            today_iso(),
        ),
    )
    return jsonify(load_state())


@app.route("/api/tasks/<int:task_id>", methods=["PUT"])
def update_task(task_id):
    require_login()
    payload = request.get_json(force=True)
    execute(
        """
        UPDATE development_tasks
        SET title = ?, category = ?, expected_time = ?, notes = ?
        WHERE id = ?
        """,
        (
            clean(payload.get("title"), "Untitled task"),
            clean(payload.get("category"), "Hardware"),
            clean(payload.get("expected_time"), "Not estimated"),
            clean(payload.get("notes")),
            task_id,
        ),
    )
    return jsonify(load_state())


@app.route("/api/tasks/<int:task_id>", methods=["DELETE"])
def delete_task(task_id):
    require_login()
    execute("DELETE FROM development_tasks WHERE id = ?", (task_id,))
    return jsonify(load_state())


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=8765, debug=False)
