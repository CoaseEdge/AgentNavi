from __future__ import annotations

import unittest

from agentnavi.mcp.server import APP_MIME_TYPE, APP_URI


class MCPHostContractTest(unittest.TestCase):
    def test_resource_contract_is_stable(self) -> None:
        self.assertEqual(APP_MIME_TYPE, "text/html;profile=mcp-app")
        self.assertEqual(APP_URI, "ui://agentnavi/app.html")


if __name__ == "__main__":
    unittest.main()
