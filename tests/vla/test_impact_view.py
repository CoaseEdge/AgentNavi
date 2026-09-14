from __future__ import annotations

import hashlib
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.impact_view import impact_view_data
from agentnavi.mcp.adapters.impact import impact_text, impact_to_view


class ImpactViewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "private" / "fixture"
        files = {
            "src/caller.py": "from .focus import run\n",
            "src/focus.py": "from .dependency import value\ndef run(): return value\n",
            "src/dependency.py": "value = 1\n",
            "tests/test_focus.py": "from src.focus import run\ndef test_run(): assert run()\n",
        }
        for relative, content in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.database = ensure_database(Settings.load(self.base / "home"))
        now = "2026-09-15T10:00:00+00:00"
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO projects(id,name,root,kind,created_at,updated_at,last_scan_at)
                   VALUES ('fixture','Fixture',?,'software',?,?,?)""",
                (str(self.root.resolve()), now, now, now),
            )
            ids = {}
            for relative in files:
                ids[relative] = Database.upsert_node(
                    connection, project_id="fixture", layer=1, kind="file", key=relative,
                    label=Path(relative).name, source="repository",
                )
                absolute = self.root / relative
                stat = absolute.stat()
                connection.execute(
                    "INSERT INTO file_state(project_id,path,mtime_ns,size,digest,updated_at) VALUES ('fixture',?,?,?,?,?)",
                    (relative, stat.st_mtime_ns, stat.st_size, hashlib.blake2s(absolute.read_bytes()).hexdigest(), now),
                )
            for source, relation, target in (
                ("src/caller.py", "imports", "src/focus.py"),
                ("src/focus.py", "imports", "src/dependency.py"),
                ("tests/test_focus.py", "tests", "src/focus.py"),
            ):
                Database.upsert_edge(connection, project_id="fixture", layer=1, source_id=ids[source], relation=relation, target_id=ids[target], source="extractor")
            concept_ids = {}
            for key, label, path in (("focus", "Focus", "src/focus.py"), ("dependency", "Dependency", "src/dependency.py")):
                concept_ids[key] = Database.upsert_node(connection, project_id="fixture", layer=2, kind="concept", key=key, label=label, source="semantic-heuristic", confidence=.8)
                Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=concept_ids[key], relation="implemented_by", target_id=ids[path], source="semantic-heuristic", confidence=.8)
            Database.upsert_edge(
                connection, project_id="fixture", layer=2, source_id=concept_ids["focus"], relation="depends_on", target_id=concept_ids["dependency"],
                source="semantic-heuristic", confidence=.75,
                data={"evidence": [{"source": "src/focus.py", "target": "src/dependency.py", "physical_relation": "imports"}]},
            )
            connection.execute("INSERT INTO tasks(id,project_id,title,status,created_at,updated_at) VALUES ('task-1','fixture','修改 Focus','completed',?,?)", (now, now))
            task_node = Database.upsert_node(connection, project_id="fixture", layer=3, kind="task", key="task-1", label="修改 Focus", source="task-events")
            Database.upsert_edge(connection, project_id="fixture", layer=3, source_id=task_node, relation="modified", target_id=ids["src/focus.py"], source="task-events")
            connection.commit()
        with self.database.connect() as connection:
            self.project = connection.execute("SELECT * FROM projects WHERE id='fixture'").fetchone()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add_file(self, connection, relative: str) -> str:
        absolute = self.root / relative
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text("VALUE = 1\n", encoding="utf-8")
        file_id = Database.upsert_node(connection, project_id="fixture", layer=1, kind="file", key=relative, label=absolute.name, source="repository")
        stat = absolute.stat()
        connection.execute("INSERT INTO file_state(project_id,path,mtime_ns,size,digest,updated_at) VALUES ('fixture',?,?,?,?,?)", (relative, stat.st_mtime_ns, stat.st_size, hashlib.blake2s(absolute.read_bytes()).hexdigest(), "2026-09-15T10:00:00+00:00"))
        return file_id

    def test_fixed_layout_uses_real_layered_facts_and_is_deterministic(self) -> None:
        before = self.database.settings.database_path.read_bytes()
        first = impact_view_data(self.database, self.project, "src/focus.py")
        second = impact_view_data(self.database, self.project, "src/focus.py")
        self.assertEqual(before, self.database.settings.database_path.read_bytes())
        self.assertEqual(first, second)
        self.assertEqual(first["layout"], "incoming-focus-outgoing")
        self.assertEqual(first["focus"]["entity"]["path"], "src/focus.py")
        self.assertEqual({item["peer"]["path"] for item in first["incoming"]}, {"src/caller.py", "tests/test_focus.py"})
        self.assertEqual([item["peer"]["path"] for item in first["outgoing"]], ["src/dependency.py"])
        self.assertEqual(first["semantic"][0]["peer"]["label"], "Dependency")
        self.assertEqual(first["history"][0]["entity"]["label"], "修改 Focus")
        self.assertEqual(first["testRecommendations"][0]["path"], "tests/test_focus.py")
        self.assertTrue(first["risks"])
        self.assertEqual([item["label"] for item in first["actions"]], ["它做什么", "谁调用它", "它依赖谁", "如果修改它", "过去谁改过它"])
        with self.database.connect() as connection:
            for item in first["incoming"] + first["outgoing"]:
                row = connection.execute("SELECT * FROM edges WHERE id=?", (item["relation"]["id"],)).fetchone()
                self.assertEqual(item["relation"]["sourceId"], row["source_id"])
                self.assertEqual(item["relation"]["targetId"], row["target_id"])

    def test_stale_and_forged_semantic_evidence_are_not_explained(self) -> None:
        (self.root / "src/dependency.py").write_text("value = 2\n", encoding="utf-8")
        data = impact_view_data(self.database, self.project, "src/focus.py")
        self.assertEqual(data["outgoing"], [])
        self.assertEqual(data["semantic"], [])
        self.assertTrue(any(item["code"] == "IMPACT_SEMANTIC_EVIDENCE_INSUFFICIENT" for item in data["warnings"]))

    def test_adapter_and_text_are_independent_strict_projections(self) -> None:
        core = impact_view_data(self.database, self.project, "Focus")
        view = impact_to_view(core).to_dict()
        text = impact_text(core)
        self.assertEqual(view["view"], "impact")
        self.assertNotIn(str(self.root), str(view))
        self.assertIn("Incoming → Focus → Outgoing", text)
        self.assertIn("History（下", text)
        broken = {**core, "incoming": [*core["incoming"], *core["incoming"] * 8]}
        with self.assertRaises(ValueError):
            impact_to_view(broken)

    def test_each_physical_lane_is_independently_sorted_capped_and_query_bounded(self) -> None:
        focus_id = Database.node_id("fixture", 1, "file", "src/focus.py")
        with self.database.connect() as connection:
            for index in reversed(range(12)):
                incoming = self._add_file(connection, f"src/incoming_{index:02d}.py")
                outgoing = self._add_file(connection, f"src/outgoing_{index:02d}.py")
                Database.upsert_edge(connection, project_id="fixture", layer=1, source_id=incoming, relation="imports", target_id=focus_id, source="extractor")
                Database.upsert_edge(connection, project_id="fixture", layer=1, source_id=focus_id, relation="imports", target_id=outgoing, source="extractor")
            connection.commit()

        original_connect = self.database.connect
        statements: list[str] = []

        @contextmanager
        def traced_connect():
            with original_connect() as connection:
                connection.set_trace_callback(statements.append)
                try:
                    yield connection
                finally:
                    connection.set_trace_callback(None)

        with patch.object(self.database, "connect", traced_connect):
            data = impact_view_data(self.database, self.project, "src/focus.py")
        self.assertEqual(len(data["incoming"]), 8)
        self.assertEqual(len(data["outgoing"]), 8)
        self.assertEqual([item["peer"]["path"] for item in data["incoming"]], sorted(item["peer"]["path"] for item in data["incoming"]))
        self.assertEqual([item["peer"]["path"] for item in data["outgoing"]], sorted(item["peer"]["path"] for item in data["outgoing"]))
        self.assertEqual(sum("impact-physical-lane" in sql for sql in statements), 2)
        self.assertEqual(sum("impact-physical-lookup" in sql for sql in statements), 1)
        self.assertIn("IMPACT_PHYSICAL_TRUNCATED", {item["code"] for item in data["warnings"]})


if __name__ == "__main__":
    unittest.main()
