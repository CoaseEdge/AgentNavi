from __future__ import annotations

import unittest

from agentnavi.benchmark import VLA_BENCHMARK_VIEWS


class VLABenchmarkUnitTest(unittest.TestCase):
    def test_view_set_is_fixed(self) -> None:
        self.assertEqual(len(VLA_BENCHMARK_VIEWS), 6)
        self.assertEqual(len(set(VLA_BENCHMARK_VIEWS)), 6)


if __name__ == "__main__":
    unittest.main()
