from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.engine import scan_project
from agentnavi.mcp.adapters.context import context_text, context_view
from agentnavi.query import _context_file_actions, _fresh_context_paths, context_data
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

    def _add_indexed_file(self, connection, relative: str, content: str) -> str:
        absolute = self.root / relative
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_text(content, encoding="utf-8")
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
        return file_id

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
            chain["relatedConcept"]["evidence"][0]["kind"], "physical-relation"
        )
        self.assertIn(
            f"会员升级 —depends_on→ {chain['relatedConcept']['label']} "
            "—implemented_by→ src/payment/service.py",
            payment["why"],
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

        direct = next(
            item for item in data["navigation"]["readingOrder"]
            if item["path"] == "src/membership/upgrade.py"
        )
        self.assertIn(
            "会员升级 —implemented_by→ src/membership/upgrade.py", direct["why"]
        )
        incoming = context_data(self.database, self.project, "payment")
        membership = next(
            item for item in incoming["navigation"]["readingOrder"]
            if item["path"] == "src/membership/upgrade.py"
        )
        incoming_chain = next(
            item for item in membership["chains"]
            if item["conceptRelation"] is not None
        )
        self.assertIn(
            f"{incoming_chain['sourceConcept']['label']} ←depends_on— 会员升级 "
            "—implemented_by→ src/membership/upgrade.py",
            membership["why"],
        )

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

    def test_automatic_one_hop_requires_directional_physical_evidence(self) -> None:
        with self.database.connect() as connection:
            edge = connection.execute(
                """SELECT edge.* FROM edges edge
                    JOIN nodes source ON source.id=edge.source_id
                    JOIN nodes target ON target.id=edge.target_id
                    WHERE edge.project_id=? AND edge.layer=2
                      AND source.label='会员升级' AND target.label='payment'
                      AND edge.relation='depends_on'""",
                (self.project["id"],),
            ).fetchone()
            self.assertIsNotNone(edge)
            connection.execute(
                "UPDATE edges SET data_json=? WHERE id=?",
                (json.dumps({"evidence": [{
                    "source": "src/payment/service.py",
                    "target": "src/membership/upgrade.py",
                    "physical_relation": "imports",
                }]}), edge["id"]),
            )
            connection.commit()

        invalid = context_data(self.database, self.project, "会员升级")
        self.assertIn("src/payment/service.py", {item["path"] for item in invalid["files"]})
        self.assertNotIn(
            "src/payment/service.py",
            {item["path"] for item in invalid["navigation"]["readingOrder"]},
        )
        self.assertIn(
            "CONTEXT_NAVIGATION_RELATION_EVIDENCE_INSUFFICIENT",
            {warning["code"] for warning in invalid["warnings"]},
        )

        with self.database.connect() as connection:
            connection.execute(
                "UPDATE edges SET source='human-overlay', data_json='{}' WHERE id=?",
                (edge["id"],),
            )
            connection.commit()
        decided = context_data(self.database, self.project, "会员升级")
        payment = next(
            item for item in decided["navigation"]["readingOrder"]
            if item["path"] == "src/payment/service.py"
        )
        relation = next(
            chain["conceptRelation"] for chain in payment["chains"]
            if chain["conceptRelation"] is not None
        )
        self.assertEqual(relation["evidence"][0]["kind"], "human-decision")
        self.assertNotIn("path", relation["evidence"][0])

    def test_actions_use_all_fresh_candidates_and_fair_bounded_history(self) -> None:
        now = utc_now()
        with self.database.connect() as connection:
            helper_id = self._add_indexed_file(
                connection, "src/membership/helper.py", "VALUE = 'membership helper'\n"
            )
            upgrade = connection.execute(
                "SELECT id FROM nodes WHERE project_id=? AND kind='file' AND key=?",
                (self.project["id"], "src/membership/upgrade.py"),
            ).fetchone()
            payment = connection.execute(
                "SELECT id FROM nodes WHERE project_id=? AND kind='file' AND key=?",
                (self.project["id"], "src/payment/service.py"),
            ).fetchone()
            self.assertIsNotNone(upgrade)
            self.assertIsNotNone(payment)
            Database.upsert_edge(
                connection, project_id=self.project["id"], layer=1,
                source_id=helper_id, relation="imports", target_id=upgrade["id"],
                source="extractor",
            )
            for index in range(41):
                task_id = f"private-history-{index:02d}"
                connection.execute(
                    """INSERT INTO tasks(
                           id, project_id, agent, title, status, summary,
                           created_at, updated_at, closed_at
                       ) VALUES (?, ?, 'codex', ?, 'completed', '', ?, ?, ?)""",
                    (
                        task_id, self.project["id"], f"/private/task-{index}",
                        now, now, now,
                    ),
                )
                node_id = Database.upsert_node(
                    connection, project_id=self.project["id"], layer=3,
                    kind="task", key=task_id, label=f"history-{index}",
                    source="task-events",
                )
                Database.upsert_edge(
                    connection, project_id=self.project["id"], layer=3,
                    source_id=node_id, relation="modified", target_id=upgrade["id"],
                    source="task-events",
                )
            connection.execute(
                """INSERT INTO tasks(
                       id, project_id, agent, title, status, summary,
                       created_at, updated_at, closed_at
                   ) VALUES ('safe-payment-task', ?, 'codex', '调整支付入口',
                             'completed', '', ?, ?, ?)""",
                (self.project["id"], now, now, now),
            )
            task_node = Database.upsert_node(
                connection, project_id=self.project["id"], layer=3, kind="task",
                key="safe-payment-task", label="调整支付入口", source="task-events",
            )
            Database.upsert_edge(
                connection, project_id=self.project["id"], layer=3,
                source_id=task_node, relation="modified", target_id=payment["id"],
                source="task-events",
            )
            connection.commit()

        data = context_data(self.database, self.project, "membership")
        by_path = {
            item["path"]: item for item in data["navigation"]["readingOrder"]
        }
        self.assertIn(
            {"path": "src/membership/helper.py", "relation": "imports"},
            by_path["src/membership/upgrade.py"]["dependents"],
        )
        self.assertEqual(
            by_path["src/payment/service.py"]["history"][0]["title"],
            "调整支付入口",
        )
        history_action = next(
            action for action in by_path["src/membership/upgrade.py"]["actions"]
            if action["kind"] == "history"
        )
        self.assertIn("当前有界结果中未展示", history_action["summary"])
        warning_codes = {warning["code"] for warning in data["warnings"]}
        self.assertIn("CONTEXT_NAVIGATION_HISTORY_TRUNCATED", warning_codes)
        self.assertIn("CONTEXT_NAVIGATION_HISTORY_FILTERED", warning_codes)

    def test_fourth_safe_history_marks_display_truncation(self) -> None:
        now = utc_now()
        with self.database.connect() as connection:
            upgrade = connection.execute(
                "SELECT id FROM nodes WHERE project_id=? AND kind='file' AND key=?",
                (self.project["id"], "src/membership/upgrade.py"),
            ).fetchone()
            self.assertIsNotNone(upgrade)
            for index in range(4):
                task_id = f"safe-history-{index}"
                connection.execute(
                    """INSERT INTO tasks(
                           id, project_id, agent, title, status, summary,
                           created_at, updated_at, closed_at
                       ) VALUES (?, ?, 'codex', ?, 'completed', '', ?, ?, ?)""",
                    (task_id, self.project["id"], f"安全任务 {index}", now, now, now),
                )
                task_node = Database.upsert_node(
                    connection, project_id=self.project["id"], layer=3, kind="task",
                    key=task_id, label=f"安全任务 {index}", source="task-events",
                )
                Database.upsert_edge(
                    connection, project_id=self.project["id"], layer=3,
                    source_id=task_node, relation="modified", target_id=upgrade["id"],
                    source="task-events",
                )
            connection.commit()

        data = context_data(self.database, self.project, "membership")
        upgrade_item = next(
            item for item in data["navigation"]["readingOrder"]
            if item["path"] == "src/membership/upgrade.py"
        )
        self.assertEqual(len(upgrade_item["history"]), 3)
        history_action = next(
            item for item in upgrade_item["actions"] if item["kind"] == "history"
        )
        self.assertIn("近期相关历史任务（最多 3 项）", history_action["summary"])
        self.assertIn(
            "CONTEXT_NAVIGATION_HISTORY_TRUNCATED",
            {warning["code"] for warning in data["warnings"]},
        )

    def test_large_history_uses_indexed_per_target_constant_query_budget(self) -> None:
        baseline = context_data(self.database, self.project, "membership")
        payment = next(
            item for item in baseline["navigation"]["readingOrder"]
            if item["path"] == "src/payment/service.py"
        )
        chain = payment["chains"][0]
        file_id = chain["file"]["id"]
        now = utc_now()
        task_rows = []
        node_rows = []
        edge_rows = []
        for index in range(5000):
            task_id = f"bulk-history-{index:04d}"
            node_id = Database.node_id(self.project["id"], 3, "task", task_id)
            edge_id = Database.edge_id(
                self.project["id"], 3, node_id, "modified", file_id
            )
            task_rows.append((
                task_id, self.project["id"], "codex", f"批量历史 {index}",
                "completed", "", now, now, now,
            ))
            node_rows.append((
                node_id, self.project["id"], 3, "task", task_id,
                f"批量历史 {index}", "{}", 1.0, "task-events", now, now,
            ))
            edge_rows.append((
                edge_id, self.project["id"], 3, node_id, "modified", file_id,
                "{}", 1.0, "task-events", now, now,
            ))
        statements: list[str] = []
        vm_steps = 0

        def count_step() -> int:
            nonlocal vm_steps
            vm_steps += 1
            return 0

        with self.database.connect() as connection:
            connection.executemany(
                """INSERT INTO tasks(
                       id, project_id, agent, title, status, summary,
                       created_at, updated_at, closed_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                task_rows,
            )
            connection.executemany(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", node_rows
            )
            connection.executemany(
                "INSERT INTO edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", edge_rows
            )
            connection.commit()
            plan = connection.execute(
                """EXPLAIN QUERY PLAN
                SELECT task.id FROM edges AS edge INDEXED BY idx_edges_target
                JOIN nodes task_node ON task_node.id=edge.source_id
                JOIN tasks task ON task.id=task_node.key
                WHERE edge.project_id=? AND edge.layer=3 AND edge.target_id=?
                ORDER BY edge.rowid
                LIMIT 41""",
                (self.project["id"], file_id),
            ).fetchall()
            connection.set_trace_callback(statements.append)
            connection.set_progress_handler(count_step, 1)
            try:
                actions, state = _context_file_actions(
                    connection,
                    self.project["id"],
                    [{"path": payment["path"]}],
                    [{"path": payment["path"], "_chains": [chain]}],
                )
            finally:
                connection.set_progress_handler(None, 0)
                connection.set_trace_callback(None)

        self.assertTrue(any("idx_edges_target" in str(tuple(row)) for row in plan))
        self.assertFalse(any("TEMP B-TREE" in str(tuple(row)) for row in plan))
        history_sql = [
            statement for statement in statements
            if "context-navigation-history-indexed" in statement
        ]
        self.assertEqual(len(history_sql), 1)
        self.assertLess(vm_steps, 10000)
        self.assertTrue(state["history_truncated"])
        self.assertEqual(len(actions[payment["path"]]["history"]), 3)

    def test_symlink_candidate_is_rejected_from_explanation_layer(self) -> None:
        target = self.root / "src/payment/real-service.py"
        target.write_text(
            (self.root / "src/payment/service.py").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        linked = self.root / "src/payment/service.py"
        linked.unlink()
        linked.symlink_to(target.name)

        data = context_data(self.database, self.project, "会员升级")
        self.assertIn("src/payment/service.py", {item["path"] for item in data["files"]})
        self.assertNotIn(
            "src/payment/service.py",
            {item["path"] for item in data["navigation"]["readingOrder"]},
        )
        self.assertIn(
            "CONTEXT_NAVIGATION_SYMLINK_UNVERIFIABLE",
            {warning["code"] for warning in data["warnings"]},
        )

    def test_symlink_introduced_after_read_is_detected_by_lexical_recheck(self) -> None:
        with self.database.connect() as connection, patch(
            "agentnavi.query._context_path_has_symlink", side_effect=[False, True]
        ):
            fresh, unverifiable, symlinks = _fresh_context_paths(
                connection, self.project, ["src/payment/service.py"]
            )
        self.assertEqual(fresh, set())
        self.assertEqual(unverifiable, set())
        self.assertEqual(symlinks, {"src/payment/service.py"})

    def test_text_lists_every_navigation_and_partial_warning(self) -> None:
        data = context_data(self.database, self.project, "会员升级")
        data["project"]["last_scan_at"] = None
        data["warnings"] = [
            {"code": code, "message": f"{code} message", "evidence": []}
            for code in (
                "CONTEXT_NAVIGATION_STALE_FILES",
                "CONTEXT_NAVIGATION_FRESHNESS_BUDGET",
                "CONTEXT_NAVIGATION_EVIDENCE_INSUFFICIENT",
                "CONTEXT_NAVIGATION_HISTORY_TRUNCATED",
            )
        ]
        view = context_view(data)
        text = context_text(data)
        self.assertEqual(view.source_state.status, "partial")
        for warning in view.warnings:
            self.assertIn(f"[{warning.code}] {warning.message}", text)

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
