import io
from datetime import date
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MAIN_DB = ROOT / "codex_measurement_main_test.sqlite3"
ANALYTICS_TEST_DB = ROOT / "codex_measurement_analytics_test.sqlite3"
UPLOAD_TEST_DB = ROOT / "codex_measurement_upload_test.sqlite3"
MEASUREMENT_TEST_DIR = ROOT / "codex_measurement_data_test"
shutil.rmtree(MEASUREMENT_TEST_DIR, ignore_errors=True)
for database_path in (MAIN_DB, ANALYTICS_TEST_DB, UPLOAD_TEST_DB):
    for candidate in (database_path, Path(f"{database_path}-wal"), Path(f"{database_path}-shm")):
        candidate.unlink(missing_ok=True)
os.environ.update(
    {
        "TERACOTA_DB_PATH": str(MAIN_DB),
        "TERACOTA_MEASUREMENT_DATA_DIR": str(MEASUREMENT_TEST_DIR),
        "TERACOTA_MEASUREMENT_ANALYTICS_DB": str(ANALYTICS_TEST_DB),
        "TERACOTA_MEASUREMENT_UPLOAD_DB": str(UPLOAD_TEST_DB),
        "TERACOTA_MEASUREMENT_UPLOAD_TOKEN": "test-measurement-token-longer-than-24-characters",
        "TERACOTA_APP_LOG_PATH": str(MEASUREMENT_TEST_DIR / "logs" / "teracota.log"),
        "TERACOTA_SMTP_USERNAME": "notifications@example.com",
        "TERACOTA_SMTP_APP_PASSWORD": "test-app-password",
        "TERACOTA_SMTP_FROM": "notifications@example.com",
        "TERACOTA_SEED_DEMO": "false",
    }
)

import app as app_module  # noqa: E402
from email_notifications import (  # noqa: E402
    deliver_pending,
    queue_daily_summaries,
    queue_health_warning,
)
from measurements.config import save_source_mapping, source_mappings  # noqa: E402
from measurements.database import ANALYTICS_DB, UPLOAD_DB, connect, replace_job_summary  # noqa: E402
from measurements.queries import jobs_by_color_last_30_days, metric_series, options  # noqa: E402
from measurements.settings import OBJECT_CACHE_DIR  # noqa: E402
from measurements.storage import sha256_path  # noqa: E402
from measurements.summarizer import summarize_csv  # noqa: E402


class MeasurementIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.app.config.update(TESTING=True)
        app_module.init_db()
        with app_module.get_db() as connection:
            connection.execute(
                """
                INSERT INTO systems(name,location,status,owner,last_visit,next_visit,notes)
                VALUES('Robot 1','Client Plant','Operational','Unassigned','','','')
                """
            )
            connection.execute(
                """
                INSERT INTO systems(name,location,status,owner,last_visit,next_visit,notes)
                VALUES('Robot 2','Client Plant','Operational','Unassigned','','','')
                """
            )
            connection.execute(
                "INSERT INTO locations(name,contacts,notes,display_rank) VALUES('Client Plant','','',1)"
            )
            connection.execute(
                """
                INSERT INTO system_status_history(system_id,status,started_at,note)
                SELECT id,status,'2026-01-01','Initial test status' FROM systems
                """
            )
        cls.client = app_module.app.test_client()
        with cls.client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "teraview"

    @classmethod
    def tearDownClass(cls):
        for path in (MAIN_DB, ANALYTICS_TEST_DB, UPLOAD_TEST_DB):
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                candidate.unlink(missing_ok=True)
        shutil.rmtree(MEASUREMENT_TEST_DIR, ignore_errors=True)

    def test_unknown_upload_client_is_rejected(self):
        response = self.client.post(
            "/api/uploads/prepare",
            headers={"Authorization": "Bearer test-measurement-token-longer-than-24-characters"},
            json={"client": "Unknown", "source_id": "test", "files": []},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("existing location", response.get_json()["error"])

    def test_reorganized_templates_and_assets_are_served(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
        for asset_path in (
            "/assets/css/app.css",
            "/assets/js/app.js",
            "/assets/js/admin_monitor.js",
            "/assets/css/admin_monitor.css",
            "/assets/js/admin_notifications.js",
            "/assets/css/admin_notifications.css",
            "/assets/measurements/admin.css",
            "/assets/measurements/admin.js",
            "/assets/measurements/history.css",
            "/assets/measurements/history.js",
            "/assets/measurements/files.css",
            "/assets/measurements/files.js",
            "/assets/measurements/vendor/plotly-basic-2.35.2.min.js",
        ):
            with self.subTest(asset_path=asset_path):
                response = self.client.get(asset_path)
                try:
                    self.assertEqual(response.status_code, 200)
                finally:
                    response.close()

        anonymous = app_module.app.test_client()
        self.assertEqual(anonymous.get("/login").status_code, 200)

        history = self.client.get("/measurements/location/Client%20Plant")
        self.assertEqual(history.status_code, 200)
        html = history.get_data(as_text=True)
        self.assertIn(
            'src="/assets/measurements/vendor/plotly-basic-2.35.2.min.js"', html
        )
        self.assertNotIn("https://cdn.plot.ly", html)
        self.assertIn("Raw CSV Files", html)

        anonymous_files = anonymous.get("/measurements/files/Client%20Plant")
        self.assertEqual(anonymous_files.status_code, 302)
        self.assertIn("/login", anonymous_files.headers["Location"])

    def test_admin_monitoring_failures_and_authentication(self):
        health = self.client.get("/api/admin/server-health")
        self.assertEqual(health.status_code, 200)
        payload = health.get_json()
        self.assertEqual(payload["application"]["status"], "online")
        self.assertIn("used_percent", payload["memory"])
        self.assertIn("free_bytes", payload["disk"]["root"])
        self.assertIn("operations_database_bytes", payload["storage"])
        self.assertIn("pending_batches", payload["measurements"])

        with connect(UPLOAD_DB) as connection:
            connection.execute(
                """
                INSERT INTO upload_batches(
                    id,source_id,client,status,expected_files,created_at,import_attempts,error
                ) VALUES('failed-test-batch','test-source','Client Plant','IMPORT_FAILED',1,
                         '2026-09-25T04:00:00+00:00',1,'Unable to parse test CSV')
                """
            )
            connection.execute(
                """
                INSERT INTO upload_items(
                    id,batch_id,filename,object_key,size_bytes,sha256,status,error
                ) VALUES('failed-test-item','failed-test-batch','bad.csv','incoming/bad.csv',10,
                         'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                         'IMPORT_FAILED','Missing required column')
                """
            )
        failures = self.client.get("/api/admin/measurement-import-failures")
        self.assertEqual(failures.status_code, 200)
        failed_batch = failures.get_json()["batches"][0]
        self.assertEqual(failed_batch["id"], "failed-test-batch")
        self.assertEqual(failed_batch["failed_files"][0]["filename"], "bad.csv")
        retry = self.client.post(
            "/api/admin/measurement-import-failures/failed-test-batch/retry"
        )
        self.assertEqual(retry.status_code, 200)
        with connect(UPLOAD_DB) as connection:
            status = connection.execute(
                "SELECT status FROM upload_batches WHERE id='failed-test-batch'"
            ).fetchone()["status"]
            self.assertEqual(status, "VERIFIED")
            connection.execute("DELETE FROM upload_batches WHERE id='failed-test-batch'")

        self.client.get("/admin")
        logs = self.client.get("/api/admin/application-logs?level=ALL&limit=9999")
        self.assertEqual(logs.status_code, 200)
        log_payload = logs.get_json()
        self.assertEqual(log_payload["limit"], 500)
        self.assertTrue(log_payload["entries"])
        self.assertTrue(
            any("GET /admin" in entry["message"] for entry in log_payload["entries"])
        )
        app_module.app.logger.warning("Monitoring warning test")
        warnings = self.client.get(
            "/api/admin/application-logs?level=WARNING&limit=100"
        ).get_json()["entries"]
        self.assertTrue(warnings)
        self.assertTrue(all(entry["level"] == "WARNING" for entry in warnings))

        anonymous = app_module.app.test_client()
        before = len(self.client.get("/api/admin/application-logs?limit=500").get_json()["entries"])
        self.assertEqual(anonymous.get("/api/admin/server-health").status_code, 401)
        self.assertEqual(
            anonymous.get("/api/admin/measurement-import-failures").status_code,
            401,
        )
        self.assertEqual(anonymous.get("/login").status_code, 200)
        after = self.client.get("/api/admin/application-logs?limit=500").get_json()
        self.assertEqual(len(after["entries"]), before)

    def test_email_notifications_are_location_scoped_durable_and_idempotent(self):
        anonymous = app_module.app.test_client()
        self.assertEqual(
            anonymous.get("/api/admin/notification-settings").status_code,
            401,
        )

        app_module.execute(
            """
            INSERT INTO notification_settings(
                location,recipients,update_notifications,daily_summary,updated_at
            ) VALUES('Client Plant','[\"legacy@example.com\"]',1,0,'2026-09-25T00:00:00Z')
            """
        )
        legacy = self.client.get("/api/admin/notification-settings").get_json()["locations"][0]
        self.assertEqual(
            legacy["recipients"],
            [{"email": "legacy@example.com", "update_notifications": True, "daily_summary": False}],
        )

        saved = self.client.put(
            "/api/admin/notification-settings/Client%20Plant",
            json={
                "recipients": [
                    {
                        "email": "team@example.com",
                        "update_notifications": True,
                        "daily_summary": False,
                    },
                    {
                        "email": "second@example.com",
                        "update_notifications": False,
                        "daily_summary": True,
                    },
                ],
            },
        )
        self.assertEqual(saved.status_code, 200)
        setting = saved.get_json()["locations"][0]
        self.assertEqual(
            setting["recipients"],
            [
                {
                    "email": "team@example.com",
                    "update_notifications": True,
                    "daily_summary": False,
                },
                {
                    "email": "second@example.com",
                    "update_notifications": False,
                    "daily_summary": True,
                },
            ],
        )
        self.assertTrue(setting["update_notifications"])
        self.assertTrue(setting["daily_summary"])

        app_module.execute(
            "INSERT INTO locations(name,contacts,notes,display_rank) VALUES('No Systems','','',99)"
        )
        listed_locations = {
            item["location"]
            for item in self.client.get("/api/admin/notification-settings").get_json()["locations"]
        }
        self.assertNotIn("No Systems", listed_locations)
        app_module.execute("DELETE FROM locations WHERE name='No Systems'")

        systems = app_module.query_all("SELECT id,name FROM systems ORDER BY id")
        robot_one = next(system for system in systems if system["name"] == "Robot 1")
        save_source_mapping("Client Plant", robot_one["id"], "Robot_1_SN101")
        replace_job_summary(
            {
                "client": "Client Plant",
                "car_id": "daily-test-car",
                "body_id": "daily-test-body",
                "job_date": "2026-09-24",
                "job_time": "23:30:00",
                "color": "Test",
                "layer_count": 0,
                "scopes": [
                    {
                        "source": "Robot_1_SN101",
                        "raw_count": 100,
                        "deduplicated_count": 80,
                        "aligned_count": 90,
                        "valid_count": 72,
                        "alignment_percentage": 90.0,
                        "valid_percentage": 90.0,
                        "metrics": {},
                    }
                ],
            },
            {
                "filename": "daily-summary-test.csv",
                "object_key": "tests/daily-summary-test.csv",
                "sha256": "b" * 64,
                "size_bytes": 100,
            },
        )
        visit = self.client.post(
            "/api/site-visits",
            json={
                "location": "Client Plant",
                "system_ids": [robot_one["id"]],
                "date": "2026-09-24",
                "engineer": "Test Engineer",
                "type": ["Calibration"],
                "summary": "Verified reference response.",
            },
        )
        self.assertEqual(visit.status_code, 200)
        immediate = app_module.query_one(
            "SELECT * FROM email_outbox WHERE notification_type='UPDATE' ORDER BY id DESC LIMIT 1"
        )
        self.assertEqual(immediate["location"], "Client Plant")
        self.assertIn("Site visit", immediate["subject"])
        self.assertIn("Verified reference response", immediate["body_text"])

        visit_record = app_module.query_one(
            "SELECT id FROM maintenance_records WHERE summary=?",
            ("Verified reference response.",),
        )
        edited_visit = self.client.put(
            f"/api/maintenance/{visit_record['id']}",
            json={
                "date": "2026-09-24",
                "engineer": "Test Engineer",
                "type": ["Calibration"],
                "summary": "Verified reference response and saved results.",
            },
        )
        self.assertEqual(edited_visit.status_code, 200)
        visit_email = app_module.query_one(
            "SELECT * FROM email_outbox WHERE notification_type='UPDATE' ORDER BY id DESC LIMIT 1"
        )
        self.assertIn("Site visit edited", visit_email["subject"])
        self.assertIn(
            "Summary: Verified reference response. -> Verified reference response and saved results.",
            visit_email["body_text"],
        )

        reported_issue = self.client.post(
            f"/api/systems/{robot_one['id']}/issues",
            json={
                "system_ids": [robot_one["id"]],
                "title": "Notification test issue",
                "severity": "Low",
                "opened": "2026-09-24",
                "status": "Open",
                "reported_by": "Test Engineer",
                "related_to": ["Software"],
                "notes": "Initial issue notes.",
            },
        )
        self.assertEqual(reported_issue.status_code, 200)
        issue = app_module.query_one(
            "SELECT id FROM system_issues WHERE title='Notification test issue'"
        )
        edited_issue = self.client.put(
            f"/api/issues/{issue['id']}",
            json={
                "system_ids": [robot_one["id"]],
                "title": "Notification test issue",
                "severity": "Medium",
                "opened": "2026-09-24",
                "status": "Open",
                "reported_by": "Test Engineer",
                "related_to": ["Software"],
                "notes": "Initial issue notes.",
            },
        )
        self.assertEqual(edited_issue.status_code, 200)
        issue_email = app_module.query_one(
            "SELECT * FROM email_outbox WHERE notification_type='UPDATE' ORDER BY id DESC LIMIT 1"
        )
        self.assertIn("Issue edited", issue_email["subject"])
        self.assertIn("Severity: Low -> Medium", issue_email["body_text"])

        self.assertEqual(queue_daily_summaries(MAIN_DB, date(2026, 9, 24)), 1)
        self.assertEqual(queue_daily_summaries(MAIN_DB, date(2026, 9, 24)), 0)
        daily = app_module.query_one(
            "SELECT * FROM email_outbox WHERE notification_type='DAILY' ORDER BY id DESC LIMIT 1"
        )
        self.assertIn("Daily measurement summary for 2026-09-24", daily["subject"])
        self.assertIn("Robot 1", daily["body_text"])
        self.assertIn("Comparison: Sep 22, 2026 to Sep 24, 2026", daily["body_text"])
        self.assertIn("2026-09-24 | 1 | 100 | 90.0% | 90.0%", daily["body_text"])
        self.assertIn("2026-09-23 | 0 | 0 | 0.0% | 0.0%", daily["body_text"])
        self.assertIn("<table", daily["body_html"])
        self.assertIn("Robot 1", daily["body_html"])
        self.assertNotIn("display:none", daily["body_html"])
        self.assertNotIn("display:inline-block", daily["body_html"])
        self.assertNotIn("background:#12747b;color:#ffffff", daily["body_html"])
        self.assertNotIn("Site visits", daily["body_text"])

        sent_messages = []

        class FakeSmtp:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def login(self, username, password):
                self.username = username

            def send_message(self, message):
                sent_messages.append(message)

        with patch("email_notifications.smtplib.SMTP_SSL", FakeSmtp):
            sent, failed = deliver_pending(MAIN_DB)
        self.assertEqual((sent, failed), (5, 0))
        self.assertEqual(len(sent_messages), 5)
        recipients_by_subject = {
            "daily" if "Daily measurement summary" in message["Subject"] else "update": message["To"]
            for message in sent_messages
        }
        self.assertEqual(recipients_by_subject["update"], "team@example.com")
        self.assertEqual(recipients_by_subject["daily"], "second@example.com")
        self.assertTrue(all(message.is_multipart() for message in sent_messages))
        statuses = app_module.query_all("SELECT DISTINCT status FROM email_outbox")
        self.assertEqual(statuses, [{"status": "SENT"}])

    def test_health_warning_email_is_deduplicated_and_recovers(self):
        app_module.execute(
            """
            INSERT INTO notification_settings(
                location,recipients,update_notifications,daily_summary,updated_at
            ) VALUES('Client Plant','[{"email":"alerts@example.com","update_notifications":true,"daily_summary":false}]',1,0,'2026-10-06T00:00:00Z')
            ON CONFLICT(location) DO UPDATE SET recipients=excluded.recipients,
                update_notifications=1,daily_summary=0,updated_at=excluded.updated_at
            """
        )
        high_health = {
            "memory": {"used_percent": 91, "swap_used_percent": 10},
            "cpu": {"load_1m": 0.5, "logical_processors": 2},
            "disk": {
                "root": {"used_percent": 30},
                "measurement": {"used_percent": 30},
            },
            "measurements": {"failed_batches": 0},
        }
        healthy = {
            **high_health,
            "memory": {"used_percent": 40, "swap_used_percent": 5},
        }
        failed_import_only = {
            **healthy,
            "measurements": {"failed_batches": 5},
        }
        with patch("server_monitoring.collect_server_health", return_value=failed_import_only):
            self.assertEqual(queue_health_warning(MAIN_DB), 0)
        app_module.execute(
            """
            INSERT INTO health_alert_state(id,active_signature,last_queued_at,updated_at)
            VALUES(1,'failed-imports','2026-10-07T00:00:00Z','2026-10-07T00:00:00Z')
            ON CONFLICT(id) DO UPDATE SET active_signature='failed-imports',
                last_queued_at='2026-10-07T00:00:00Z',updated_at='2026-10-07T00:00:00Z'
            """
        )
        with patch("server_monitoring.collect_server_health", return_value=failed_import_only):
            self.assertEqual(queue_health_warning(MAIN_DB), 0)
        cleared = app_module.query_one(
            "SELECT active_signature FROM health_alert_state WHERE id=1"
        )
        self.assertEqual(cleared["active_signature"], "")
        with patch("server_monitoring.collect_server_health", return_value=high_health):
            self.assertEqual(queue_health_warning(MAIN_DB), 1)
            self.assertEqual(queue_health_warning(MAIN_DB), 0)
        warning = app_module.query_one(
            "SELECT * FROM email_outbox WHERE notification_type='HEALTH' ORDER BY id DESC LIMIT 1"
        )
        self.assertIn("Server health warning", warning["subject"])
        self.assertIn("Memory usage is 91.0%", warning["body_text"])
        with patch("server_monitoring.collect_server_health", return_value=healthy):
            self.assertEqual(queue_health_warning(MAIN_DB), 1)
        recovery = app_module.query_one(
            "SELECT * FROM email_outbox WHERE notification_type='HEALTH' ORDER BY id DESC LIMIT 1"
        )
        self.assertIn("recovered", recovery["subject"])

    def test_jobs_by_color_uses_all_robot_sources(self):
        payload = jobs_by_color_last_30_days("Client Plant", date(2026, 9, 24))
        self.assertEqual(payload["start"], "2026-08-26")
        self.assertEqual(payload["end"], "2026-09-24")
        self.assertEqual(payload["rows"], [{"color": "Test", "jobs": 1}])

    def test_multiple_source_aliases_can_map_to_one_system(self):
        systems = app_module.query_all("SELECT id,name FROM systems ORDER BY id")
        robot_two = next(system for system in systems if system["name"] == "Robot 2")
        save_source_mapping("Client Plant", robot_two["id"], "Robot_2_SN203")
        save_source_mapping("Client Plant", robot_two["id"], "Robot_2_SN999")
        aliases = [
            row["source_name"]
            for row in source_mappings("Client Plant")
            if row["system_id"] == robot_two["id"]
        ]
        self.assertEqual(aliases, ["Robot_2_SN203", "Robot_2_SN999"])

    def test_page_shell_matches_requested_view_before_javascript_loads(self):
        statistics = self.client.get("/statistics")
        self.assertEqual(statistics.status_code, 200)
        statistics_html = statistics.get_data(as_text=True)
        self.assertIn('data-page="statistics"', statistics_html)
        self.assertIn('class="statistics-page "', statistics_html)
        self.assertIn('class="dashboard hidden"', statistics_html)

        issues = self.client.get("/issues")
        self.assertEqual(issues.status_code, 200)
        issues_html = issues.get_data(as_text=True)
        self.assertIn('data-page="issues"', issues_html)
        self.assertIn('class="open-issues-page "', issues_html)
        self.assertIn('class="dashboard hidden"', issues_html)

    def test_status_history_note_is_saved(self):
        state = self.client.get("/api/state").get_json()
        entry = state["systems"][0]["status_history"][0]
        response = self.client.put(
            f'/api/status-history/{entry["id"]}',
            json={
                "status": entry["status"],
                "started_at": entry["started_at"],
                "note": "Verified after scheduled inspection",
            },
        )
        self.assertEqual(response.status_code, 200)
        updated = next(
            item
            for system in response.get_json()["systems"]
            for item in system["status_history"]
            if item["id"] == entry["id"]
        )
        self.assertEqual(updated["note"], "Verified after scheduled inspection")

    def test_thickness_combines_matching_layer_names_across_layer_counts(self):
        replace_job_summary(
            {
                "client": "Client Plant",
                "car_id": "layer-test-car",
                "body_id": "layer-test-body",
                "job_date": "2026-03-23",
                "job_time": "02:00:00",
                "color": "Layer Test",
                "layer_count": 4,
                "scopes": [
                    {
                        "source": "Robot_2_SN203",
                        "raw_count": 10,
                        "deduplicated_count": 10,
                        "aligned_count": 10,
                        "valid_count": 10,
                        "alignment_percentage": 100.0,
                        "valid_percentage": 100.0,
                        "metrics": {
                            "Thickness_1": {
                                "count": 10,
                                "mean": 50.0,
                                "stdev": 1.0,
                                "min": 48.0,
                                "max": 52.0,
                            }
                        },
                    }
                ],
            },
            {
                "filename": "layer-count-test.csv",
                "object_key": "tests/layer-count-test.csv",
                "sha256": "c" * 64,
                "size_bytes": 100,
            },
        )
        robot_two = app_module.query_one("SELECT id FROM systems WHERE name='Robot 2'")
        aliases = [
            row["source_name"]
            for row in source_mappings("Client Plant")
            if row["system_id"] == robot_two["id"]
        ]
        payload = metric_series(
            "Client Plant",
            {"start": "2026-03-23", "end": "2026-03-23"},
            "thickness",
            "daily",
            aliases=aliases,
        )
        clearcoat = [point for point in payload["points"] if point["series"] == "Clearcoat"]
        self.assertEqual(len(clearcoat), 1)
        self.assertGreaterEqual(clearcoat[0]["count"], 10)

    def test_sample_summary_and_system_filtered_api(self):
        sample = (
            ROOT
            / "tests"
            / "fixtures"
            / "2613616_W6_13_2_25529_R7_(2026-03-23 01;37;39).csv"
        )
        config = {
            "client": "Client Plant",
            "car_id_index": 0,
            "body_id_index": 2,
            "layer_names": {
                "3": ["Clearcoat", "Basecoat", "Primer"],
                "4": ["Clearcoat", "Basecoat", "Primer", "E-coat"],
                "5": ["Clearcoat", "Basecoat", "Primer", "E-coat", "Substrate"],
            },
        }
        summary = summarize_csv(sample, config)
        object_key = f"test-fixtures/{sample.name}"
        stored_csv = OBJECT_CACHE_DIR / object_key
        stored_csv.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sample, stored_csv)
        job_id = replace_job_summary(
            summary,
            {
                "filename": sample.name,
                "object_key": object_key,
                "sha256": sha256_path(sample),
                "size_bytes": sample.stat().st_size,
            },
        )
        self.assertIn("Robot_2_SN203", options("Client Plant")["sources"])
        robot_two = app_module.query_one("SELECT id FROM systems WHERE name='Robot 2'")
        response = self.client.get(
            f"/api/measurements/summary?location=Client%20Plant&system_id={robot_two['id']}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["jobs"], 1)
        self.assertGreater(response.get_json()["measurements"], 0)
        page = self.client.get(f"/measurements/system/{robot_two['id']}")
        self.assertEqual(page.status_code, 200)
        page_html = page.get_data(as_text=True)
        self.assertIn("Last 30 calendar days", page_html)
        self.assertNotIn('id="measurementCar"', page_html)
        self.assertNotIn('id="measurementSummaryText"', page_html)
        self.assertIn("measurement-chart-stack", page_html)
        self.assertIn('option value="weekly"', page_html)
        self.assertIn('id="measurementThicknessVariation"', page_html)
        self.assertNotIn('id="measurementThicknessTable"', page_html)
        self.assertIn('id="measurementPerformanceChart"', page_html)
        self.assertIn('data-performance-mode="count"', page_html)
        self.assertIn('data-performance-mode="percentage"', page_html)
        self.assertNotIn('id="measurementRateChart"', page_html)
        self.assertNotIn('id="measurementValidChart"', page_html)
        weekly = self.client.get(
            f"/api/measurements/metrics/thickness?location=Client%20Plant&system_id={robot_two['id']}&view=weekly"
        )
        self.assertEqual(weekly.status_code, 200)
        self.assertTrue(weekly.get_json()["points"])
        self.assertEqual(weekly.get_json()["points"][0]["x"], "2026-03-23")

        files_page = self.client.get("/measurements/files/Client%20Plant")
        self.assertEqual(files_page.status_code, 200)
        files_html = files_page.get_data(as_text=True)
        self.assertIn("Raw Measurement Files", files_html)
        self.assertIn("March", files_html)
        self.assertIn("Download ZIP", files_html)

        listing = self.client.get(
            "/api/measurements/files?location=Client%20Plant&year=2026&month=3"
        )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.get_json()["total"], 1)
        self.assertEqual(listing.get_json()["files"][0]["id"], job_id)

        individual = self.client.get(
            f"/measurements/files/file/{job_id}", buffered=True
        )
        try:
            self.assertEqual(individual.status_code, 200)
            self.assertEqual(individual.data, sample.read_bytes())
            self.assertIn("attachment", individual.headers["Content-Disposition"])
        finally:
            individual.close()

        archive = self.client.get(
            "/measurements/files/Client%20Plant/archive/2026/3", buffered=True
        )
        try:
            self.assertEqual(archive.status_code, 200)
            with zipfile.ZipFile(io.BytesIO(archive.data)) as bundle:
                names = bundle.namelist()
                self.assertEqual(
                    names,
                    [f"Client-Plant/2026/03/{sample.name}"],
                )
                self.assertEqual(bundle.read(names[0]), sample.read_bytes())
        finally:
            archive.close()

    def test_z_admin_location_rename_moves_measurement_history(self):
        response = self.client.put(
            "/api/admin/locations/Client%20Plant/rename", json={"name": "Client Plant Updated"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(app_module.query_one("SELECT id FROM systems WHERE location='Client Plant'"))
        self.assertIsNotNone(
            app_module.query_one("SELECT id FROM systems WHERE location='Client Plant Updated'")
        )
        with connect(ANALYTICS_DB) as connection:
            clients = [row[0] for row in connection.execute("SELECT DISTINCT client FROM jobs")]
        self.assertEqual(clients, ["Client Plant Updated"])


if __name__ == "__main__":
    unittest.main()
