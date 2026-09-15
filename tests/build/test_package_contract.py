from __future__ import annotations

import importlib
import importlib.resources
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path


class PackageBuildContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[2]
        with (self.root / "pyproject.toml").open("rb") as handle:
            self.pyproject = tomllib.load(handle)

    def test_setuptools_src_layout_is_declared(self) -> None:
        self.assertEqual(
            self.pyproject["build-system"]["build-backend"],
            "setuptools.build_meta",
        )
        self.assertEqual(self.pyproject["tool"]["setuptools"]["package-dir"], {"": "src"})
        self.assertEqual(self.pyproject["tool"]["setuptools"]["packages"]["find"]["where"], ["src"])

    def test_console_script_targets_are_importable_callables(self) -> None:
        scripts = self.pyproject["project"]["scripts"]
        self.assertEqual(set(scripts), {"agentnavi", "navi"})

        for name, target in scripts.items():
            with self.subTest(script=name):
                module_name, attribute_name = target.split(":", 1)
                value = importlib.import_module(module_name)
                for segment in attribute_name.split("."):
                    value = getattr(value, segment)
                self.assertTrue(callable(value))

    def test_mcp_is_an_exact_optional_dependency(self) -> None:
        project = self.pyproject["project"]

        self.assertEqual(project["dependencies"], [])
        self.assertEqual(project["optional-dependencies"]["mcp"], ["mcp>=2,<3"])

    def test_mcp_app_html_is_declared_as_package_data(self) -> None:
        setuptools = self.pyproject["tool"]["setuptools"]
        self.assertTrue(setuptools["include-package-data"])
        self.assertEqual(
            setuptools["package-data"]["agentnavi"],
            ["mcp/resources/*.html", "mcp/resources/*.txt"],
        )
        resource = importlib.resources.files("agentnavi.mcp.resources").joinpath(
            "agentnavi-app.html"
        )
        self.assertTrue(resource.is_file())
        self.assertIn("AgentNavi ContextMap", resource.read_text(encoding="utf-8"))

    def test_wheel_contains_app_and_complete_production_bundle_notices(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    ".",
                    "--no-deps",
                    "--wheel-dir",
                    temporary_directory,
                ],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            wheels = list(Path(temporary_directory).glob("agentnavi-*.whl"))
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(wheels[0]) as archive:
                names = set(archive.namelist())
                resource_root = "agentnavi/mcp/resources/"
                self.assertIn(f"{resource_root}agentnavi-app.html", names)
                notice_name = f"{resource_root}THIRD_PARTY_NOTICES.txt"
                self.assertIn(notice_name, names)
                notices = archive.read(notice_name).decode("utf-8")

        for package in (
            "@modelcontextprotocol/ext-apps 2.0.0",
            "@modelcontextprotocol/client 2.0.0",
            "@modelcontextprotocol/core 2.0.0",
            "zod 4.6.5",
            "pkce-challenge 5.0.1",
            "@standard-schema/spec 1.1.0",
        ):
            self.assertIn(package, notices)
        self.assertIn("Apache License\n                           Version 2.0", notices)
        self.assertIn(
            "Copyright (c) 2024-2025 Model Context Protocol a Series of LF Projects, LLC.",
            notices,
        )
        self.assertIn("Copyright (c) 2025 Colin McDonnell", notices)
        self.assertIn("Copyright (c) 2024 Colin McDonnell", notices)

    def test_all_packaged_python_sources_compile(self) -> None:
        source_files = sorted((self.root / "src" / "agentnavi").rglob("*.py"))
        self.assertTrue(source_files)
        for source_file in source_files:
            with self.subTest(source=source_file.relative_to(self.root).as_posix()):
                compile(source_file.read_text(encoding="utf-8"), str(source_file), "exec")


if __name__ == "__main__":
    unittest.main()
