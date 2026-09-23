from pathlib import Path
import sqlite3
import unittest
from unittest import mock

from measurement_uploader import measurement_uploader as uploader


class MeasurementUploaderTests(unittest.TestCase):
    def test_large_discovery_is_split_into_server_sized_batches(self):
        root = Path(__file__).resolve().parent
        journal_path = root / "codex_uploader_test.sqlite3"
        lock_path = journal_path.with_suffix(journal_path.suffix + ".lock")
        for path in (journal_path, lock_path):
            path.unlink(missing_ok=True)
        try:
            files = [
                {
                    "path": root / f"measurement-{index:04d}.csv",
                    "name": f"measurement-{index:04d}.csv",
                    "size": 100 + index,
                    "mtime_ns": index,
                    "mtime": float(index),
                    "sha256": f"{index:064x}",
                }
                for index in range(1001)
            ]
            config = {
                "journal_path": str(journal_path),
                "source_id": "test-pc",
                "client": "Test Client",
                "expected_daily_files": 0,
                "max_attempts": 1,
                "retry_delay_seconds": 1,
            }
            prepared_sizes = []

            def request_json(_config, _method, route, payload=None):
                if route == "/api/uploads/prepare":
                    prepared_sizes.append(len(payload["files"]))
                    batch_id = f"batch-{len(prepared_sizes)}"
                    return {
                        "batch_id": batch_id,
                        "items": [
                            {"name": item["name"], "upload": {"skip": True}}
                            for item in payload["files"]
                        ],
                    }
                if route.endswith("/complete"):
                    return {"status": "VERIFIED"}
                raise AssertionError(f"Unexpected route: {route}")

            with (
                mock.patch.object(uploader, "discover", return_value=files),
                mock.patch.object(uploader, "reconcile"),
                mock.patch.object(uploader, "request_json", side_effect=request_json),
                mock.patch.object(uploader, "upload_file"),
            ):
                self.assertEqual(uploader.run(config), 0)

            self.assertEqual(prepared_sizes, [1000, 1])
            connection = sqlite3.connect(config["journal_path"])
            try:
                verified = connection.execute(
                    "SELECT COUNT(*) FROM uploaded_files WHERE status='VERIFIED'"
                ).fetchone()[0]
                last_scan = connection.execute(
                    "SELECT value FROM uploader_state WHERE key='last_successful_scan_date'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(verified, 1001)
            self.assertIsNotNone(last_scan)
        finally:
            for path in (journal_path, lock_path):
                path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
