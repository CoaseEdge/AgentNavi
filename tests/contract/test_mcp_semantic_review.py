from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.mcp.server import create_server
from agentnavi.utils import utc_now


class SemanticReviewContractTestCase(unittest.TestCase):
    def test_server_review_and_app_decision_persist_overlay_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            root = Path(temporary) / "project"
            root.mkdir()
            database = ensure_database(Settings.load(home))
            now = utc_now()
            with database.connect() as connection:
                connection.execute(
                    """INSERT INTO projects(id, name, root, kind, created_at, updated_at, last_scan_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("fixture", "Fixture", str(root), "software", now, now, now),
                )
                Database.upsert_node(
                    connection, project_id="fixture", layer=2, kind="concept", key="membership",
                    label="Membership", data={"active": True}, confidence=0.42, source="semantic-heuristic",
                )
                connection.commit()

            async def exercise() -> None:
                from mcp import Client

                async with Client(create_server(home=home), raise_exceptions=True) as client:
                    result = await client.call_tool("agentnavi_semantic_review", {"project_id": "fixture"})
                    self.assertFalse(result.is_error)
                    item = result.structured_content["data"]["reviewItems"][0]
                    self.assertEqual(item["allowedActions"], ["accept", "reject"])
                    self.assertEqual(item["evidence"][0]["layer"], "L2")
                    decision = await client.call_tool(
                        "agentnavi_review_decide",
                        {"review_id": item["reviewId"], "decision": "accept", "project_id": "fixture"},
                    )
                    self.assertFalse(decision.is_error)
                    with database.connect() as connection:
                        row = connection.execute(
                            "SELECT value_json FROM semantic_overlays WHERE project_id=?",
                            ("fixture",),
                        ).fetchone()
                        self.assertIsNotNone(row)
                        self.assertIn("evidence", row["value_json"])

            asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
