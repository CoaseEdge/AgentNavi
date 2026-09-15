from __future__ import annotations

import unittest
from pathlib import Path


class Release030IntegrationTest(unittest.TestCase):
    def test_changelog_mentions_vla_release(self) -> None:
        root = Path(__file__).resolve().parents[2]
        text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## 0.3.0", text)
        self.assertIn("VLA", text)


if __name__ == "__main__":
    unittest.main()
