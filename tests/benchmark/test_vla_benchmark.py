from __future__ import annotations

import unittest

from agentnavi.benchmark import VLA_BENCHMARK_VIEWS, evaluate_vla_surface


class VLABenchmarkTestCase(unittest.TestCase):
    def test_fixed_vla_surface_passes_when_quality_does_not_regress(self) -> None:
        results = {
            view: {
                "required_paths": [f"src/{view}.py"],
                "returned_paths": [f"src/{view}.py"],
                "candidate_count_budget": 4,
                "candidate_count": 4,
                "model_text_token_budget": 100,
                "model_tokens": 105,
                "view_contract_ok": True,
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
                "candidate_count_budget": 1,
                "candidate_count": 2,
                "model_text_token_budget": 100,
                "model_tokens": 106,
                "view_contract_ok": False,
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
                "required_paths": ["README.md"],
                "returned_paths": ["README.md"],
                "candidate_count_budget": 0,
                "candidate_count": 0,
                "model_text_token_budget": 0,
                "model_tokens": 0,
            }
            for view in VLA_BENCHMARK_VIEWS
        }
        report = evaluate_vla_surface(results)
        self.assertFalse(report["passed"])
        self.assertEqual(set(report["invalid_views"]), set(VLA_BENCHMARK_VIEWS))

    def test_benchmark_reports_view_contract_not_task_success(self) -> None:
        results = {
            view: {
                "required_paths": ["README.md"],
                "returned_paths": ["README.md"],
                "candidate_count_budget": 2,
                "candidate_count": 2,
                "model_text_token_budget": 100,
                "model_tokens": 100,
                "view_contract_ok": True,
                "measured": True,
            }
            for view in VLA_BENCHMARK_VIEWS
        }
        report = evaluate_vla_surface(results)
        self.assertTrue(report["passed"])
        self.assertTrue(report["view_contract_ok"])
        self.assertNotIn("task_success_not_lower", report)

    def test_candidate_expansion_uses_fixture_gate(self) -> None:
        results = {
            view: {
                "required_paths": ["README.md"],
                "returned_paths": ["README.md"],
                "candidate_count_budget": 2,
                "candidate_count": 3,
                "model_text_token_budget": 100,
                "model_tokens": 100,
                "view_contract_ok": True,
                "measured": True,
            }
            for view in VLA_BENCHMARK_VIEWS
        }
        report = evaluate_vla_surface(results, gates={
            "necessary_file_recall": 1.0,
            "candidate_set_expansion": 0.5,
            "max_model_text_budget_increase": 0.05,
        })
        self.assertTrue(report["passed"])


if __name__ == "__main__":
    unittest.main()
