from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseBuildVerificationTest(unittest.TestCase):
    def test_build_metadata_declares_python_311_minimum_and_mcp_extra(self) -> None:
        text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.11"', text)
        self.assertIn('mcp = ["mcp>=2,<3"]', text)


if __name__ == "__main__":
    unittest.main()
