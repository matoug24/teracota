import os
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN_DB = ROOT / "codex_measurement_main_test.sqlite3"
ANALYTICS_TEST_DB = ROOT / "codex_measurement_analytics_test.sqlite3"
UPLOAD_TEST_DB = ROOT / "codex_measurement_upload_test.sqlite3"
for database_path in (MAIN_DB, ANALYTICS_TEST_DB, UPLOAD_TEST_DB):
    for candidate in (database_path, Path(f"{database_path}-wal"), Path(f"{database_path}-shm")):
        candidate.unlink(missing_ok=True)
os.environ.update(
    {
        "TERACOTA_DB_PATH": str(MAIN_DB),
        "TERACOTA_MEASUREMENT_DATA_DIR": str(ROOT),
        "TERACOTA_MEASUREMENT_ANALYTICS_DB": str(ANALYTICS_TEST_DB),
        "TERACOTA_MEASUREMENT_UPLOAD_DB": str(UPLOAD_TEST_DB),
        "TERACOTA_MEASUREMENT_UPLOAD_TOKEN": "test-measurement-token-longer-than-24-characters",
        "TERACOTA_SEED_DEMO": "false",
    }
)

import app as app_module  # noqa: E402
from measurements.config import save_source_mapping, source_mappings  # noqa: E402
from measurements.database import ANALYTICS_DB, connect, replace_job_summary  # noqa: E402
from measurements.queries import options  # noqa: E402
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
        cls.client = app_module.app.test_client()
        with cls.client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "teraview"

    @classmethod
    def tearDownClass(cls):
        for path in (MAIN_DB, ANALYTICS_TEST_DB, UPLOAD_TEST_DB):
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                candidate.unlink(missing_ok=True)

    def test_unknown_upload_client_is_rejected(self):
        response = self.client.post(
            "/api/uploads/prepare",
            headers={"Authorization": "Bearer test-measurement-token-longer-than-24-characters"},
            json={"client": "Unknown", "source_id": "test", "files": []},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("existing location", response.get_json()["error"])

    def test_reorganized_templates_and_assets_are_served(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        for asset_path in (
            "/assets/css/app.css",
            "/assets/js/app.js",
            "/assets/measurements/admin.css",
            "/assets/measurements/admin.js",
            "/assets/measurements/history.css",
            "/assets/measurements/history.js",
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
        replace_job_summary(
            summary,
            {
                "filename": sample.name,
                "object_key": "test/sample.csv",
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
        self.assertEqual(self.client.get(f"/measurements/system/{robot_two['id']}").status_code, 200)

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
