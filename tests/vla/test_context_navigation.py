from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.engine import scan_project
from agentnavi.mcp.adapters.context import context_text, context_view
from agentnavi.query import context_data
from agentnavi.registry import add_project, resolve_project
from agentnavi.utils import utc_now


class ContextNavigationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.root = self.base / "private" / "fixture"
        self.root.mkdir(parents=True)
        files = {
            "pyproject.toml": "[project]\nname='fixture'\nversion='0.1.0'\n",
            "src/payment/service.py": "def charge(value):\n    return value > 0\n",
            "src/membership/upgrade.py": (
                "from src.payment.service import charge\n\n"
                "def upgrade(value):\n    return charge(value)\n"
            ),
            "tests/test_upgrade.py": (
                "from src.membership.upgrade import upgrade\n\n"
                "def test_upgrade():\n    assert upgrade(1)\n"
            ),
            "docs/membership.md": (
                "# 会员升级\n\n会员升级依赖[\u652f\u4ed8](../src/payment/service.py)。\n"
            ),
        }
        for relative, content in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.database = ensure_database(Settings.load(self.base / "agentnavi-home"))
        project = add_project(self.database, self.root)
        scan_project(self.database, project, full=True)
        self.project = resolve_project(self.database, project["id"])

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_navigation_keeps_recall_and_existing_candidate_bounds(self) -> None:
        data = context_data(
            self.database, self.project, "修改会员升级和支付逻辑"
        )

        paths = [item["path"] for item in data["files"]]
        self.assertLessEqual(len(paths), 12)
        self.assertLessEqual(len(data["concepts"]), 5)
        self.assertTrue(all(len(item["neighbors"]) <= 10 for item in data["concepts"]))
        self.assertTrue({
            "src/membership/upgrade.py",
            "src/payment/service.py",
            "tests/test_upgrade.py",
        }.issubset(paths))
        reading = data["navigation"]["readingOrder"]
        self.assertTrue(reading)
        self.assertEqual(reading[0]["path"], "src/membership/upgrade.py")
        self.assertEqual(
            [item["position"] for item in reading], list(range(1, len(reading) + 1))
        )
        self.assertTrue(set(item["path"] for item in reading).issubset(paths))
        for index, item in enumerate(reading):
            self.assertTrue(item["why"])
            self.assertTrue(item["evidence"])
            self.assertEqual(
                [action["label"] for action in item["actions"]],
                ["它做什么", "为什么相关", "谁依赖它", "过去谁改过", "如果改它"],
            )
            expected_next = reading[index + 1]["path"] if index + 1 < len(reading) else None
            actual_next = item["nextStep"]["path"] if item["nextStep"] else None
            self.assertEqual(actual_next, expected_next)

    def test_one_hop_chain_uses_real_nodes_edges_and_direction(self) -> None:
        data = context_data(
            self.database, self.project, "修改会员升级和支付逻辑"
        )
        payment = next(
            item for item in data["navigation"]["readingOrder"]
            if item["path"] == "src/payment/service.py"
        )
        chain = next(
            item for item in payment["chains"]
            if item["conceptRelation"] is not None
        )
        self.assertEqual(chain["sourceConcept"]["label"], "会员升级")
        self.assertEqual(chain["conceptRelation"]["relation"], "depends_on")
        self.assertEqual(chain["fileRelation"]["relation"], "implemented_by")
        self.assertEqual(chain["file"]["path"], "src/payment/service.py")
        self.assertEqual(
            chain["relatedConcept"]["evidence"][0]["source"],
            chain["conceptRelation"]["source"],
        )
        with self.database.connect() as connection:
            for edge in (chain["conceptRelation"], chain["fileRelation"]):
                row = connection.execute(
                    "SELECT * FROM edges WHERE id=?", (edge["id"],)
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["source_id"], edge["sourceId"])
                self.assertEqual(row["target_id"], edge["targetId"])
                self.assertEqual(row["relation"], edge["relation"])
            for entity in (
                chain["sourceConcept"], chain["relatedConcept"], chain["file"]
            ):
                row = connection.execute(
                    "SELECT * FROM nodes WHERE id=?", (entity["id"],)
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row["label"], entity["label"])

    def test_sorting_and_caps_are_stable_before_navigation_projection(self) -> None:
        with self.database.connect() as connection:
            concept = connection.execute(
                "SELECT * FROM nodes WHERE project_id=? AND layer=2 AND label=?",
                (self.project["id"], "会员升级"),
            ).fetchone()
            self.assertIsNotNone(concept)
            for index in reversed(range(20)):
                relative = f"src/membership/extra_{index:02d}.py"
                absolute = self.root / relative
                absolute.write_text(f"VALUE = {index}\n", encoding="utf-8")
                file_id = Database.upsert_node(
                    connection,
                    project_id=self.project["id"],
                    layer=1,
                    kind="file",
                    key=relative,
                    label=absolute.name,
                    data={"language": "python"},
                    source="repository",
                )
                Database.upsert_edge(
                    connection,
                    project_id=self.project["id"],
                    layer=2,
                    source_id=concept["id"],
                    relation="implemented_by",
                    target_id=file_id,
                    source="semantic-heuristic",
                )
                stat = absolute.stat()
                connection.execute(
                    """INSERT INTO file_state(
                           project_id, path, mtime_ns, size, digest, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(project_id, path) DO UPDATE SET
                         mtime_ns=excluded.mtime_ns, size=excluded.size,
                         digest=excluded.digest, updated_at=excluded.updated_at""",
                    (
                        self.project["id"], relative, stat.st_mtime_ns, stat.st_size,
                        hashlib.blake2s(absolute.read_bytes()).hexdigest(), utc_now(),
                    ),
                )
            connection.commit()

        first = context_data(self.database, self.project, "会员升级")
        second = context_data(self.database, self.project, "会员升级")
        first_paths = [item["path"] for item in first["files"]]
        self.assertEqual(first_paths, [item["path"] for item in second["files"]])
        self.assertEqual(len(first_paths), 12)
        self.assertEqual(len(first["navigation"]["readingOrder"]), 12)
        self.assertEqual(
            {item["path"] for item in first["navigation"]["readingOrder"]},
            set(first_paths),
        )
        self.assertTrue(all(
            len(item["chains"]) <= 3
            and all(chain["conceptRelation"] is None for chain in item["chains"])
            for item in first["navigation"]["readingOrder"]
        ))

    def test_stale_and_absolute_candidates_never_enter_navigation_wire(self) -> None:
        private_canary = str(self.root / "secret.py")
        with self.database.connect() as connection:
            concept = connection.execute(
                "SELECT * FROM nodes WHERE project_id=? AND layer=2 AND label=?",
                (self.project["id"], "会员升级"),
            ).fetchone()
            self.assertIsNotNone(concept)
            unsafe_id = Database.upsert_node(
                connection,
                project_id=self.project["id"],
                layer=1,
                kind="file",
                key=private_canary,
                label="secret.py",
                source="repository",
            )
            Database.upsert_edge(
                connection,
                project_id=self.project["id"],
                layer=2,
                source_id=concept["id"],
                relation="implemented_by",
                target_id=unsafe_id,
                source="semantic-heuristic",
            )
            connection.commit()
        stale = self.root / "src/payment/service.py"
        stale.write_text("def charge(value):\n    return False\n", encoding="utf-8")

        data = context_data(self.database, self.project, "会员升级")
        candidate_paths = {item["path"] for item in data["files"]}
        navigation_paths = {
            item["path"] for item in data["navigation"]["readingOrder"]
        }
        self.assertNotIn(private_canary, candidate_paths)
        self.assertIn("src/payment/service.py", candidate_paths)
        self.assertNotIn("src/payment/service.py", navigation_paths)
        self.assertIn(
            "CONTEXT_NAVIGATION_STALE_FILES",
            {warning["code"] for warning in data["warnings"]},
        )
        wire = context_view(data).to_json()
        self.assertNotIn(private_canary, wire)
        self.assertEqual(context_view(data).source_state.status, "stale")

    def test_adapter_text_and_structured_navigation_are_equivalent_and_strict(self) -> None:
        data = context_data(
            self.database, self.project, "修改会员升级和支付逻辑"
        )
        view = context_view(data).to_dict()
        text = context_text(data)
        reading = view["data"]["navigation"]["readingOrder"]
        for item in reading:
            self.assertIn(item["path"], text)
            self.assertIn(item["why"], text)
            if item["nextStep"]:
                self.assertIn(item["nextStep"]["path"], text)
        self.assertIn("Why：", text)
        self.assertIn("Evidence：", text)
        self.assertIn("Next Step：", text)
        self.assertNotIn("root", view["project"])
        self.assertNotIn(str(self.root.resolve()), json.dumps(view, ensure_ascii=False))

        malformed = json.loads(json.dumps(data))
        malformed["navigation"]["readingOrder"][0]["chains"][0]["fileRelation"][
            "targetId"
        ] = "wrong-file"
        with self.assertRaises(ValueError):
            context_view(malformed)


if __name__ == "__main__":
    unittest.main()
