from __future__ import annotations

import asyncio
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
                self.assertEqual(visualize_tool.output_schema, context_tool.output_schema)
                self.assertEqual(
                    visualize_tool.input_schema["properties"]["view"],
                    {"const": "context", "title": "View", "type": "string"},
                )
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
                    tools_by_name["agentnavi_visualize"].output_schema,
                    tools_by_name["agentnavi_context"].output_schema,
                )
                fallback = await client.call_tool(
                    "agentnavi_visualize",
                    {"view": "context", "query": "会员入口"},
                )
                self.assertTrue(fallback.is_error)
                self.assertEqual(
                    fallback.structured_content["code"],
                    "PROJECT_REQUIRED",
                )
                self.assertIn("AgentNavi 错误", fallback.content[0].text)
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

        with tempfile.TemporaryDirectory() as temporary_directory:
            asyncio.run(verify(Path(temporary_directory) / "agentnavi-home"))


if __name__ == "__main__":
    unittest.main()
