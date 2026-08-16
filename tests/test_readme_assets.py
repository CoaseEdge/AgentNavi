from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


class ReadmeAssetTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.cover = self.root / "assets" / "readme" / "agentnavi-cover.svg"
        self.readme = self.root / "README.md"

    def test_cover_is_well_formed_local_svg_with_product_positioning(self) -> None:
        tree = ET.parse(self.cover)
        root = tree.getroot()
        self.assertTrue(root.tag.endswith("svg"))

        content = self.cover.read_text(encoding="utf-8")
        for phrase in (
            "AgentNavi",
            "Project Context Navigation Engine",
            "让 AI Agent 先看懂项目，再开始工作",
            "L1",
            "L2",
            "L3",
        ):
            self.assertIn(phrase, content)

        self.assertNotIn("<script", content.lower())
        for element in root.iter():
            for name, value in element.attrib.items():
                if name.endswith("href"):
                    self.assertFalse(value.startswith(("http://", "https://")))

    def test_readme_references_cover_and_repository_badges(self) -> None:
        content = self.readme.read_text(encoding="utf-8")
        self.assertIn("assets/readme/agentnavi-cover.svg", content)
        self.assertIn("actions/workflows/ci.yml/badge.svg", content)
        self.assertIn("img.shields.io/github/last-commit/Andrewlislin/AgentNavi", content)
        self.assertIn("DeepSeek%20Harness-supported", content)
        self.assertIn("benchmark%20fixture-75%25", content)


if __name__ == "__main__":
    unittest.main()
