from __future__ import annotations

import json
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from agentnavi.config import Settings
from agentnavi.database import ensure_database
from agentnavi.engine import scan_project
from agentnavi.registry import add_project
from agentnavi.utils import json_loads


class FormatScanningIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.database = ensure_database(Settings.load(self.base / "agentnavi-home"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_text(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_scan_persists_structured_code_and_science_resources(self) -> None:
        self.write_text("pyproject.toml", "[project]\nname='format-sample'\nversion='0.1.0'\n")
        self.write_text("data/source.csv", "id,value\n1,3.5\n2,4.5\n")
        self.write_text("config/pipeline.json", json.dumps({"input": "../data/source.csv"}))
        self.write_text("go.mod", "module example.com/format\n")
        self.write_text("internal/pay/pay.go", "package pay\nfunc Charge() {}\n")
        self.write_text("cmd/main.go", 'package main\nimport "example.com/format/internal/pay"\nfunc main(){pay.Charge()}\n')

        header = repr({"descr": "<f8", "fortran_order": False, "shape": (2, 2)})
        header_bytes = (header + " " * (64 - len(header) - 1) + "\n").encode("latin1")
        (self.root / "data/matrix.npy").write_bytes(
            b"\x93NUMPY" + bytes([1, 0]) + struct.pack("<H", len(header_bytes)) + header_bytes
        )
        sqlite_path = self.root / "data/science.sqlite"
        connection = sqlite3.connect(sqlite_path)
        connection.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY, value REAL)")
        connection.commit()
        connection.close()

        project = add_project(self.database, self.root)
        report = scan_project(self.database, project, full=True)
        self.assertGreater(report.physical_nodes, report.total_files)

        with self.database.connect() as connection:
            csv_node = connection.execute(
                "SELECT * FROM nodes WHERE project_id=? AND layer=1 AND kind='file' AND key='data/source.csv'",
                (project["id"],),
            ).fetchone()
            self.assertIsNotNone(csv_node)
            csv_data = json_loads(csv_node["data_json"], {})
            self.assertIn("dataset", csv_data["roles"])
            self.assertEqual(csv_data["columns"], ["id", "value"])
            self.assertTrue(csv_data["extractor_signature"])

            json_id = self.database.node_id(project["id"], 1, "file", "config/pipeline.json")
            csv_id = self.database.node_id(project["id"], 1, "file", "data/source.csv")
            edge = connection.execute(
                "SELECT * FROM edges WHERE project_id=? AND layer=1 AND source_id=? AND target_id=? AND relation='references'",
                (project["id"], json_id, csv_id),
            ).fetchone()
            self.assertIsNotNone(edge)

            go_id = self.database.node_id(project["id"], 1, "file", "cmd/main.go")
            pay_id = self.database.node_id(project["id"], 1, "file", "internal/pay/pay.go")
            edge = connection.execute(
                "SELECT * FROM edges WHERE project_id=? AND layer=1 AND source_id=? AND target_id=? AND relation='imports'",
                (project["id"], go_id, pay_id),
            ).fetchone()
            self.assertIsNotNone(edge)

            kinds = {
                row["kind"]
                for row in connection.execute(
                    "SELECT kind FROM nodes WHERE project_id=? AND layer=1 AND kind!='file'",
                    (project["id"],),
                )
            }
            self.assertIn("column", kinds)
            self.assertIn("array", kinds)
            self.assertIn("database_table", kinds)
            self.assertIn("symbol", kinds)

            concept_edges = {
                row["relation"]
                for row in connection.execute(
                    "SELECT relation FROM edges WHERE project_id=? AND layer=2",
                    (project["id"],),
                )
            }
            self.assertIn("data_provided_by", concept_edges)

        second = scan_project(self.database, project, full=False)
        self.assertEqual(second.changed_files, 0)


if __name__ == "__main__":
    unittest.main()
