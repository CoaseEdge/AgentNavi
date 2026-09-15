from __future__ import annotations

import re
import tomllib
import unittest
from importlib.resources import files
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

    def test_vla_release_metadata_is_current_and_builds_artifacts(self) -> None:
        readme = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertIn("version-0.3.0", readme)
        with (self.root / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]
        self.assertEqual(project["urls"]["Repository"], "https://github.com/CoaseEdge/AgentNavi")
        workflow = (self.root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("python -m build --sdist --wheel", workflow)
        self.assertIn("dist/*", workflow)
        skill = files("agentnavi").joinpath("resources", "skills", "vla", "SKILL.md")
        self.assertTrue(skill.is_file())


if __name__ == "__main__":
    unittest.main()
