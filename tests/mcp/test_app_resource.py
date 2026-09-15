from __future__ import annotations

import asyncio
import importlib.resources
import importlib.util
import tempfile
import unittest
from pathlib import Path


def _mcp_sdk_available() -> bool:
    try:
        return importlib.util.find_spec("mcp.server") is not None
    except ModuleNotFoundError:
        return False


MCP_AVAILABLE = _mcp_sdk_available()


class MCPAppStaticContractTestCase(unittest.TestCase):
    def test_generated_app_is_a_single_self_contained_csp_resource(self) -> None:
        html = (
            importlib.resources.files("agentnavi.mcp.resources")
            .joinpath("agentnavi-app.html")
            .read_text(encoding="utf-8")
        )

        self.assertIn("AgentNavi ContextMap", html)
        self.assertIn("default-src 'none'", html)
        self.assertIn("connect-src 'none'", html)
        self.assertNotRegex(html, r"<script[^>]+\bsrc=")
        self.assertNotRegex(html, r"<link[^>]+\bhref=")


@unittest.skipUnless(MCP_AVAILABLE, "需要安装 agentnavi[mcp]")
class MCPAppResourceContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary_directory.name) / "agentnavi-home"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def _verify_server(self) -> None:
        from mcp import Client

        from agentnavi.mcp.server import (
            APP_MIME_TYPE,
            APP_RESOURCE_META,
            APP_TOOL_META,
            APP_URI,
            create_server,
        )

        async with Client(create_server(home=self.home), raise_exceptions=True) as client:
            self.assertEqual(
                client.server_capabilities.extensions,
                {"io.modelcontextprotocol/ui": {}},
            )
            resources = (await client.list_resources()).resources
            self.assertEqual(len(resources), 1)
            resource = resources[0]
            self.assertEqual(str(resource.uri), APP_URI)
            self.assertEqual(resource.mime_type, APP_MIME_TYPE)
            self.assertEqual(resource.meta, APP_RESOURCE_META)

            result = await client.read_resource(APP_URI)
            self.assertEqual(len(result.contents), 1)
            self.assertEqual(str(result.contents[0].uri), APP_URI)
            self.assertEqual(result.contents[0].mime_type, APP_MIME_TYPE)
            self.assertEqual(result.contents[0].meta, APP_RESOURCE_META)
            self.assertIn("AgentNavi ContextMap", result.contents[0].text)

            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            self.assertEqual(set(tools), {"agentnavi_context", "agentnavi_visualize"})
            self.assertFalse(tools["agentnavi_context"].meta)
            self.assertEqual(tools["agentnavi_visualize"].meta, APP_TOOL_META)
            self.assertEqual(
                tools["agentnavi_visualize"].input_schema["required"],
                ["view", "query"],
            )
            self.assertEqual(
                tools["agentnavi_visualize"].input_schema["properties"]["view"]["const"],
                "context",
            )
            self.assertEqual(
                tools["agentnavi_visualize"].output_schema,
                tools["agentnavi_context"].output_schema,
            )

            fallback = await client.call_tool(
                "agentnavi_visualize",
                {"view": "context", "query": "会员入口"},
            )
            self.assertTrue(fallback.is_error)
            self.assertEqual(fallback.structured_content["code"], "PROJECT_REQUIRED")
            self.assertIn("AgentNavi 错误", fallback.content[0].text)

    def test_in_process_resource_and_presentation_tool_contract(self) -> None:
        asyncio.run(self._verify_server())


if __name__ == "__main__":
    unittest.main()
