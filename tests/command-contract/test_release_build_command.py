from __future__ import annotations

import unittest

from agentnavi.cli import build_parser


class ReleaseBuildCommandContractTest(unittest.TestCase):
    def test_mcp_command_remains_discoverable_from_base_install(self) -> None:
        args = build_parser().parse_args(["mcp", "--transport", "stdio"])
        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.transport, "stdio")


if __name__ == "__main__":
    unittest.main()
