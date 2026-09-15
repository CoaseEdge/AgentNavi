from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.utils import utc_now


def _mcp_sdk_available() -> bool:
    try:
        return importlib.util.find_spec("mcp.server") is not None
    except ModuleNotFoundError:
        return False


MCP_AVAILABLE = _mcp_sdk_available()


@unittest.skipUnless(MCP_AVAILABLE, "需要安装 agentnavi[mcp]")
class MCPContextToolContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.home = self.base / "agentnavi-home"
        self.project_root = self.base / "private" / "project"
        self.project_root.mkdir(parents=True)
        self.database = ensure_database(Settings.load(self.home))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _add_project(
        self,
        *,
        project_id: str = "fixture",
        scanned: bool = True,
        file_path: str = "src/membership.py",
    ) -> None:
        now = utc_now()
        absolute_file = self.project_root / file_path
        absolute_file.parent.mkdir(parents=True, exist_ok=True)
        absolute_file.write_text("VALUE = 1\n", encoding="utf-8")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(
                    id, name, root, kind, created_at, updated_at, last_scan_at
                ) VALUES (?, 'Fixture', ?, 'software', ?, ?, ?)
                """,
                (
                    project_id,
                    str(self.project_root.resolve()),
                    now,
                    now,
                    now if scanned else None,
                ),
            )
            file_id = Database.upsert_node(
                connection,
                project_id=project_id,
                layer=1,
                kind="file",
                key=file_path,
                label=Path(file_path).name,
                data={"language": "python", "privateExtension": str(self.project_root)},
                source="repository",
            )
            concept_id = Database.upsert_node(
                connection,
                project_id=project_id,
                layer=2,
                kind="concept",
                key="membership",
                label="会员",
                data={"keywords": ["会员"]},
                confidence=0.9,
                source="semantic-heuristic",
            )
            Database.upsert_edge(
                connection,
                project_id=project_id,
                layer=2,
                source_id=concept_id,
                relation="implemented_by",
                target_id=file_id,
                confidence=0.85,
                source="semantic-heuristic",
            )
            stat = absolute_file.stat()
            connection.execute(
                """INSERT INTO file_state(
                       project_id, path, mtime_ns, size, digest, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    project_id, file_path, stat.st_mtime_ns, stat.st_size,
                    hashlib.blake2s(absolute_file.read_bytes()).hexdigest(), now,
                ),
            )
            connection.commit()

    def _add_overview_documents(self) -> None:
        (self.project_root / "docs").mkdir(exist_ok=True)
        (self.project_root / "README.md").write_text(
            """# Fixture\n\n## 一句话理解\n\n帮助协作者理解会员项目。\n\n## 问题定义\n\n减少重复搜索与上下文遗漏。\n\n## 解决方式\n\n用项目图谱和文档证据提供导航。\n""",
            encoding="utf-8",
        )
        (self.project_root / "docs" / "architecture.md").write_text(
            """# 架构\n\n## 主流程\n\n1. membership 接收请求\n2. 解析项目\n3. 读取文档\n4. 汇总事实\n5. 生成概览\n6. 投影视图\n7. 返回证据\n""",
            encoding="utf-8",
        )
        with self.database.connect() as connection:
            for path in ("README.md", "docs/architecture.md"):
                Database.upsert_node(
                    connection,
                    project_id="fixture",
                    layer=1,
                    kind="file",
                    key=path,
                    label=Path(path).name,
                    data={"language": "markdown"},
                    source="repository",
                )
                absolute = self.project_root / path
                stat = absolute.stat()
                connection.execute(
                    """INSERT INTO file_state(
                           project_id, path, mtime_ns, size, digest, updated_at
                       ) VALUES ('fixture', ?, ?, ?, ?, ?)""",
                    (
                        path,
                        stat.st_mtime_ns,
                        stat.st_size,
                        hashlib.blake2s(absolute.read_bytes()).hexdigest(),
                        utc_now(),
                    ),
                )
            connection.commit()

    async def _call(
        self,
        arguments: Any,
        *,
        tool_name: str = "agentnavi_context",
    ):
        from mcp import Client

        from agentnavi.mcp.server import create_server

        async with Client(create_server(home=self.home), raise_exceptions=True) as client:
            return await client.call_tool(tool_name, arguments)

    def test_visualize_tool_returns_context_view_and_readable_fallback(self) -> None:
        self._add_project()

        result = asyncio.run(
            self._call(
                {"view": "context", "query": "会员", "project_id": "fixture"},
                tool_name="agentnavi_visualize",
            )
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["schemaVersion"], "agentnavi.vla.v1")
        self.assertEqual(result.structured_content["view"], "context")
        self.assertEqual(
            result.structured_content["data"]["files"][0]["path"],
            "src/membership.py",
        )
        reading = result.structured_content["data"]["navigation"]["readingOrder"]
        self.assertEqual(reading[0]["path"], "src/membership.py")
        self.assertTrue(reading[0]["why"])
        self.assertTrue(reading[0]["evidence"])
        self.assertEqual(reading[0]["nextStep"], None)
        self.assertEqual(
            [action["label"] for action in reading[0]["actions"]],
            ["它做什么", "为什么相关", "谁依赖它", "过去谁改过", "如果改它"],
        )
        self.assertIn("会员", result.content[0].text)
        self.assertIn("src/membership.py", result.content[0].text)
        self.assertIn("Why：", result.content[0].text)
        self.assertIn("Evidence：", result.content[0].text)
        self.assertIn("Next Step：", result.content[0].text)

    def test_context_navigation_adapter_failure_is_public_and_sanitized(self) -> None:
        from agentnavi.query import context_data

        self._add_project()
        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE id='fixture'"
            ).fetchone()
        assert project is not None
        private_message = f"bad endpoint at {self.database.settings.database_path}"
        cases = []
        bad_endpoint = context_data(self.database, project, "会员")
        bad_endpoint["navigation"]["readingOrder"][0]["chains"][0][
            "fileRelation"
        ]["targetId"] = private_message
        cases.append(bad_endpoint)
        self_relation = context_data(self.database, project, "会员")
        chain = self_relation["navigation"]["readingOrder"][0]["chains"][0]
        chain["relatedConcept"] = json.loads(json.dumps(chain["sourceConcept"]))
        chain["conceptRelation"] = {
            **json.loads(json.dumps(chain["fileRelation"])),
            "id": "self-loop",
            "sourceId": chain["sourceConcept"]["id"],
            "targetId": chain["sourceConcept"]["id"],
            "relation": "related_to",
        }
        cases.append(self_relation)
        cross_kind = context_data(self.database, project, "会员")
        chain = cross_kind["navigation"]["readingOrder"][0]["chains"][0]
        chain["file"]["id"] = chain["sourceConcept"]["id"]
        chain["fileRelation"]["targetId"] = chain["sourceConcept"]["id"]
        cases.append(cross_kind)

        for bad_core in cases:
            with self.subTest(case=cases.index(bad_core)), patch(
                "agentnavi.query.context_data", return_value=bad_core
            ):
                result = asyncio.run(
                    self._call({"query": "会员", "project_id": "fixture"})
                )
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content["code"], "INTERNAL_ERROR")
            wire = json.dumps(
                result.model_dump(by_alias=True), ensure_ascii=False, default=str
            )
            self.assertNotIn(private_message, wire)
            self.assertNotIn(str(self.database.settings.database_path), wire)

    def test_context_projects_all_nine_navigation_warnings_without_text_drift(self) -> None:
        from agentnavi.query import context_data

        self._add_project(scanned=False)
        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE id='fixture'"
            ).fetchone()
        assert project is not None
        core = context_data(self.database, project, "会员")
        codes = [
            "CONTEXT_NAVIGATION_STALE_FILES",
            "CONTEXT_NAVIGATION_FRESHNESS_BUDGET",
            "CONTEXT_NAVIGATION_SYMLINK_UNVERIFIABLE",
            "CONTEXT_NAVIGATION_RELATION_EVIDENCE_INSUFFICIENT",
            "CONTEXT_NAVIGATION_RELATION_EVIDENCE_TRUNCATED",
            "CONTEXT_NAVIGATION_EVIDENCE_INSUFFICIENT",
            "CONTEXT_NAVIGATION_DEPENDENCY_TRUNCATED",
            "CONTEXT_NAVIGATION_HISTORY_TRUNCATED",
            "CONTEXT_NAVIGATION_HISTORY_FILTERED",
        ]
        core["warnings"] = [
            {"code": code, "message": f"{code} message", "evidence": []}
            for code in codes
        ]
        with patch("agentnavi.query.context_data", return_value=core):
            result = asyncio.run(
                self._call({"query": "会员", "project_id": "fixture"})
            )

        self.assertFalse(result.is_error)
        structured = [item["code"] for item in result.structured_content["warnings"]]
        self.assertEqual(structured, ["SOURCE_NOT_INDEXED", *codes])
        self.assertEqual(len(structured), 10)
        for warning in result.structured_content["warnings"]:
            self.assertIn(f"[{warning['code']}] {warning['message']}", result.content[0].text)

        overflow = json.loads(json.dumps(core))
        overflow["warnings"].append({
            "code": "OVERFLOW_WARNING_SENTINEL",
            "message": "第十一条合法提示不得越过 Adapter",
            "evidence": [],
        })
        with patch("agentnavi.query.context_data", return_value=overflow):
            rejected = asyncio.run(
                self._call({"query": "会员", "project_id": "fixture"})
            )
        self.assertTrue(rejected.is_error)
        self.assertEqual(rejected.structured_content["code"], "INTERNAL_ERROR")
        rejected_wire = json.dumps(
            rejected.model_dump(by_alias=True), ensure_ascii=False, default=str
        )
        self.assertNotIn("OVERFLOW_WARNING_SENTINEL", rejected_wire)
        self.assertNotIn("第十一条合法提示", rejected_wire)

    def test_visualize_tool_returns_repository_overview_without_query(self) -> None:
        self._add_project()
        self._add_overview_documents()

        result = asyncio.run(
            self._call(
                {"view": "repo-overview", "project_id": "fixture"},
                tool_name="agentnavi_visualize",
            )
        )

        self.assertFalse(result.is_error)
        payload = result.structured_content
        self.assertEqual(payload["view"], "repo-overview")
        self.assertIn("会员项目", payload["data"]["purpose"]["summary"])
        self.assertEqual(len(payload["data"]["workflow"]), 7)
        self.assertEqual(payload["data"]["readingOrder"][0]["path"], "README.md")
        self.assertIn("做什么", result.content[0].text)
        self.assertIn("建议阅读顺序", result.content[0].text)
        wire = json.dumps(result.model_dump(by_alias=True), ensure_ascii=False, default=str)
        self.assertNotIn(str(self.project_root.resolve()), wire)
        self.assertNotIn(str(self.database.settings.database_path), wire)

    def test_visualize_tool_returns_all_repository_tour_depths_without_query(self) -> None:
        self._add_project()
        self._add_overview_documents()

        result = asyncio.run(
            self._call(
                {"view": "repo-tour", "project_id": "fixture"},
                tool_name="agentnavi_visualize",
            )
        )

        self.assertFalse(result.is_error)
        payload = result.structured_content
        self.assertEqual(payload["view"], "repo-tour")
        self.assertEqual(
            [tier["depth"] for tier in payload["data"]["tiers"]],
            ["one-minute", "five-minutes", "source-deep-dive"],
        )
        self.assertIn("1 分钟", result.content[0].text)
        self.assertIn("技术说明", result.content[0].text)
        wire = json.dumps(result.model_dump(by_alias=True), ensure_ascii=False, default=str)
        self.assertNotIn(str(self.project_root.resolve()), wire)
        self.assertNotIn(str(self.database.settings.database_path), wire)

    def test_visualize_tool_returns_architecture_and_flow(self) -> None:
        self._add_project()
        self._add_overview_documents()

        architecture = asyncio.run(
            self._call(
                {"view": "architecture", "project_id": "fixture"},
                tool_name="agentnavi_visualize",
            )
        )
        flow = asyncio.run(
            self._call(
                {"view": "flow", "query": "修改会员", "project_id": "fixture"},
                tool_name="agentnavi_visualize",
            )
        )

        self.assertFalse(architecture.is_error)
        self.assertEqual(architecture.structured_content["view"], "architecture")
        self.assertEqual(
            architecture.structured_content["data"]["layout"],
            "cognitive-components",
        )
        self.assertIn("系统架构", architecture.content[0].text)
        self.assertFalse(flow.is_error)
        self.assertEqual(flow.structured_content["view"], "flow")
        self.assertEqual(len(flow.structured_content["data"]["steps"]), 7)
        self.assertEqual(flow.structured_content["data"]["exampleTask"]["title"], "修改会员")
        self.assertIn("关键源码", flow.content[0].text)
        wire = json.dumps(
            [architecture.model_dump(by_alias=True), flow.model_dump(by_alias=True)],
            ensure_ascii=False,
            default=str,
        )
        self.assertNotIn(str(self.project_root.resolve()), wire)
        self.assertNotIn(str(self.database.settings.database_path), wire)

    def test_architecture_adapter_rejection_returns_public_error_dto(self) -> None:
        self._add_project()
        self._add_overview_documents()
        private_message = f"too much evidence at {self.database.settings.database_path}"
        evidence = {
            "kind": "graph", "summary": "mapping", "layer": "L2",
            "source": "semantic-heuristic", "confidence": 0.8,
            "path": "src/membership.py",
        }
        bad_core = {
            "project": {"id": "fixture", "name": "Fixture", "kind": "software"},
            "sourceState": {"status": "ready"},
            "layout": "cognitive-components",
            "summary": {
                "text": "Fixture architecture",
                "explanationSource": "derived-presentation",
                "evidence": [evidence],
            },
            "components": [{
                "id": "concept:membership", "name": "Membership", "group": "core",
                "responsibility": "Handles membership", "paths": ["src/membership.py"],
                "entity": {
                    "id": "concept:membership", "kind": "concept", "label": "Membership",
                    "layer": "L2", "source": "semantic-heuristic", "confidence": 0.8,
                    "evidence": [evidence],
                },
                "evidence": [evidence, evidence, evidence, {**evidence, "summary": private_message}],
            }],
            "connections": [], "entryPoints": [],
            "stats": {"files": 1, "concepts": 1, "tasks": 0, "documentsRead": 2},
            "warnings": [],
        }

        with patch(
            "agentnavi.repository_views.repository_architecture_data",
            return_value=bad_core,
        ):
            result = asyncio.run(
                self._call(
                    {"view": "architecture", "project_id": "fixture"},
                    tool_name="agentnavi_visualize",
                )
            )

        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["code"], "INTERNAL_ERROR")
        wire = json.dumps(result.model_dump(by_alias=True), ensure_ascii=False, default=str)
        self.assertNotIn(private_message, wire)
        self.assertNotIn(str(self.database.settings.database_path), wire)

    def test_architecture_and_flow_provenance_rejections_are_public(self) -> None:
        from agentnavi.repository_views import (
            repository_architecture_data,
            repository_flow_data,
        )

        self._add_project()
        self._add_overview_documents()
        with self.database.connect() as connection:
            project = connection.execute(
                "SELECT * FROM projects WHERE id='fixture'"
            ).fetchone()
        assert project is not None
        architecture = repository_architecture_data(self.database, project)
        flow = repository_flow_data(self.database, project, query="修改会员")
        self.assertTrue(architecture["components"])
        self.assertTrue(flow["steps"][0]["keyFiles"])
        private_message = f"invalid provenance at {self.database.settings.database_path}"

        cases: list[tuple[str, dict[str, Any]]] = []
        wrong_component = json.loads(json.dumps(architecture))
        wrong_component["components"][0]["entity"]["layer"] = "L1"
        wrong_component["components"][0]["entity"]["label"] = private_message
        cases.append(("architecture", wrong_component))
        wrong_connection = json.loads(json.dumps(architecture))
        component_id = wrong_connection["components"][0]["id"]
        wrong_connection["connections"].append({
            "id": "invalid-layer-edge", "sourceId": component_id,
            "targetId": component_id, "relation": "related_to", "layer": "L1",
            "source": "semantic-heuristic", "confidence": 0.8,
            "evidence": wrong_connection["components"][0]["evidence"],
        })
        cases.append(("architecture", wrong_connection))
        wrong_entry = json.loads(json.dumps(architecture))
        key_file = flow["steps"][0]["keyFiles"][0]
        wrong_entry["entryPoints"].append({
            "path": key_file["path"], "reason": "entry",
            "entity": {**key_file["entity"], "layer": "L2"},
            "evidence": key_file["evidence"],
        })
        cases.append(("architecture", wrong_entry))
        wrong_key_entity = json.loads(json.dumps(flow))
        wrong_key_entity["steps"][0]["keyFiles"][0]["entity"]["layer"] = "L2"
        cases.append(("flow", wrong_key_entity))
        wrong_key_relation = json.loads(json.dumps(flow))
        wrong_key_relation["steps"][0]["keyFiles"][0]["relation"]["layer"] = "L1"
        cases.append(("flow", wrong_key_relation))
        request_with_provenance = json.loads(json.dumps(flow))
        request_with_provenance["exampleTask"]["entity"] = flow["steps"][0]["keyFiles"][0]["entity"]
        request_with_provenance["exampleTask"]["evidence"] = flow["steps"][0]["evidence"]
        cases.append(("flow", request_with_provenance))
        malformed_history = json.loads(json.dumps(flow))
        malformed_history["exampleTask"] = {
            "title": "history",
            "source": "task-events",
            "entity": {
                "id": "task", "kind": "task", "label": private_message,
                "layer": "L3", "source": "task-events", "confidence": 1,
                "evidence": [],
            },
        }
        cases.append(("flow", malformed_history))

        for view, bad_core in cases:
            function = (
                "repository_architecture_data"
                if view == "architecture"
                else "repository_flow_data"
            )
            with self.subTest(view=view, function=function), patch(
                f"agentnavi.repository_views.{function}", return_value=bad_core
            ):
                result = asyncio.run(
                    self._call(
                        {"view": view, "project_id": "fixture"},
                        tool_name="agentnavi_visualize",
                    )
                )
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["code"], "INTERNAL_ERROR")
                wire = json.dumps(
                    result.model_dump(by_alias=True), ensure_ascii=False, default=str
                )
                self.assertNotIn(private_message, wire)
                self.assertNotIn(str(self.database.settings.database_path), wire)

    def test_context_tool_returns_equivalent_text_and_vla_view_without_paths(self) -> None:
        self._add_project()

        result = asyncio.run(self._call({"query": "会员", "project_id": "fixture"}))
        fallback = asyncio.run(self._call({"query": "会员"}))

        self.assertFalse(result.is_error)
        payload = result.structured_content
        self.assertEqual(payload["schemaVersion"], "agentnavi.vla.v1")
        self.assertEqual(payload["view"], "context")
        self.assertEqual(
            payload["project"],
            {"id": "fixture", "name": "Fixture", "kind": "software"},
        )
        self.assertEqual(payload["sourceState"]["status"], "ready")
        self.assertNotIn("query", payload["data"])
        self.assertEqual(payload["data"]["files"][0]["path"], "src/membership.py")
        self.assertEqual(fallback.structured_content["project"]["id"], "fixture")
        text = result.content[0].text
        self.assertIn("会员", text)
        self.assertIn("src/membership.py", text)
        self.assertIn("Fixture", text)
        serialized = json.dumps(payload, ensure_ascii=False)
        for secret in (
            str(self.project_root.resolve()),
            str(self.database.settings.database_path),
            str(self.database.settings.event_log_path),
            "privateExtension",
        ):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, serialized)

    def test_workspace_selection_and_unscanned_source_warning(self) -> None:
        self._add_project(scanned=False)

        result = asyncio.run(
            self._call({"query": "会员", "workspace": str(self.project_root / "src")})
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["sourceState"], {"status": "partial"})
        self.assertEqual(
            [warning["code"] for warning in result.structured_content["warnings"]],
            ["SOURCE_NOT_INDEXED"],
        )
        self.assertIn("尚未完成索引", result.content[0].text)

    def test_project_errors_are_stable_and_do_not_leak_internal_paths(self) -> None:
        required = asyncio.run(self._call({"query": "会员"}))
        missing = asyncio.run(
            self._call({"query": "会员", "project_id": str(self.project_root.resolve())})
        )

        for result, expected_code in (
            (required, "PROJECT_REQUIRED"),
            (missing, "PROJECT_NOT_FOUND"),
        ):
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content["code"], expected_code)
            wire = json.dumps(result.model_dump(by_alias=True), ensure_ascii=False, default=str)
            self.assertNotIn(str(self.project_root.resolve()), wire)
            self.assertNotIn(str(self.database.settings.database_path), wire)
        self.assertEqual(required.structured_content["details"], {"candidateCount": 0})

    def test_invalid_argument_types_and_values_are_public_and_never_echoed(self) -> None:
        private_tokens = (
            "/private/posix-secret.py",
            r"C:\\Users\\alice\\windows-secret.py",
            r"\\server\share\unc-secret.py",
            "file:///Users/alice/url-secret.py",
            "file:/Users/alice/short-url-secret.py",
            "vscode://file/Users/alice/editor-secret.py",
            "vscode-insiders://file/Users/alice/insiders-secret.py",
            "cursor://file/Users/alice/cursor-secret.py",
            "custom-editor://file/Users/alice/custom-secret.py",
        )
        cases = (
            ("agentnavi_context", None, "query"),
            (
                "agentnavi_visualize",
                {"query": private_tokens[0], "project_id": private_tokens[1]},
                "view",
            ),
            (
                "agentnavi_visualize",
                {"view": private_tokens[0], "workspace": private_tokens[2]},
                "view",
            ),
            (
                "agentnavi_context",
                {"project_id": private_tokens[4]},
                "query",
            ),
            (
                "agentnavi_visualize",
                {"view": private_tokens[0], "query": "会员"},
                "view",
            ),
            (
                "agentnavi_visualize",
                {"view": "context", "query": list(private_tokens)},
                "query",
            ),
            (
                "agentnavi_context",
                {"query": "会员", "project_id": {"value": private_tokens[1]}},
                "project_id",
            ),
            (
                "agentnavi_context",
                {"query": "会员", "workspace": list(private_tokens)},
                "workspace",
            ),
            ("agentnavi_context", {"query": "   "}, "query"),
        )

        for tool_name, arguments, field in cases:
            with self.subTest(tool=tool_name, field=field):
                result = asyncio.run(self._call(arguments, tool_name=tool_name))
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["code"], "INVALID_ARGUMENT")
                self.assertEqual(result.structured_content["details"], {"field": field})
                wire = json.dumps(
                    result.model_dump(by_alias=True),
                    ensure_ascii=False,
                    default=str,
                )
                for token in private_tokens:
                    self.assertNotIn(token, wire)

    def test_unknown_failures_map_to_sanitized_internal_error(self) -> None:
        self._add_project()
        private_message = f"SQLite failed at {self.database.settings.database_path}"

        with patch("agentnavi.query.context_data", side_effect=RuntimeError(private_message)):
            result = asyncio.run(self._call({"query": "会员", "project_id": "fixture"}))

        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["code"], "INTERNAL_ERROR")
        self.assertIn("不可直接重试", result.content[0].text)
        wire = json.dumps(result.model_dump(by_alias=True), ensure_ascii=False, default=str)
        self.assertNotIn(private_message, wire)
        self.assertNotIn(str(self.database.settings.database_path), wire)

    def test_runtime_schema_rejects_invalid_success_envelopes(self) -> None:
        from pydantic import ValidationError

        from agentnavi.mcp.runtime import (
            ArchitectureViewOutput,
            ContextViewOutput,
            FlowViewOutput,
            RepositoryOverviewViewOutput,
            RepositoryTourViewOutput,
            VisualizeViewOutput,
        )

        self._add_project()
        result = asyncio.run(
            self._call({"query": "会员", "project_id": "fixture"})
        )
        payload = result.structured_content
        ContextViewOutput.model_validate(payload)
        VisualizeViewOutput.model_validate(payload)
        invalid_context = json.loads(json.dumps(payload))
        invalid_context["data"]["navigation"]["readingOrder"][0]["chains"][0][
            "fileRelation"
        ]["targetId"] = "wrong-file"
        with self.assertRaises(ValidationError):
            ContextViewOutput.model_validate(invalid_context)
        invalid_contexts = []
        fractional = json.loads(json.dumps(payload))
        fractional["data"]["navigation"]["readingOrder"][0]["position"] = 1.5
        invalid_contexts.append(fractional)
        wrong_action = json.loads(json.dumps(payload))
        wrong_action["data"]["navigation"]["readingOrder"][0]["actions"].reverse()
        invalid_contexts.append(wrong_action)
        wrong_next = json.loads(json.dumps(payload))
        wrong_next["data"]["navigation"]["readingOrder"][0]["nextStep"] = {
            "path": wrong_next["data"]["files"][0]["path"], "reason": "循环"
        }
        invalid_contexts.append(wrong_next)
        outside_dependent = json.loads(json.dumps(payload))
        outside_dependent["data"]["navigation"]["readingOrder"][0][
            "dependents"
        ] = [{"path": "src/not-candidate.py", "relation": "imports"}]
        invalid_contexts.append(outside_dependent)
        cross_kind = json.loads(json.dumps(payload))
        cross_chain = cross_kind["data"]["navigation"]["readingOrder"][0]["chains"][0]
        cross_chain["file"]["id"] = cross_chain["sourceConcept"]["id"]
        cross_chain["fileRelation"]["targetId"] = cross_chain["sourceConcept"]["id"]
        invalid_contexts.append(cross_kind)
        self_relation = json.loads(json.dumps(payload))
        self_chain = self_relation["data"]["navigation"]["readingOrder"][0]["chains"][0]
        self_chain["relatedConcept"] = json.loads(json.dumps(self_chain["sourceConcept"]))
        self_chain["conceptRelation"] = {
            **json.loads(json.dumps(self_chain["fileRelation"])),
            "id": "self-loop", "sourceId": self_chain["sourceConcept"]["id"],
            "targetId": self_chain["sourceConcept"]["id"], "relation": "related_to",
        }
        invalid_contexts.append(self_relation)
        for invalid in invalid_contexts:
            with self.assertRaises(ValidationError):
                ContextViewOutput.model_validate(invalid)

        overview = asyncio.run(
            self._call(
                {"view": "repo-overview", "project_id": "fixture"},
                tool_name="agentnavi_visualize",
            )
        ).structured_content
        RepositoryOverviewViewOutput.model_validate(overview)
        VisualizeViewOutput.model_validate(overview)
        with self.assertRaises(ValidationError):
            ContextViewOutput.model_validate(overview)

        self._add_overview_documents()
        tour = asyncio.run(
            self._call(
                {"view": "repo-tour", "project_id": "fixture"},
                tool_name="agentnavi_visualize",
            )
        ).structured_content
        RepositoryTourViewOutput.model_validate(tour)
        VisualizeViewOutput.model_validate(tour)
        invalid_tour = json.loads(json.dumps(tour))
        invalid_tour["data"]["tiers"][0]["stops"][0]["entity"]["confidence"] = 1.1
        with self.assertRaises(ValidationError):
            RepositoryTourViewOutput.model_validate(invalid_tour)

        architecture = asyncio.run(
            self._call(
                {"view": "architecture", "project_id": "fixture"},
                tool_name="agentnavi_visualize",
            )
        ).structured_content
        ArchitectureViewOutput.model_validate(architecture)
        VisualizeViewOutput.model_validate(architecture)
        for target_path, layer in ((("components", 0, "entity"), "L1"),):
            invalid = json.loads(json.dumps(architecture))
            target = invalid["data"]
            for part in target_path:
                target = target[part]
            target["layer"] = layer
            with self.assertRaises(ValidationError):
                ArchitectureViewOutput.model_validate(invalid)

        flow = asyncio.run(
            self._call(
                {"view": "flow", "query": "修改会员", "project_id": "fixture"},
                tool_name="agentnavi_visualize",
            )
        ).structured_content
        FlowViewOutput.model_validate(flow)
        VisualizeViewOutput.model_validate(flow)
        invalid_entry = json.loads(json.dumps(architecture))
        key_file = flow["data"]["steps"][0]["keyFiles"][0]
        invalid_entry["data"]["entryPoints"].append({
            "path": key_file["path"], "reason": "entry",
            "entity": {**key_file["entity"], "layer": "L2"},
            "evidence": key_file["evidence"],
        })
        with self.assertRaises(ValidationError):
            ArchitectureViewOutput.model_validate(invalid_entry)
        invalid_connection = json.loads(json.dumps(architecture))
        component_id = invalid_connection["data"]["components"][0]["id"]
        invalid_connection["data"]["connections"].append({
            "id": "invalid-layer-edge", "sourceId": component_id,
            "targetId": component_id, "relation": "related_to", "layer": "L1",
            "source": "semantic-heuristic", "confidence": 0.8,
            "evidence": invalid_connection["data"]["components"][0]["evidence"],
        })
        with self.assertRaises(ValidationError):
            ArchitectureViewOutput.model_validate(invalid_connection)
        for field, layer in (("entity", "L2"), ("relation", "L1")):
            invalid = json.loads(json.dumps(flow))
            invalid["data"]["steps"][0]["keyFiles"][0][field]["layer"] = layer
            with self.assertRaises(ValidationError):
                FlowViewOutput.model_validate(invalid)
        request_with_provenance = json.loads(json.dumps(flow))
        request_with_provenance["data"]["exampleTask"]["entity"] = (
            flow["data"]["steps"][0]["keyFiles"][0]["entity"]
        )
        with self.assertRaises(ValidationError):
            FlowViewOutput.model_validate(request_with_provenance)
        history = json.loads(json.dumps(flow))
        task_evidence = {
            "kind": "task-record", "summary": "task history", "layer": "L3",
            "source": "task-events", "confidence": 1,
        }
        history["data"]["exampleTask"] = {
            "title": "history", "source": "task-events",
            "entity": {
                "id": "task", "kind": "task", "label": "history", "layer": "L3",
                "source": "task-events", "confidence": 1, "evidence": [task_evidence],
            },
            "evidence": [task_evidence],
        }
        FlowViewOutput.model_validate(history)
        for mutate in (
            lambda value: value["data"]["exampleTask"].pop("evidence"),
            lambda value: value["data"]["exampleTask"]["entity"].update(layer="L2"),
            lambda value: value["data"]["exampleTask"]["entity"].update(source="repository"),
            lambda value: value["data"]["exampleTask"]["evidence"][0].update(source="repository"),
        ):
            invalid = json.loads(json.dumps(history))
            mutate(invalid)
            with self.assertRaises(ValidationError):
                FlowViewOutput.model_validate(invalid)

        invalid_payloads = []
        for field, value in (
            ("schemaVersion", "agentnavi.vla.invalid"),
            ("project", {}),
            ("sourceState", {}),
            ("data", {}),
        ):
            invalid = json.loads(json.dumps(payload))
            invalid[field] = value
            invalid_payloads.append(invalid)

        for invalid in invalid_payloads:
            with self.subTest(payload=invalid):
                with self.assertRaises(ValidationError):
                    ContextViewOutput.model_validate(invalid)

        for invalid_error in (
            {
                "code": "UNEXPECTED",
                "message": "error",
                "retryable": False,
                "details": {},
            },
            {
                "code": "INTERNAL_ERROR",
                "message": "error",
                "retryable": False,
                "details": {},
                "extra": True,
            },
        ):
            with self.subTest(error=invalid_error):
                with self.assertRaises(ValidationError):
                    ContextViewOutput.model_validate(invalid_error)

    def test_path_like_queries_succeed_without_echoing_absolute_style_tokens(self) -> None:
        self._add_project()

        cases = (
            "/",
            "inspect / now",
            "/api/users",
            "fix /api/users endpoint",
            f"fix {self.project_root}/secret.py now",
            r"fix C:\\Users\\alice\\secret.py now",
            "inspect file:///Users/alice/secret.py",
            "inspect file:/Users/alice/secret.py",
            "inspect vscode://file/Users/alice/secret.py",
            "inspect vscode-insiders://file/Users/alice/secret.py",
            "inspect cursor://file/Users/alice/secret.py",
            "inspect custom-editor://file/Users/alice/secret.py",
            f"inspect cursor://file{self.project_root}/secret.py",
            f"inspect custom-editor://file{self.project_root}/secret.py",
        )
        for query in cases:
            with self.subTest(query=query):
                result = asyncio.run(
                    self._call({"query": query, "project_id": "fixture"})
                )
                self.assertFalse(result.is_error)
                self.assertNotIn("query", result.structured_content["data"])
                self.assertIn("查询含路径，已隐藏", result.content[0].text)
                self.assertNotIn(f"当前查询：{query}", result.content[0].text)
                self.assertNotIn(str(self.project_root), result.content[0].text)
                self.assertNotIn(
                    str(self.project_root),
                    json.dumps(result.structured_content, ensure_ascii=False),
                )

        url = "inspect https://example.com/api/users"
        url_result = asyncio.run(
            self._call({"query": url, "project_id": "fixture"})
        )
        self.assertFalse(url_result.is_error)
        self.assertIn(url, url_result.content[0].text)
        self.assertNotIn("查询含路径，已隐藏", url_result.content[0].text)
        http_file_host = "inspect https://file.example.com/api/users"
        http_file_result = asyncio.run(
            self._call({"query": http_file_host, "project_id": "fixture"})
        )
        self.assertFalse(http_file_result.is_error)
        self.assertIn(http_file_host, http_file_result.content[0].text)

    def test_long_relative_paths_are_preserved_without_truncation(self) -> None:
        long_path = f"src/{'a' * 238}.py"
        self._add_project(file_path=long_path)

        result = asyncio.run(
            self._call({"query": long_path, "project_id": "fixture"})
        )

        self.assertFalse(result.is_error)
        self.assertEqual(
            result.structured_content["data"]["files"][0]["path"], long_path
        )
        self.assertIn(long_path, result.content[0].text)

    def test_neighbor_order_is_stable_across_insertion_orders(self) -> None:
        self._add_project()

        def replace_neighbors(labels: list[str]) -> None:
            with self.database.connect() as connection:
                concept_id = connection.execute(
                    "SELECT id FROM nodes WHERE project_id='fixture' AND key='membership'"
                ).fetchone()["id"]
                connection.execute(
                    "DELETE FROM edges WHERE project_id='fixture' AND source_id=?",
                    (concept_id,),
                )
                connection.execute(
                    "DELETE FROM nodes WHERE project_id='fixture' AND key LIKE 'neighbor-%'"
                )
                for label in labels:
                    neighbor_id = Database.upsert_node(
                        connection,
                        project_id="fixture",
                        layer=2,
                        kind="concept",
                        key=f"neighbor-{label.lower()}",
                        label=label,
                        confidence=0.8,
                        source="semantic-heuristic",
                    )
                    Database.upsert_edge(
                        connection,
                        project_id="fixture",
                        layer=2,
                        source_id=concept_id,
                        relation="depends_on",
                        target_id=neighbor_id,
                        confidence=0.8,
                        source="semantic-heuristic",
                    )
                connection.commit()

        replace_neighbors(["Zulu", "Alpha"])
        first = asyncio.run(
            self._call({"query": "会员", "project_id": "fixture"})
        ).structured_content["data"]["concepts"][0]["neighbors"]
        replace_neighbors(["Alpha", "Zulu"])
        second = asyncio.run(
            self._call({"query": "会员", "project_id": "fixture"})
        ).structured_content["data"]["concepts"][0]["neighbors"]

        self.assertEqual(first, second)
        self.assertEqual([neighbor["label"] for neighbor in first], ["Alpha", "Zulu"])


if __name__ == "__main__":
    unittest.main()
