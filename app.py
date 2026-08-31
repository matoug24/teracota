from datetime import date
import os
import sqlite3

from flask import Flask, abort, jsonify, render_template, request, session


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA", BASE_DIR),
    "Teraview",
    "TeraCota",
)
DATABASE = os.environ.get(
    "TERACOTA_DB_PATH",
    os.path.join(DEFAULT_DATA_DIR, "teracota.sqlite3"),
)

app = Flask(__name__, template_folder=BASE_DIR, static_folder=BASE_DIR, static_url_path="/assets")
app.secret_key = os.environ.get("TERACOTA_SECRET_KEY", "teracota-local-session-key")

LOGIN_USERNAME = "teraview"
LOGIN_PASSWORD = "pythagorus"


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
        CREATE TABLE IF NOT EXISTS maintenance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            engineer TEXT NOT NULL,
            type TEXT NOT NULL,
            summary TEXT,
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
    seed_db()


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
        execute(
            """
            INSERT INTO maintenance_records
                (system_id, date, engineer, type, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            record,
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

    tasks = query_all("SELECT * FROM development_tasks ORDER BY category, created_at DESC, id DESC")
    return {"authenticated": is_authenticated(), "systems": systems, "tasks": tasks}


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


def clean_severity(value):
    severity = clean(value, "Medium").title()
    if severity == "Critical":
        severity = "High"
    return severity if severity in ("Low", "Medium", "High") else "Medium"


@app.route("/")
@app.route("/index.html")
def index():
    return render_template("index.html", initial_page="home", initial_system_id="")


@app.route("/systems/<int:system_id>")
def system_detail(system_id):
    return render_template("index.html", initial_page="system", initial_system_id=system_id)


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
    return jsonify({"id": cursor.lastrowid, **load_state()})


@app.route("/api/systems/<int:system_id>", methods=["PUT"])
def update_system(system_id):
    require_login()
    payload = request.get_json(force=True)
    execute(
        """
        UPDATE systems
        SET name = ?, location = ?, status = ?, owner = ?, last_visit = ?, next_visit = ?, notes = ?
        WHERE id = ?
        """,
        (
            clean(payload.get("name"), "Untitled System"),
            clean(payload.get("location"), "Unknown Location"),
            clean(payload.get("status"), "Operational"),
            "Unassigned",
            clean(payload.get("last_visit")),
            "",
            clean(payload.get("notes")),
            system_id,
        ),
    )
    return jsonify(load_state())


@app.route("/api/systems/<int:system_id>", methods=["DELETE"])
def delete_system(system_id):
    require_login()
    execute("DELETE FROM maintenance_records WHERE system_id = ?", (system_id,))
    execute("DELETE FROM system_issues WHERE system_id = ?", (system_id,))
    execute("DELETE FROM systems WHERE id = ?", (system_id,))
    return jsonify(load_state())


@app.route("/api/systems/<int:system_id>/maintenance", methods=["POST"])
def create_maintenance(system_id):
    require_login()
    payload = request.get_json(force=True)
    visit_date = clean(payload.get("date"), today_iso())
    execute(
        """
        INSERT INTO maintenance_records
            (system_id, date, engineer, type, summary)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            system_id,
            visit_date,
            clean(payload.get("engineer"), "Unknown engineer"),
            clean_categories(payload.get("type")),
            clean(payload.get("summary")),
        ),
    )
    execute("UPDATE systems SET last_visit = ? WHERE id = ?", (visit_date, system_id))
    return jsonify(load_state())


@app.route("/api/maintenance/<int:record_id>", methods=["PUT"])
def update_maintenance(record_id):
    require_login()
    payload = request.get_json(force=True)
    visit_date = clean(payload.get("date"), today_iso())
    execute(
        """
        UPDATE maintenance_records
        SET date = ?, engineer = ?, type = ?, summary = ?
        WHERE id = ?
        """,
        (
            visit_date,
            clean(payload.get("engineer"), "Unknown engineer"),
            clean_categories(payload.get("type")),
            clean(payload.get("summary")),
            record_id,
        ),
    )
    record = query_one("SELECT system_id FROM maintenance_records WHERE id = ?", (record_id,))
    if record:
        latest = query_one(
            "SELECT date FROM maintenance_records WHERE system_id = ? ORDER BY date DESC LIMIT 1",
            (record["system_id"],),
        )
        execute("UPDATE systems SET last_visit = ? WHERE id = ?", (latest["date"] if latest else "", record["system_id"]))
    return jsonify(load_state())


@app.route("/api/maintenance/<int:record_id>", methods=["DELETE"])
def delete_maintenance(record_id):
    require_login()
    record = query_one("SELECT system_id FROM maintenance_records WHERE id = ?", (record_id,))
    execute("DELETE FROM maintenance_records WHERE id = ?", (record_id,))
    if record:
        latest = query_one(
            "SELECT date FROM maintenance_records WHERE system_id = ? ORDER BY date DESC LIMIT 1",
            (record["system_id"],),
        )
        execute("UPDATE systems SET last_visit = ? WHERE id = ?", (latest["date"] if latest else "", record["system_id"]))
    return jsonify(load_state())


@app.route("/api/systems/<int:system_id>/issues", methods=["POST"])
def create_issue(system_id):
    require_login()
    payload = request.get_json(force=True)
    severity = clean_severity(payload.get("severity"))
    issue_status = clean(payload.get("status"), "Open")
    if issue_status not in ("Open", "Closed"):
        issue_status = "Open"
    execute(
        """
        INSERT INTO system_issues
            (system_id, title, severity, opened, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            system_id,
            clean(payload.get("title"), "Untitled issue"),
            severity,
            clean(payload.get("opened"), today_iso()),
            issue_status,
            clean(payload.get("notes")),
        ),
    )
    if severity == "High":
        execute(
            """
            UPDATE systems
            SET status = 'Needs Maintenance'
            WHERE id = ? AND status = 'Operational'
            """,
            (system_id,),
        )
    return jsonify(load_state())


@app.route("/api/issues/<int:issue_id>", methods=["PUT"])
def update_issue(issue_id):
    require_login()
    payload = request.get_json(force=True)
    issue_status = clean(payload.get("status"), "Open")
    if issue_status not in ("Open", "Closed"):
        issue_status = "Open"
    execute(
        """
        UPDATE system_issues
        SET title = ?, severity = ?, opened = ?, status = ?, notes = ?
        WHERE id = ?
        """,
        (
            clean(payload.get("title"), "Untitled issue"),
            clean_severity(payload.get("severity")),
            clean(payload.get("opened"), today_iso()),
            issue_status,
            clean(payload.get("notes")),
            issue_id,
        ),
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
