from __future__ import annotations

import asyncio
from dataclasses import dataclass
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def _mcp_sdk_available() -> bool:
    try:
        return importlib.util.find_spec("mcp.server") is not None
    except ModuleNotFoundError:
        return False


MCP_AVAILABLE = _mcp_sdk_available()


class MCPServerContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]

    def _environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        source_path = str(self.root / "src")
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            f"{source_path}{os.pathsep}{existing}" if existing else source_path
        )
        return environment

    def test_core_modules_import_when_mcp_sdk_is_unavailable(self) -> None:
        script = """
import builtins

real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == "mcp" or name.startswith("mcp."):
        raise AssertionError("MCP SDK must not be imported eagerly")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import agentnavi.cli
import agentnavi.mcp
import agentnavi.mcp.errors
import agentnavi.mcp.project_resolver
import agentnavi.mcp.server
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.root,
            env=self._environment(),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_argument_middleware_discards_non_mapping_sensitive_payloads(self) -> None:
        from agentnavi.mcp.server import _complete_required_tool_arguments

        @dataclass(frozen=True)
        class FakeContext:
            method: str
            params: dict[str, object]

        private_tokens = (
            "/private/posix.py",
            r"C:\\Users\\alice\\windows.py",
            r"\\server\share\unc.py",
            "file:///Users/alice/url.py",
        )

        async def call_next(ctx: FakeContext) -> dict[str, object]:
            return ctx.params

        for tool_name in ("agentnavi_context", "agentnavi_visualize"):
            with self.subTest(tool=tool_name):
                result = asyncio.run(
                    _complete_required_tool_arguments(
                        FakeContext(
                            method="tools/call",
                            params={"name": tool_name, "arguments": list(private_tokens)},
                        ),
                        call_next,
                    )
                )
                arguments = result["arguments"]
                self.assertEqual(arguments["query"], None)
                if tool_name == "agentnavi_visualize":
                    self.assertEqual(arguments["view"], None)
                wire = str(result)
                for token in private_tokens:
                    self.assertNotIn(token, wire)

    @unittest.skipUnless(MCP_AVAILABLE, "需要安装 agentnavi[mcp]")
    def test_in_process_client_initializes_and_discovers_context_tool(self) -> None:
        from mcp import Client

        from agentnavi.mcp.server import create_server

        async def verify(home: Path) -> None:
            server = create_server(home=home)
            async with Client(server, raise_exceptions=True) as client:
                self.assertIsNotNone(client.server_info)
                self.assertEqual(client.server_info.name, "AgentNavi")
                self.assertEqual(
                    client.server_capabilities.extensions,
                    {"io.modelcontextprotocol/ui": {}},
                )
                tools = (await client.list_tools()).tools
                tools_by_name = {tool.name: tool for tool in tools}
                self.assertEqual(
                    set(tools_by_name),
                    {"agentnavi_context", "agentnavi_visualize"},
                )
                context_tool = tools_by_name["agentnavi_context"]
                visualize_tool = tools_by_name["agentnavi_visualize"]
                self.assertEqual(context_tool.input_schema["required"], ["query"])
                self.assertEqual(
                    set(context_tool.input_schema["properties"]),
                    {"query", "project_id", "workspace"},
                )
                self.assertEqual(
                    set(context_tool.output_schema["required"]),
                    {
                        "schemaVersion",
                        "view",
                        "project",
                        "sourceState",
                        "data",
                        "warnings",
                    },
                )
                self.assertEqual(
                    context_tool.output_schema["properties"]["view"]["const"],
                    "context",
                )
                self.assertEqual(
                    context_tool.output_schema["properties"]["warnings"]["maxItems"],
                    10,
                )
                self.assertEqual(
                    context_tool.output_schema["properties"]["data"]["$ref"],
                    "#/$defs/ContextDataOutput",
                )
                self.assertNotIn(
                    "RepositoryOverviewDataOutput",
                    context_tool.output_schema["$defs"],
                )
                reading_schema = context_tool.output_schema["$defs"]["ContextReadingOutput"]
                self.assertEqual(
                    reading_schema["properties"]["position"]["type"], "integer"
                )
                self.assertEqual(reading_schema["properties"]["position"]["minimum"], 1)
                self.assertEqual(reading_schema["properties"]["position"]["maximum"], 12)
                self.assertEqual(reading_schema["properties"]["actions"]["minItems"], 5)
                self.assertEqual(reading_schema["properties"]["actions"]["maxItems"], 5)
                self.assertEqual(
                    context_tool.output_schema["$defs"]["ContextActionOutput"]
                    ["properties"]["kind"]["enum"],
                    ["purpose", "relevance", "dependents", "history", "impact"],
                )
                self.assertEqual(
                    visualize_tool.output_schema["anyOf"],
                    [
                        {"$ref": "#/$defs/ContextViewOutput"},
                        {"$ref": "#/$defs/RepositoryOverviewViewOutput"},
                        {"$ref": "#/$defs/RepositoryTourViewOutput"},
                        {"$ref": "#/$defs/ArchitectureViewOutput"},
                        {"$ref": "#/$defs/FlowViewOutput"},
                    ],
                )
                self.assertEqual(
                    visualize_tool.input_schema["properties"]["view"],
                    {
                        "enum": ["context", "repo-overview", "repo-tour", "architecture", "flow"],
                        "title": "View",
                        "type": "string",
                    },
                )
                self.assertEqual(visualize_tool.input_schema["required"], ["view"])
                self.assertTrue(context_tool.annotations.read_only_hint)
                self.assertFalse(context_tool.annotations.destructive_hint)
                self.assertTrue(context_tool.annotations.idempotent_hint)
                self.assertFalse(context_tool.annotations.open_world_hint)
                resources = (await client.list_resources()).resources
                self.assertEqual(
                    [str(resource.uri) for resource in resources],
                    ["ui://agentnavi/app.html"],
                )
                self.assertEqual((await client.list_prompts()).prompts, [])

        with tempfile.TemporaryDirectory() as temporary_directory:
            asyncio.run(verify(Path(temporary_directory) / "agentnavi-home"))

    @unittest.skipUnless(MCP_AVAILABLE, "需要安装 agentnavi[mcp]")
    def test_stdio_client_initializes_without_stdout_log_pollution(self) -> None:
        from mcp import Client, StdioServerParameters, stdio_client

        async def verify(home: Path) -> None:
            from agentnavi.config import Settings
            from agentnavi.database import ensure_database
            from agentnavi.engine import scan_project
            from agentnavi.registry import add_project

            project_root = home.parent / "stdio-fixture"
            (project_root / "docs").mkdir(parents=True)
            (project_root / "README.md").write_text(
                "# Fixture\n\n## 一句话理解\n\n帮助协作者理解项目。\n\n"
                "## 问题定义\n\n减少重复搜索。\n\n"
                "## 解决方式\n\n用证据提供导航。\n",
                encoding="utf-8",
            )
            (project_root / "docs" / "architecture.md").write_text(
                "# 架构\n\n## 主流程\n\n1. 接收\n2. 解析\n3. 读取\n"
                "4. 汇总\n5. 投影\n6. 返回\n",
                encoding="utf-8",
            )
            database = ensure_database(Settings.load(home))
            project = add_project(database, project_root, project_id="stdio-fixture")
            scan_project(database, project, full=True)
            server = StdioServerParameters(
                command=sys.executable,
                args=["-m", "agentnavi", "--home", str(home), "mcp"],
                env={"PYTHONPATH": str(self.root / "src")},
            )
            # v2.0 的 Client 尚不能直接接收 StdioServerParameters；显式使用
            # 官方 transport API 可覆盖完整的 mcp>=2,<3 声明范围。
            async with Client(stdio_client(server)) as client:
                self.assertEqual(client.server_info.name, "AgentNavi")
                self.assertEqual(
                    client.server_capabilities.extensions,
                    {"io.modelcontextprotocol/ui": {}},
                )
                tools = (await client.list_tools()).tools
                tools_by_name = {tool.name: tool for tool in tools}
                self.assertEqual(
                    set(tools_by_name),
                    {"agentnavi_context", "agentnavi_visualize"},
                )
                resources = (await client.list_resources()).resources
                self.assertEqual(
                    [str(resource.uri) for resource in resources],
                    ["ui://agentnavi/app.html"],
                )
                self.assertEqual(
                    resources[0].mime_type,
                    "text/html;profile=mcp-app",
                )
                self.assertEqual(
                    resources[0].meta,
                    {
                        "ui": {
                            "prefersBorder": True,
                            "csp": {
                                "connectDomains": [],
                                "resourceDomains": [],
                                "frameDomains": [],
                                "baseUriDomains": [],
                            },
                        }
                    },
                )
                content = (await client.read_resource("ui://agentnavi/app.html")).contents[0]
                self.assertEqual(content.mime_type, "text/html;profile=mcp-app")
                self.assertEqual(content.meta, resources[0].meta)
                self.assertIn("AgentNavi ContextMap", content.text)
                self.assertFalse(tools_by_name["agentnavi_context"].meta)
                self.assertEqual(
                    tools_by_name["agentnavi_visualize"].meta,
                    {"ui": {"resourceUri": "ui://agentnavi/app.html"}},
                )
                self.assertEqual(
                    tools_by_name["agentnavi_context"]
                    .output_schema["properties"]["view"]["const"],
                    "context",
                )
                self.assertEqual(
                    tools_by_name["agentnavi_visualize"].output_schema["anyOf"],
                    [
                        {"$ref": "#/$defs/ContextViewOutput"},
                        {"$ref": "#/$defs/RepositoryOverviewViewOutput"},
                        {"$ref": "#/$defs/RepositoryTourViewOutput"},
                        {"$ref": "#/$defs/ArchitectureViewOutput"},
                        {"$ref": "#/$defs/FlowViewOutput"},
                    ],
                )
                overview = await client.call_tool(
                    "agentnavi_visualize",
                    {"view": "repo-overview", "project_id": "stdio-fixture"},
                )
                self.assertFalse(overview.is_error)
                self.assertEqual(overview.structured_content["view"], "repo-overview")
                overview_wire = str(overview.model_dump(by_alias=True))
                self.assertNotIn(str(project_root.resolve()), overview_wire)
                self.assertIn("README.md", overview.content[0].text)
                tour = await client.call_tool(
                    "agentnavi_visualize",
                    {"view": "repo-tour", "project_id": "stdio-fixture"},
                )
                self.assertFalse(tour.is_error)
                self.assertEqual(tour.structured_content["view"], "repo-tour")
                self.assertEqual(
                    [tier["depth"] for tier in tour.structured_content["data"]["tiers"]],
                    ["one-minute", "five-minutes", "source-deep-dive"],
                )
                self.assertIn("技术说明", tour.content[0].text)
                self.assertNotIn(
                    str(project_root.resolve()),
                    str(tour.model_dump(by_alias=True)),
                )
                context_result = await client.call_tool(
                    "agentnavi_visualize",
                    {"view": "context", "query": "会员入口"},
                )
                self.assertFalse(context_result.is_error)
                self.assertEqual(
                    context_result.structured_content["view"],
                    "context",
                )
                self.assertIn("AgentNavi 项目上下文", context_result.content[0].text)
                missing_context_query = await client.call_tool(
                    "agentnavi_visualize",
                    {"view": "context", "project_id": "stdio-fixture"},
                )
                self.assertTrue(missing_context_query.is_error)
                self.assertEqual(
                    missing_context_query.structured_content["code"],
                    "INVALID_ARGUMENT",
                )
                self.assertEqual(
                    missing_context_query.structured_content["details"],
                    {"field": "query"},
                )
                invalid_token = "/private/stdio-secret.py"
                invalid = await client.call_tool(
                    "agentnavi_visualize",
                    {"view": invalid_token, "query": "会员入口"},
                )
                self.assertTrue(invalid.is_error)
                self.assertEqual(invalid.structured_content["code"], "INVALID_ARGUMENT")
                self.assertNotIn(
                    invalid_token,
                    str(invalid.model_dump(by_alias=True)),
                )
                missing_tokens = (
                    r"C:\\Users\\alice\\missing-query.py",
                    r"\\server\share\missing-view.py",
                    "file:///Users/alice/sibling.py",
                )
                for tool_name, arguments in (
                    (
                        "agentnavi_visualize",
                        {"query": missing_tokens[0], "workspace": missing_tokens[1]},
                    ),
                    (
                        "agentnavi_context",
                        {"project_id": missing_tokens[2]},
                    ),
                    ("agentnavi_context", None),
                ):
                    result = await client.call_tool(tool_name, arguments)
                    self.assertTrue(result.is_error)
                    self.assertEqual(
                        result.structured_content["code"],
                        "INVALID_ARGUMENT",
                    )
                    wire = str(result.model_dump(by_alias=True))
                    for token in missing_tokens:
                        self.assertNotIn(token, wire)

        with tempfile.TemporaryDirectory() as temporary_directory:
            asyncio.run(verify(Path(temporary_directory) / "agentnavi-home"))


if __name__ == "__main__":
    unittest.main()
