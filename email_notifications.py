"""Durable per-location email notifications and daily operations summaries."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
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
            CREATE INDEX IF NOT EXISTS idx_email_outbox_delivery
                ON email_outbox(status, next_attempt_at, id);
            CREATE INDEX IF NOT EXISTS idx_email_outbox_location
                ON email_outbox(location, created_at DESC);
            """
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
) -> int:
    now = _iso()
    cursor = connection.execute(
        """
        INSERT INTO email_outbox(
            location,notification_type,recipients,subject,body_text,status,
            attempts,created_at,next_attempt_at,updated_at
        ) VALUES(?,?,?,?,?,'PENDING',0,?,?,?)
        """,
        (
            location,
            notification_type,
            json.dumps(recipients, separators=(",", ":")),
            subject.replace("\r", " ").replace("\n", " ")[:240],
            body_text,
            now,
            now,
            now,
        ),
    )
    return int(cursor.lastrowid)


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
        lines = [
            "TeraCota 2000 operational update",
            "",
            f"Location: {location}",
            f"Type: {category}",
            f"Action: {action}",
            f"Item: {title}",
        ]
        for label, value in details.items():
            text = str(value or "").strip()
            if text:
                lines.append(f"{label}: {text}")
        lines.extend(["", f"Changed by: {actor}", f"Recorded at: {_iso()}"])
        base_url = os.environ.get(
            "TERACOTA_PUBLIC_URL", "https://teracota.matoug.com"
        ).rstrip("/")
        lines.append(f"Open TeraCota: {base_url}/locations/{quote(location, safe='')}")
        return _queue_message(
            connection,
            location,
            "UPDATE",
            recipients,
            f"[TeraCota] {location} - {category} {action.lower()}: {title}",
            "\n".join(lines),
        )


def _daily_summary_body(connection: sqlite3.Connection, location: str, summary_date: str) -> str:
    visits = connection.execute(
        """
        SELECT sv.id,sv.date,sv.engineer,
               GROUP_CONCAT(DISTINCT s.name) AS systems,
               GROUP_CONCAT(DISTINCT NULLIF(m.type,'')) AS purposes,
               GROUP_CONCAT(DISTINCT NULLIF(m.summary,'')) AS summaries
        FROM site_visits sv
        LEFT JOIN maintenance_records m ON m.visit_id=sv.id
        LEFT JOIN systems s ON s.id=m.system_id
        WHERE sv.location=? AND sv.date=?
        GROUP BY sv.id ORDER BY sv.id
        """,
        (location, summary_date),
    ).fetchall()
    opened = connection.execute(
        """
        SELECT i.id,i.title,i.severity,i.status,i.reported_by,
               GROUP_CONCAT(DISTINCT s.name) AS systems
        FROM system_issues i
        JOIN system_issue_links l ON l.issue_id=i.id
        JOIN systems s ON s.id=l.system_id
        WHERE s.location=? AND i.opened=?
        GROUP BY i.id ORDER BY i.id
        """,
        (location, summary_date),
    ).fetchall()
    closed = connection.execute(
        """
        SELECT i.id,i.title,i.severity,i.reported_by,
               GROUP_CONCAT(DISTINCT s.name) AS systems
        FROM system_issues i
        JOIN system_issue_links l ON l.issue_id=i.id
        JOIN systems s ON s.id=l.system_id
        WHERE s.location=? AND i.closed_date=?
        GROUP BY i.id ORDER BY i.id
        """,
        (location, summary_date),
    ).fetchall()
    updates = connection.execute(
        """
        SELECT u.date,u.update_type,u.reported_by,u.notes,s.name AS system_name
        FROM system_updates u JOIN systems s ON s.id=u.system_id
        WHERE s.location=? AND u.date=? ORDER BY u.id
        """,
        (location, summary_date),
    ).fetchall()
    status_changes = connection.execute(
        """
        SELECT h.status,h.note,s.name AS system_name
        FROM system_status_history h JOIN systems s ON s.id=h.system_id
        WHERE s.location=? AND h.started_at=? ORDER BY h.id
        """,
        (location, summary_date),
    ).fetchall()

    lines = [
        "TeraCota 2000 daily operations summary",
        "",
        f"Location: {location}",
        f"Date: {summary_date}",
        "",
        f"Site visits ({len(visits)})",
    ]
    lines.extend(
        f"- {row['engineer']} | {row['systems'] or 'No system'} | "
        f"{row['purposes'] or 'Purpose not specified'} | {row['summaries'] or 'No summary'}"
        for row in visits
    )
    if not visits:
        lines.append("- None")

    lines.extend(["", f"Issues opened ({len(opened)})"])
    lines.extend(
        f"- {row['title']} ({row['severity']}, {row['status']}) | {row['systems']} | "
        f"reported by {row['reported_by'] or 'Unknown'}"
        for row in opened
    )
    if not opened:
        lines.append("- None")

    lines.extend(["", f"Issues closed ({len(closed)})"])
    lines.extend(
        f"- {row['title']} ({row['severity']}) | {row['systems']}"
        for row in closed
    )
    if not closed:
        lines.append("- None")

    lines.extend(["", f"System updates ({len(updates)})"])
    lines.extend(
        f"- {row['system_name']} | {row['update_type']} | "
        f"reported by {row['reported_by'] or 'Unknown'} | {row['notes'] or 'No notes'}"
        for row in updates
    )
    if not updates:
        lines.append("- None")

    lines.extend(["", f"Status changes ({len(status_changes)})"])
    lines.extend(
        f"- {row['system_name']} -> {row['status']} | {row['note'] or 'No note'}"
        for row in status_changes
    )
    if not status_changes:
        lines.append("- None")

    base_url = os.environ.get(
        "TERACOTA_PUBLIC_URL", "https://teracota.matoug.com"
    ).rstrip("/")
    lines.extend(["", f"Open TeraCota: {base_url}/locations/{quote(location, safe='')}"])
    return "\n".join(lines)


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
            body = _daily_summary_body(connection, setting["location"], date_text)
            _queue_message(
                connection,
                setting["location"],
                "DAILY",
                recipients,
                f"[TeraCota] {setting['location']} - Daily summary for {date_text}",
                body,
            )
            connection.execute(
                "INSERT INTO daily_notification_runs(location,summary_date,queued_at) VALUES(?,?,?)",
                (setting["location"], date_text, _iso()),
            )
            queued += 1
    return queued


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
    sent, failed = deliver_pending(args.database)
    print(f"Sent {sent} notification emails; failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
