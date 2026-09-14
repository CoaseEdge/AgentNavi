from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.mcp.adapters.architecture import architecture_text, architecture_view
from agentnavi.mcp.adapters.flow import flow_text, flow_view
from agentnavi.repository_views import (
    repository_architecture_data,
    repository_flow_data,
)


class ArchitectureFlowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.root = self.base / "private" / "fixture"
        (self.root / "docs").mkdir(parents=True)
        (self.root / "src" / "fixture").mkdir(parents=True)
        files = {
            "README.md": (
                "# Fixture\n\n## 一句话理解\n\n为 Agent 提供可追溯的项目导航。\n\n"
                "## 问题定义\n\n任务需要快速找到真实入口。\n\n"
                "## 解决方式\n\n用三层图谱缩小候选集。\n"
            ),
            "docs/architecture.md": (
                "# Architecture\n\n## 主流程\n\n"
                "1. CLI 接收任务\n"
                "2. Scanner 扫描仓库\n"
                "3. Graph 组织事实\n"
                "4. Query 缩小候选\n"
                "5. MCP 把 Context 交给 Agent\n"
            ),
            "pyproject.toml": (
                "[project]\nname='fixture'\n[project.scripts]\nfixture='fixture.cli:run'\n"
            ),
            "src/fixture/cli.py": "def run():\n    return 0\n",
            "src/fixture/scanner.py": "def scan():\n    return []\n",
            "src/fixture/graph.py": "class Graph:\n    pass\n",
            "src/fixture/query.py": "def query():\n    return []\n",
            "src/fixture/mcp.py": "def deliver():\n    return {}\n",
        }
        for relative, content in files.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

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

            concept_ids: dict[str, str] = {}
            for key, label, path in (
                ("entry", "CLI", "src/fixture/cli.py"),
                ("scanner", "Scanner", "src/fixture/scanner.py"),
                ("graph", "Graph", "src/fixture/graph.py"),
                ("query", "Query", "src/fixture/query.py"),
                ("mcp", "MCP", "src/fixture/mcp.py"),
            ):
                concept_ids[key] = Database.upsert_node(
                    connection,
                    project_id="fixture",
                    layer=2,
                    kind="concept",
                    key=key,
                    label=label,
                    data={"active": True, "keywords": [key]},
                    confidence=0.8,
                    source="semantic-heuristic",
                )
                Database.upsert_edge(
                    connection,
                    project_id="fixture",
                    layer=2,
                    source_id=concept_ids[key],
                    relation="implemented_by",
                    target_id=file_ids[path],
                    data={"path": path},
                    confidence=0.75,
                    source="semantic-heuristic",
                )

            for source, target, source_path, target_path in (
                ("entry", "scanner", "src/fixture/cli.py", "src/fixture/scanner.py"),
                ("scanner", "graph", "src/fixture/scanner.py", "src/fixture/graph.py"),
                ("graph", "query", "src/fixture/graph.py", "src/fixture/query.py"),
                ("query", "mcp", "src/fixture/query.py", "src/fixture/mcp.py"),
            ):
                Database.upsert_edge(
                    connection,
                    project_id="fixture",
                    layer=2,
                    source_id=concept_ids[source],
                    relation="depends_on",
                    target_id=concept_ids[target],
                    data={
                        "evidence": [{
                            "source": source_path,
                            "target": target_path,
                            "physical_relation": "imports",
                        }]
                    },
                    confidence=0.78,
                    source="semantic-heuristic",
                )
                Database.upsert_edge(
                    connection,
                    project_id="fixture",
                    layer=1,
                    source_id=file_ids[source_path],
                    relation="imports",
                    target_id=file_ids[target_path],
                    source="extractor",
                )

            connection.execute(
                """INSERT INTO tasks(
                       id, project_id, title, status, summary,
                       created_at, updated_at, closed_at
                   ) VALUES (
                       'task-flow', 'fixture', '修改会员升级', 'completed',
                       '完成上下文导航', ?, ?, ?
                   )""",
                (now, now, now),
            )
            Database.upsert_node(
                connection,
                project_id="fixture",
                layer=3,
                kind="task",
                key="task-flow",
                label="修改会员升级",
                source="task-events",
            )
            connection.commit()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _project(self):
        with self.database.connect() as connection:
            return connection.execute("SELECT * FROM projects WHERE id='fixture'").fetchone()

    def test_architecture_is_deterministic_fixed_layout_of_real_facts(self) -> None:
        before = self.database.settings.database_path.read_bytes()
        first = repository_architecture_data(self.database, self._project())
        second = repository_architecture_data(self.database, self._project())

        self.assertEqual(before, self.database.settings.database_path.read_bytes())
        self.assertEqual(first, second)
        self.assertEqual(first["layout"], "cognitive-components")
        self.assertGreaterEqual(len(first["components"]), 5)
        self.assertLessEqual(len(first["components"]), 8)
        self.assertEqual(first["entryPoints"][0]["path"], "src/fixture/cli.py")
        self.assertTrue(first["summary"]["text"])
        self.assertTrue(first["summary"]["evidence"])
        self.assertEqual(len(first["connections"]), 4)

        with self.database.connect() as connection:
            nodes = {
                row["id"]: row
                for row in connection.execute("SELECT * FROM nodes WHERE project_id='fixture'")
            }
            edges = {
                row["id"]: row
                for row in connection.execute("SELECT * FROM edges WHERE project_id='fixture'")
            }
        for component in first["components"]:
            row = nodes[component["entity"]["id"]]
            self.assertEqual(component["entity"]["label"], row["label"])
            self.assertIn(component["group"], {"entry", "core", "support"})
            self.assertTrue(component["evidence"])
        for relation in first["connections"]:
            row = edges[relation["id"]]
            self.assertEqual(relation["sourceId"], row["source_id"])
            self.assertEqual(relation["targetId"], row["target_id"])
            self.assertEqual(relation["relation"], row["relation"])
            self.assertTrue(relation["evidence"])
        component_ids = {component["id"] for component in first["components"]}
        self.assertTrue(all(
            relation["sourceId"] in component_ids and relation["targetId"] in component_ids
            for relation in first["connections"]
        ))

    def test_forged_and_stale_physical_evidence_is_never_a_connection(self) -> None:
        entry_id = Database.node_id("fixture", 2, "concept", "entry")
        scanner_id = Database.node_id("fixture", 2, "concept", "scanner")
        edge_id = Database.edge_id("fixture", 2, entry_id, "depends_on", scanner_id)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE edges SET data_json=? WHERE id=?",
                (json.dumps({"evidence": [{
                    "source": "src/fixture/cli.py",
                    "target": "src/fixture/scanner.py",
                    "physical_relation": "forged",
                }]}), edge_id),
            )
            connection.commit()

        forged = repository_architecture_data(self.database, self._project())
        self.assertNotIn(edge_id, {edge["id"] for edge in forged["connections"]})
        self.assertIn(
            "ARCHITECTURE_CONNECTIONS_TRUNCATED",
            {warning["code"] for warning in forged["warnings"]},
        )

        graph_file = self.root / "src" / "fixture" / "graph.py"
        graph_file.write_text("class ReplacedGraph:\n    pass\n", encoding="utf-8")
        stale = repository_architecture_data(self.database, self._project())
        selected_paths = {
            path
            for component in stale["components"]
            for path in component["paths"]
        }
        self.assertNotIn("src/fixture/graph.py", selected_paths)
        self.assertIn(
            "SOURCE_SNAPSHOT_STALE",
            {warning["code"] for warning in stale["warnings"]},
        )

    def test_connection_cap_keeps_endpoints_and_preserves_a_real_cycle(self) -> None:
        concept_ids = [
            Database.node_id("fixture", 2, "concept", key)
            for key in ("entry", "scanner", "graph", "query", "mcp")
        ]
        with self.database.connect() as connection:
            cycle_id = Database.upsert_edge(
                connection,
                project_id="fixture",
                layer=2,
                source_id=concept_ids[-1],
                relation="depends_on",
                target_id=concept_ids[0],
                source="human-overlay",
            )
            for source_id in concept_ids:
                for target_id in concept_ids:
                    if source_id == target_id:
                        continue
                    Database.upsert_edge(
                        connection,
                        project_id="fixture",
                        layer=2,
                        source_id=source_id,
                        relation="related_to",
                        target_id=target_id,
                        source="human-overlay",
                    )
            connection.commit()

        architecture = repository_architecture_data(self.database, self._project())
        component_ids = {component["id"] for component in architecture["components"]}
        connection_ids = {edge["id"] for edge in architecture["connections"]}
        self.assertEqual(len(architecture["connections"]), 12)
        self.assertIn(cycle_id, connection_ids)
        self.assertTrue(all(
            edge["sourceId"] in component_ids and edge["targetId"] in component_ids
            for edge in architecture["connections"]
        ))
        self.assertIn(
            "ARCHITECTURE_CONNECTIONS_TRUNCATED",
            {warning["code"] for warning in architecture["warnings"]},
        )

    def test_flow_has_five_to_seven_evidence_backed_expandable_steps(self) -> None:
        flow = repository_flow_data(self.database, self._project(), "修改会员升级")

        self.assertEqual(flow["layout"], "numbered-task-flow")
        self.assertEqual(flow["exampleTask"]["title"], "修改会员升级")
        self.assertEqual(len(flow["steps"]), 5)
        self.assertEqual([step["step"] for step in flow["steps"]], [1, 2, 3, 4, 5])
        self.assertEqual(flow["steps"][0]["input"], "进入主流程的请求或数据")
        self.assertEqual(flow["steps"][-1]["output"], "主流程结果")
        self.assertIsNone(flow["steps"][-1]["nextStep"])
        self.assertTrue(all(step["purpose"] and step["why"] for step in flow["steps"]))
        self.assertTrue(all(step["evidence"] for step in flow["steps"]))
        self.assertTrue(all(len(step["keyFiles"]) <= 3 for step in flow["steps"]))
        self.assertEqual(flow["steps"][0]["keyFiles"][0]["path"], "src/fixture/cli.py")
        self.assertEqual(flow["steps"][1]["keyFiles"][0]["path"], "src/fixture/scanner.py")

    def test_install_list_is_not_presented_as_execution_flow(self) -> None:
        document = self.root / "docs" / "architecture.md"
        document.write_text(
            "# Setup\n\n## 安装步骤\n\n1. 下载\n2. 解压\n3. 配置\n4. 安装\n5. 启动\n",
            encoding="utf-8",
        )
        stat = document.stat()
        with self.database.connect() as connection:
            connection.execute(
                """UPDATE file_state SET mtime_ns=?, size=?, digest=?
                   WHERE project_id='fixture' AND path='docs/architecture.md'""",
                (
                    stat.st_mtime_ns,
                    stat.st_size,
                    hashlib.blake2s(document.read_bytes()).hexdigest(),
                ),
            )
            connection.commit()

        flow = repository_flow_data(self.database, self._project())

        self.assertEqual(flow["steps"], [])
        self.assertIn(
            "FLOW_EXECUTION_EVIDENCE_MISSING",
            {warning["code"] for warning in flow["warnings"]},
        )

    def test_adapters_are_allowlisted_independent_and_reject_malformed_layouts(self) -> None:
        architecture = repository_architecture_data(self.database, self._project())
        flow = repository_flow_data(self.database, self._project(), "修改会员升级")
        architecture["private"] = str(self.root)
        flow["private"] = str(self.root)

        architecture_wire = architecture_view(architecture).to_dict()
        flow_wire = flow_view(flow).to_dict()
        self.assertEqual(architecture_wire["view"], "architecture")
        self.assertEqual(flow_wire["view"], "flow")
        self.assertNotIn("private", json.dumps(architecture_wire, ensure_ascii=False))
        self.assertNotIn("private", json.dumps(flow_wire, ensure_ascii=False))
        with patch(
            "agentnavi.mcp.adapters.architecture.architecture_view",
            side_effect=AssertionError("text must not call view"),
        ):
            self.assertIn("系统架构", architecture_text(architecture))
        with patch(
            "agentnavi.mcp.adapters.flow.flow_view",
            side_effect=AssertionError("text must not call view"),
        ):
            self.assertIn("任务流", flow_text(flow))

        invalid_endpoint = json.loads(json.dumps(architecture))
        invalid_endpoint["connections"][0]["targetId"] = "missing"
        with self.assertRaises(ValueError):
            architecture_view(invalid_endpoint)
        duplicate_component = json.loads(json.dumps(architecture))
        duplicate_component["components"].append(duplicate_component["components"][0])
        with self.assertRaises(ValueError):
            architecture_view(duplicate_component)
        discontinuous = json.loads(json.dumps(flow))
        discontinuous["steps"][2]["step"] = 9
        with self.assertRaises(ValueError):
            flow_view(discontinuous)
        wrong_next = json.loads(json.dumps(flow))
        wrong_next["steps"][0]["nextStep"] = "skip"
        with self.assertRaises(ValueError):
            flow_view(wrong_next)


if __name__ == "__main__":
    unittest.main()
