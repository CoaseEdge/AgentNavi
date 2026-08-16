from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


class ReleaseMetadataTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_current_release_matches_package_and_notes(self) -> None:
        env_text = (self.root / ".github" / "release" / "current.env").read_text(encoding="utf-8")
        tag_match = re.search(r"^TAG=(v[^\s]+)$", env_text, re.MULTILINE)
        self.assertIsNotNone(tag_match)
        tag = tag_match.group(1)

        with (self.root / "pyproject.toml").open("rb") as handle:
            version = tomllib.load(handle)["project"]["version"]
        self.assertEqual(tag, f"v{version}")

        notes = self.root / ".github" / "release" / f"{tag}.md"
        self.assertTrue(notes.is_file())
        notes_text = notes.read_text(encoding="utf-8")
        self.assertIn(f"AgentNavi {tag}", notes_text)
        self.assertIn("Apache License 2.0", notes_text)

        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"## {version} —", changelog)


if __name__ == "__main__":
    unittest.main()
