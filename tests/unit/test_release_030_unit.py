from __future__ import annotations

import unittest

from agentnavi import __version__


class Release030UnitTest(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(__version__, "0.3.0")


if __name__ == "__main__":
    unittest.main()
