from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        source_path = str(self.root / "src")
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            f"{source_path}{os.pathsep}{existing}" if existing else source_path
        )
        return environment

    def test_module_entrypoint_initializes_an_external_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            home = Path(temporary_directory) / "agentnavi-home"
            result = subprocess.run(
                [sys.executable, "-m", "agentnavi", "--home", str(home), "init"],
                cwd=self.root,
                env=self._environment(),
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("AgentNavi 已初始化", result.stdout)
            self.assertTrue((home / "agentnavi.db").is_file())
            self.assertTrue((home / "events.jsonl").is_file())
            self.assertTrue((home / "semantic-overlays.jsonl").is_file())

    def test_repository_governance_contract_is_materialized(self) -> None:
        config_path = self.root / ".repo-governance.json"
        workflow_path = self.root / ".github" / "workflows" / "repo-governance.yml"

        self.assertTrue(config_path.is_file(), "缺少 Repo Governance 配置")
        self.assertTrue(workflow_path.is_file(), "缺少 Repo Governance CI caller")

        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["preset"]["name"], "python-service")
        engine_commit = config["engineCommitSha"]
        self.assertRegex(engine_commit, re.compile(r"^[0-9a-f]{40}$"))
        self.assertIn(engine_commit, workflow_path.read_text(encoding="utf-8"))

    def test_repository_governance_covers_all_product_source_and_test_trees(self) -> None:
        config = json.loads(
            (self.root / ".repo-governance.json").read_text(encoding="utf-8")
        )
        categories = config["testCategories"]
        mappings = config["changeCategoryMappings"]

        self.assertIn("tests/test_*.py", categories["integration"])
        self.assertIn(
            "integrations/deepseek-harness/test/**",
            categories["command-contract"],
        )
        for path in ("ui/test/**", "ui/tests/**"):
            self.assertIn(path, categories["unit"])

        product_sources = {
            "src/**",
            "integrations/deepseek-harness/src/**",
            "ui/src/**",
        }
        self.assertTrue(product_sources.issubset(mappings["source"]))
        high_impact_sources = {
            path
            for mapping in config["highImpactMappings"]
            for path in mapping["businessPaths"]
        }
        self.assertTrue(product_sources.issubset(high_impact_sources))

        non_python_tests = {
            "integrations/deepseek-harness/test/**",
            "ui/test/**",
            "ui/tests/**",
        }
        self.assertTrue(non_python_tests.issubset(mappings["tests"]))
        package_manifests = {
            "integrations/deepseek-harness/package*.json",
            "ui/package*.json",
        }
        self.assertTrue(package_manifests.issubset(mappings["dependencies"]))


if __name__ == "__main__":
    unittest.main()
