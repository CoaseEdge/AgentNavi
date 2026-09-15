from __future__ import annotations

import unittest

from agentnavi.benchmark import VLA_BENCHMARK_VIEWS, evaluate_vla_surface


class VLABenchmarkIntegrationTest(unittest.TestCase):
    def test_all_fixed_views_are_evaluated_together(self) -> None:
        payload = {
            view: {"required_paths": [], "returned_paths": [], "baseline_candidate_count": 0,
                   "candidate_count": 0, "baseline_model_tokens": 0, "model_tokens": 0,
                   "baseline_success": False, "success": True}
            for view in VLA_BENCHMARK_VIEWS
        }
        self.assertTrue(evaluate_vla_surface(payload)["passed"])


if __name__ == "__main__":
    unittest.main()
