from __future__ import annotations

import tempfile
import unittest
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.history_view import HISTORY_TASK_SCAN_LIMIT, history_view_data
from agentnavi.mcp.adapters.history import history_text, history_view
from agentnavi.query import history_data, task_detail_data


class HistoryViewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "private" / "fixture"
        self.root.mkdir(parents=True)
        self.database = ensure_database(Settings.load(self.base / "home"))
        now = "2026-09-15T10:00:00+00:00"
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO projects(id,name,root,kind,created_at,updated_at,last_scan_at)
                   VALUES ('fixture','Fixture',?,'software',?,?,?)""",
                (str(self.root), now, now, now),
            )
            self.file_ids = {}
            for path in ("src/a.py", "src/b.py", "tests/test_a.py"):
                absolute = self.root / path
                absolute.parent.mkdir(parents=True, exist_ok=True)
                absolute.write_text("VALUE = 1\n", encoding="utf-8")
                self.file_ids[path] = Database.upsert_node(
                    connection, project_id="fixture", layer=1, kind="file", key=path,
                    label=Path(path).name, source="repository",
                )
            self.concept_id = Database.upsert_node(
                connection, project_id="fixture", layer=2, kind="concept", key="core",
                label="Core", source="semantic-heuristic", confidence=.8,
            )
            self._add_task(connection, "older", "旧任务", "2026-09-15T09:00:00+00:00", "2026-09-15T12:00:00+00:00")
            self._add_task(connection, "same-a", "同时间 A", "2026-09-15T11:00:00+00:00", "2026-09-15T13:00:00+00:00")
            self._add_task(connection, "same-b", "同时间 B", "2026-09-15T08:00:00+00:00", "2026-09-15T13:00:00+00:00")
            connection.commit()
        with self.database.connect() as connection:
            self.project = connection.execute("SELECT * FROM projects WHERE id='fixture'").fetchone()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add_task(self, connection, task_id: str, title: str, created: str, updated: str) -> str:
        connection.execute(
            """INSERT INTO tasks(id,project_id,title,status,summary,created_at,updated_at,closed_at)
               VALUES (?, 'fixture', ?, 'completed', ?, ?, ?, ?)""",
            (task_id, title, f"{title} 的摘要", created, updated, updated),
        )
        return Database.upsert_node(
            connection, project_id="fixture", layer=3, kind="task", key=task_id,
            label=title, source="task-events",
        )

    def test_timeline_uses_authoritative_time_and_stable_tie_break(self) -> None:
        core = history_view_data(self.database, self.project)
        self.assertEqual([item["taskId"] for item in core["timeline"]], ["same-b", "same-a", "older"])
        self.assertEqual(core, history_view_data(self.database, self.project))
        wire = history_view(core).to_dict()
        self.assertEqual(wire["view"], "history")
        self.assertNotIn(str(self.root), str(wire))
        text = history_text(core)
        self.assertIn("Task Timeline（新到旧）", text)
        self.assertIn("不是原始工具调用的无损还原", text)

    def test_task_detail_has_real_l3_relations_and_cli_history_stays_compatible(self) -> None:
        with self.database.connect() as connection:
            task_node = Database.node_id("fixture", 3, "task", "same-b")
            for relation, target in (("modified", self.file_ids["src/a.py"]), ("tested", self.file_ids["tests/test_a.py"]), ("affects", self.concept_id)):
                Database.upsert_edge(connection, project_id="fixture", layer=3, source_id=task_node,
                                     relation=relation, target_id=target, source="task-events")
            connection.commit()
        detail = task_detail_data(self.database, self.project, "same-b")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual({item["relation"]["relation"] for item in detail["relations"]}, {"modified", "tested", "affects"})
        core = history_view_data(self.database, self.project, task_id="same-b")
        self.assertEqual(core["taskDetail"]["taskId"], "same-b")
        self.assertEqual(len(history_data(self.database, self.project, "同时间", limit=5)), 2)

        with self.database.connect() as connection:
            for index in range(25):
                self._add_task(
                    connection, f"newer-{index:02d}", f"较新任务 {index}",
                    f"2026-09-16T{index % 24:02d}:00:00+00:00",
                    f"2026-09-16T{index % 24:02d}:30:00+00:00",
                )
            connection.commit()
        selected = history_view_data(self.database, self.project, task_id="older")
        self.assertEqual(selected["taskDetail"]["taskId"], "older")
        self.assertIn("older", {item["taskId"] for item in selected["timeline"]})

    def test_story_groups_multiple_paths_without_claiming_lossless_or_causality(self) -> None:
        with self.database.connect() as connection:
            task_node = Database.node_id("fixture", 3, "task", "same-b")
            for path in ("src/a.py", "src/b.py"):
                Database.upsert_edge(connection, project_id="fixture", layer=3, source_id=task_node,
                                     relation="modified", target_id=self.file_ids[path], source="task-events")
            connection.commit()
        core = history_view_data(self.database, self.project)
        item = next(story for story in core["story"] if story["task"]["id"] == Database.node_id("fixture", 3, "task", "same-b"))
        modified = next(group for group in item["groups"] if group["relation"] == "modified")
        self.assertEqual(modified["paths"], ["src/a.py", "src/b.py"])
        self.assertIn("不是原始工具调用的无损还原", item["disclaimer"])
        self.assertIn("不据此推断因果", item["disclaimer"])
        forged = json.loads(json.dumps(core))
        forged["story"][0]["groups"][0]["paths"].append("src/forged.py")
        with self.assertRaises(ValueError):
            history_view(forged)

        with self.database.connect() as connection:
            older_node = Database.node_id("fixture", 3, "task", "older")
            Database.upsert_edge(connection, project_id="fixture", layer=3, source_id=older_node,
                                 relation="modified", target_id=self.file_ids["src/a.py"], source="task-events")
            connection.commit()
        conflicting = history_view_data(self.database, self.project)
        entries = [entry for timeline in conflicting["timeline"] for entry in timeline["relations"] if entry["entity"].get("path") == "src/a.py"]
        self.assertGreaterEqual(len(entries), 2)
        entries[-1]["entity"]["label"] = "laundered.py"
        with self.assertRaises(ValueError):
            history_view(conflicting)

    def test_unsafe_rows_filter_before_display_and_scan_is_bounded(self) -> None:
        with self.database.connect() as connection:
            unsafe_file = Database.upsert_node(
                connection, project_id="fixture", layer=1, kind="file",
                key="/private/edge-secret.py", label="secret.py", source="/private/provider",
            )
            Database.upsert_edge(
                connection, project_id="fixture", layer=3,
                source_id=Database.node_id("fixture", 3, "task", "same-b"),
                relation="modified", target_id=unsafe_file, source="task-events",
            )
            for index in range(25):
                task_id = f"unsafe-{index:02d}"
                self._add_task(connection, task_id, f"/private/secret-{index}", f"2026-09-15T14:{index:02d}:00+00:00", f"2026-09-15T14:{index:02d}:00+00:00")
            safe_node = self._add_task(connection, "later-safe", "合法任务", "2026-09-15T07:00:00+00:00", "2026-09-15T07:00:00+00:00")
            Database.upsert_edge(connection, project_id="fixture", layer=3, source_id=safe_node,
                                 relation="modified", target_id=self.file_ids["src/a.py"], source="task-events")
            connection.commit()
        core = history_view_data(self.database, self.project)
        self.assertIn("later-safe", {item["taskId"] for item in core["timeline"]})
        self.assertIn("HISTORY_UNSAFE_FILTERED", {warning["code"] for warning in core["warnings"]})
        self.assertNotIn("/private/secret", str(history_view(core).to_dict()))
        self.assertNotIn("/private/provider", str(core))
        self.assertNotIn("edge-secret", str(core))

        with self.database.connect() as connection:
            for index in range(HISTORY_TASK_SCAN_LIMIT + 5):
                self._add_task(connection, f"bulk-{index:04d}", f"批量任务 {index}", "2026-09-14T10:00:00+00:00", "2026-09-14T10:00:00+00:00")
            connection.commit()
        bounded = history_view_data(self.database, self.project)
        self.assertIn("HISTORY_SCAN_TRUNCATED", {warning["code"] for warning in bounded["warnings"]})
        self.assertLessEqual(len(bounded["timeline"]), 20)

    def test_empty_history_has_honest_warning(self) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM edges WHERE layer=3")
            connection.execute("DELETE FROM nodes WHERE layer=3")
            connection.execute("DELETE FROM tasks")
            connection.commit()
        core = history_view_data(self.database, self.project)
        self.assertEqual(core["timeline"], [])
        self.assertIn("HISTORY_EVIDENCE_INSUFFICIENT", {warning["code"] for warning in core["warnings"]})

    def test_relation_garbage_is_filtered_before_cap_with_fixed_query_budget(self) -> None:
        task_node = Database.node_id("fixture", 3, "task", "same-b")
        with self.database.connect() as connection:
            Database.upsert_edge(connection, project_id="fixture", layer=3, source_id=task_node,
                                 relation="modified", target_id=self.file_ids["src/a.py"], source="task-events")
            for index in range(70):
                target = Database.upsert_node(
                    connection, project_id="fixture", layer=2, kind="concept",
                    key=f"garbage-{index:02d}", label=f"Garbage {index}", source="test",
                )
                Database.upsert_edge(connection, project_id="fixture", layer=3, source_id=task_node,
                                     relation=f"garbage-{index:02d}", target_id=target, source="task-events")
            connection.commit()
        statements: list[str] = []
        original = self.database.connect

        @contextmanager
        def traced_connect():
            with original() as connection:
                connection.set_trace_callback(statements.append)
                yield connection

        with patch.object(self.database, "connect", traced_connect):
            core = history_view_data(self.database, self.project, "同时间 B")
        self.assertEqual(core["timeline"][0]["relations"][0]["entity"]["path"], "src/a.py")
        self.assertIn("HISTORY_RELATION_FILTERED", {warning["code"] for warning in core["warnings"]})
        self.assertLessEqual(sum("history-relation-" in statement for statement in statements), 8)


if __name__ == "__main__":
    unittest.main()
