from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from agentnavi.cli import main
from agentnavi.mcp.setup import build_setup_config


class MCPSetupContractTestCase(unittest.TestCase):
    def test_targets_generate_without_writing_files(self) -> None:
        for target in ("claude", "vscode", "codex", "generic"):
            config = build_setup_config(target=target, home="/tmp/agentnavi")
            self.assertIsInstance(config, dict)
            self.assertIn("agentnavi", json.dumps(config))

    def test_setup_prints_json_and_does_not_modify_host(self) -> None:
        with patch("pathlib.Path.write_text") as write_text:
            result = main(["mcp", "setup", "--target", "generic"])

        self.assertEqual(result, 0)
        write_text.assert_not_called()

    def test_invalid_target_is_rejected_by_parser(self) -> None:
        with self.assertRaises(SystemExit):
            main(["mcp", "setup", "--target", "unknown"])


if __name__ == "__main__":
    unittest.main()
