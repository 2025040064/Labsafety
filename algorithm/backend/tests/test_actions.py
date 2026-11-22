"""Checks that mini-program actions land on the same parameters as the desktop app."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine/src"))

import actions


class ActionTest(unittest.TestCase):
    def setUp(self):
        self.experiment, self.industrial, self.loop = Mock(), Mock(), Mock()
        self.patch = patch.object(actions, "services",
                                  return_value=(self.experiment, self.industrial, self.loop))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.progress = Mock()
        self.cancel = Mock(return_value=False)

    def call_action(self, action, params=None):
        return actions.run_action(Path.cwd(), action, params or {}, self.progress,
                                  self.cancel, "local")

    def test_number_and_seconds_ranges(self):
        self.assertEqual(actions.number({"seed": 42}, "seed", 42, 0, 100), 42)
        for value in (-1, 2147483648, True, "42", 1.5):
            with self.assertRaises(ValueError):
                actions.number({"seed": value}, "seed", 42, 0, 2147483647)
        self.assertEqual(actions.seconds({}, "timeout", 120.0, 0.1, 86400), 120.0)
        for value in (0, 86401, "120", True):
            with self.assertRaises(ValueError):
                actions.seconds({"timeout": value}, "timeout", 120, 0.1, 86400)

    def test_train_uses_desktop_defaults(self):
        self.call_action("train", {"dataset": "steel_energy", "seed": 42, "estimators": 300})
        call = self.experiment.run_experiment.call_args
        self.assertEqual(call[0][0], "baseline")
        overrides = call[0][2]
        self.assertEqual(overrides["model"]["n_estimators"], 300)
        self.assertEqual(overrides["model"]["n_jobs"], 4)
        self.assertEqual(overrides["experiment"]["seed"], 42)
        self.assertEqual(overrides["optimization"]["max_trials"], 12)
        self.assertEqual(overrides["optimization"]["timeout_seconds"], 120)
        self.assertNotIn("early_stopping_rounds", overrides["model"])

    def test_optimize_passes_algorithm_and_budget(self):
        self.call_action("optimize", {"dataset": "cnc_milling_2025", "algorithm": "rac_hpo",
                              "trials": 48, "timeout": 300})
        call = self.experiment.run_experiment.call_args
        self.assertEqual(call[0][0], "rac_hpo")
        self.assertEqual(call[0][2]["optimization"], {"max_trials": 48, "timeout_seconds": 300.0})

    def test_industrial_defaults_match_desktop(self):
        self.call_action("industrial_run", {"dataset": "ai4i_2020", "model": "xgboost",
                                    "algorithm": "tpe", "decision": "time_oof"})
        call = self.industrial.run.call_args
        self.assertEqual(call[0][:6], ("ai4i_2020", "xgboost", "tpe", None, 42, 24))
        self.assertEqual(call[0][6], 300.0)
        self.assertEqual(call[0][7], {"n_estimators": 120, "n_jobs": 4,
                                      "decision_mode": "time_oof"})

    def test_loop_create_validates_policy_and_forwards_manual_demo(self):
        self.loop.create_session.return_value = {"id": "LOOP-test",
                                                 "authorized_policy_sha256": "abc"}
        self.call_action("loop_create", {"authorize": True, "manual": True,
                                 "policy": {"scenario": "feature_shift", "max_trials": 12,
                                            "timeout_seconds": 60, "training_budget_seconds": 120}})
        policy = self.loop.create_session.call_args[0][2]
        self.assertEqual(policy["max_trials"], 12)
        self.assertEqual(policy["training_budget_seconds"], 120.0)
        self.assertEqual(self.loop.run_session.call_args[1]["manual_demo"], True)
        with self.assertRaises(ValueError):
            self.call_action("loop_create", {"policy": {"max_trials": 49}})

    def test_loop_run_forwards_manual_demo(self):
        self.call_action("loop_run", {"id": "LOOP-abc", "manual": True})
        self.assertEqual(self.loop.run_session.call_args[1]["manual_demo"], True)

    def test_upload_path_uses_directory_for_multiple_files(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd() / "rac-data") as root:
            directory = Path(root) / "uploads" / "UP-test"
            directory.mkdir(parents=True)
            (directory / "part1.csv").write_text("a\n", encoding="utf-8")
            self.assertEqual(actions.upload_path(root, "UP-test"), directory / "part1.csv")
            (directory / "part2.mat").write_text("b\n", encoding="utf-8")
            self.assertEqual(actions.upload_path(root, "UP-test"), directory)
            (directory / "part2.mat").unlink()
            self.assertEqual(actions.upload_path(root, "UP-test"), directory / "part1.csv")


if __name__ == "__main__":
    unittest.main()
