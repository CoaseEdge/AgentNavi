from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentnavi.cli import build_parser, main


class MCPCLIContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]

    def test_parser_defaults_to_stdio_and_exposes_http_options(self) -> None:
        args = build_parser().parse_args(["mcp"])

        self.assertEqual(args.command, "mcp")
        self.assertEqual(args.transport, "stdio")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 8000)

    def test_missing_optional_extra_writes_only_a_safe_stderr_hint(self) -> None:
        missing_sdk = ModuleNotFoundError("No module named 'mcp'", name="mcp")
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("agentnavi.mcp.server.run_stdio", side_effect=missing_sdk):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["mcp"])

        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "未安装 MCP 可选依赖。请运行：pip install 'agentnavi[mcp]'\n",
        )

    def test_base_install_execution_does_not_require_mcp(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.root / "src")
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-m",
                    "agentnavi",
                    "--home",
                    temporary_directory,
                    "mcp",
                ],
                cwd=self.root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr,
            "未安装 MCP 可选依赖。请运行：pip install 'agentnavi[mcp]'\n",
        )

    def test_base_install_help_exposes_mcp_without_importing_the_sdk(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.root / "src")
        result = subprocess.run(
            [sys.executable, "-S", "-m", "agentnavi", "--help"],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("mcp", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_internal_import_errors_are_not_misreported_as_missing_extra(self) -> None:
        internal_error = ModuleNotFoundError(
            "No module named 'internal_dependency'",
            name="internal_dependency",
        )

        with patch("agentnavi.mcp.server.run_stdio", side_effect=internal_error):
            with self.assertRaises(ModuleNotFoundError):
                main(["mcp"])

    def test_home_is_forwarded_to_mcp_server(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch("agentnavi.mcp.server.run_stdio") as run_stdio:
                result = main(["--home", temporary_directory, "mcp"])

        self.assertEqual(result, 0)
        run_stdio.assert_called_once_with(home=temporary_directory)

    def test_http_transport_is_loopback_by_default(self) -> None:
        with patch("agentnavi.mcp.server.run_http") as run_http:
            result = main(["mcp", "--transport", "http"])

        self.assertEqual(result, 0)
        run_http.assert_called_once_with(home=None, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    unittest.main()
