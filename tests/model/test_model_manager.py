"""
tests/model/test_model_manager.py

Runs against the real configs/solutions/*.yaml files -- no mocking.
These files are stable, checked-in data, so testing against them
directly (rather than a fixture) catches real drift if a solution's
module/class fields ever get renamed or removed without updating this.

Run from repo root:
    python3 -m unittest tests.model.test_model_manager -v
"""
import sys
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))

from framework.model.model_manager import ModelManager, list_available_solutions


class TestListAvailableSolutions(unittest.TestCase):
    def test_returns_all_three_registered_solutions(self):
        names = list_available_solutions()
        self.assertIn("ppe_industrial", names)
        self.assertIn("driver_monitoring", names)
        self.assertIn("healthcare_monitoring", names)

    def test_returns_sorted_list(self):
        names = list_available_solutions()
        self.assertEqual(names, sorted(names))


class TestModelManagerLoad(unittest.TestCase):
    def test_load_ppe_industrial(self):
        instance, pgie_path = ModelManager.load("ppe_industrial")
        self.assertEqual(type(instance).__name__, "PPEIndustrialSolution")
        self.assertTrue(pgie_path.endswith("config_infer_primary_ppe.txt"))
        self.assertTrue(os.path.isfile(pgie_path))

    def test_load_driver_monitoring(self):
        instance, pgie_path = ModelManager.load("driver_monitoring")
        self.assertEqual(type(instance).__name__, "DriverMonitoringSolution")
        self.assertTrue(pgie_path.endswith("config_infer_primary_driver.txt"))
        self.assertTrue(os.path.isfile(pgie_path))

    def test_load_healthcare_monitoring(self):
        instance, pgie_path = ModelManager.load("healthcare_monitoring")
        self.assertEqual(type(instance).__name__, "HealthcareMonitoringSolution")
        self.assertTrue(pgie_path.endswith("config_infer_primary_healthcare.txt"))
        self.assertTrue(os.path.isfile(pgie_path))

    def test_load_returns_fresh_instance_each_call(self):
        instance_a, _ = ModelManager.load("ppe_industrial")
        instance_b, _ = ModelManager.load("ppe_industrial")
        self.assertIsNot(instance_a, instance_b)

    def test_load_unknown_solution_raises_value_error(self):
        with self.assertRaises(ValueError):
            ModelManager.load("does_not_exist")

    def test_load_unknown_solution_error_message_is_actionable(self):
        with self.assertRaises(ValueError) as ctx:
            ModelManager.load("does_not_exist")
        self.assertIn("does_not_exist", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
