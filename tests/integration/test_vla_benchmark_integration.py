from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from agentnavi.benchmark import VLA_BENCHMARK_VIEWS, evaluate_vla_surface, run_vla_benchmark
from agentnavi.config import Settings
from agentnavi.database import ensure_database
from agentnavi.engine import scan_project
from agentnavi.registry import add_project


class VLABenchmarkIntegrationTest(unittest.TestCase):
    def test_empty_fixture_measurements_are_rejected_for_all_views(self) -> None:
        payload = {
            view: {"required_paths": [], "returned_paths": [], "baseline_candidate_count": 0,
                   "candidate_count": 0, "baseline_model_tokens": 0, "model_tokens": 0,
                   "baseline_success": False, "success": True}
            for view in VLA_BENCHMARK_VIEWS
        }
        report = evaluate_vla_surface(payload)
        self.assertFalse(report["passed"])
        self.assertEqual(set(report["invalid_views"]), set(VLA_BENCHMARK_VIEWS))

    def test_runner_executes_real_core_and_adapter_views(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "fixture"
            (root / "docs").mkdir(parents=True)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "README.md").write_text(
                "# Fixture\n\n## Purpose\nA small benchmark fixture.\n\n## Workflow\n1. Read docs\n2. Run code\n",
                encoding="utf-8",
            )
            (root / "docs" / "architecture.md").write_text(
                "# Architecture\n\n## Flow\n1. Read\n2. Execute\n", encoding="utf-8"
            )
            (root / "src" / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
            (root / "tests" / "test_app.py").write_text("def test_app():\n    assert True\n", encoding="utf-8")
            (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
            database = ensure_database(Settings.load(Path(directory) / "home"))
            project = add_project(database, root, project_id="fixture")
            scan_project(database, project, full=True)
            fixture = {
                "gates": {"necessary_file_recall": 1.0, "max_model_text_budget_increase": 0.05},
                "cases": [
                    {
                        "view": "repo-overview", "required_paths": ["README.md"],
                        "required_observations": ["data.purpose.summary"],
                        "baseline_candidate_count": 4, "baseline_model_tokens": 100,
                        "baseline_success": True,
                    },
                    {
                        "view": "repo-tour", "required_paths": ["README.md"],
                        "required_observations": ["data.tiers"],
                        "baseline_candidate_count": 4, "baseline_model_tokens": 100,
                        "baseline_success": True,
                    },
                    {
                        "view": "context", "query": "app", "required_paths": ["src/app.py"],
                        "required_observations": ["data.files"], "baseline_candidate_count": 4,
                        "baseline_model_tokens": 100, "baseline_success": True,
                    },
                    {
                        "view": "impact", "selector": "src/app.py", "required_paths": ["src/app.py"],
                        "required_observations": ["data.focus"], "baseline_candidate_count": 4,
                        "baseline_model_tokens": 100, "baseline_success": True,
                    },
                    {
                        "view": "history", "query": "", "required_paths": ["README.md"],
                        "required_observations": ["data.timeline"], "baseline_candidate_count": 4,
                        "baseline_model_tokens": 100, "baseline_success": True,
                    },
                    {
                        "view": "semantic-review", "required_paths": ["README.md"],
                        "required_observations": ["data.stats"], "baseline_candidate_count": 4,
                        "baseline_model_tokens": 100, "baseline_success": True,
                    },
                ],
            }
            fixture_path = Path(directory) / "vla.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            report = run_vla_benchmark(database, project, fixture_path=fixture_path)

        self.assertEqual(set(report["observations"]), set(VLA_BENCHMARK_VIEWS))
        self.assertIn("metrics", report)


if __name__ == "__main__":
    unittest.main()
