from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from agentnavi.benchmark import VLA_BENCHMARK_VIEWS, evaluate_vla_surface, run_vla_benchmark
from agentnavi.config import Settings
from agentnavi.database import ensure_database
from agentnavi.engine import scan_project
from agentnavi.registry import add_project
from agentnavi.tasks import close_task, create_task, record_event


class VLABenchmarkIntegrationTest(unittest.TestCase):
    def test_empty_fixture_measurements_are_rejected_for_all_views(self) -> None:
        payload = {
            view: {"required_paths": [], "returned_paths": [], "candidate_count_budget": 0,
                   "candidate_count": 0, "model_text_token_budget": 0, "model_tokens": 0,
                   "view_contract_ok": True}
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
            task = create_task(database, project_id="fixture", title="design task", prompt="design")
            record_event(
                database, project=project, agent="fixture", event_type="read",
                task_id=task["id"], paths=["README.md"],
            )
            close_task(database, task["id"], summary="design completed")
            fixture_path = Path(__file__).parents[1] / "fixtures" / "vla-0.3.0.json"
            report = run_vla_benchmark(database, project, fixture_path=fixture_path)

        self.assertEqual(set(report["observations"]), set(VLA_BENCHMARK_VIEWS))
        self.assertIn("metrics", report)
        self.assertTrue(report["passed"], report)
        self.assertTrue(all(item["measured"] for item in report["observations"].values()))


if __name__ == "__main__":
    unittest.main()
