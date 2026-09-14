from __future__ import annotations

import hashlib
import inspect
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from agentnavi.config import Settings
from agentnavi import impact_view as impact_module
from agentnavi.database import Database, ensure_database
from agentnavi.impact_view import (
    _anchor_mapping_rows, _focus_concept_rows, _history_rows, _lane_rows,
    _resolve_focus, _semantic_rows, _tested_by_rows, impact_view_data,
)
from agentnavi.mcp.adapters.impact import impact_text, impact_to_view
from agentnavi.semantic_relations import CONCEPT_FILE_MAPPING_RELATIONS


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

    def test_semantic_peer_reuses_canonical_node_evidence_across_relations_and_directions(self) -> None:
        focus = Database.node_id("fixture", 2, "concept", "focus")
        dependency = Database.node_id("fixture", 2, "concept", "dependency")
        focus_file = Database.node_id("fixture", 1, "file", "src/focus.py")
        dependency_file = Database.node_id("fixture", 1, "file", "src/dependency.py")
        with self.database.connect() as connection:
            Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=focus,
                                 relation="related_to", target_id=dependency,
                                 source="semantic-heuristic", confidence=.6,
                                 data={"evidence": [{"source": "src/focus.py", "target": "src/dependency.py", "physical_relation": "imports"}]})
            Database.upsert_edge(connection, project_id="fixture", layer=1, source_id=dependency_file,
                                 relation="imports", target_id=focus_file, source="extractor")
            Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=dependency,
                                 relation="related_to", target_id=focus,
                                 source="semantic-heuristic", confidence=.65,
                                 data={"evidence": [{"source": "src/dependency.py", "target": "src/focus.py", "physical_relation": "imports"}]})
            connection.commit()
        core = impact_view_data(self.database, self.project, "Focus")
        peers = [item["peer"] for item in core["semantic"] if item["peer"]["id"] == dependency]
        self.assertEqual(len(peers), 3)
        self.assertTrue(all(peer["evidence"] == peers[0]["evidence"] for peer in peers))
        self.assertTrue(all(peer["evidence"] != item["evidence"] for peer, item in
                            ((entry["peer"], entry) for entry in core["semantic"])))
        impact_to_view(core)

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

    def test_concept_focus_exposes_every_fresh_anchor_and_lane_via_path(self) -> None:
        focus_concept = Database.node_id("fixture", 2, "concept", "focus")
        with self.database.connect() as connection:
            second = self._add_file(connection, "src/focus_worker.py")
            caller = self._add_file(connection, "src/worker_caller.py")
            Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=focus_concept,
                                 relation="configured_by", target_id=second,
                                 source="semantic-heuristic", confidence=.8)
            Database.upsert_edge(connection, project_id="fixture", layer=1, source_id=caller,
                                 relation="imports", target_id=second, source="extractor")
            connection.commit()
        core = impact_view_data(self.database, self.project, "Focus")
        self.assertEqual({item["entity"]["path"] for item in core["anchorFiles"]},
                         {"src/focus.py", "src/focus_worker.py"})
        self.assertIn("src/focus_worker.py", {item["viaPath"] for item in core["incoming"]})
        self.assertEqual(len(impact_to_view(core).data["anchorFiles"]), 2)
        text = impact_text(core)
        self.assertIn("implemented_by", text)
        self.assertIn("configured_by", text)
        self.assertGreaterEqual(text.count("Evidence："), 2)

    def test_safe_fresh_filter_precedes_anchor_and_lane_display_caps(self) -> None:
        focus_concept = Database.node_id("fixture", 2, "concept", "focus")
        with self.database.connect() as connection:
            later_anchor = self._add_file(connection, "src/later_anchor.py")
            Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=focus_concept,
                                 relation="implemented_by", target_id=later_anchor,
                                 source="semantic-heuristic", confidence=.8)
            later_peer = self._add_file(connection, "src/later_peer.py")
            Database.upsert_edge(connection, project_id="fixture", layer=1, source_id=later_peer,
                                 relation="imports", target_id=later_anchor, source="extractor")
            for index in range(10):
                stale = self._add_file(connection, f"src/stale_anchor_{index}.py")
                Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=focus_concept,
                                     relation="implemented_by", target_id=stale,
                                     source="semantic-heuristic", confidence=.8)
                (self.root / f"src/stale_anchor_{index}.py").write_text("CHANGED = 1\n", encoding="utf-8")
            for index in range(9):
                stale_peer = self._add_file(connection, f"src/stale_peer_{index}.py")
                Database.upsert_edge(connection, project_id="fixture", layer=1, source_id=stale_peer,
                                     relation="imports", target_id=later_anchor, source="extractor")
                (self.root / f"src/stale_peer_{index}.py").write_text("CHANGED = 1\n", encoding="utf-8")
            connection.commit()
        core = impact_view_data(self.database, self.project, "Focus")
        self.assertIn("src/later_anchor.py", {item["entity"]["path"] for item in core["anchorFiles"]})
        self.assertIn("src/later_peer.py", {item["peer"]["path"] for item in core["incoming"]})
        codes = {item["code"] for item in core["warnings"]}
        self.assertIn("IMPACT_ANCHOR_STALE_FILTERED", codes)
        self.assertIn("IMPACT_LANE_STALE_FILTERED", codes)
        self.assertEqual(core["sourceState"]["status"], "stale")

    def test_test_recommendations_require_real_tests_or_tested_by(self) -> None:
        focus_id = Database.node_id("fixture", 1, "file", "src/focus.py")
        focus_concept = Database.node_id("fixture", 2, "concept", "focus")
        with self.database.connect() as connection:
            misleading = self._add_file(connection, "tests/test_name_only.py")
            semantic_test = self._add_file(connection, "checks/focus_contract.py")
            Database.upsert_edge(connection, project_id="fixture", layer=1, source_id=misleading,
                                 relation="imports", target_id=focus_id, source="extractor")
            Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=focus_concept,
                                 relation="tested_by", target_id=semantic_test,
                                 source="semantic-heuristic", confidence=.8)
            connection.commit()
        recommendations = impact_view_data(self.database, self.project, "Focus")["testRecommendations"]
        paths = {item["path"] for item in recommendations}
        self.assertIn("checks/focus_contract.py", paths)
        self.assertNotIn("tests/test_name_only.py", paths)
        self.assertEqual({item["basis"] for item in recommendations}, {"physical-tests", "semantic-tested-by"})

    def test_tested_by_file_focus_uses_ownership_allowlist_not_anchor_allowlist(self) -> None:
        focus_concept = Database.node_id("fixture", 2, "concept", "focus")
        with self.database.connect() as connection:
            test_file = self._add_file(connection, "checks/focus_contract.py")
            Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=focus_concept,
                                 relation="tested_by", target_id=test_file,
                                 source="semantic-heuristic", confidence=.8)
            connection.commit()
        core = impact_view_data(self.database, self.project, "checks/focus_contract.py")
        self.assertEqual(core["anchorFiles"][0]["mapping"], None)
        self.assertEqual(core["focusConcepts"][0]["mapping"]["relation"], "tested_by")
        self.assertEqual(impact_to_view(core).data["focusConcepts"][0]["mapping"]["relation"], "tested_by")
        self.assertIn("tested_by", impact_text(core))
        broken = {**core, "focusConcepts": [{**core["focusConcepts"][0],
                  "mapping": {**core["focusConcepts"][0]["mapping"], "relation": "owns"}}]}
        with self.assertRaises(ValueError):
            impact_to_view(broken)

    def test_selector_like_metacharacters_are_literal_and_evidence_caps_are_strict(self) -> None:
        with self.assertRaises(LookupError):
            impact_view_data(self.database, self.project, "%_")
        core = impact_view_data(self.database, self.project, "src/focus.py")
        for field in ("history", "testRecommendations", "risks"):
            if not core[field]:
                continue
            broken = {**core, field: [dict(core[field][0], evidence=core[field][0]["evidence"] * 4)]}
            with self.subTest(field=field), self.assertRaises(ValueError):
                impact_to_view(broken)
        changed = dict(core["anchorFiles"][0]["entity"])
        changed["evidence"] = [{**changed["evidence"][0], "summary": "不同证据"}]
        with self.assertRaises(ValueError):
            impact_to_view({**core, "anchorFiles": [{**core["anchorFiles"][0], "entity": changed}]})
        history = dict(core["history"][0]); task = dict(history["entity"])
        task["evidence"] = [{**task["evidence"][0], "summary": "不同任务证据"}]
        history["entity"] = task
        with self.assertRaises(ValueError):
            impact_to_view({**core, "history": [history]})
        for value in (True, float("nan"), 1.25):
            broken_evidence = {**core["focus"]["evidence"][0], "lineStart": value}
            with self.subTest(line=value), self.assertRaises((TypeError, ValueError)):
                impact_to_view({**core, "focus": {**core["focus"], "evidence": [broken_evidence]}})
        for value in (True, float("nan"), 1.1, -0.1):
            broken_entity = {**core["focus"]["entity"], "confidence": value}
            with self.subTest(confidence=value), self.assertRaises((TypeError, ValueError)):
                impact_to_view({**core, "focus": {**core["focus"], "entity": broken_entity}})
        blank = {**core, "revision": "   "}
        with self.assertRaises(ValueError):
            impact_to_view(blank)
        for value in (True, -1, 1.5, "1"):
            with self.subTest(stat=value), self.assertRaises((TypeError, ValueError)):
                impact_to_view({**core, "stats": {**core["stats"], "files": value}})

    def test_large_lane_population_uses_endpoint_index_and_constant_scan_budget(self) -> None:
        focus_id = Database.node_id("fixture", 1, "file", "src/focus.py")
        now = "2026-09-15T10:00:00+00:00"
        with self.database.connect() as connection:
            nodes = [(f"bulk-node-{index}", "fixture", 1, "file", f"bulk/{index}.py",
                      f"{index}.py", "{}", 1.0, "repository", now, now)
                     for index in range(5000)]
            connection.executemany(
                "INSERT INTO nodes(id,project_id,layer,kind,key,label,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                nodes,
            )
            edges = [(f"bulk-edge-{index}", "fixture", 1, f"bulk-node-{index}", "imports",
                      focus_id, "{}", 1.0, "extractor", now, now)
                     for index in range(5000)]
            connection.executemany(
                "INSERT INTO edges(id,project_id,layer,source_id,relation,target_id,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                edges,
            )
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT edge.rowid FROM edges AS edge INDEXED BY idx_edges_target WHERE edge.project_id=? AND edge.layer=1 AND edge.target_id=? ORDER BY edge.rowid DESC LIMIT 25",
                ("fixture", focus_id),
            ).fetchall()
            connection.commit()
        detail = " ".join(str(row[3]).upper() for row in plan)
        self.assertIn("IDX_EDGES_TARGET", detail)
        self.assertNotIn("TEMP B-TREE", detail)
        data = impact_view_data(self.database, self.project, "src/focus.py")
        self.assertIn("IMPACT_LANE_SCAN_TRUNCATED", {item["code"] for item in data["warnings"]})
        self.assertLessEqual(len(data["incoming"]), 8)

    def test_large_unrelated_semantic_population_uses_endpoint_indexes_and_exact_physical_lookup(self) -> None:
        now = "2026-09-15T10:00:00+00:00"
        with self.database.connect() as connection:
            target = Database.upsert_node(connection, project_id="fixture", layer=2, kind="concept",
                                          key="bulk-target", label="Bulk target", source="semantic-heuristic")
            nodes = [(f"bulk-concept-{index}", "fixture", 2, "concept", f"bulk-{index}",
                      f"Bulk {index}", "{}", .8, "semantic-heuristic", now, now)
                     for index in range(5000)]
            connection.executemany(
                "INSERT INTO nodes(id,project_id,layer,kind,key,label,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                nodes,
            )
            edges = [(f"bulk-semantic-{index}", "fixture", 2, f"bulk-concept-{index}",
                      "depends_on", target, "{}", .7, "semantic-heuristic", now, now)
                     for index in range(5000)]
            connection.executemany(
                "INSERT INTO edges(id,project_id,layer,source_id,relation,target_id,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                edges,
            )
            focus_concept = Database.node_id("fixture", 2, "concept", "focus")
            plans = []
            for index_name, endpoint in (("idx_edges_source_semantic_v2", "source_id"),
                                         ("idx_edges_target_semantic_v2", "target_id")):
                plans.extend(connection.execute(
                    f"""EXPLAIN QUERY PLAN SELECT edge.rowid FROM edges edge INDEXED BY {index_name}
                        WHERE edge.project_id=? AND edge.layer=2 AND edge.{endpoint}=?
                          AND edge.relation NOT IN ('implemented_by','configured_by','tested_by',
                              'documented_by','data_provided_by','analyzed_by','uses_asset','produced_by')
                        ORDER BY edge.rowid DESC LIMIT 25""",
                    ("fixture", focus_concept),
                ).fetchall())
            plans.extend(connection.execute(
                "EXPLAIN QUERY PLAN SELECT edge.* FROM edges edge WHERE edge.id IN (?,?)",
                ("missing-a", "missing-b"),
            ).fetchall())
            vm_steps = 0
            def count_vm() -> int:
                nonlocal vm_steps
                vm_steps += 1
                return 0
            connection.set_progress_handler(count_vm, 1)
            _semantic_rows(connection, "fixture", focus_concept, "outgoing")
            semantic_vm_steps = vm_steps
            vm_steps = 0
            connection.execute("SELECT edge.* FROM edges edge WHERE edge.id IN (?,?)",
                               ("missing-a", "missing-b")).fetchall()
            exact_vm_steps = vm_steps
            connection.set_progress_handler(None, 0)
            connection.commit()
        details = " ".join(str(row[3]).upper() for row in plans)
        self.assertIn("IDX_EDGES_SOURCE_SEMANTIC_V2", details)
        self.assertIn("IDX_EDGES_TARGET_SEMANTIC_V2", details)
        self.assertIn("SQLITE_AUTOINDEX_EDGES_1", details)
        self.assertNotIn("TEMP B-TREE", details)
        self.assertLess(semantic_vm_steps, 1000)
        self.assertLess(exact_vm_steps, 1000)
        statements: list[str] = []
        original_connect = self.database.connect
        @contextmanager
        def traced_connect():
            with original_connect() as connection:
                connection.set_trace_callback(statements.append)
                try:
                    yield connection
                finally:
                    connection.set_trace_callback(None)
        with patch.object(self.database, "connect", traced_connect):
            data = impact_view_data(self.database, self.project, "Focus")
        self.assertIn(sum("impact-semantic-incoming" in sql for sql in statements), {1, 2})
        self.assertIn(sum("impact-semantic-outgoing" in sql for sql in statements), {1, 2})
        self.assertEqual(sum("impact-physical-lookup-exact" in sql for sql in statements), 1)
        self.assertEqual(data["semantic"][0]["peer"]["label"], "Dependency")

    def test_focus_resolution_uses_exact_indexes_with_large_unrelated_population(self) -> None:
        now = "2026-09-15T10:00:00+00:00"
        with self.database.connect() as connection:
            connection.executemany(
                "INSERT INTO nodes(id,project_id,layer,kind,key,label,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(f"resolve-bulk-{index}", "fixture", 2, "concept", f"resolve-{index}",
                  f"Resolve {index}", "{}", .8, "semantic-heuristic", now, now)
                 for index in range(10000)],
            )
            plans = connection.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM nodes INDEXED BY idx_nodes_label WHERE project_id=? AND label=? AND layer=2 AND kind='concept' LIMIT 2",
                ("fixture", "Focus"),
            ).fetchall()
            vm_steps = 0
            def count_vm() -> int:
                nonlocal vm_steps
                vm_steps += 1
                return 0
            connection.set_progress_handler(count_vm, 1)
            resolved = _resolve_focus(connection, "fixture", "Focus")
            connection.set_progress_handler(None, 0)
            connection.commit()
        detail = " ".join(str(row[3]).upper() for row in plans)
        self.assertIn("IDX_NODES_LABEL", detail)
        self.assertNotIn("TEMP B-TREE", detail)
        self.assertLess(vm_steps, 300)
        self.assertEqual(resolved["key"], "focus")
        data = impact_view_data(self.database, self.project, "src/focus.py")
        self.assertEqual(data["focus"]["entity"]["path"], "src/focus.py")

    def test_tested_by_lookup_is_per_concept_index_bounded_and_warns(self) -> None:
        now = "2026-09-15T10:00:00+00:00"
        focus = Database.node_id("fixture", 2, "concept", "focus")
        with self.database.connect() as connection:
            target = Database.node_id("fixture", 1, "file", "tests/test_focus.py")
            connection.executemany(
                "INSERT INTO nodes(id,project_id,layer,kind,key,label,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(f"tested-bulk-{index}", "fixture", 2, "concept", f"tested-bulk-{index}",
                  f"Tested bulk {index}", "{}", .8, "semantic-heuristic", now, now)
                 for index in range(5000)],
            )
            connection.executemany(
                "INSERT INTO edges(id,project_id,layer,source_id,relation,target_id,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(f"tested-edge-{index}", "fixture", 2, f"tested-bulk-{index}", "tested_by",
                  target, "{}", .8, "semantic-heuristic", now, now) for index in range(5000)],
            )
            for index in range(25):
                test_file = self._add_file(connection, f"checks/tested_{index:02d}.py")
                Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=focus,
                                     relation="tested_by", target_id=test_file,
                                     source="semantic-heuristic", confidence=.8)
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT edge.rowid FROM edges edge INDEXED BY idx_edges_source WHERE edge.project_id=? AND edge.layer=2 AND edge.source_id=? AND edge.relation='tested_by' ORDER BY edge.rowid DESC LIMIT 25",
                ("fixture", focus),
            ).fetchall()
            vm_steps = 0
            def count_vm() -> int:
                nonlocal vm_steps
                vm_steps += 1
                return 0
            connection.set_progress_handler(count_vm, 1)
            rows = _tested_by_rows(connection, "fixture", focus)
            connection.set_progress_handler(None, 0)
            connection.commit()
        detail = " ".join(str(row[3]).upper() for row in plan)
        self.assertIn("IDX_EDGES_SOURCE", detail)
        self.assertNotIn("TEMP B-TREE", detail)
        self.assertLess(vm_steps, 1500)
        self.assertEqual(len(rows), 24)
        data = impact_view_data(self.database, self.project, "Focus")
        self.assertIn("IMPACT_TESTED_BY_SCAN_TRUNCATED", {item["code"] for item in data["warnings"]})

    def test_raw_endpoint_windows_bound_invalid_edge_populations_before_filtering(self) -> None:
        self.assertNotIn("MATERIALIZED", inspect.getsource(impact_module))
        now = "2026-09-15T10:00:00+00:00"
        focus_file = Database.node_id("fixture", 1, "file", "src/focus.py")
        focus_concept = Database.node_id("fixture", 2, "concept", "focus")
        with self.database.connect() as connection:
            connection.executemany(
                "INSERT INTO nodes(id,project_id,layer,kind,key,label,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(f"raw-concept-{index}", "fixture", 2, "concept", f"raw-{index}",
                  f"Raw {index}", "{}", .8, "semantic-heuristic", now, now)
                 for index in range(5000)],
            )
            connection.executemany(
                "INSERT INTO nodes(id,project_id,layer,kind,key,label,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(f"raw-file-{index}", "fixture", 1, "file", f"raw/{index}.py",
                  f"{index}.py", "{}", 1.0, "repository", now, now)
                 for index in range(5000)],
            )
            connection.executemany(
                "INSERT INTO nodes(id,project_id,layer,kind,key,label,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(f"raw-history-{index}", "fixture", 3, "file", f"raw-history-{index}",
                  f"History {index}", "{}", 1.0, "task-events", now, now)
                 for index in range(5000)],
            )
            edge_sets = (
                [(f"raw-lane-{index}", "fixture", 1, f"raw-concept-{index}", "imports",
                  focus_file, "{}", 1.0, "extractor", now, now) for index in range(5000)],
                [(f"raw-semantic-{index}", "fixture", 2, focus_concept, "related_to",
                  f"raw-file-{index}", "{}", .8, "semantic-heuristic", now, now) for index in range(5000)],
                [(f"raw-tested-{index}", "fixture", 2, focus_concept, "tested_by",
                  f"raw-concept-{index}", "{}", .8, "semantic-heuristic", now, now) for index in range(5000)],
                [(f"raw-anchor-{index}", "fixture", 2, focus_concept, "implemented_by",
                  f"raw-concept-{index}", "{}", .8, "semantic-heuristic", now, now) for index in range(5000)],
                [(f"raw-focus-{index}", "fixture", 2, f"raw-file-{index}", "implemented_by",
                  focus_file, "{}", .8, "semantic-heuristic", now, now) for index in range(5000)],
                [(f"raw-history-edge-{index}", "fixture", 3, f"raw-history-{index}", "modified",
                  focus_file, "{}", 1.0, "task-events", now, now) for index in range(5000)],
            )
            for edges in edge_sets:
                connection.executemany(
                "INSERT INTO edges(id,project_id,layer,source_id,relation,target_id,data_json,confidence,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    edges,
                )
            step_counts = []
            results = []
            statements: list[str] = []
            connection.set_trace_callback(statements.append)
            for query in (
                lambda: _lane_rows(connection, "fixture", focus_file, "incoming"),
                lambda: _semantic_rows(connection, "fixture", focus_concept, "outgoing"),
                lambda: _tested_by_rows(connection, "fixture", focus_concept),
                lambda: _anchor_mapping_rows(connection, "fixture", focus_concept),
                lambda: _focus_concept_rows(connection, "fixture", focus_file),
                lambda: _history_rows(connection, "fixture", focus_file),
            ):
                steps = 0
                def count_vm() -> int:
                    nonlocal steps
                    steps += 1
                    return 0
                connection.set_progress_handler(count_vm, 1)
                result = query()
                connection.set_progress_handler(None, 0)
                results.append(result); step_counts.append(steps)
            connection.set_trace_callback(None)
            plans = [row for statement in statements if "impact-" in statement
                     for row in connection.execute(f"EXPLAIN QUERY PLAN {statement}").fetchall()]
            connection.commit()
        self.assertEqual([len(result) for result in results], [0, 0, 0, 0, 0, 0])
        self.assertTrue(all(result.raw_truncated for result in results))
        self.assertTrue(all(steps < 2000 for steps in step_counts), step_counts)
        detail = " ".join(str(row[3]).upper() for row in plans)
        self.assertIn("IDX_EDGES_TARGET", detail)
        self.assertIn("IDX_EDGES_SOURCE", detail)
        self.assertIn("IDX_EDGES_SOURCE_SEMANTIC_V2", detail)
        self.assertNotIn("TEMP B-TREE", detail)
        concept_data = impact_view_data(self.database, self.project, "Focus")
        file_data = impact_view_data(self.database, self.project, "src/focus.py")
        codes = {item["code"] for data in (concept_data, file_data) for item in data["warnings"]}
        self.assertIn("IMPACT_LANE_SCAN_TRUNCATED", codes)
        self.assertIn("IMPACT_SEMANTIC_SCAN_TRUNCATED", codes)
        self.assertIn("IMPACT_TESTED_BY_SCAN_TRUNCATED", codes)
        self.assertIn("IMPACT_ANCHOR_SCAN_TRUNCATED", codes)
        self.assertIn("IMPACT_FOCUS_CONCEPTS_TRUNCATED", codes)
        self.assertIn("IMPACT_HISTORY_SCAN_TRUNCATED", codes)

    def test_l1_edges_do_not_starve_focus_concepts_or_l3_history(self) -> None:
        focus_file = Database.node_id("fixture", 1, "file", "src/focus.py")
        with self.database.connect() as connection:
            for index in range(40):
                caller = self._add_file(connection, f"src/starvation_caller_{index:02d}.py")
                Database.upsert_edge(connection, project_id="fixture", layer=1, source_id=caller,
                                     relation="imports", target_id=focus_file, source="extractor")
            connection.commit()
        data = impact_view_data(self.database, self.project, "src/focus.py")
        self.assertIn("Focus", {item["entity"]["label"] for item in data["focusConcepts"]})
        self.assertEqual(data["history"][0]["entity"]["label"], "修改 Focus")

    def test_l3_edges_do_not_starve_l1_incoming_and_tests(self) -> None:
        focus_file = Database.node_id("fixture", 1, "file", "src/focus.py")
        now = "2026-09-15T10:00:00+00:00"
        with self.database.connect() as connection:
            for index in range(45):
                task_id = f"starvation-task-{index}"
                connection.execute(
                    "INSERT INTO tasks(id,project_id,title,status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (task_id, "fixture", f"Task {index}", "completed", now, now),
                )
                task = Database.upsert_node(connection, project_id="fixture", layer=3, kind="task",
                                            key=task_id, label=f"Task {index}", source="task-events")
                Database.upsert_edge(connection, project_id="fixture", layer=3, source_id=task,
                                     relation="modified", target_id=focus_file, source="task-events")
            connection.commit()
        data = impact_view_data(self.database, self.project, "src/focus.py")
        incoming = {item["peer"]["path"] for item in data["incoming"]}
        self.assertIn("src/caller.py", incoming)
        self.assertIn("tests/test_focus.py", incoming)
        self.assertIn("tests/test_focus.py", {item["path"] for item in data["testRecommendations"]})

    def test_l2_semantic_edges_do_not_starve_anchor_mappings(self) -> None:
        focus = Database.node_id("fixture", 2, "concept", "focus")
        with self.database.connect() as connection:
            for index in range(40):
                peer = Database.upsert_node(connection, project_id="fixture", layer=2, kind="concept",
                                            key=f"starvation-peer-{index}", label=f"Peer {index}",
                                            source="semantic-heuristic", confidence=.8)
                Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=focus,
                                     relation="related_to", target_id=peer,
                                     source="semantic-heuristic", confidence=.7)
            connection.commit()
        data = impact_view_data(self.database, self.project, "Focus")
        self.assertIn("src/focus.py", {item["entity"]["path"] for item in data["anchorFiles"]})

    def test_mapping_categories_do_not_starve_bidirectional_semantic_edges(self) -> None:
        focus = Database.node_id("fixture", 2, "concept", "focus")
        dependency = Database.node_id("fixture", 2, "concept", "dependency")
        with self.database.connect() as connection:
            for index in range(40):
                mapping_file = self._add_file(connection, f"src/mapping_{index:02d}.py")
                Database.upsert_edge(
                    connection, project_id="fixture", layer=2, source_id=focus,
                    relation="implemented_by", target_id=mapping_file,
                    source="semantic-heuristic", confidence=.8,
                )
                document_file = self._add_file(connection, f"docs/mapping_{index:02d}.md")
                Database.upsert_edge(
                    connection, project_id="fixture", layer=2, source_id=focus,
                    relation="documented_by", target_id=document_file,
                    source="semantic-heuristic", confidence=.8,
                )
                dataset_file = self._add_file(connection, f"data/mapping_{index:02d}.csv")
                Database.upsert_edge(
                    connection, project_id="fixture", layer=2, source_id=focus,
                    relation="data_provided_by", target_id=dataset_file,
                    source="semantic-heuristic", confidence=.8,
                )
            for index in range(40):
                mapping_source = Database.upsert_node(
                    connection, project_id="fixture", layer=2, kind="concept",
                    key=f"incoming-mapping-{index}", label=f"Incoming mapping {index}",
                    source="semantic-heuristic", confidence=.8,
                )
                for relation in ("configured_by", "tested_by", "documented_by",
                                 "data_provided_by"):
                    Database.upsert_edge(
                        connection, project_id="fixture", layer=2, source_id=mapping_source,
                        relation=relation, target_id=focus,
                        source="semantic-heuristic", confidence=.8,
                    )
            Database.upsert_edge(
                connection, project_id="fixture", layer=2, source_id=dependency,
                relation="depends_on", target_id=focus,
                source="human-overlay", confidence=1.0,
            )
            connection.commit()

        data = impact_view_data(self.database, self.project, "Focus")
        relations = {(item["direction"], item["relation"]["relation"], item["peer"]["label"])
                     for item in data["semantic"]}
        self.assertIn(("outgoing", "depends_on", "Dependency"), relations)
        self.assertIn(("incoming", "depends_on", "Dependency"), relations)

        with self.database.connect() as connection:
            for direction in ("outgoing", "incoming"):
                rows = _semantic_rows(connection, "fixture", focus, direction)
                self.assertTrue(rows)
                self.assertTrue(all(row["relation"] not in CONCEPT_FILE_MAPPING_RELATIONS
                                    for row in rows))

    def test_new_category_indexes_are_created_for_an_existing_database(self) -> None:
        names = {"idx_edges_source_relation", "idx_edges_target_relation",
                 "idx_edges_target_provenance", "idx_edges_source_semantic_v2",
                 "idx_edges_target_semantic_v2"}
        with self.database.connect() as connection:
            for name in names:
                connection.execute(f"DROP INDEX {name}")
            connection.commit()
        ensure_database(self.database.settings)
        with self.database.connect() as connection:
            existing = {str(row[1]) for row in connection.execute("PRAGMA index_list(edges)")}
        self.assertTrue(names <= existing)

    def test_semantic_per_concept_scan_budget_is_visible(self) -> None:
        focus = Database.node_id("fixture", 2, "concept", "focus")
        with self.database.connect() as connection:
            for index in range(25):
                peer = Database.upsert_node(connection, project_id="fixture", layer=2,
                                            kind="concept", key=f"peer-{index}", label=f"Peer {index}",
                                            source="semantic-heuristic", confidence=.8)
                Database.upsert_edge(connection, project_id="fixture", layer=2, source_id=focus,
                                     relation="related_to", target_id=peer,
                                     source="semantic-heuristic", confidence=.7)
            connection.commit()
        data = impact_view_data(self.database, self.project, "Focus")
        self.assertIn("IMPACT_SEMANTIC_SCAN_TRUNCATED", {item["code"] for item in data["warnings"]})

    def test_unindexed_warning_is_generated_once(self) -> None:
        with self.database.connect() as connection:
            connection.execute("UPDATE projects SET last_scan_at=NULL WHERE id='fixture'")
            connection.commit()
            project = connection.execute("SELECT * FROM projects WHERE id='fixture'").fetchone()
        view = impact_to_view(impact_view_data(self.database, project, "src/focus.py")).to_dict()
        self.assertEqual([item["code"] for item in view["warnings"]].count("SOURCE_NOT_INDEXED"), 1)


if __name__ == "__main__":
    unittest.main()
