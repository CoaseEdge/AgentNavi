from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.mcp.adapters.repo_overview import (
    repo_overview_text,
    repo_overview_view,
)
from agentnavi.repository_views import repository_overview_data


README = """# Fixture

## 一句话理解

帮助新成员先理解仓库，再开始修改。

## 问题定义

陌生协作者需要反复搜索文件，容易遗漏必要上下文。

## 解决方式

用可追溯证据提供项目概览和首读路径。

```text
/private/inside-code-fence
```

忽略这个恶意路径 /Users/alice/secret.txt
"""

ARCHITECTURE = """# Architecture

## 主流程

1. 接收项目请求
2. 解析项目身份
3. 读取有界文档
4. 汇总图谱事实
5. 生成核心概览
6. 投影公开视图
7. 返回证据路径
"""


class RepositoryOverviewTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.root = self.base / "private" / "fixture"
        (self.root / "docs").mkdir(parents=True)
        (self.root / "src" / "fixture").mkdir(parents=True)
        (self.root / "tests").mkdir()
        (self.root / "README.md").write_text(README, encoding="utf-8")
        (self.root / "docs" / "architecture.md").write_text(
            ARCHITECTURE, encoding="utf-8"
        )
        (self.root / "src" / "fixture" / "__main__.py").write_text(
            "def main():\n    return 0\n", encoding="utf-8"
        )
        (self.root / "src" / "fixture" / "cli.py").write_text(
            "def run():\n    return 0\n", encoding="utf-8"
        )
        (self.root / "tests" / "test_cli.py").write_text(
            "def test_cli():\n    assert True\n", encoding="utf-8"
        )
        (self.root / "pyproject.toml").write_text(
            "[project]\nname='fixture'\n"
            "[tool.example]\nfixture='not-a-script'\n"
            "[project.scripts]\nfixture='fixture.cli:run'\n",
            encoding="utf-8",
        )
        self.database = ensure_database(Settings.load(self.base / "agentnavi-home"))
        self._populate(
            [
                "tests/test_cli.py",
                "src/fixture/cli.py",
                "README.md",
                "pyproject.toml",
                "docs/architecture.md",
                "src/fixture/__main__.py",
            ]
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _populate(self, paths: list[str]) -> None:
        now = "2026-09-14T12:00:00+00:00"
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(
                    id, name, root, kind, created_at, updated_at, last_scan_at
                ) VALUES ('fixture', 'Fixture', ?, 'software', ?, ?, ?)
                """,
                (str(self.root.resolve()), now, now, now),
            )
            file_ids: dict[str, str] = {}
            for path in paths:
                file_ids[path] = Database.upsert_node(
                    connection,
                    project_id="fixture",
                    layer=1,
                    kind="file",
                    key=path,
                    label=Path(path).name,
                    data={"language": "python"},
                    source="repository",
                )
                absolute = self.root / path
                stat = absolute.stat()
                connection.execute(
                    """INSERT INTO file_state(
                           project_id, path, mtime_ns, size, digest, updated_at
                       ) VALUES ('fixture', ?, ?, ?, ?, ?)""",
                    (
                        path,
                        stat.st_mtime_ns,
                        stat.st_size,
                        hashlib.blake2s(absolute.read_bytes()).hexdigest(),
                        now,
                    ),
                )
            for key, label, linked_paths in (
                ("runtime", "Runtime", ["src/fixture/__main__.py", "src/fixture/cli.py"]),
                ("docs", "Documentation", ["README.md", "docs/architecture.md"]),
            ):
                concept_id = Database.upsert_node(
                    connection,
                    project_id="fixture",
                    layer=2,
                    kind="concept",
                    key=key,
                    label=label,
                    data={"active": True, "file_count": len(linked_paths)},
                    confidence=0.8,
                    source="semantic-heuristic",
                )
                for path in linked_paths:
                    Database.upsert_edge(
                        connection,
                        project_id="fixture",
                        layer=2,
                        source_id=concept_id,
                        relation="implemented_by",
                        target_id=file_ids[path],
                        confidence=0.75,
                        source="semantic-heuristic",
                    )
            connection.commit()

    def _sync_file_state(self, path: str) -> None:
        absolute = self.root / path
        stat = absolute.stat()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO file_state(project_id, path, mtime_ns, size, digest, updated_at)
                   VALUES ('fixture', ?, ?, ?, ?, '2026-09-14T12:01:00+00:00')
                   ON CONFLICT(project_id, path) DO UPDATE SET
                     mtime_ns=excluded.mtime_ns, size=excluded.size,
                     digest=excluded.digest, updated_at=excluded.updated_at""",
                (
                    path,
                    stat.st_mtime_ns,
                    stat.st_size,
                    hashlib.blake2s(absolute.read_bytes()).hexdigest(),
                ),
            )
            connection.commit()

    def _project(self):
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT * FROM projects WHERE id='fixture'"
            ).fetchone()

    def test_overview_has_evidence_workflow_modules_and_reading_entry(self) -> None:
        core = repository_overview_data(self.database, self._project())

        self.assertIn("先理解仓库", core["purpose"]["summary"])
        self.assertIn("反复搜索", core["need"]["problem"]["summary"])
        self.assertIn("可追溯证据", core["need"]["solution"]["summary"])
        self.assertEqual(len(core["workflow"]), 7)
        self.assertLessEqual(core["stats"]["documentsRead"], 6)
        self.assertEqual([step["step"] for step in core["workflow"]], list(range(1, 8)))
        self.assertEqual(core["workflow"][0]["evidence"][0]["path"], "docs/architecture.md")
        self.assertEqual([module["name"] for module in core["modules"]], ["Documentation", "Runtime"])
        reading_paths = [entry["path"] for entry in core["readingOrder"]]
        self.assertEqual(reading_paths[:2], ["README.md", "docs/architecture.md"])
        self.assertIn("pyproject.toml", reading_paths)
        self.assertIn("src/fixture/cli.py", reading_paths)
        self.assertIn("src/fixture/__main__.py", reading_paths)
        self.assertIn("tests/test_cli.py", reading_paths)
        manifest_entry = next(
            item for item in core["readingOrder"] if item["path"] == "src/fixture/cli.py"
        )
        self.assertEqual(manifest_entry["evidence"][0]["line_start"], 6)
        runtime = next(item for item in core["modules"] if item["name"] == "Runtime")
        self.assertNotIn("line_start", runtime["evidence"][0])

    def test_results_are_deterministic_across_database_insertion_order(self) -> None:
        first = repository_overview_data(self.database, self._project())
        first["project"].pop("root")

        other_home = self.base / "other-home"
        other_database = ensure_database(Settings.load(other_home))
        original = self.database
        self.database = other_database
        self._populate(
            [
                "src/fixture/__main__.py",
                "docs/architecture.md",
                "pyproject.toml",
                "README.md",
                "src/fixture/cli.py",
                "tests/test_cli.py",
            ]
        )
        second = repository_overview_data(other_database, self._project())
        second["project"].pop("root")
        self.database = original

        self.assertEqual(first, second)

    def test_missing_document_evidence_returns_warnings_without_invention(self) -> None:
        (self.root / "README.md").write_text(
            "普通说明，不包含受支持的证据标题。\n", encoding="utf-8"
        )
        (self.root / "docs" / "architecture.md").unlink()

        core = repository_overview_data(self.database, self._project())

        self.assertEqual(core["purpose"]["summary"], "")
        self.assertEqual(core["workflow"], [])
        self.assertIn(
            "WORKFLOW_EVIDENCE_MISSING",
            {warning["code"] for warning in core["warnings"]},
        )

    def test_missing_explicit_solution_is_empty_and_warned(self) -> None:
        (self.root / "README.md").write_text(
            "# Fixture\n\n## 一句话理解\n\n只描述项目目的。\n\n## 问题定义\n\n重复搜索。\n",
            encoding="utf-8",
        )
        self._sync_file_state("README.md")

        core = repository_overview_data(self.database, self._project())

        self.assertEqual(core["need"]["solution"]["summary"], "")
        self.assertIn("NEED_EVIDENCE_MISSING", {item["code"] for item in core["warnings"]})

    def test_snapshot_status_detects_modified_new_and_deleted_documents(self) -> None:
        ready = repository_overview_data(self.database, self._project())
        self.assertEqual(ready["sourceState"]["status"], "ready")
        self.assertTrue(ready["sourceState"]["revision"].startswith("snapshot_"))
        ready_view = repo_overview_view(ready).to_dict()
        self.assertEqual(ready_view["sourceState"]["status"], "ready")
        self.assertEqual(
            ready_view["sourceState"]["revision"], ready["sourceState"]["revision"]
        )
        self.assertEqual(
            ready["sourceState"]["revision"],
            repository_overview_data(self.database, self._project())["sourceState"]["revision"],
        )

        (self.root / "README.md").write_text(README + "\nchanged\n", encoding="utf-8")
        modified = repository_overview_data(self.database, self._project())
        self.assertEqual(modified["sourceState"]["status"], "stale")
        self.assertEqual(
            repo_overview_view(modified).to_dict()["sourceState"]["status"],
            "stale",
        )
        self.assertIn("SOURCE_SNAPSHOT_STALE", {item["code"] for item in modified["warnings"]})

        self._sync_file_state("README.md")
        (self.root / "README.mdx").write_text("# 新文档\n", encoding="utf-8")
        new_document = repository_overview_data(self.database, self._project())
        self.assertEqual(new_document["sourceState"]["status"], "stale")
        (self.root / "README.mdx").unlink()

        (self.root / "README.md").unlink()
        deleted = repository_overview_data(self.database, self._project())
        self.assertEqual(deleted["sourceState"]["status"], "stale")

    def test_unindexed_live_readme_is_not_presented_as_l1_evidence(self) -> None:
        with self.database.connect() as connection:
            connection.execute("UPDATE projects SET last_scan_at=NULL WHERE id='fixture'")
            connection.commit()

        core = repository_overview_data(self.database, self._project())

        self.assertEqual(core["sourceState"]["status"], "partial")
        self.assertEqual(core["stats"]["documentsRead"], 0)
        self.assertEqual(core["purpose"], {"summary": "", "evidence": []})

    def test_reading_order_resolves_package_manifest_entry(self) -> None:
        (self.root / "pyproject.toml").unlink()
        (self.root / "bin").mkdir()
        (self.root / "bin" / "start.js").write_text(
            "export function main() {}\n", encoding="utf-8"
        )
        (self.root / "package.json").write_text(
            '{"name":"fixture","bin":{"fixture":"./bin/start.js"}}\n',
            encoding="utf-8",
        )
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM nodes WHERE project_id='fixture' AND layer=1 AND key='pyproject.toml'"
            )
            connection.execute(
                "DELETE FROM file_state WHERE project_id='fixture' AND path='pyproject.toml'"
            )
            for path in ("package.json", "bin/start.js"):
                Database.upsert_node(
                    connection,
                    project_id="fixture",
                    layer=1,
                    kind="file",
                    key=path,
                    label=Path(path).name,
                    source="repository",
                )
                absolute = self.root / path
                stat = absolute.stat()
                connection.execute(
                    """INSERT INTO file_state(
                           project_id, path, mtime_ns, size, digest, updated_at
                       ) VALUES ('fixture', ?, ?, ?, ?, '2026-09-14T12:01:00+00:00')""",
                    (
                        path,
                        stat.st_mtime_ns,
                        stat.st_size,
                        hashlib.blake2s(absolute.read_bytes()).hexdigest(),
                    ),
                )
            connection.commit()

        core = repository_overview_data(self.database, self._project())
        reading_paths = [item["path"] for item in core["readingOrder"]]

        self.assertIn("package.json", reading_paths)
        self.assertIn("bin/start.js", reading_paths)
        entry = next(item for item in core["readingOrder"] if item["path"] == "bin/start.js")
        self.assertIn("manifest", entry["reason"])
        self.assertEqual(entry["evidence"][0]["path"], "package.json")
        self.assertNotIn("line_start", entry["evidence"][0])

    def test_workflow_accepts_five_and_seven_and_stably_truncates_eight(self) -> None:
        def set_steps(count: int, *, continuation: bool = False) -> dict[str, object]:
            lines = ["# Architecture", "", "## 主流程", ""]
            for index in range(1, count + 1):
                lines.append(f"{index}. 标题 {index}")
                if continuation and index == 1:
                    lines.append("    这是第一步的详细说明。")
            (self.root / "docs" / "architecture.md").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
            self._sync_file_state("docs/architecture.md")
            return repository_overview_data(self.database, self._project())

        five = set_steps(5, continuation=True)
        self.assertEqual(len(five["workflow"]), 5)
        self.assertIn("详细说明", five["workflow"][0]["detail"])
        self.assertEqual(five["workflow"][0]["evidence"][0]["line_start"], 5)
        self.assertEqual(five["workflow"][0]["evidence"][0]["line_end"], 6)
        seven = set_steps(7)
        self.assertEqual(len(seven["workflow"]), 7)
        eight = set_steps(8)
        self.assertEqual(len(eight["workflow"]), 7)
        self.assertEqual([item["step"] for item in eight["workflow"]], list(range(1, 8)))
        self.assertIn("WORKFLOW_TRUNCATED", {item["code"] for item in eight["warnings"]})

    def test_large_document_digest_and_single_stream_read_are_authoritative(self) -> None:
        prefix = "# Fixture\n\n## 一句话理解\n\n可信概览。\n"
        original = (prefix + "A" * (64 * 1024)).encode()
        readme = self.root / "README.md"
        readme.write_bytes(original)
        self._sync_file_state("README.md")

        open_count = 0
        real_open = Path.open

        def counting_open(path: Path, *args, **kwargs):
            nonlocal open_count
            if path.resolve() == readme.resolve():
                open_count += 1
            return real_open(path, *args, **kwargs)

        with patch("pathlib.Path.open", new=counting_open):
            ready = repository_overview_data(self.database, self._project())
        self.assertEqual(ready["sourceState"]["status"], "ready")
        self.assertEqual(open_count, 1)
        self.assertIn("可信概览", ready["purpose"]["summary"])

        stat = readme.stat()
        replacement = bytearray(original)
        replacement[-1] = ord("B")
        readme.write_bytes(replacement)
        os.utime(readme, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        stale = repository_overview_data(self.database, self._project())
        self.assertEqual(stale["sourceState"]["status"], "stale")
        self.assertNotIn("可信概览", stale["purpose"]["summary"])

        readme.write_bytes(original)
        self._sync_file_state("README.md")
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE file_state SET digest='' WHERE project_id='fixture' AND path='README.md'"
            )
            connection.commit()
        unverifiable = repository_overview_data(self.database, self._project())
        self.assertEqual(unverifiable["sourceState"]["status"], "stale")
        self.assertNotIn("可信概览", unverifiable["purpose"]["summary"])

    def test_changed_final_output_paths_are_stale_and_removed(self) -> None:
        cases = (
            ("src/fixture/__main__.py", "modify"),
            ("src/fixture/cli.py", "delete"),
            ("tests/test_cli.py", "modify"),
            ("pyproject.toml", "modify"),
        )

        for path, action in cases:
            with self.subTest(path=path, action=action):
                absolute = self.root / path
                original = absolute.read_bytes()
                if action == "delete":
                    absolute.unlink()
                else:
                    absolute.write_bytes(original + b"\n# changed\n")

                core = repository_overview_data(self.database, self._project())
                self.assertEqual(core["sourceState"]["status"], "stale")
                public_paths = {
                    item_path
                    for module in core["modules"]
                    for item_path in module["paths"]
                }
                public_paths.update(item["path"] for item in core["readingOrder"])
                evidence_paths = {
                    evidence["path"]
                    for item in [*core["modules"], *core["readingOrder"]]
                    for evidence in item["evidence"]
                    if "path" in evidence
                }
                self.assertNotIn(path, public_paths)
                self.assertNotIn(path, evidence_paths)
                self.assertIn(
                    "SOURCE_SNAPSHOT_STALE",
                    {warning["code"] for warning in core["warnings"]},
                )

                absolute.parent.mkdir(parents=True, exist_ok=True)
                absolute.write_bytes(original)
                self._sync_file_state(path)

    def test_code_fences_private_paths_and_outside_symlinks_never_leak(self) -> None:
        (self.root / "README.md").write_text(
            "# Fixture\n\n## 一句话理解\n\n"
            "打开 cursor://file/Users/alice/private/project。\n\n"
            "## 问题定义\n\n位置 custom-editor://file/Users/alice/private/problem。\n\n"
            "## 解决方式\n\n位置 vscode-insiders://file/Users/alice/private/solution "
            "和 file:/Users/alice/private/solution。\n",
            encoding="utf-8",
        )
        self._sync_file_state("README.md")
        outside = self.base / "outside.md"
        outside.write_text("TOP-SECRET /private/outside", encoding="utf-8")
        decisions = self.root / "docs" / "decisions"
        decisions.mkdir()
        (decisions / "0001-secret.md").symlink_to(outside)
        with self.database.connect() as connection:
            Database.upsert_node(
                connection,
                project_id="fixture",
                layer=1,
                kind="file",
                key="docs/decisions/0001-secret.md",
                label="0001-secret.md",
                source="repository",
            )
            connection.commit()

        core = repository_overview_data(self.database, self._project())
        view = repo_overview_view(core).to_dict()
        text = repo_overview_text(core)
        wire = json.dumps(view, ensure_ascii=False)

        for secret in (
            "TOP-SECRET",
            "/private/inside",
            "/Users/alice",
            "file:/",
            "vscode://file/",
            "vscode-insiders://file/",
            "cursor://file/",
            "custom-editor://file/",
            str(outside),
        ):
            self.assertNotIn(secret, wire)
            self.assertNotIn(secret, text)
        self.assertIn("DOCUMENT_OUTSIDE_PROJECT", wire)

    def test_document_read_is_bounded_and_adapter_is_strictly_allowlisted(self) -> None:
        oversized = "# Fixture\n\n## 一句话理解\n\n首段有效。\n" + "普通说明。\n" * 5000
        (self.root / "README.md").write_text(oversized, encoding="utf-8")
        self._sync_file_state("README.md")
        core = repository_overview_data(self.database, self._project())
        core["rawRows"] = [{"root": str(self.root), "sourceContent": "secret"}]
        core["events"] = [{"payload": "secret-event"}]
        core["project"]["privateExtension"] = str(self.root)
        core["modules"][0]["summary"] = f"cursor://file{self.root}/secret.py"
        core["modules"][1]["summary"] = "参考 https://example.com/modules"

        view = repo_overview_view(core).to_dict()
        wire = json.dumps(view, ensure_ascii=False)

        self.assertIn("DOCUMENT_TRUNCATED", {warning["code"] for warning in core["warnings"]})
        for forbidden in ("rawRows", "sourceContent", "secret-event", "privateExtension", str(self.root)):
            self.assertNotIn(forbidden, wire)
        self.assertEqual(
            view["data"]["modules"][0]["summary"],
            "[内容含路径，已隐藏]",
        )
        self.assertEqual(
            view["data"]["modules"][1]["summary"],
            "参考 https://example.com/modules",
        )

    def test_text_and_view_are_independently_generated_and_core_is_read_only(self) -> None:
        def table_snapshot() -> dict[str, list[tuple[object, ...]]]:
            with self.database.connect() as connection:
                self.assertEqual(connection.total_changes, 0)
                return {
                    table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
                    for table in ("projects", "nodes", "edges", "file_state", "tasks")
                }

        before_tables = table_snapshot()
        before = self.database.settings.database_path.read_bytes()
        core = repository_overview_data(self.database, self._project())
        after = self.database.settings.database_path.read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(before_tables, table_snapshot())

        with patch(
            "agentnavi.mcp.adapters.repo_overview.repo_overview_view",
            side_effect=AssertionError("text must not call view"),
        ):
            text = repo_overview_text(core)
        self.assertIn(core["purpose"]["summary"], text)
        self.assertIn(core["need"]["problem"]["summary"], text)
        self.assertIn(core["need"]["solution"]["summary"], text)
        self.assertGreaterEqual(text.count("证据：README.md:"), 3)
        self.assertIn("证据：docs/architecture.md:", text)
        self.assertIn("关键文件：", text)
        self.assertIn("证据：src/fixture/", text)
        self.assertIn("证据：pyproject.toml:", text)
        self.assertEqual(repo_overview_view(core).to_dict()["view"], "repo-overview")


if __name__ == "__main__":
    unittest.main()
