from __future__ import annotations

import unittest

from agentnavi.cli import build_parser


class ReleaseBuildCommandContractTest(unittest.TestCase):
    def test_release_cli_is_parseable(self) -> None:
        self.assertEqual(build_parser().parse_args(["mcp"]).command, "mcp")


if __name__ == "__main__":
    unittest.main()
