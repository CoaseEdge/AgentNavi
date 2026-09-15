from __future__ import annotations

import unittest

from agentnavi.cli import build_parser


class VLABenchmarkCommandContractTest(unittest.TestCase):
    def test_vla_benchmark_command_is_discoverable(self) -> None:
        args = build_parser().parse_args(["benchmark", "vla", "fixture.json", "--json"])
        self.assertEqual(args.benchmark_command, "vla")
        self.assertTrue(args.json)


if __name__ == "__main__":
    unittest.main()
