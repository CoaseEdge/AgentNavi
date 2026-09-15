from __future__ import annotations

import unittest

from agentnavi import __version__


class VLARelease030TestCase(unittest.TestCase):
    def test_package_version_is_locked_to_vla_release(self) -> None:
        self.assertEqual(__version__, "0.3.0")


if __name__ == "__main__":
    unittest.main()
