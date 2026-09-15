from __future__ import annotations

from pathlib import Path
import unittest


class VLAIntegrationPolicyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]

    def test_policy_freezes_tool_routing_and_boundaries(self) -> None:
        policy = (self.root / "docs" / "vla.md").read_text(encoding="utf-8")
        for term in (
            "agentnavi_context", "agentnavi_impact", "agentnavi_history",
            "agentnavi_semantic_review", "agentnavi_visualize", "agentnavi_review_decide",
            "content", "structuredContent", "文本 fallback", "POSIX 相对路径",
        ):
            self.assertIn(term, policy)
        self.assertIn("不能访问 SQLite", policy)

    def test_generic_skill_is_present_and_does_not_authorize_database_access(self) -> None:
        skill = (self.root / "integrations" / "vla" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: agentnavi-vla", skill)
        self.assertIn("agentnavi_visualize", skill)
        self.assertIn("不能访问 SQLite", skill)
        self.assertNotIn("innerHTML", skill)


if __name__ == "__main__":
    unittest.main()
