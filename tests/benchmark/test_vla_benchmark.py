from __future__ import annotations

import unittest

from agentnavi.benchmark import VLA_BENCHMARK_VIEWS, evaluate_vla_surface


class VLABenchmarkTestCase(unittest.TestCase):
    def test_fixed_vla_surface_passes_when_quality_does_not_regress(self) -> None:
        results = {
            view: {
                "required_paths": [f"src/{view}.py"],
                "returned_paths": [f"src/{view}.py"],
                "baseline_candidate_count": 4,
                "candidate_count": 4,
                "baseline_model_tokens": 100,
                "model_tokens": 105,
                "baseline_success": True,
                "success": True,
                "measured": True,
            }
            for view in VLA_BENCHMARK_VIEWS
        }
        report = evaluate_vla_surface(results)
        self.assertTrue(report["passed"])
        self.assertEqual(report["necessary_file_recall"], 1.0)

    def test_benchmark_rejects_recall_or_candidate_regression(self) -> None:
        results = {
            view: {
                "required_paths": ["src/required.py"],
                "returned_paths": [],
                "baseline_candidate_count": 1,
                "candidate_count": 2,
                "baseline_model_tokens": 100,
                "model_tokens": 106,
                "baseline_success": True,
                "success": False,
                "measured": True,
            }
            for view in VLA_BENCHMARK_VIEWS
        }
        report = evaluate_vla_surface(results)
        self.assertFalse(report["passed"])
        self.assertEqual(report["necessary_file_recall"], 0.0)

    def test_empty_measurements_never_pass(self) -> None:
        results = {
            view: {
                "required_paths": [],
                "returned_paths": [],
                "baseline_candidate_count": 0,
                "candidate_count": 0,
                "baseline_model_tokens": 0,
                "model_tokens": 0,
                "baseline_success": False,
                "success": False,
            }
            for view in VLA_BENCHMARK_VIEWS
        }
        report = evaluate_vla_surface(results)
        self.assertFalse(report["passed"])
        self.assertEqual(set(report["invalid_views"]), set(VLA_BENCHMARK_VIEWS))


if __name__ == "__main__":
    unittest.main()
