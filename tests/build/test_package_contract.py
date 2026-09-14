from __future__ import annotations

import importlib
import importlib.resources
import tomllib
import unittest
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
            ["mcp/resources/*.html"],
        )
        resource = importlib.resources.files("agentnavi.mcp.resources").joinpath(
            "agentnavi-app.html"
        )
        self.assertTrue(resource.is_file())
        self.assertIn("AgentNavi ContextMap", resource.read_text(encoding="utf-8"))

    def test_all_packaged_python_sources_compile(self) -> None:
        source_files = sorted((self.root / "src" / "agentnavi").rglob("*.py"))
        self.assertTrue(source_files)
        for source_file in source_files:
            with self.subTest(source=source_file.relative_to(self.root).as_posix()):
                compile(source_file.read_text(encoding="utf-8"), str(source_file), "exec")


if __name__ == "__main__":
    unittest.main()
