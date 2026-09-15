from __future__ import annotations

import json
import unittest
from pathlib import Path


class VLABenchmarkContractTest(unittest.TestCase):
    def test_fixture_freezes_quality_gates(self) -> None:
        path = Path(__file__).resolve().parents[2] / "docs" / "benchmarks" / "vla-0.3.0.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["gates"]["max_model_text_budget_increase"], 0.05)
        self.assertEqual(data["gates"]["necessary_file_recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
