"""
tests/database/test_data_manager.py

Runs against a fresh temp SQLite file per test -- never touches any
real platform.db.

Run from repo root:
    python3 -m unittest tests.database.test_data_manager -v
"""
import sys
import os
import tempfile
import time
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))

from framework.database.data_manager import DataManager


class DataManagerTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.remove(self.db_path)
        self.dm = DataManager(db_path=self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)


class TestLogEvent(DataManagerTestCase):
    def test_log_event_persists_all_fields(self):
        self.dm.log_event(
            camera_slot="0", solution="ppe_industrial", event_type="no_helmet",
            person_id="1", screenshot_path="123_1_no_helmet.jpg", timestamp=1000.5,
        )
        rows = self.dm.get_recent_events(limit=10)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["camera_slot"], "0")
        self.assertEqual(row["solution"], "ppe_industrial")
        self.assertEqual(row["event_type"], "no_helmet")
        self.assertEqual(row["person_id"], "1")
        self.assertEqual(row["screenshot_path"], "123_1_no_helmet.jpg")
        self.assertEqual(row["timestamp"], 1000.5)

    def test_log_event_handles_underscore_containing_person_id(self):
        self.dm.log_event(
            camera_slot="0", solution="driver_monitoring", event_type="no_seatbelt",
            person_id="driver_seatbelt", screenshot_path="456_driver_seatbelt_no_seatbelt.jpg",
        )
        rows = self.dm.get_recent_events(limit=10)
        self.assertEqual(rows[0]["person_id"], "driver_seatbelt")
        self.assertEqual(rows[0]["event_type"], "no_seatbelt")

    def test_log_event_defaults_timestamp_to_now_when_omitted(self):
        before = time.time()
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_vest")
        after = time.time()
        row = self.dm.get_recent_events(limit=1)[0]
        self.assertGreaterEqual(row["timestamp"], before)
        self.assertLessEqual(row["timestamp"], after)

    def test_log_event_upload_style_camera_slot(self):
        self.dm.log_event(
            camera_slot="upload_d53fb4792c134d989dc7c5b59f7e249d",
            solution="ppe_industrial", event_type="no_vest", person_id="1",
        )
        row = self.dm.get_recent_events(limit=1)[0]
        self.assertTrue(row["camera_slot"].startswith("upload_"))


class TestGetRecentEvents(DataManagerTestCase):
    def test_returns_most_recent_first(self):
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_helmet", timestamp=100)
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_vest", timestamp=200)
        rows = self.dm.get_recent_events(limit=10)
        self.assertEqual(rows[0]["event_type"], "no_vest")
        self.assertEqual(rows[1]["event_type"], "no_helmet")

    def test_limit_is_respected(self):
        for i in range(5):
            self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_helmet", timestamp=float(i))
        rows = self.dm.get_recent_events(limit=2)
        self.assertEqual(len(rows), 2)

    def test_filters_by_camera_slot(self):
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_helmet")
        self.dm.log_event(camera_slot="1", solution="ppe_industrial", event_type="no_vest")
        rows = self.dm.get_recent_events(camera_slot="0")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["camera_slot"], "0")

    def test_filters_by_solution(self):
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_helmet")
        self.dm.log_event(camera_slot="0", solution="driver_monitoring", event_type="drowsy")
        rows = self.dm.get_recent_events(solution="driver_monitoring")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["solution"], "driver_monitoring")


class TestClearScreenshotPaths(DataManagerTestCase):
    def test_nulls_screenshot_path_but_keeps_event_rows(self):
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_helmet", screenshot_path="a.jpg")
        self.dm.clear_screenshot_paths()
        rows = self.dm.get_recent_events(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["screenshot_path"])

    def test_returns_the_paths_that_were_cleared(self):
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_helmet", screenshot_path="a.jpg")
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_vest", screenshot_path="b.jpg")
        cleared = self.dm.clear_screenshot_paths()
        self.assertCountEqual(cleared, ["a.jpg", "b.jpg"])


class TestDeleteEventsMatchingCameraPrefix(DataManagerTestCase):
    def test_deletes_only_matching_prefix(self):
        self.dm.log_event(camera_slot="upload_abc123", solution="ppe_industrial", event_type="no_helmet")
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_vest")
        deleted_count = self.dm.delete_events_matching_camera_prefix("upload_")
        self.assertEqual(deleted_count, 1)
        remaining = self.dm.get_recent_events(limit=10)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["camera_slot"], "0")


class TestGetEventCounts(DataManagerTestCase):
    def test_counts_grouped_by_event_type(self):
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_helmet")
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_helmet")
        self.dm.log_event(camera_slot="0", solution="ppe_industrial", event_type="no_vest")
        counts = self.dm.get_event_counts()
        self.assertEqual(counts["no_helmet"], 2)
        self.assertEqual(counts["no_vest"], 1)


if __name__ == "__main__":
    unittest.main()
