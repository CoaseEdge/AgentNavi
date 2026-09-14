from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.mcp.adapters.repo_tour import repo_tour_text, repo_tour_view
from agentnavi.repository_views import _tour_graph_facts, repository_tour_data


class RepositoryTourTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.root = self.base / "private" / "fixture"
        (self.root / "docs").mkdir(parents=True)
        (self.root / "src" / "fixture").mkdir(parents=True)
        (self.root / "tests").mkdir()
        files = {
            "README.md": (
                "# Fixture\n\n## 一句话理解\n\n帮助新成员理解仓库。\n\n"
                "## 问题定义\n\n陌生协作者容易遗漏上下文。\n\n"
                "## 解决方式\n\n用可追溯证据提供导航。\n"
            ),
            "docs/architecture.md": (
                "# Architecture\n\n## 主流程\n\n1. 接收请求\n2. 解析项目\n"
                "3. 读取文档\n4. 汇总事实\n5. 生成导航\n6. 投影视图\n7. 返回证据\n"
            ),
            "docs/adr/0001-model.md": (
                "# 模型边界\n\n使用不可变记录表达对外数据，避免展示层读取数据库行。\n"
            ),
            "pyproject.toml": (
                "[project]\nname='fixture'\n[project.scripts]\nfixture='fixture.cli:run'\n"
            ),
            "src/fixture/cli.py": "def run():\n    return 0\n",
            "src/fixture/model.py": "class PublicModel:\n    pass\n",
            "tests/test_cli.py": "def test_cli():\n    assert True\n",
        }
        for relative, content in files.items():
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_text(content, encoding="utf-8")

        self.database = ensure_database(Settings.load(self.base / "agentnavi-home"))
        now = "2026-09-15T10:00:00+00:00"
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO projects(id, name, root, kind, created_at, updated_at, last_scan_at)
                   VALUES ('fixture', 'Fixture', ?, 'software', ?, ?, ?)""",
                (str(self.root.resolve()), now, now, now),
            )
            file_ids: dict[str, str] = {}
            for relative in files:
                file_ids[relative] = Database.upsert_node(
                    connection,
                    project_id="fixture",
                    layer=1,
                    kind="file",
                    key=relative,
                    label=Path(relative).name,
                    source="repository",
                )
                absolute = self.root / relative
                stat = absolute.stat()
                connection.execute(
                    """INSERT INTO file_state(project_id, path, mtime_ns, size, digest, updated_at)
                       VALUES ('fixture', ?, ?, ?, ?, ?)""",
                    (
                        relative,
                        stat.st_mtime_ns,
                        stat.st_size,
                        hashlib.blake2s(absolute.read_bytes()).hexdigest(),
                        now,
                    ),
                )
            concept_id = Database.upsert_node(
                connection,
                project_id="fixture",
                layer=2,
                kind="concept",
                key="runtime",
                label="Runtime",
                data={"active": True},
                confidence=0.8,
                source="semantic-heuristic",
            )
            for relative in ("src/fixture/cli.py", "src/fixture/model.py", "pyproject.toml"):
                Database.upsert_edge(
                    connection,
                    project_id="fixture",
                    layer=2,
                    source_id=concept_id,
                    relation="implemented_by",
                    target_id=file_ids[relative],
                    confidence=0.75,
                    source="semantic-heuristic",
                )
            docs_id = Database.upsert_node(
                connection,
                project_id="fixture",
                layer=2,
                kind="concept",
                key="documentation",
                label="Documentation",
                data={"active": True},
                confidence=0.78,
                source="semantic-heuristic",
            )
            for relative in ("README.md", "docs/architecture.md"):
                Database.upsert_edge(
                    connection,
                    project_id="fixture",
                    layer=2,
                    source_id=docs_id,
                    relation="documented_by",
                    target_id=file_ids[relative],
                    source="semantic-heuristic",
                )
            symbol_id = Database.upsert_node(
                connection,
                project_id="fixture",
                layer=1,
                kind="symbol",
                key="src/fixture/model.py#symbol:class:PublicModel",
                label="PublicModel",
                data={"parent_path": "src/fixture/model.py", "symbol_kind": "class"},
                source="extractor",
            )
            Database.upsert_edge(
                connection,
                project_id="fixture",
                layer=1,
                source_id=file_ids["src/fixture/model.py"],
                relation="contains",
                target_id=symbol_id,
                source="extractor",
            )
            for source_path, relation, target_path in (
                ("src/fixture/cli.py", "imports", "src/fixture/model.py"),
                ("tests/test_cli.py", "tests", "src/fixture/cli.py"),
            ):
                Database.upsert_edge(
                    connection,
                    project_id="fixture",
                    layer=1,
                    source_id=file_ids[source_path],
                    relation=relation,
                    target_id=file_ids[target_path],
                    source="extractor",
                )
            connection.execute(
                """INSERT INTO tasks(
                       id, project_id, agent, title, status, summary,
                       created_at, updated_at, closed_at
                   ) VALUES (
                       'task-tour', 'fixture', 'codex', '调整模型边界', 'completed',
                       '完成不可变数据结构', ?, ?, ?
                   )""",
                (now, now, now),
            )
            task_node = Database.upsert_node(
                connection,
                project_id="fixture",
                layer=3,
                kind="task",
                key="task-tour",
                label="调整模型边界",
                source="task-events",
            )
            Database.upsert_edge(
                connection,
                project_id="fixture",
                layer=3,
                source_id=task_node,
                relation="modified",
                target_id=file_ids["src/fixture/model.py"],
                source="task-events",
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _project(self):
        with self.database.connect() as connection:
            return connection.execute("SELECT * FROM projects WHERE id='fixture'").fetchone()

    def test_core_returns_three_deterministic_evidence_only_depths_read_only(self) -> None:
        before = self.database.settings.database_path.read_bytes()
        first = repository_tour_data(self.database, self._project())
        second = repository_tour_data(self.database, self._project())

        self.assertEqual(before, self.database.settings.database_path.read_bytes())
        self.assertEqual(first, second)
        self.assertEqual(
            [tier["depth"] for tier in first["tiers"]],
            ["one-minute", "five-minutes", "source-deep-dive"],
        )
        self.assertEqual(len(first["tiers"][0]["stops"]), 4)
        self.assertEqual(
            {stop["kind"] for stop in first["tiers"][0]["stops"]},
            {"purpose", "why", "workflow", "module"},
        )
        one_minute = {stop["kind"]: stop for stop in first["tiers"][0]["stops"]}
        self.assertEqual(len(one_minute["workflow"]["evidence"]), 7)
        self.assertIn("接收请求", one_minute["workflow"]["plainLanguage"])
        self.assertIn("返回证据", one_minute["workflow"]["plainLanguage"])
        self.assertIn("Runtime", one_minute["module"]["plainLanguage"])
        self.assertIn("Documentation", one_minute["module"]["plainLanguage"])
        self.assertEqual(one_minute["module"]["relations"], [])
        self.assertEqual(
            {stop["kind"] for stop in first["tiers"][1]["stops"]},
            {
                "purpose", "why", "workflow", "module", "data-structure",
                "task", "file", "history",
            },
        )
        self.assertEqual(
            {stop["kind"] for stop in first["tiers"][2]["stops"]},
            {"file", "symbol", "dependency", "test", "task-history", "evidence"},
        )
        for tier in first["tiers"]:
            for stop in tier["stops"]:
                self.assertTrue(stop["kind"])
                self.assertTrue(stop["plainLanguage"])
                self.assertTrue(stop["technicalExplanation"])
                self.assertTrue(stop["evidence"])
                self.assertTrue(stop["entity"])
        deep = first["tiers"][2]["stops"]
        self.assertTrue(deep[0]["entity"]["path"].startswith("src/"))
        with self.database.connect() as connection:
            edges = {
                row["id"]: row
                for row in connection.execute("SELECT * FROM edges WHERE project_id='fixture'")
            }
        for relation in [edge for stop in deep for edge in stop["relations"]]:
            row = edges[relation["id"]]
            self.assertEqual(relation["sourceId"], row["source_id"])
            self.assertEqual(relation["targetId"], row["target_id"])
            self.assertEqual(relation["relation"], row["relation"])
            self.assertEqual(relation["source"], row["source"])
            self.assertEqual(relation["confidence"], row["confidence"])
            self.assertTrue(relation["evidence"])

    def test_missing_evidence_omits_unsupported_stops_and_warns(self) -> None:
        for relative in ("README.md", "docs/architecture.md"):
            (self.root / relative).unlink()

        tour = repository_tour_data(self.database, self._project())

        self.assertEqual(tour["sourceState"]["status"], "stale")
        self.assertEqual(
            {stop["kind"] for stop in tour["tiers"][0]["stops"]},
            {"module"},
        )
        self.assertIn(
            "TOUR_EVIDENCE_INSUFFICIENT",
            {warning["code"] for warning in tour["warnings"]},
        )
        self.assertFalse(
            any(
                not stop["evidence"]
                for tier in tour["tiers"]
                for stop in tier["stops"]
            )
        )

        with self.database.connect() as connection:
            connection.execute("UPDATE projects SET last_scan_at=NULL WHERE id='fixture'")
            connection.commit()
        partial = repository_tour_data(self.database, self._project())
        self.assertIn("SOURCE_NOT_INDEXED", repo_tour_text(partial))

    def test_adapter_allowlists_fields_and_text_is_independent(self) -> None:
        core = repository_tour_data(self.database, self._project())
        core["rawRows"] = [{"root": str(self.root), "content": "secret"}]
        core["tiers"][0]["stops"][0]["private"] = str(self.root)

        view = repo_tour_view(core).to_dict()
        wire = json.dumps(view, ensure_ascii=False)
        self.assertEqual(view["view"], "repo-tour")
        self.assertNotIn("rawRows", wire)
        self.assertNotIn("private", wire)
        self.assertNotIn(str(self.root), wire)

        with patch(
            "agentnavi.mcp.adapters.repo_tour.repo_tour_view",
            side_effect=AssertionError("text must not call view"),
        ):
            text = repo_tour_text(core)
        self.assertIn("1 分钟", text)
        self.assertIn("技术说明", text)
        self.assertIn("证据：", text)
        self.assertNotIn(str(self.root), text)

    def test_adapter_rejects_invalid_tiers_confidence_and_empty_edge_evidence(self) -> None:
        core = repository_tour_data(self.database, self._project())
        invalid_cases = []
        duplicate = json.loads(json.dumps(core))
        duplicate["tiers"][2]["depth"] = "one-minute"
        invalid_cases.append(duplicate)
        over_limit = json.loads(json.dumps(core))
        over_limit["tiers"][0]["stops"].append(over_limit["tiers"][0]["stops"][0])
        invalid_cases.append(over_limit)
        confidence = json.loads(json.dumps(core))
        confidence["tiers"][0]["stops"][0]["entity"]["confidence"] = 1.1
        invalid_cases.append(confidence)
        empty_edge = json.loads(json.dumps(core))
        relation = next(
            edge
            for tier in empty_edge["tiers"]
            for stop in tier["stops"]
            for edge in stop["relations"]
        )
        relation["evidence"] = []
        invalid_cases.append(empty_edge)

        for invalid in invalid_cases:
            with self.subTest():
                with self.assertRaises((TypeError, ValueError)):
                    repo_tour_view(invalid)

    def test_class_and_test_survive_large_other_categories(self) -> None:
        with self.database.connect() as connection:
            model_id = Database.node_id("fixture", 1, "file", "src/fixture/model.py")
            for index in range(30):
                symbol_id = Database.upsert_node(
                    connection,
                    project_id="fixture",
                    layer=1,
                    kind="symbol",
                    key=f"src/fixture/model.py#symbol:function:f{index:02d}",
                    label=f"f{index:02d}",
                    data={"symbol_kind": "function"},
                    source="extractor",
                )
                Database.upsert_edge(
                    connection,
                    project_id="fixture",
                    layer=1,
                    source_id=model_id,
                    relation="contains",
                    target_id=symbol_id,
                    source="extractor",
                )
            paths = []
            ids = []
            for index in range(35):
                path = f"src/generated/f{index:02d}.py"
                paths.append(path)
                ids.append(Database.upsert_node(
                    connection,
                    project_id="fixture",
                    layer=1,
                    kind="file",
                    key=path,
                    label=path,
                    source="filesystem",
                ))
            for index in range(33):
                Database.upsert_edge(
                    connection,
                    project_id="fixture",
                    layer=1,
                    source_id=ids[index],
                    relation="imports",
                    target_id=ids[index + 1],
                    source="extractor",
                )
            Database.upsert_edge(
                connection,
                project_id="fixture",
                layer=1,
                source_id=ids[-1],
                relation="tests",
                target_id=ids[0],
                source="extractor",
            )
            facts = _tour_graph_facts(
                connection,
                "fixture",
                {"src/fixture/model.py", *paths},
            )

        self.assertEqual(facts["data_structures"][0]["label"], "PublicModel")
        self.assertEqual(len(facts["dependencies"]), 4)
        self.assertEqual(len(facts["tests"]), 1)

    def test_unsafe_recent_tasks_do_not_crowd_out_safe_completed_task(self) -> None:
        with self.database.connect() as connection:
            for index in range(3):
                connection.execute(
                    """INSERT INTO tasks(
                           id, project_id, title, status, summary,
                           created_at, updated_at, closed_at
                       ) VALUES (?, 'fixture', ?, 'completed', '不可显示', ?, ?, ?)""",
                    (
                        f"unsafe-{index}",
                        f"/private/task-{index}",
                        f"2026-09-15T11:0{index}:00+00:00",
                        f"2026-09-15T11:0{index}:00+00:00",
                        f"2026-09-15T11:0{index}:00+00:00",
                    ),
                )
            connection.commit()

        tour = repository_tour_data(self.database, self._project())
        task_stop = next(
            stop for stop in tour["tiers"][1]["stops"] if stop["kind"] == "task"
        )
        self.assertEqual(task_stop["plainLanguage"], "调整模型边界")

    def test_more_than_sixteen_unsafe_tasks_do_not_hide_safe_task(self) -> None:
        with self.database.connect() as connection:
            for index in range(17):
                moment = f"2026-09-15T11:00:{index:02d}+00:00"
                connection.execute(
                    """INSERT INTO tasks(
                           id, project_id, title, status, summary,
                           created_at, updated_at, closed_at
                       ) VALUES (?, 'fixture', ?, 'completed', 'unsafe', ?, ?, ?)""",
                    (f"unsafe-window-{index:02d}", f"/private/task-{index}", moment, moment, moment),
                )
            connection.commit()

        tour = repository_tour_data(self.database, self._project())
        task_stop = next(
            stop for stop in tour["tiers"][1]["stops"] if stop["kind"] == "task"
        )
        self.assertEqual(task_stop["plainLanguage"], "调整模型边界")

    def test_more_than_twenty_four_unsafe_histories_do_not_hide_safe_history(self) -> None:
        with self.database.connect() as connection:
            file_id = Database.node_id("fixture", 1, "file", "src/fixture/model.py")
            for index in range(25):
                moment = f"2026-09-15T11:00:{index:02d}+00:00"
                task_id = f"unsafe-history-{index:02d}"
                connection.execute(
                    """INSERT INTO tasks(
                           id, project_id, title, status, summary,
                           created_at, updated_at, closed_at
                       ) VALUES (?, 'fixture', ?, 'completed', 'unsafe', ?, ?, ?)""",
                    (task_id, f"/private/history-{index}", moment, moment, moment),
                )
                task_node = Database.upsert_node(
                    connection,
                    project_id="fixture",
                    layer=3,
                    kind="task",
                    key=task_id,
                    label=f"Unsafe {index}",
                    source="task-events",
                )
                Database.upsert_edge(
                    connection,
                    project_id="fixture",
                    layer=3,
                    source_id=task_node,
                    relation="modified",
                    target_id=file_id,
                    source="task-events",
                )
            connection.commit()

        tour = repository_tour_data(self.database, self._project())
        history_stop = next(
            stop
            for stop in tour["tiers"][2]["stops"]
            if stop["kind"] == "task-history"
        )
        self.assertEqual(history_stop["plainLanguage"], "调整模型边界")

    def test_real_entity_refs_match_node_rows_in_snapshot(self) -> None:
        overrides = {
            Database.node_id("fixture", 1, "file", "src/fixture/cli.py"):
                ("CLI Entry Node", "scan-file", 0.61),
            Database.node_id("fixture", 1, "file", "docs/adr/0001-model.md"):
                ("ADR File Node", "scan-doc", 0.62),
            Database.node_id("fixture", 3, "task", "task-tour"):
                ("Task Event Node", "event-replay", 0.63),
        }
        with self.database.connect() as connection:
            for node_id, (label, source, confidence) in overrides.items():
                connection.execute(
                    "UPDATE nodes SET label=?, source=?, confidence=? WHERE id=?",
                    (label, source, confidence, node_id),
                )
            connection.commit()

        tour = repository_tour_data(self.database, self._project())
        with self.database.connect() as connection:
            rows = {
                row["id"]: row
                for row in connection.execute("SELECT * FROM nodes WHERE project_id='fixture'")
            }

        matched_ids = set()
        for tier in tour["tiers"]:
            for stop in tier["stops"]:
                entity = stop["entity"]
                row = rows.get(entity["id"])
                if row is None:
                    continue
                matched_ids.add(entity["id"])
                self.assertEqual(entity["kind"], row["kind"])
                self.assertEqual(entity["label"], row["label"])
                self.assertEqual(entity["layer"], f"L{row['layer']}")
                self.assertEqual(entity["source"], row["source"])
                self.assertEqual(entity["confidence"], row["confidence"])

        self.assertTrue(set(overrides).issubset(matched_ids))
        adr = next(
            stop for stop in tour["tiers"][1]["stops"] if stop["kind"] == "history"
        )
        self.assertEqual(adr["entity"]["kind"], "file")


if __name__ == "__main__":
    unittest.main()
