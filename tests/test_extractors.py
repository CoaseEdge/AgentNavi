from __future__ import annotations

import json
import sqlite3
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from agentnavi.extractors.api import ExtractionContext
from agentnavi.extractors.registry import load_registry


class ExtractorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_text(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def context(self, relative: str, *, all_paths: set[str] | None = None) -> ExtractionContext:
        path = self.root / relative
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
            is_text = b"\x00" not in raw[:4096]
        except UnicodeDecodeError:
            text = None
            is_text = False
        return ExtractionContext(
            project_id="sample",
            project_root=self.root,
            relative_path=relative,
            absolute_path=path,
            all_paths=frozenset(all_paths or {relative}),
            language=path.suffix.lstrip("."),
            size=len(raw),
            digest="digest",
            is_text=is_text,
            text=text,
            max_file_bytes=10 * 1024 * 1024,
        )

    def extract(self, relative: str, *, all_paths: set[str] | None = None):
        return load_registry(include_plugins=False).extract(self.context(relative, all_paths=all_paths))

    def test_json_and_yaml_resolve_project_file_references(self) -> None:
        self.write_text("data/source.csv", "id,value\n1,2\n")
        self.write_text("config/pipeline.json", json.dumps({"input": "../data/source.csv", "steps": ["clean"]}))
        self.write_text("config/pipeline.yaml", "input: ../data/source.csv\nmode: clean\n")
        paths = {"data/source.csv", "config/pipeline.json", "config/pipeline.yaml"}
        json_result = self.extract("config/pipeline.json", all_paths=paths)
        yaml_result = self.extract("config/pipeline.yaml", all_paths=paths)
        self.assertIn(("references", "data/source.csv"), {(d.relation, d.target_path) for d in json_result.dependencies})
        self.assertIn(("references", "data/source.csv"), {(d.relation, d.target_path) for d in yaml_result.dependencies})
        self.assertIn("configuration", json_result.roles)

    def test_toml_ini_xml_csv_and_sql_extract_structure(self) -> None:
        self.write_text("settings.toml", "[database]\npath='data.sqlite'\n")
        self.write_text("settings.ini", "[service]\nendpoint = local\n")
        self.write_text("layout.xml", "<root><include href='settings.toml'/><item/><item/></root>")
        self.write_text("sample.csv", "id,name,score\n1,Ada,9.5\n2,Lin,8\n")
        self.write_text("query.sql", "INSERT INTO report SELECT * FROM sales JOIN customer ON 1=1;")
        paths = {"settings.toml", "settings.ini", "layout.xml", "sample.csv", "query.sql"}
        toml = self.extract("settings.toml", all_paths=paths)
        ini = self.extract("settings.ini", all_paths=paths)
        xml = self.extract("layout.xml", all_paths=paths)
        csv_result = self.extract("sample.csv", all_paths=paths)
        sql = self.extract("query.sql", all_paths=paths)
        self.assertEqual(toml.metadata["sections"], ["database"])
        self.assertEqual(ini.metadata["sections"], ["service"])
        self.assertEqual(xml.metadata["element_counts"]["item"], 2)
        self.assertEqual(csv_result.metadata["column_count"], 3)
        self.assertEqual(csv_result.metadata["row_count"], 2)
        self.assertEqual(set(sql.metadata["read_tables"]), {"sales", "customer"})
        self.assertEqual(sql.metadata["write_tables"], ["report"])
        self.assertTrue(any(resource.kind == "database_table" for resource in sql.resources))

    def test_notebook_extracts_cells_and_python_imports(self) -> None:
        self.write_text("lib/helper.py", "VALUE=1\n")
        notebook = {
            "nbformat": 4,
            "metadata": {"kernelspec": {"language": "python"}},
            "cells": [
                {"cell_type": "markdown", "source": ["See [data](data.csv)"], "metadata": {}},
                {"cell_type": "code", "source": ["from lib.helper import VALUE"], "metadata": {}, "execution_count": 1},
            ],
        }
        self.write_text("analysis.ipynb", json.dumps(notebook))
        self.write_text("data.csv", "x\n1\n")
        result = self.extract("analysis.ipynb", all_paths={"analysis.ipynb", "lib/helper.py", "data.csv"})
        self.assertEqual(result.metadata["cell_count"], 2)
        targets = {item.target_path for item in result.dependencies}
        self.assertIn("lib/helper.py", targets)
        self.assertIn("data.csv", targets)
        self.assertEqual(len([item for item in result.resources if item.kind == "notebook_cell"]), 2)

    def test_xlsx_extracts_worksheets_without_openpyxl(self) -> None:
        path = self.root / "model.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "xl/workbook.xml",
                """<?xml version='1.0' encoding='UTF-8'?>
                <workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
                  xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>
                  <sheets><sheet name='Inputs' sheetId='1' r:id='rId1'/><sheet name='Forecast' sheetId='2' r:id='rId2'/></sheets>
                </workbook>""",
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                """<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData/></worksheet>""",
            )
            archive.writestr(
                "xl/worksheets/sheet2.xml",
                """<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData><row><c><f>'Inputs'!A1</f></c></row></sheetData></worksheet>""",
            )
        result = self.extract("model.xlsx")
        self.assertEqual(result.metadata["sheet_count"], 2)
        self.assertEqual([item.label for item in result.resources], ["Inputs", "Forecast"])
        self.assertIn("spreadsheet", result.roles)

    def test_more_programming_languages_extract_imports_and_symbols(self) -> None:
        self.write_text("go.mod", "module example.com/app\n")
        self.write_text("internal/pay/pay.go", "package pay\nfunc Charge() {}\n")
        self.write_text("cmd/main.go", 'package main\nimport "example.com/app/internal/pay"\nfunc main(){pay.Charge()}\n')
        self.write_text("src/lib.rs", "mod payment;\npub fn start() {}\n")
        self.write_text("src/payment.rs", "pub struct Payment;\n")
        self.write_text("include/pay.h", "void charge();\n")
        self.write_text("native/main.cpp", '#include "../include/pay.h"\nint main(){return 0;}\n')
        paths = {"go.mod", "internal/pay/pay.go", "cmd/main.go", "src/lib.rs", "src/payment.rs", "include/pay.h", "native/main.cpp"}
        go = self.extract("cmd/main.go", all_paths=paths)
        rust = self.extract("src/lib.rs", all_paths=paths)
        cpp = self.extract("native/main.cpp", all_paths=paths)
        self.assertIn("internal/pay/pay.go", {item.target_path for item in go.dependencies})
        self.assertIn("src/payment.rs", {item.target_path for item in rust.dependencies})
        self.assertIn("include/pay.h", {item.target_path for item in cpp.dependencies})
        self.assertTrue(any(item.label == "main" for item in go.resources))

    def test_npy_npz_and_sqlite_are_understood_without_optional_dependencies(self) -> None:
        def npy_bytes(shape=(2, 3), descr="<f8") -> bytes:
            header = repr({"descr": descr, "fortran_order": False, "shape": shape})
            header_bytes = (header + " " * (64 - len(header) - 1) + "\n").encode("latin1")
            return b"\x93NUMPY" + bytes([1, 0]) + struct.pack("<H", len(header_bytes)) + header_bytes

        (self.root / "array.npy").write_bytes(npy_bytes())
        with zipfile.ZipFile(self.root / "arrays.npz", "w") as archive:
            archive.writestr("x.npy", npy_bytes((4,), "<i4"))
            archive.writestr("y.npy", npy_bytes((2, 2), "<f4"))
        database_path = self.root / "science.sqlite"
        connection = sqlite3.connect(database_path)
        connection.executescript("CREATE TABLE samples(id INTEGER PRIMARY KEY, value REAL); CREATE VIEW sample_values AS SELECT value FROM samples;")
        connection.close()

        npy = self.extract("array.npy")
        npz = self.extract("arrays.npz")
        sqlite_result = self.extract("science.sqlite")
        self.assertEqual(npy.metadata["shape"], [2, 3])
        self.assertEqual(npz.metadata["array_count"], 2)
        self.assertGreaterEqual(sqlite_result.metadata["object_counts"]["table"], 1)
        self.assertTrue(any(item.kind == "database_table" for item in sqlite_result.resources))

    def test_optional_science_formats_degrade_with_diagnostics(self) -> None:
        (self.root / "data.parquet").write_bytes(b"PAR1stubPAR1")
        result = self.extract("data.parquet")
        self.assertIn("dataset", result.roles)
        self.assertTrue(result.warnings or result.resources)

    def test_registry_signature_is_stable_and_lists_format_families(self) -> None:
        first = load_registry(include_plugins=False)
        second = load_registry(include_plugins=False)
        self.assertEqual(first.signature, second.signature)
        identifiers = {item.extractor_id for item in first.descriptors()}
        self.assertIn("builtin.structured", identifiers)
        self.assertIn("builtin.science", identifiers)
        self.assertIn("builtin.code.multilanguage", identifiers)


if __name__ == "__main__":
    unittest.main()
