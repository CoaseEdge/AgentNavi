from __future__ import annotations

import unittest
from pathlib import Path


class Release030ContractTest(unittest.TestCase):
    def test_release_metadata_is_present(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.assertIn("TAG=v0.3.0", (root / ".github" / "release" / "current.env").read_text())
        self.assertTrue((root / ".github" / "release" / "v0.3.0.md").is_file())


if __name__ == "__main__":
    unittest.main()
