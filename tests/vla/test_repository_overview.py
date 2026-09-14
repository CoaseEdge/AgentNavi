from __future__ import annotations

import json
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
from agentnavi.utils import utc_now


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
            "[project]\nname='fixture'\n", encoding="utf-8"
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
        now = utc_now()
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

    def _project(self):
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT * FROM projects WHERE id='fixture'"
            ).fetchone()

    def test_overview_has_evidence_workflow_modules_and_reading_entry(self) -> None:
        core = repository_overview_data(self.database, self._project())

        self.assertIn("先理解仓库", core["purpose"]["summary"])
        self.assertIn("反复搜索", core["need"]["problem"]["summary"])
        self.assertEqual(len(core["workflow"]), 7)
        self.assertLessEqual(core["stats"]["documentsRead"], 6)
        self.assertEqual([step["step"] for step in core["workflow"]], list(range(1, 8)))
        self.assertEqual(core["workflow"][0]["evidence"][0]["path"], "docs/architecture.md")
        self.assertEqual([module["name"] for module in core["modules"]], ["Documentation", "Runtime"])
        reading_paths = [entry["path"] for entry in core["readingOrder"]]
        self.assertEqual(reading_paths[:2], ["README.md", "docs/architecture.md"])
        self.assertIn("src/fixture/__main__.py", reading_paths)
        self.assertIn("src/fixture/cli.py", reading_paths)

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

    def test_code_fences_private_paths_and_outside_symlinks_never_leak(self) -> None:
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

        for secret in ("TOP-SECRET", "/private/inside", "/Users/alice", str(outside)):
            self.assertNotIn(secret, wire)
            self.assertNotIn(secret, text)
        self.assertIn("DOCUMENT_OUTSIDE_PROJECT", wire)

    def test_document_read_is_bounded_and_adapter_is_strictly_allowlisted(self) -> None:
        oversized = "# Fixture\n\n## 一句话理解\n\n首段有效。\n" + "普通说明。\n" * 5000
        (self.root / "README.md").write_text(oversized, encoding="utf-8")
        core = repository_overview_data(self.database, self._project())
        core["rawRows"] = [{"root": str(self.root), "sourceContent": "secret"}]
        core["events"] = [{"payload": "secret-event"}]
        core["project"]["privateExtension"] = str(self.root)

        view = repo_overview_view(core).to_dict()
        wire = json.dumps(view, ensure_ascii=False)

        self.assertIn("DOCUMENT_TRUNCATED", {warning["code"] for warning in core["warnings"]})
        for forbidden in ("rawRows", "sourceContent", "secret-event", "privateExtension", str(self.root)):
            self.assertNotIn(forbidden, wire)

    def test_text_and_view_are_independently_generated_and_core_is_read_only(self) -> None:
        before = self.database.settings.database_path.read_bytes()
        core = repository_overview_data(self.database, self._project())
        after = self.database.settings.database_path.read_bytes()
        self.assertEqual(before, after)

        with patch(
            "agentnavi.mcp.adapters.repo_overview.repo_overview_view",
            side_effect=AssertionError("text must not call view"),
        ):
            text = repo_overview_text(core)
        self.assertIn(core["purpose"]["summary"], text)
        self.assertEqual(repo_overview_view(core).to_dict()["view"], "repo-overview")


if __name__ == "__main__":
    unittest.main()
