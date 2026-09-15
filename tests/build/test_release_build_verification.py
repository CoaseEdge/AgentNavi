from __future__ import annotations

import unittest
from pathlib import Path


class ReleaseBuildVerificationTest(unittest.TestCase):
    def test_project_version_is_030(self) -> None:
        text = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "0.3.0"', text)


if __name__ == "__main__":
    unittest.main()
