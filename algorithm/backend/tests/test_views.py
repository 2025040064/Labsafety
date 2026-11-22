"""Regression checks for the compact workspace views."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from views import (charts, compare_rows, event_rows, filter_rows, paginate, run_summary,
                   trial_rows)


def trial(number, objective, params=None, status="completed", mae=None):
    return {"number": number, "objective": objective, "status": status,
            "params": params or {"learning_rate": 0.05, "num_leaves": 20},
            "metrics": {"overall_mae": mae if mae is not None else objective},
            "duration_seconds": 1.0 + number / 10}


def detail():
    return {"id": "EXP-test", "algorithm": "tpe", "target_unit": "kWh",
            "status": "completed", "best_params": {"learning_rate": 0.02},
            "config": {"experiment": {"seed": 42}, "optimization": {"max_trials": 4}},
            "trials": [trial(-1, 30.0, {}), trial(1, 25.0, mae=24.0),
                       trial(2, 22.0, mae=21.0), trial(3, 24.0, mae=23.0),
                       trial(4, None, status="failed")]}


class ViewTest(unittest.TestCase):
    def test_trial_rows_excludes_baseline_and_keeps_compact_fields(self):
        rows = trial_rows(detail())
        self.assertEqual([row["number"] for row in rows], [1, 2, 3, 4])
        self.assertEqual(rows[1]["objective"], 22.0)
        self.assertEqual(rows[1]["mae"], 21.0)
        self.assertIsNone(rows[3]["objective"])
        self.assertIn("learning_rate", rows[0]["params"])

    def test_summary_drops_full_trials_but_keeps_counts_and_baseline(self):
        summary = run_summary(detail())
        self.assertNotIn("trials", summary)
        self.assertEqual(summary["trial_count"], 4)
        self.assertEqual(summary["baseline"]["objective"], 30.0)
        self.assertNotIn("history", summary["charts"])
        self.assertIn("objective_label", summary["charts"])
        self.assertEqual(summary["charts"]["parameters"], ["learning_rate", "num_leaves"])

    def test_history_keeps_full_sequence_and_monotonic_best(self):
        series = charts(detail())
        self.assertEqual([item["number"] for item in series["history"]], [1, 2, 3, 4])
        self.assertEqual([item["best"] for item in series["history"]], [25.0, 22.0, 22.0, 22.0])
        self.assertEqual(series["distribution"]["count"], 3)
        self.assertEqual(sum(item["count"] for item in series["distribution"]["bins"]), 3)

    def test_filtered_charts_only_change_selected_charts(self):
        series = charts(detail(), [1, 2])
        self.assertEqual(len(series["history"]), 4)
        self.assertEqual(series["distribution"]["count"], 2)
        self.assertEqual(series["filtered_numbers"], [1, 2])
        self.assertEqual(len(series["parallel"]["rows"]), 2)
        self.assertTrue(all(0 <= value <= 1 for row in series["parallel"]["rows"]
                            for value in row["values"] if value is not None))

    def test_filter_by_query_status_and_parameter_range(self):
        rows = trial_rows(detail())
        self.assertEqual(len(filter_rows(rows, query="num_leaves")), 4)
        self.assertEqual(len(filter_rows(rows, status="failed")), 1)
        self.assertEqual(len(filter_rows(rows, param="learning_rate", low=0.04, high=0.06)), 4)
        self.assertEqual(len(filter_rows(rows, param="learning_rate", low=0.06)), 0)

    def test_sort_places_missing_values_last_and_paging_bounds(self):
        rows = trial_rows(detail())
        descending = filter_rows(rows, sort="objective", order="desc")
        self.assertEqual([row["number"] for row in descending], [1, 3, 2, 4])
        page = paginate(rows, page=2, size=2)
        self.assertEqual((page["total"], page["page"], page["size"]), (4, 2, 2))
        self.assertEqual([row["number"] for row in page["rows"]], [3, 4])
        self.assertEqual(paginate(rows, page=99, size=2)["rows"], [])
        self.assertEqual(paginate(rows, size=500)["size"], 100)

    def test_compare_rows_exposes_data_and_version_evidence(self):
        rows = compare_rows([detail()])
        self.assertEqual(rows[0]["seed"], 42)
        self.assertEqual(rows[0]["max_trials"], 4)
        self.assertEqual(rows[0]["target_unit"], "kWh")
        self.assertIsNone(rows[0]["data_sha256"])

    def test_event_search_returns_match_count_and_page(self):
        events = [{"type": "started"}, {"type": "trial_completed", "number": 1},
                  {"type": "trial_completed", "number": 2}]
        result = event_rows(events, query="trial_completed", page=1, size=1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["rows"][0]["number"], 1)
        self.assertEqual(event_rows(events, query="trial_completed", page=2, size=1)["rows"][0]["number"], 2)


if __name__ == "__main__":
    unittest.main()
