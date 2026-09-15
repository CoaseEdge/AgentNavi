from __future__ import annotations

import unittest

from agentnavi.mcp.setup import build_setup_config


class MCPSetupUnitTest(unittest.TestCase):
    def test_setup_is_deterministic(self) -> None:
        self.assertEqual(
            build_setup_config(target="generic"),
            build_setup_config(target="generic"),
        )


if __name__ == "__main__":
    unittest.main()
