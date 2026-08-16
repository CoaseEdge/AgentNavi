from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from agentnavi.extractors.api import ExtractionContext
from agentnavi.extractors.code_go_rust import _go_extract
from agentnavi.extractors.registry import ExtractionRegistry
from agentnavi.extractors.scientific import ScientificDataExtractor
from agentnavi.extractors.scientific_numpy import _npz_extract
from agentnavi.extractors.structured_json import _json_lines_extract
from agentnavi.extractors.structured_tabular import _csv_extract
from agentnavi.extractors.structured_xlsx import _xlsx_extract


class FixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, relative: str, content: str | bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def context(
        self,
        relative: str,
        *,
        paths: set[str] | None = None,
        text: bool = True,
        max_binary: int = 256 * 1024 * 1024,
        max_entries: int = 10_000,
        max_archive_bytes: int = 64 * 1024 * 1024,
        max_line: int = 1024 * 1024,
        max_stream: int = 64 * 1024 * 1024,
    ) -> ExtractionContext:
        path = self.root / relative
        raw = path.read_bytes()
        return ExtractionContext(
            project_id="p",
            project_root=self.root,
            relative_path=relative,
            absolute_path=path,
            all_paths=frozenset(paths or {relative}),
            language=path.suffix.lstrip("."),
            size=len(raw),
            digest="x",
            is_text=text,
            text=raw.decode("utf-8") if text else None,
            max_file_bytes=4 * 1024 * 1024,
            max_binary_file_bytes=max_binary,
            max_archive_entries=max_entries,
            max_archive_uncompressed_bytes=max_archive_bytes,
            max_line_chars=max_line,
            max_stream_chars=max_stream,
        )

    def test_match_failure_is_diagnostic(self) -> None:
        self.write("sample.txt", "x")

        class Broken:
            extractor_id = "broken"
            extractor_version = "1"
            priority = 10

            def matches(self, context):
                raise RuntimeError("boom")

            def extract(self, context):
                raise AssertionError

        result = ExtractionRegistry([Broken()]).extract(self.context("sample.txt"))
        self.assertTrue(any("匹配失败" in warning and "boom" in warning for warning in result.warnings))

    def test_go_multi_file_package_is_ambiguous_not_arbitrary(self) -> None:
        self.write("go.mod", "module example.com/app\n")
        self.write("internal/pay/a.go", "package pay\nfunc A(){}\n")
        self.write("internal/pay/b.go", "package pay\nfunc B(){}\n")
        self.write("cmd/main.go", 'package main\nimport "example.com/app/internal/pay"\nfunc main(){}\n')
        paths = {"go.mod", "internal/pay/a.go", "internal/pay/b.go", "cmd/main.go"}
        result = _go_extract(self.context("cmd/main.go", paths=paths))
        self.assertEqual(result.dependencies, ())
        self.assertEqual(result.external_dependencies, ())
        ambiguous = result.metadata["ambiguous_local_imports"]
        self.assertEqual(ambiguous[0]["candidate_count"], 2)
        self.assertTrue(any("未建立任意文件级依赖" in warning for warning in result.warnings))

    def test_xlsx_formula_relations_have_source_worksheet(self) -> None:
        path = self.root / "model.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "xl/workbook.xml",
                """<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
                xmlns:r='http://schemas.openxmlformats.org/officeDocument/2006/relationships'>
                <sheets><sheet name='Inputs' sheetId='1' r:id='rId1'/>
                <sheet name='Forecast' sheetId='2' r:id='rId2'/>
                <sheet name='Scenario' sheetId='3' r:id='rId3'/></sheets></workbook>""",
            )
            archive.writestr(
                "xl/_rels/workbook.xml.rels",
                """<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>
                <Relationship Id='rId1' Target='worksheets/sheet1.xml'/>
                <Relationship Id='rId2' Target='worksheets/sheet2.xml'/>
                <Relationship Id='rId3' Target='worksheets/sheet3.xml'/></Relationships>""",
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData/></worksheet>",
            )
            archive.writestr(
                "xl/worksheets/sheet2.xml",
                "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData><row><c><f>'Inputs'!A1</f></c></row></sheetData></worksheet>",
            )
            archive.writestr(
                "xl/worksheets/sheet3.xml",
                "<worksheet xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheetData><row><c><f>'Inputs'!A1+'Inputs'!B2</f></c></row></sheetData></worksheet>",
            )
        result = _xlsx_extract(self.context("model.xlsx", text=False))
        edges = {(relation.source_key, relation.target_key): relation.data["count"] for relation in result.resource_relations}
        self.assertEqual(edges[("sheet:1:Forecast", "sheet:0:Inputs")], 1)
        self.assertEqual(edges[("sheet:2:Scenario", "sheet:0:Inputs")], 2)
        self.assertFalse(any(relation.source_key is None for relation in result.resource_relations))

    def test_xlsx_archive_entry_budget(self) -> None:
        path = self.root / "many.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "xl/workbook.xml",
                "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'><sheets/></workbook>",
            )
            archive.writestr("a", "")
            archive.writestr("b", "")
        result = _xlsx_extract(self.context("many.xlsx", text=False, max_entries=2))
        self.assertTrue(any("超过 2 项预算" in warning for warning in result.warnings))

    def test_streamed_line_and_total_budgets(self) -> None:
        self.write("large.jsonl", json.dumps({"value": "x" * 100}) + "\n" + '{"id": 2}\n')
        self.write("large.csv", "id,value\n1," + "x" * 100 + "\n2,ok\n")
        json_result = _json_lines_extract(
            self.context("large.jsonl", text=False, max_line=64, max_stream=128)
        )
        csv_result = _csv_extract(
            self.context("large.csv", text=False, max_line=64, max_stream=128)
        )
        self.assertGreaterEqual(json_result.metadata["oversized_lines"], 1)
        self.assertGreaterEqual(csv_result.metadata["oversized_lines"], 1)
        self.assertTrue(any("单行超过" in warning for warning in json_result.warnings))
        self.assertTrue(any("单行超过" in warning for warning in csv_result.warnings))

        self.write("budget.jsonl", '{"id": 1}\n' * 20)
        budget_result = _json_lines_extract(
            self.context("budget.jsonl", text=False, max_line=64, max_stream=80)
        )
        self.assertTrue(budget_result.metadata["stream_budget_reached"])
        self.assertTrue(any("总预算" in warning for warning in budget_result.warnings))

    def test_science_binary_size_budget(self) -> None:
        header = repr({"descr": "<f8", "fortran_order": False, "shape": (2,)})
        header_bytes = (header + " " * (64 - len(header) - 1) + "\n").encode("latin1")
        self.write("array.npy", b"\x93NUMPY" + bytes([1, 0]) + struct.pack("<H", len(header_bytes)) + header_bytes)
        result = ScientificDataExtractor().extract(
            self.context("array.npy", text=False, max_binary=1)
        )
        self.assertTrue(result.metadata["skipped_due_to_size"])
        self.assertTrue(any("二进制解析预算" in warning for warning in result.warnings))

    def test_npz_archive_entry_budget(self) -> None:
        path = self.root / "arrays.npz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("a.npy", b"not-npy")
            archive.writestr("b.npy", b"not-npy")
        result = _npz_extract(self.context("arrays.npz", text=False, max_entries=1))
        self.assertTrue(any("超过 1 项预算" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
