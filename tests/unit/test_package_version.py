from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

import agentnavi


class PackageVersionTestCase(unittest.TestCase):
    def test_runtime_version_matches_project_metadata(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with (root / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]

        self.assertEqual(agentnavi.__version__, project["version"])


if __name__ == "__main__":
    unittest.main()
