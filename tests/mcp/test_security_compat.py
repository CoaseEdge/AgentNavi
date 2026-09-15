from __future__ import annotations

import re
import unittest
from pathlib import Path

from agentnavi.mcp.server import APP_MIME_TYPE, APP_URI, APP_TOOL_META


class MCPSecurityCompatibilityTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]

    def test_app_resource_contract_is_local_and_mcp_apps_compatible(self) -> None:
        self.assertEqual(APP_MIME_TYPE, "text/html;profile=mcp-app")
        self.assertEqual(APP_URI, "ui://agentnavi/app.html")
        self.assertEqual(APP_TOOL_META["ui"]["resourceUri"], APP_URI)

    def test_ui_source_has_no_dom_injection_or_network_escape_hatches(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in (self.root / "ui" / "src").rglob("*.ts"))
        self.assertNotIn("innerHTML", source)
        self.assertNotRegex(source, r"\beval\s*\(")
        self.assertNotRegex(source, r"\bfetch\s*\(")
        self.assertNotIn("localhost", source)
        self.assertNotIn("127.0.0.1", source)

    def test_public_protocol_source_does_not_embed_absolute_workspace_paths(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in (self.root / "src" / "agentnavi" / "mcp").rglob("*.py"))
        self.assertNotRegex(source, r"/Users/|[A-Za-z]:\\\\|file://")


if __name__ == "__main__":
    unittest.main()
