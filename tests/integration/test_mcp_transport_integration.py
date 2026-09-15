from __future__ import annotations

import unittest
from unittest.mock import patch

from agentnavi.cli import main


class MCPTransportIntegrationTest(unittest.TestCase):
    def test_http_command_keeps_loopback_default(self) -> None:
        with patch("agentnavi.mcp.server.run_http") as run_http:
            self.assertEqual(main(["mcp", "--transport", "http"]), 0)
        run_http.assert_called_once_with(home=None, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    unittest.main()
