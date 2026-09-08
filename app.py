import csv
from datetime import date, datetime, timezone
import hmac
import io
import json
import os
import sqlite3
from urllib.parse import urlsplit

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.environ.get(
    "TERACOTA_DB_PATH",
    os.path.join(BASE_DIR, "teracota.sqlite3"),
)


def env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


PRODUCTION = os.environ.get("TERACOTA_ENV", "development").strip().lower() == "production"
DEFAULT_SECRET_KEY = "teracota-local-session-key"
DEFAULT_PASSWORD = "pythagorus"

app = Flask(__name__, template_folder=BASE_DIR, static_folder=None)
app.config.update(
    SECRET_KEY=os.environ.get("TERACOTA_SECRET_KEY", DEFAULT_SECRET_KEY),
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=env_flag("TERACOTA_COOKIE_SECURE", PRODUCTION),
)
if env_flag("TERACOTA_BEHIND_PROXY"):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

LOGIN_USERNAME = os.environ.get("TERACOTA_USERNAME", "teraview")
LOGIN_PASSWORD = os.environ.get("TERACOTA_PASSWORD", DEFAULT_PASSWORD)
if PRODUCTION and app.config["SECRET_KEY"] == DEFAULT_SECRET_KEY:
    raise RuntimeError("TERACOTA_SECRET_KEY must be set in production")
if PRODUCTION and LOGIN_PASSWORD == DEFAULT_PASSWORD:
    raise RuntimeError("TERACOTA_PASSWORD must be changed in production")
THEME_FILES = {
    "standard": "flask_styles.css",
    "control-room": "flask_styles_control_room.css",
    "instrument": "flask_styles_instrument.css",
}
APP_ASSETS = {
    "flask_app_v3.js",
    "flask_styles.css",
    "flask_styles_control_room.css",
    "flask_styles_instrument.css",
}
EXPORT_TABLES = (
    "systems",
    "site_visits",
    "maintenance_records",
    "system_issues",
    "system_updates",
    "development_tasks",
    "system_status_history",
    "locations",
    "app_settings",
    "deleted_items",
)
OPTIONAL_IMPORT_TABLES = {"system_updates"}


def get_db():
    database_path = os.path.abspath(DATABASE)
    os.makedirs(os.path.dirname(database_path), exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
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
    execute("PRAGMA journal_mode = WAL")
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
    ensure_column("system_issues", "related_to", "TEXT NOT NULL DEFAULT ''")
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
        CREATE TABLE IF NOT EXISTS system_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            update_type TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (system_id) REFERENCES systems (id)
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS locations (
            name TEXT PRIMARY KEY,
            contacts TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            display_rank INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    ensure_column("locations", "display_rank", "INTEGER NOT NULL DEFAULT 0")
    execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS deleted_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type TEXT NOT NULL,
            original_id INTEGER,
            label TEXT NOT NULL,
            payload TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            deleted_by TEXT NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS visitor_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visited_at TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            device TEXT NOT NULL,
            browser TEXT NOT NULL,
            platform TEXT NOT NULL,
            user_agent TEXT NOT NULL,
            username TEXT NOT NULL
        )
        """
    )
    execute("CREATE INDEX IF NOT EXISTS idx_deleted_items_deleted_at ON deleted_items (deleted_at DESC)")
    execute("CREATE INDEX IF NOT EXISTS idx_visitor_logs_visited_at ON visitor_logs (visited_at DESC)")
    execute("CREATE INDEX IF NOT EXISTS idx_system_updates_system_date ON system_updates (system_id, date DESC)")
    if env_flag("TERACOTA_SEED_DEMO", not PRODUCTION):
        seed_db()
    sync_locations()
    normalize_location_ranks()
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
    systems = query_all(
        """
        SELECT systems.*
        FROM systems
        LEFT JOIN locations ON locations.name = systems.location
        ORDER BY COALESCE(locations.display_rank, 2147483647), systems.name
        """
    )
    for system in systems:
        system["maintenance"] = query_all(
            "SELECT * FROM maintenance_records WHERE system_id = ? ORDER BY date DESC, id DESC",
            (system["id"],),
        )
        for record in system["maintenance"]:
            record["type"] = clean_categories(record["type"])
        system["issues"] = query_all(
            "SELECT * FROM system_issues WHERE system_id = ? ORDER BY opened DESC, id DESC",
            (system["id"],),
        )
        for issue in system["issues"]:
            issue["severity"] = clean_severity(issue["severity"])
            issue["related_to"] = clean_issue_relations(issue.get("related_to"))
        system["status_history"] = query_all(
            "SELECT * FROM system_status_history WHERE system_id = ? ORDER BY started_at, id",
            (system["id"],),
        )
        system["updates"] = query_all(
            "SELECT * FROM system_updates WHERE system_id = ? ORDER BY date DESC, id DESC",
            (system["id"],),
        )
        for update in system["updates"]:
            update["update_type"] = clean_update_types(update["update_type"])

    tasks = query_all("SELECT * FROM development_tasks ORDER BY category, created_at DESC, id DESC")
    locations = query_all("SELECT * FROM locations ORDER BY display_rank, name")
    deleted_items = query_all(
        """
        SELECT id, item_type, original_id, label, deleted_at, deleted_by
        FROM deleted_items
        ORDER BY deleted_at DESC, id DESC
        """
    )
    return {
        "authenticated": is_authenticated(),
        "username": LOGIN_USERNAME if is_authenticated() else "",
        "systems": systems,
        "tasks": tasks,
        "locations": locations,
        "deleted_items": deleted_items,
        "theme": get_active_theme(),
    }


def clean(value, default=""):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def today_iso():
    return date.today().isoformat()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_authenticated():
    return session.get("logged_in") is True


def require_login():
    if not is_authenticated():
        abort(401)


def safe_next_url(value):
    value = clean(value, "/")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/"):
        return "/"
    return value


@app.before_request
def enforce_authentication():
    public_endpoints = {"login_page", "login", "health_check"}
    if is_authenticated() or request.endpoint in public_endpoints:
        return None
    if request.path.startswith("/api/"):
        return jsonify({"message": "Authentication required"}), 401
    next_url = request.full_path.rstrip("?")
    return redirect(url_for("login_page", next=next_url))


def visitor_details(user_agent):
    value = (user_agent or "").lower()
    if "ipad" in value or "tablet" in value:
        device = "Tablet"
    elif any(token in value for token in ("mobile", "iphone", "android")):
        device = "Mobile"
    elif any(token in value for token in ("curl", "wget", "python-requests")):
        device = "Automated client"
    else:
        device = "Desktop"

    if "edg/" in value:
        browser = "Edge"
    elif "chrome/" in value or "crios/" in value:
        browser = "Chrome"
    elif "firefox/" in value or "fxios/" in value:
        browser = "Firefox"
    elif "safari/" in value:
        browser = "Safari"
    elif "curl/" in value:
        browser = "curl"
    else:
        browser = "Other"

    if "windows" in value:
        platform = "Windows"
    elif "android" in value:
        platform = "Android"
    elif any(token in value for token in ("iphone", "ipad", "ios")):
        platform = "iOS"
    elif any(token in value for token in ("macintosh", "mac os")):
        platform = "macOS"
    elif "linux" in value:
        platform = "Linux"
    else:
        platform = "Other"
    return device, browser, platform


@app.before_request
def record_page_visit():
    page_endpoints = {
        "index",
        "system_detail",
        "location_detail",
        "admin_page",
        "logs_page",
        "statistics_page",
    }
    if not is_authenticated() or request.method != "GET" or request.endpoint not in page_endpoints:
        return None
    user_agent = request.headers.get("User-Agent", "")
    device, browser, platform = visitor_details(user_agent)
    try:
        execute(
            """
            INSERT INTO visitor_logs
                (visited_at, ip_address, method, path, device, browser, platform, user_agent, username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now_iso(),
                request.remote_addr or "Unknown",
                request.method,
                request.full_path.rstrip("?"),
                device,
                browser,
                platform,
                user_agent,
                LOGIN_USERNAME,
            ),
        )
    except sqlite3.Error:
        app.logger.exception("Unable to record visitor log")
    return None


def clean_categories(value):
    allowed = ["Calibration", "Alignment", "Commissioning", "Troubleshooting"]
    aliases = {"optics": "Alignment"}
    values = value if isinstance(value, list) else str(value or "").split(",")
    selected = []
    for item in values:
        normalized = aliases.get(str(item).strip().lower(), str(item).strip().title())
        if normalized in allowed and normalized not in selected:
            selected.append(normalized)
    return ", ".join(selected)


def clean_issue_relations(value):
    allowed = ["Head", "HAS", "Fiber", "Optics", "Electronic", "Software", "Other"]
    lookup = {item.lower(): item for item in allowed}
    values = value if isinstance(value, list) else str(value or "").split(",")
    selected = []
    for item in values:
        normalized = lookup.get(str(item).strip().lower())
        if normalized and normalized not in selected:
            selected.append(normalized)
    return ", ".join(selected)


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


def archive_deleted_item(db, item_type, original_id, label, payload):
    db.execute(
        """
        INSERT INTO deleted_items
            (item_type, original_id, label, payload, deleted_at, deleted_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            item_type,
            original_id,
            label,
            json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
            utc_now_iso(),
            LOGIN_USERNAME,
        ),
    )


def insert_database_row(db, table, row):
    allowed_columns = {column["name"] for column in db.execute(f"PRAGMA table_info({table})").fetchall()}
    values = {key: value for key, value in row.items() if key in allowed_columns}
    if "id" in values and db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (values["id"],)).fetchone():
        values.pop("id")
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    cursor = db.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )
    return cursor.lastrowid


def restore_deleted_record(db, deleted_item):
    try:
        payload = json.loads(deleted_item["payload"])
    except (TypeError, json.JSONDecodeError):
        return None, "The archived record is damaged and cannot be restored"

    item_type = deleted_item["item_type"]
    if item_type == "issue":
        issue = payload.get("issue")
        if not isinstance(issue, dict):
            return None, "The archived issue is incomplete"
        if not db.execute("SELECT 1 FROM systems WHERE id = ?", (issue.get("system_id"),)).fetchone():
            return None, "Restore the related system before restoring this issue"
        insert_database_row(db, "system_issues", issue)
        return [], None

    if item_type not in ("visit", "site_visit"):
        return None, "This archived record type cannot be restored"

    records = payload.get("records")
    if not isinstance(records, list) or not records:
        return None, "The archived visit is incomplete"
    system_ids = {record.get("system_id") for record in records if isinstance(record, dict)}
    if None in system_ids or any(
        not db.execute("SELECT 1 FROM systems WHERE id = ?", (system_id,)).fetchone()
        for system_id in system_ids
    ):
        return None, "Restore the related system before restoring this visit"

    site_visit = payload.get("site_visit")
    restored_visit_id = None
    if isinstance(site_visit, dict):
        original_visit_id = site_visit.get("id")
        existing = db.execute("SELECT id FROM site_visits WHERE id = ?", (original_visit_id,)).fetchone()
        restored_visit_id = existing["id"] if existing else insert_database_row(db, "site_visits", site_visit)

    for record in records:
        record = dict(record)
        if record.get("visit_id") is not None:
            record["visit_id"] = restored_visit_id
        insert_database_row(db, "maintenance_records", record)
    return sorted(system_ids), None


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


def clean_update_types(value):
    allowed = ("Software", "Calibration")
    lookup = {item.lower(): item for item in allowed}
    values = value if isinstance(value, list) else str(value or "").split(",")
    selected = []
    for item in values:
        normalized = lookup.get(str(item).strip().lower())
        if normalized and normalized not in selected:
            selected.append(normalized)
    return ", ".join(selected)


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


@app.route("/assets/<path:filename>")
def app_asset(filename):
    if filename not in APP_ASSETS:
        abort(404)
    return send_from_directory(BASE_DIR, filename)


def sync_locations():
    existing = {row["name"] for row in query_all("SELECT name FROM locations")}
    current_max = query_one("SELECT COALESCE(MAX(display_rank), 0) AS maximum FROM locations")
    next_rank = current_max["maximum"] + 1
    for row in query_all("SELECT DISTINCT location FROM systems WHERE location <> '' ORDER BY location"):
        if row["location"] in existing:
            continue
        execute(
            "INSERT INTO locations (name, contacts, notes, display_rank) VALUES (?, '', '', ?)",
            (row["location"], next_rank),
        )
        existing.add(row["location"])
        next_rank += 1


def normalize_location_ranks():
    locations = query_all("SELECT name FROM locations ORDER BY display_rank, name")
    with get_db() as db:
        for rank, location in enumerate(locations, start=1):
            db.execute(
                "UPDATE locations SET display_rank = ? WHERE name = ?",
                (rank, location["name"]),
            )
        db.commit()


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


@app.route("/logs")
def logs_page():
    return render_page("logs")


@app.route("/statistics")
def statistics_page():
    return render_page("statistics")


@app.route("/login", methods=["GET", "POST"])
def login_page():
    next_url = safe_next_url(request.values.get("next"))
    if is_authenticated():
        return redirect(next_url)

    error = ""
    status = 200
    if request.method == "POST":
        username = clean(request.form.get("username"))
        password = clean(request.form.get("password"))
        if hmac.compare_digest(username, LOGIN_USERNAME) and hmac.compare_digest(password, LOGIN_PASSWORD):
            session.clear()
            session["logged_in"] = True
            return redirect(next_url)
        error = "Invalid username or password."
        status = 401

    return render_template("login.html", error=error, next_url=next_url), status


@app.route("/api/state")
def api_state():
    return jsonify(load_state())


@app.route("/api/logs")
def api_logs():
    require_login()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1
    try:
        per_page = min(max(int(request.args.get("per_page", 50)), 1), 200)
    except ValueError:
        per_page = 50
    total = query_one("SELECT COUNT(*) AS total FROM visitor_logs")["total"]
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    logs = query_all(
        "SELECT * FROM visitor_logs ORDER BY visited_at DESC, id DESC LIMIT ? OFFSET ?",
        (per_page, (page - 1) * per_page),
    )
    return jsonify({"logs": logs, "total": total, "page": page, "pages": pages, "per_page": per_page})


@app.route("/healthz")
def health_check():
    try:
        query_one("SELECT id FROM systems LIMIT 1")
        query_one("SELECT related_to FROM system_issues LIMIT 1")
        query_one("SELECT id FROM system_updates LIMIT 1")
        query_one("SELECT id FROM visitor_logs LIMIT 1")
    except sqlite3.Error:
        app.logger.exception("TeraCota database health check failed")
        return jsonify({"status": "unhealthy"}), 503
    return jsonify({"status": "ok"})


@app.route("/api/login", methods=["POST"])
def login():
    payload = request.get_json(force=True)
    username = clean(payload.get("username"))
    password = clean(payload.get("password"))
    if hmac.compare_digest(username, LOGIN_USERNAME) and hmac.compare_digest(password, LOGIN_PASSWORD):
        session["logged_in"] = True
        return jsonify(load_state())
    return jsonify({"message": "Invalid username or password"}), 401


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"authenticated": False})


@app.route("/api/admin/export.csv")
def export_database_csv():
    require_login()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=("record_type", "table", "data_json"))
    writer.writeheader()
    for table in EXPORT_TABLES:
        writer.writerow({"record_type": "table", "table": table, "data_json": ""})
        for row in query_all(f"SELECT * FROM {table}"):
            writer.writerow(
                {
                    "record_type": "row",
                    "table": table,
                    "data_json": json.dumps(row, separators=(",", ":"), ensure_ascii=True),
                }
            )
    filename = f"teracota-backup-{today_iso()}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/admin/import", methods=["POST"])
def import_database_csv():
    require_login()
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"message": "Select a TeraCota CSV backup file"}), 400
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return jsonify({"message": "The backup must be a UTF-8 CSV file"}), 400

    reader = csv.DictReader(io.StringIO(text))
    required_headers = {"record_type", "table", "data_json"}
    if not reader.fieldnames or not required_headers.issubset(reader.fieldnames):
        return jsonify({"message": "This is not a valid TeraCota CSV backup"}), 400

    rows_by_table = {table: [] for table in EXPORT_TABLES}
    table_markers = set()
    try:
        for csv_row in reader:
            table = clean(csv_row.get("table"))
            record_type = clean(csv_row.get("record_type"))
            if table not in rows_by_table:
                raise ValueError(f"Unknown backup table: {table or 'blank'}")
            if record_type == "table":
                table_markers.add(table)
                continue
            if record_type != "row":
                raise ValueError("Unknown backup record type")
            data = json.loads(csv_row.get("data_json") or "")
            if not isinstance(data, dict):
                raise ValueError("Backup row data must be an object")
            rows_by_table[table].append(data)
    except (json.JSONDecodeError, ValueError) as error:
        return jsonify({"message": f"Invalid backup: {error}"}), 400

    missing_tables = set(EXPORT_TABLES) - table_markers - OPTIONAL_IMPORT_TABLES
    if missing_tables:
        return jsonify(
            {"message": f"Backup is missing table markers: {', '.join(sorted(missing_tables))}"}
        ), 400

    db = get_db()
    try:
        db.execute("BEGIN IMMEDIATE")
        table_columns = {
            table: {column["name"] for column in db.execute(f"PRAGMA table_info({table})").fetchall()}
            for table in EXPORT_TABLES
        }
        for table in EXPORT_TABLES:
            for row in rows_by_table[table]:
                unknown_columns = set(row) - table_columns[table]
                if unknown_columns:
                    raise ValueError(
                        f"Table {table} has unknown columns: {', '.join(sorted(unknown_columns))}"
                    )
        for table in reversed(EXPORT_TABLES):
            db.execute(f"DELETE FROM {table}")
        imported_rows = 0
        for table in EXPORT_TABLES:
            for row in rows_by_table[table]:
                insert_database_row(db, table, row)
                imported_rows += 1
        db.commit()
    except (sqlite3.Error, ValueError) as error:
        db.rollback()
        return jsonify({"message": f"Unable to restore backup: {error}"}), 400
    finally:
        db.close()

    sync_locations()
    normalize_location_ranks()
    backfill_status_history()
    return jsonify({"imported_rows": imported_rows, **load_state()})


@app.route("/api/admin/deleted-items/<int:item_id>/restore", methods=["POST"])
def restore_deleted_item(item_id):
    require_login()
    with get_db() as db:
        deleted_item = db.execute("SELECT * FROM deleted_items WHERE id = ?", (item_id,)).fetchone()
        if not deleted_item:
            abort(404)
        affected_systems, error = restore_deleted_record(db, deleted_item)
        if error:
            return jsonify({"message": error}), 400
        db.execute("DELETE FROM deleted_items WHERE id = ?", (item_id,))
        db.commit()
    for system_id in affected_systems:
        refresh_last_visit(system_id)
    return jsonify(load_state())


@app.route("/api/admin/deleted-items/<int:item_id>", methods=["DELETE"])
def permanently_delete_item(item_id):
    require_login()
    if not query_one("SELECT id FROM deleted_items WHERE id = ?", (item_id,)):
        abort(404)
    execute("DELETE FROM deleted_items WHERE id = ?", (item_id,))
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
    execute("DELETE FROM system_updates WHERE system_id = ?", (system_id,))
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


@app.route("/api/systems/<int:system_id>/updates", methods=["POST"])
def create_system_update(system_id):
    require_login()
    if not query_one("SELECT id FROM systems WHERE id = ?", (system_id,)):
        abort(404)
    payload = request.get_json(force=True)
    update_types = clean_update_types(payload.get("update_type"))
    if not update_types:
        return jsonify({"message": "Select at least one update type"}), 400
    execute(
        """
        INSERT INTO system_updates (system_id, date, update_type, notes)
        VALUES (?, ?, ?, ?)
        """,
        (
            system_id,
            clean(payload.get("date"), today_iso()),
            update_types,
            clean(payload.get("notes")),
        ),
    )
    return jsonify(load_state())


@app.route("/api/system-updates/<int:update_id>", methods=["PUT"])
def update_system_update(update_id):
    require_login()
    if not query_one("SELECT id FROM system_updates WHERE id = ?", (update_id,)):
        abort(404)
    payload = request.get_json(force=True)
    update_types = clean_update_types(payload.get("update_type"))
    if not update_types:
        return jsonify({"message": "Select at least one update type"}), 400
    execute(
        """
        UPDATE system_updates
        SET date = ?, update_type = ?, notes = ?
        WHERE id = ?
        """,
        (
            clean(payload.get("date"), today_iso()),
            update_types,
            clean(payload.get("notes")),
            update_id,
        ),
    )
    return jsonify(load_state())


@app.route("/api/system-updates/<int:update_id>", methods=["DELETE"])
def delete_system_update(update_id):
    require_login()
    if not query_one("SELECT id FROM system_updates WHERE id = ?", (update_id,)):
        abort(404)
    execute("DELETE FROM system_updates WHERE id = ?", (update_id,))
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
    with get_db() as db:
        site_visit = db.execute("SELECT * FROM site_visits WHERE id = ?", (visit_id,)).fetchone()
        if not site_visit:
            abort(404)
        records = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM maintenance_records WHERE visit_id = ? ORDER BY id",
                (visit_id,),
            ).fetchall()
        ]
        system_ids = sorted({record["system_id"] for record in records})
        archive_deleted_item(
            db,
            "site_visit",
            visit_id,
            f"{site_visit['location']} - {site_visit['date']}",
            {"site_visit": dict(site_visit), "records": records},
        )
        db.execute("DELETE FROM maintenance_records WHERE visit_id = ?", (visit_id,))
        db.execute("DELETE FROM site_visits WHERE id = ?", (visit_id,))
        db.commit()
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
    with get_db() as db:
        record = db.execute("SELECT * FROM maintenance_records WHERE id = ?", (record_id,)).fetchone()
        if not record:
            abort(404)
        system = db.execute("SELECT name FROM systems WHERE id = ?", (record["system_id"],)).fetchone()
        site_visit = None
        if record["visit_id"]:
            site_visit = db.execute("SELECT * FROM site_visits WHERE id = ?", (record["visit_id"],)).fetchone()
        archive_deleted_item(
            db,
            "visit",
            record_id,
            f"{system['name'] if system else 'System'} - {record['date']}",
            {
                "site_visit": dict(site_visit) if site_visit else None,
                "records": [dict(record)],
            },
        )
        db.execute("DELETE FROM maintenance_records WHERE id = ?", (record_id,))
        if record["visit_id"] and not db.execute(
            "SELECT 1 FROM maintenance_records WHERE visit_id = ? LIMIT 1",
            (record["visit_id"],),
        ).fetchone():
            db.execute("DELETE FROM site_visits WHERE id = ?", (record["visit_id"],))
        db.commit()
    refresh_last_visit(record["system_id"])
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
            (system_id, title, severity, opened, status, notes, reported_by, resolution_notes, closed_date, related_to)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            clean_issue_relations(payload.get("related_to")),
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
            reported_by = ?, resolution_notes = ?, closed_date = ?, related_to = ?
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
            clean_issue_relations(payload.get("related_to")),
            issue_id,
        ),
    )
    return jsonify(load_state())


@app.route("/api/locations/<path:location>", methods=["PUT"])
def update_location(location):
    require_login()
    payload = request.get_json(force=True)
    existing = query_one("SELECT display_rank FROM locations WHERE name = ?", (location,))
    current_max = query_one("SELECT COALESCE(MAX(display_rank), 0) AS maximum FROM locations")
    display_rank = existing["display_rank"] if existing else current_max["maximum"] + 1
    execute(
        """
        INSERT INTO locations (name, contacts, notes, display_rank) VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET contacts = excluded.contacts, notes = excluded.notes
        """,
        (location, clean(payload.get("contacts")), clean(payload.get("notes")), display_rank),
    )
    return jsonify(load_state())


@app.route("/api/admin/locations/order", methods=["PUT"])
def update_location_order():
    require_login()
    payload = request.get_json(force=True)
    location_names = payload.get("locations")
    if not isinstance(location_names, list):
        return jsonify({"message": "Locations must be provided as an ordered list"}), 400
    location_names = [clean(name) for name in location_names]
    active_names = {
        row["location"]
        for row in query_all("SELECT DISTINCT location FROM systems WHERE location <> ''")
    }
    if (
        any(not name for name in location_names)
        or len(location_names) != len(set(location_names))
        or set(location_names) != active_names
    ):
        return jsonify({"message": "The location order must include every active location exactly once"}), 400

    inactive_names = [
        row["name"]
        for row in query_all("SELECT name FROM locations ORDER BY display_rank, name")
        if row["name"] not in active_names
    ]
    ordered_names = location_names + inactive_names

    with get_db() as db:
        for rank, name in enumerate(ordered_names, start=1):
            db.execute(
                "UPDATE locations SET display_rank = ? WHERE name = ?",
                (rank, name),
            )
        db.commit()
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
    with get_db() as db:
        issue = db.execute("SELECT * FROM system_issues WHERE id = ?", (issue_id,)).fetchone()
        if not issue:
            abort(404)
        system = db.execute("SELECT name FROM systems WHERE id = ?", (issue["system_id"],)).fetchone()
        archive_deleted_item(
            db,
            "issue",
            issue_id,
            f"{system['name'] if system else 'System'} - {issue['title']}",
            {"issue": dict(issue)},
        )
        db.execute("DELETE FROM system_issues WHERE id = ?", (issue_id,))
        db.commit()
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
