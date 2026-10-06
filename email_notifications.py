"""Durable per-location email notifications and daily measurement summaries."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from html import escape
import json
import os
from pathlib import Path
import re
import smtplib
import sqlite3
import ssl
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, abort, jsonify, request

from measurements.daily_summary import robot_metrics_for_dates


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_RECIPIENTS = 50
MAX_DELIVERY_ATTEMPTS = 8


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _connect(database_path: str | Path) -> ClosingConnection:
    path = Path(database_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=30, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def initialize_notification_schema(database_path: str | Path) -> None:
    with _connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS notification_settings (
                location TEXT PRIMARY KEY,
                recipients TEXT NOT NULL DEFAULT '[]',
                update_notifications INTEGER NOT NULL DEFAULT 0,
                daily_summary INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (location) REFERENCES locations(name)
                    ON UPDATE CASCADE ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS email_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                recipients TEXT NOT NULL,
                subject TEXT NOT NULL,
                body_text TEXT NOT NULL,
                body_html TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                next_attempt_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sent_at TEXT,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_notification_runs (
                location TEXT NOT NULL,
                summary_date TEXT NOT NULL,
                queued_at TEXT NOT NULL,
                PRIMARY KEY (location, summary_date)
            );
            CREATE TABLE IF NOT EXISTS health_alert_state (
                id INTEGER PRIMARY KEY CHECK(id=1),
                active_signature TEXT NOT NULL DEFAULT '',
                last_queued_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_email_outbox_delivery
                ON email_outbox(status, next_attempt_at, id);
            CREATE INDEX IF NOT EXISTS idx_email_outbox_location
                ON email_outbox(location, created_at DESC);
            """
        )
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(email_outbox)")
        }
        if "body_html" not in columns:
            connection.execute(
                "ALTER TABLE email_outbox ADD COLUMN body_html TEXT NOT NULL DEFAULT ''"
            )


def parse_recipients(value) -> list[str]:
    raw_values = value if isinstance(value, list) else re.split(r"[,;\n]+", str(value or ""))
    recipients = []
    for raw in raw_values:
        candidate = parseaddr(str(raw).strip())[1].lower()
        if not candidate:
            continue
        if "\r" in candidate or "\n" in candidate or not EMAIL_PATTERN.fullmatch(candidate):
            raise ValueError(f"Invalid email address: {str(raw).strip()}")
        if candidate not in recipients:
            recipients.append(candidate)
    if len(recipients) > MAX_RECIPIENTS:
        raise ValueError(f"A location can have at most {MAX_RECIPIENTS} recipients")
    return recipients


def parse_recipient_subscriptions(
    value,
    default_updates: bool = False,
    default_daily: bool = False,
) -> list[dict]:
    raw_values = value if isinstance(value, list) else re.split(r"[,;\n]+", str(value or ""))
    subscriptions = []
    positions = {}
    for raw in raw_values:
        if isinstance(raw, dict):
            email_value = raw.get("email")
            updates = bool(raw.get("update_notifications"))
            daily = bool(raw.get("daily_summary"))
        else:
            email_value = raw
            updates = default_updates
            daily = default_daily
        emails = parse_recipients([email_value])
        if not emails:
            continue
        email = emails[0]
        if email in positions:
            existing = subscriptions[positions[email]]
            existing["update_notifications"] = existing["update_notifications"] or updates
            existing["daily_summary"] = existing["daily_summary"] or daily
            continue
        positions[email] = len(subscriptions)
        subscriptions.append(
            {
                "email": email,
                "update_notifications": updates,
                "daily_summary": daily,
            }
        )
    if len(subscriptions) > MAX_RECIPIENTS:
        raise ValueError(f"A location can have at most {MAX_RECIPIENTS} recipients")
    return subscriptions


def _stored_subscriptions(row) -> list[dict]:
    if not row:
        return []
    try:
        stored = json.loads(row["recipients"])
    except (TypeError, json.JSONDecodeError):
        stored = row["recipients"]
    try:
        return parse_recipient_subscriptions(
            stored,
            bool(row["update_notifications"]),
            bool(row["daily_summary"]),
        )
    except ValueError:
        return []


def smtp_configuration() -> dict:
    username = os.environ.get("TERACOTA_SMTP_USERNAME", "").strip()
    password = "".join(os.environ.get("TERACOTA_SMTP_APP_PASSWORD", "").split())
    sender = os.environ.get("TERACOTA_SMTP_FROM", username).strip()
    host = os.environ.get("TERACOTA_SMTP_HOST", "smtp.gmail.com").strip()
    security = os.environ.get("TERACOTA_SMTP_SECURITY", "ssl").strip().lower()
    default_port = 465 if security == "ssl" else 587
    try:
        port = int(os.environ.get("TERACOTA_SMTP_PORT", str(default_port)))
    except ValueError:
        port = default_port
    configured = bool(username and password and sender and host and security in {"ssl", "starttls"})
    return {
        "configured": configured,
        "username": username,
        "password": password,
        "sender": sender,
        "host": host,
        "port": port,
        "security": security,
    }


def _settings_payload(database_path: str | Path) -> dict:
    initialize_notification_schema(database_path)
    with _connect(database_path) as connection:
        locations = connection.execute(
            """
            SELECT s.location AS name
            FROM systems s
            LEFT JOIN locations l ON l.name=s.location
            WHERE s.location<>''
            GROUP BY s.location
            ORDER BY COALESCE(MAX(l.display_rank),2147483647),s.location
            """
        ).fetchall()
        saved = {
            row["location"]: row
            for row in connection.execute(
                "SELECT * FROM notification_settings ORDER BY location"
            ).fetchall()
        }
        counts = connection.execute(
            """
            SELECT
              SUM(CASE WHEN status IN ('PENDING','SENDING') THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed
            FROM email_outbox
            """
        ).fetchone()
    settings = []
    for location_row in locations:
        location = location_row["name"]
        row = saved.get(location)
        recipients = _stored_subscriptions(row)
        settings.append(
            {
                "location": location,
                "recipients": recipients,
                "update_notifications": bool(row["update_notifications"]) if row else False,
                "daily_summary": bool(row["daily_summary"]) if row else False,
            }
        )
    smtp = smtp_configuration()
    return {
        "locations": settings,
        "smtp": {
            "configured": smtp["configured"],
            "sender": smtp["sender"] if smtp["configured"] else "",
        },
        "outbox": {
            "pending": int(counts["pending"] or 0),
            "failed": int(counts["failed"] or 0),
        },
    }


def create_notification_blueprint(database_path: str | Path) -> Blueprint:
    blueprint = Blueprint("email_notifications", __name__)

    @blueprint.get("/api/admin/notification-settings")
    def notification_settings():
        return jsonify(_settings_payload(database_path))

    @blueprint.put("/api/admin/notification-settings/<path:location>")
    def update_notification_settings(location: str):
        payload = request.get_json(force=True)
        try:
            recipients = parse_recipient_subscriptions(
                payload.get("recipients"),
                bool(payload.get("update_notifications")),
                bool(payload.get("daily_summary")),
            )
        except ValueError as error:
            return jsonify({"message": str(error)}), 400
        update_notifications = any(item["update_notifications"] for item in recipients)
        daily_summary = any(item["daily_summary"] for item in recipients)
        initialize_notification_schema(database_path)
        with _connect(database_path) as connection:
            exists = connection.execute(
                "SELECT 1 FROM locations WHERE name=? UNION SELECT 1 FROM systems WHERE location=? LIMIT 1",
                (location, location),
            ).fetchone()
            if not exists:
                abort(404)
            connection.execute(
                """
                INSERT INTO notification_settings(
                    location,recipients,update_notifications,daily_summary,updated_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(location) DO UPDATE SET
                    recipients=excluded.recipients,
                    update_notifications=excluded.update_notifications,
                    daily_summary=excluded.daily_summary,
                    updated_at=excluded.updated_at
                """,
                (
                    location,
                    json.dumps(recipients, separators=(",", ":")),
                    int(update_notifications),
                    int(daily_summary),
                    _iso(),
                ),
            )
            connection.execute(
                """
                UPDATE email_outbox
                SET status='PENDING',attempts=0,next_attempt_at=?,updated_at=?,last_error=NULL
                WHERE location=? AND status='FAILED'
                """,
                (_iso(), _iso(), location),
            )
        return jsonify(_settings_payload(database_path))

    return blueprint


def _queue_message(
    connection: sqlite3.Connection,
    location: str,
    notification_type: str,
    recipients: list[str],
    subject: str,
    body_text: str,
    body_html: str = "",
) -> int:
    now = _iso()
    cursor = connection.execute(
        """
        INSERT INTO email_outbox(
            location,notification_type,recipients,subject,body_text,body_html,status,
            attempts,created_at,next_attempt_at,updated_at
        ) VALUES(?,?,?,?,?,?,'PENDING',0,?,?,?)
        """,
        (
            location,
            notification_type,
            json.dumps(recipients, separators=(",", ":")),
            subject.replace("\r", " ").replace("\n", " ")[:240],
            body_text,
            body_html,
            now,
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


def _email_document(title: str, subtitle: str, content: str, link_text: str, link_url: str) -> str:
    return f"""<!doctype html>
<html><body style="margin:0;background:#f3f6f7;color:#17262d;font-family:Arial,sans-serif">
<div style="display:none;max-height:0;overflow:hidden">{escape(subtitle)}</div>
<div style="max-width:720px;margin:0 auto;padding:28px 16px">
  <div style="border-top:5px solid #12747b;background:#ffffff;padding:26px;border-radius:7px">
    <div style="font-size:12px;font-weight:700;letter-spacing:.08em;color:#5f7078;text-transform:uppercase">TeraCota 2000</div>
    <h1 style="margin:8px 0 4px;font-size:24px;line-height:1.2;color:#10252f">{escape(title)}</h1>
    <p style="margin:0 0 22px;color:#5f7078">{escape(subtitle)}</p>
    {content}
    <p style="margin:24px 0 0"><a href="{escape(link_url, quote=True)}" style="display:inline-block;background:#12747b;color:#ffffff;text-decoration:none;font-weight:700;padding:11px 16px;border-radius:5px">{escape(link_text)}</a></p>
  </div>
</div></body></html>"""


def _details_html(details: dict) -> str:
    rows = []
    for label, value in details.items():
        text = str(value or "").strip()
        if not text:
            continue
        rows.append(
            "<tr>"
            f'<th style="width:150px;padding:9px 12px;text-align:left;vertical-align:top;color:#5f7078;border-bottom:1px solid #e3eaed">{escape(str(label))}</th>'
            f'<td style="padding:9px 12px;border-bottom:1px solid #e3eaed;white-space:pre-line">{escape(text)}</td>'
            "</tr>"
        )
    if not rows:
        return ""
    return '<table role="presentation" style="width:100%;border-collapse:collapse;font-size:14px">' + "".join(rows) + "</table>"


def queue_update_notification(
    database_path: str | Path,
    location: str,
    category: str,
    action: str,
    title: str,
    details: dict,
    actor: str,
) -> int | None:
    initialize_notification_schema(database_path)
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT recipients,update_notifications,daily_summary FROM notification_settings WHERE location=?",
            (location,),
        ).fetchone()
        if not row:
            return None
        recipients = [
            item["email"]
            for item in _stored_subscriptions(row)
            if item["update_notifications"]
        ]
        if not recipients:
            return None
        action_lower = action.lower()
        heading = f"{category} {action_lower}"
        if action_lower in {"added", "reported", "recorded"}:
            lead = f"A new {category.lower()} was {action_lower}."
        elif action_lower == "edited":
            lead = f"{category} was updated. The changed fields are shown below."
        elif action_lower == "deleted":
            lead = f"{category} was removed from the active record."
        else:
            lead = f"{category} was {action_lower}."
        lines = [heading, "", f"{title}", f"Location: {location}", lead]
        for label, value in details.items():
            text = str(value or "").strip()
            if text:
                lines.append(f"{label}: {text}")
        lines.extend(["", f"By: {actor}", f"Recorded: {_iso()}"])
        base_url = os.environ.get(
            "TERACOTA_PUBLIC_URL", "https://teracota.matoug.com"
        ).rstrip("/")
        location_url = f"{base_url}/locations/{quote(location, safe='')}"
        lines.append(f"Open location: {location_url}")
        content = (
            f'<div style="margin-bottom:18px;padding:14px;background:#f3f7f8;border-left:4px solid #12747b">'
            f'<strong style="display:block;font-size:17px">{escape(title)}</strong>'
            f'<span style="color:#5f7078">{escape(location)} · {escape(lead)}</span></div>'
            + _details_html(details)
            + f'<p style="margin:18px 0 0;color:#5f7078;font-size:13px">Recorded by {escape(actor)}</p>'
        )
        return _queue_message(
            connection,
            location,
            "UPDATE",
            recipients,
            f"[TeraCota] {location} - {heading}: {title}",
            "\n".join(lines),
            _email_document(heading, location, content, "Open location", location_url),
        )


def _display_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%b %d, %Y").replace(" 0", " ")


def _daily_summary_bodies(
    database_path: str | Path,
    location: str,
    summary_date: str,
) -> tuple[str, str]:
    target = date.fromisoformat(summary_date)
    dates = [(target - timedelta(days=offset)).isoformat() for offset in (2, 1, 0)]
    robots = robot_metrics_for_dates(database_path, location, dates)
    lines = [
        "TeraCota 2000 daily measurement summary",
        "",
        f"Location: {location}",
        f"Reporting date: {_display_date(summary_date)}",
        f"Comparison: {_display_date(dates[0])} to {_display_date(dates[-1])}",
    ]
    html_sections = []
    for robot in robots:
        lines.extend(["", robot["system_name"], "Date         | Jobs | Measurements | Alignment | Valid"])
        lines.append("-------------|------|--------------|-----------|------")
        html_rows = []
        for day in robot["days"]:
            lines.append(
                f"{day['date']} | {day['jobs']:,} | {day['measurements']:,} | "
                f"{day['alignment_percentage']:.1f}% | {day['valid_percentage']:.1f}%"
            )
            html_rows.append(
                "<tr>"
                f'<td style="padding:9px 10px;border-bottom:1px solid #e3eaed">{escape(_display_date(day["date"]))}</td>'
                f'<td style="padding:9px 10px;text-align:right;border-bottom:1px solid #e3eaed">{day["jobs"]:,}</td>'
                f'<td style="padding:9px 10px;text-align:right;border-bottom:1px solid #e3eaed">{day["measurements"]:,}</td>'
                f'<td style="padding:9px 10px;text-align:right;border-bottom:1px solid #e3eaed">{day["alignment_percentage"]:.1f}%</td>'
                f'<td style="padding:9px 10px;text-align:right;border-bottom:1px solid #e3eaed">{day["valid_percentage"]:.1f}%</td>'
                "</tr>"
            )
        warning = ""
        if not robot["source_aliases"]:
            lines.append("Measurement source: Not configured")
            warning = '<p style="margin:8px 0;color:#a15b00;font-size:13px">Measurement source is not configured for this robot.</p>'
        html_sections.append(
            f'<section style="margin:22px 0"><h2 style="margin:0 0 10px;font-size:18px;color:#10252f">{escape(robot["system_name"])}</h2>'
            '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">'
            '<thead><tr style="background:#eef4f5;color:#42545c">'
            '<th style="padding:9px 10px;text-align:left">Date</th>'
            '<th style="padding:9px 10px;text-align:right">Jobs</th>'
            '<th style="padding:9px 10px;text-align:right">Measurements</th>'
            '<th style="padding:9px 10px;text-align:right">Alignment</th>'
            '<th style="padding:9px 10px;text-align:right">Valid</th>'
            f'</tr></thead><tbody>{"".join(html_rows)}</tbody></table></div>{warning}</section>'
        )
    if not robots:
        lines.extend(["", "No systems are configured for this location."])
        html_sections.append("<p>No systems are configured for this location.</p>")

    base_url = os.environ.get(
        "TERACOTA_PUBLIC_URL", "https://teracota.matoug.com"
    ).rstrip("/")
    lines.extend(
        [
            "",
            f"Open Measurement History: {base_url}/measurements/location/"
            f"{quote(location, safe='')}",
        ]
    )
    html = _email_document(
        "Daily measurement summary",
        f"{location} · {_display_date(summary_date)} with the previous two days",
        "".join(html_sections),
        "Open Measurement History",
        f"{base_url}/measurements/location/{quote(location, safe='')}",
    )
    return "\n".join(lines), html


def queue_daily_summaries(database_path: str | Path, summary_date: date | None = None) -> int:
    initialize_notification_schema(database_path)
    if summary_date is None:
        timezone_name = os.environ.get("TERACOTA_TIMEZONE", "America/Toronto").strip()
        try:
            local_timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            local_timezone = timezone.utc
        summary_date = datetime.now(local_timezone).date() - timedelta(days=1)
    date_text = summary_date.isoformat()
    queued = 0
    with _connect(database_path) as connection:
        settings = connection.execute(
            """
            SELECT ns.* FROM notification_settings ns
            WHERE EXISTS(SELECT 1 FROM systems s WHERE s.location=ns.location)
            ORDER BY ns.location
            """
        ).fetchall()
        for setting in settings:
            recipients = [
                item["email"]
                for item in _stored_subscriptions(setting)
                if item["daily_summary"]
            ]
            if not recipients:
                continue
            existing = connection.execute(
                "SELECT 1 FROM daily_notification_runs WHERE location=? AND summary_date=?",
                (setting["location"], date_text),
            ).fetchone()
            if existing:
                continue
            body_text, body_html = _daily_summary_bodies(
                database_path, setting["location"], date_text
            )
            _queue_message(
                connection,
                setting["location"],
                "DAILY",
                recipients,
                f"[TeraCota] {setting['location']} - Daily measurement summary for {date_text}",
                body_text,
                body_html,
            )
            connection.execute(
                "INSERT INTO daily_notification_runs(location,summary_date,queued_at) VALUES(?,?,?)",
                (setting["location"], date_text, _iso()),
            )
            queued += 1
    return queued


def _health_warning_items(health: dict) -> list[tuple[str, str]]:
    warnings = []
    memory = health.get("memory") or {}
    cpu = health.get("cpu") or {}
    disk = health.get("disk") or {}
    measurements = health.get("measurements") or {}
    memory_percent = memory.get("used_percent")
    swap_percent = memory.get("swap_used_percent")
    load = cpu.get("load_1m")
    processors = max(1, int(cpu.get("logical_processors") or 1))
    root_disk = disk.get("root") or {}
    measurement_disk = disk.get("measurement") or {}
    if memory_percent is not None and float(memory_percent) >= 85:
        warnings.append(("memory", f"Memory usage is {float(memory_percent):.1f}%"))
    if swap_percent is not None and float(swap_percent) >= 70:
        warnings.append(("swap", f"Swap usage is {float(swap_percent):.1f}%"))
    if load is not None and float(load) > processors:
        warnings.append(("cpu", f"1-minute CPU load is {float(load):.2f} across {processors} CPUs"))
    root_percent = root_disk.get("used_percent")
    if root_percent is not None and float(root_percent) >= 85:
        warnings.append(("root-disk", f"Server disk usage is {float(root_percent):.1f}%"))
    measurement_percent = measurement_disk.get("used_percent")
    if (
        measurement_percent is not None
        and float(measurement_percent) >= 85
        and measurement_disk != root_disk
    ):
        warnings.append(
            ("measurement-disk", f"Measurement disk usage is {float(measurement_percent):.1f}%")
        )
    failed = int(measurements.get("failed_batches") or 0)
    if failed:
        warnings.append(("failed-imports", f"{failed} measurement import batch(es) failed"))
    return warnings


def _health_recipients(connection: sqlite3.Connection) -> list[str]:
    recipients = []
    for row in connection.execute(
        "SELECT recipients,update_notifications,daily_summary FROM notification_settings"
    ).fetchall():
        for item in _stored_subscriptions(row):
            if item["update_notifications"] and item["email"] not in recipients:
                recipients.append(item["email"])
    return recipients


def queue_health_warning(database_path: str | Path) -> int:
    """Queue a warning on a new/high server condition and a recovery when it clears."""
    from server_monitoring import collect_server_health

    initialize_notification_schema(database_path)
    log_path = Path(
        os.environ.get(
            "TERACOTA_APP_LOG_PATH",
            str(Path(database_path).resolve().parent / "logs" / "teracota.log"),
        )
    )
    try:
        backups = max(1, min(20, int(os.environ.get("TERACOTA_APP_LOG_BACKUPS", "5"))))
    except ValueError:
        backups = 5
    health = collect_server_health(database_path, log_path, backups)
    warnings = _health_warning_items(health)
    signature = ",".join(sorted(key for key, _ in warnings))
    now = _utc_now()
    base_url = os.environ.get(
        "TERACOTA_PUBLIC_URL", "https://teracota.matoug.com"
    ).rstrip("/")
    admin_url = f"{base_url}/admin"

    with _connect(database_path) as connection:
        recipients = _health_recipients(connection)
        if not recipients:
            return 0
        previous = connection.execute(
            "SELECT active_signature,last_queued_at FROM health_alert_state WHERE id=1"
        ).fetchone()
        previous_signature = previous["active_signature"] if previous else ""
        last_queued = None
        if previous and previous["last_queued_at"]:
            try:
                last_queued = datetime.fromisoformat(
                    previous["last_queued_at"].replace("Z", "+00:00")
                )
            except ValueError:
                last_queued = None
        try:
            repeat_minutes = max(
                60, int(os.environ.get("TERACOTA_HEALTH_ALERT_REPEAT_MINUTES", "360"))
            )
        except ValueError:
            repeat_minutes = 360

        if signature:
            unchanged_and_recent = (
                signature == previous_signature
                and last_queued is not None
                and now - last_queued < timedelta(minutes=repeat_minutes)
            )
            if unchanged_and_recent:
                return 0
            text_lines = [
                "TeraCota server health warning",
                "",
                *[f"- {message}" for _, message in warnings],
                "",
                f"Checked: {_iso(now)}",
                f"Open Server Health: {admin_url}",
            ]
            warning_rows = "".join(
                f'<li style="margin:8px 0">{escape(message)}</li>' for _, message in warnings
            )
            body_html = _email_document(
                "Server health warning",
                "One or more TeraCota server indicators need attention.",
                f'<div style="padding:14px;background:#fff4e5;border-left:4px solid #c06b00"><ul style="margin:0;padding-left:20px">{warning_rows}</ul></div>',
                "Open Server Health",
                admin_url,
            )
            _queue_message(
                connection,
                "__SERVER__",
                "HEALTH",
                recipients,
                f"[TeraCota] Server health warning: {len(warnings)} indicator(s)",
                "\n".join(text_lines),
                body_html,
            )
        elif previous_signature:
            _queue_message(
                connection,
                "__SERVER__",
                "HEALTH",
                recipients,
                "[TeraCota] Server health recovered",
                f"TeraCota server health recovered.\n\nChecked: {_iso(now)}\nOpen Server Health: {admin_url}",
                _email_document(
                    "Server health recovered",
                    "All monitored indicators are back below their warning thresholds.",
                    '<p style="padding:14px;background:#eaf7ef;border-left:4px solid #268653">No active server-health warnings remain.</p>',
                    "Open Server Health",
                    admin_url,
                ),
            )
        else:
            return 0

        connection.execute(
            """
            INSERT INTO health_alert_state(id,active_signature,last_queued_at,updated_at)
            VALUES(1,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                active_signature=excluded.active_signature,
                last_queued_at=excluded.last_queued_at,
                updated_at=excluded.updated_at
            """,
            (signature, _iso(now), _iso(now)),
        )
        return 1


def _claim_message(database_path: str | Path) -> dict | None:
    now = _iso()
    stale = _iso(_utc_now() - timedelta(minutes=15))
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE email_outbox SET status='PENDING',updated_at=?
            WHERE status='SENDING' AND updated_at<?
            """,
            (now, stale),
        )
        row = connection.execute(
            """
            SELECT * FROM email_outbox
            WHERE status='PENDING' AND next_attempt_at<=?
            ORDER BY id LIMIT 1
            """,
            (now,),
        ).fetchone()
        if not row:
            return None
        connection.execute(
            "UPDATE email_outbox SET status='SENDING',attempts=attempts+1,updated_at=? WHERE id=?",
            (now, row["id"]),
        )
        connection.commit()
        claimed = dict(row)
        claimed["attempts"] = int(row["attempts"]) + 1
        return claimed


def _send_message(configuration: dict, item: dict) -> None:
    recipients = parse_recipients(json.loads(item["recipients"]))
    message = EmailMessage()
    message["Subject"] = item["subject"]
    message["From"] = configuration["sender"]
    message["To"] = ", ".join(recipients)
    message.set_content(item["body_text"])
    if str(item.get("body_html") or "").strip():
        message.add_alternative(item["body_html"], subtype="html")
    context = ssl.create_default_context()
    if configuration["security"] == "ssl":
        with smtplib.SMTP_SSL(
            configuration["host"], configuration["port"], timeout=30, context=context
        ) as client:
            client.login(configuration["username"], configuration["password"])
            client.send_message(message)
        return
    with smtplib.SMTP(configuration["host"], configuration["port"], timeout=30) as client:
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
        client.login(configuration["username"], configuration["password"])
        client.send_message(message)


def deliver_pending(database_path: str | Path, maximum: int = 50) -> tuple[int, int]:
    initialize_notification_schema(database_path)
    configuration = smtp_configuration()
    if not configuration["configured"]:
        return 0, 0
    sent = 0
    failed = 0
    for _ in range(maximum):
        item = _claim_message(database_path)
        if not item:
            break
        try:
            _send_message(configuration, item)
        except Exception as error:
            failed += 1
            attempts = int(item["attempts"])
            terminal = attempts >= MAX_DELIVERY_ATTEMPTS
            delay_minutes = min(360, 2 ** min(attempts, 8))
            next_attempt = _utc_now() + timedelta(minutes=delay_minutes)
            with _connect(database_path) as connection:
                connection.execute(
                    """
                    UPDATE email_outbox
                    SET status=?,next_attempt_at=?,updated_at=?,last_error=? WHERE id=?
                    """,
                    (
                        "FAILED" if terminal else "PENDING",
                        _iso(next_attempt),
                        _iso(),
                        str(error)[:1000],
                        item["id"],
                    ),
                )
            continue
        with _connect(database_path) as connection:
            connection.execute(
                """
                UPDATE email_outbox
                SET status='SENT',sent_at=?,updated_at=?,last_error=NULL WHERE id=?
                """,
                (_iso(), _iso(), item["id"]),
            )
        sent += 1
    return sent, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="TeraCota email notification worker")
    parser.add_argument("mode", choices=("deliver", "daily"))
    parser.add_argument("--database", default=os.environ.get("TERACOTA_DB_PATH", "teracota.sqlite3"))
    parser.add_argument("--date", help="Daily summary date override in YYYY-MM-DD format")
    args = parser.parse_args()
    if args.mode == "daily":
        requested_date = date.fromisoformat(args.date) if args.date else None
        queued = queue_daily_summaries(args.database, requested_date)
        sent, failed = deliver_pending(args.database)
        print(f"Queued {queued} daily summaries; sent {sent}; failed {failed}")
        return 1 if failed else 0
    health_queued = queue_health_warning(args.database)
    sent, failed = deliver_pending(args.database)
    print(f"Queued {health_queued} health notifications; sent {sent}; failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
