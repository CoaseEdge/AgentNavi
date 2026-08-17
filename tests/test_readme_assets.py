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
        self.assertIn("img.shields.io/github/last-commit/CoaseEdge/AgentNavi", content)
        self.assertIn("DeepSeek%20Harness-supported", content)
        self.assertIn("benchmark%20fixture-75%25", content)

    def test_license_metadata_is_consistently_apache_2_0(self) -> None:
        license_text = (self.root / "LICENSE").read_text(encoding="utf-8")
        project_text = (self.root / "pyproject.toml").read_text(encoding="utf-8")
        readme_text = self.readme.read_text(encoding="utf-8")

        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        self.assertIn('license = {text = "Apache-2.0"}', project_text)
        self.assertIn("License :: OSI Approved :: Apache Software License", project_text)
        self.assertTrue(readme_text.rstrip().endswith("Apache-2.0"))


if __name__ == "__main__":
    unittest.main()
