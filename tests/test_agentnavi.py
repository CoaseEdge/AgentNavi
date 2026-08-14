from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentnavi.config import Settings
from agentnavi.database import ensure_database
from agentnavi.engine import scan_project
from agentnavi.exporter import export_obsidian
from agentnavi.hooks import ingest_hook
from agentnavi.integrations import install_integration
from agentnavi.query import build_context, impact_data
from agentnavi.registry import add_project, list_projects, resolve_project
from agentnavi.tasks import list_tasks


class AgentNaviTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.home = self.base / "agentnavi-home"
        self.project_root = self.base / "sample-project"
        self.project_root.mkdir()
        self._write_sample_project()
        self.settings = Settings.load(self.home)
        self.database = ensure_database(self.settings)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write(self, relative: str, content: str) -> Path:
        path = self.project_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _write_sample_project(self) -> None:
        self._write(
            "pyproject.toml",
            "[project]\nname = 'sample-project'\nversion = '0.1.0'\n",
        )
        self._write(
            "src/payment/service.py",
            "def charge(amount: int) -> bool:\n    return amount > 0\n",
        )
        self._write(
            "src/membership/upgrade.py",
            "from src.payment.service import charge\n\n"
            "def upgrade(price: int) -> bool:\n    return charge(price)\n",
        )
        self._write(
            "tests/test_upgrade.py",
            "from src.membership.upgrade import upgrade\n\n"
            "def test_upgrade():\n    assert upgrade(50)\n",
        )
        self._write(
            "docs/membership.md",
            "# 会员升级\n\n会员升级会调用[支付服务](../src/payment/service.py)。\n",
        )

    def _add_and_scan(self):
        project = add_project(self.database, self.project_root)
        report = scan_project(self.database, project, full=True)
        return resolve_project(self.database, project["id"]), report

    def test_scan_builds_three_layer_foundation_without_repo_intrusion(self) -> None:
        project, report = self._add_and_scan()
        self.assertEqual(report.total_files, 5)
        self.assertGreaterEqual(report.physical_edges, 3)
        self.assertGreaterEqual(report.semantic_nodes, 4)
        self.assertFalse((self.project_root / ".agentnavi").exists())
        self.assertFalse((self.project_root / ".obsidian").exists())
        self.assertFalse((self.project_root / "_graph").exists())

        with self.database.connect() as connection:
            layer1 = connection.execute(
                "SELECT COUNT(*) AS count FROM nodes WHERE project_id=? AND layer=1",
                (project["id"],),
            ).fetchone()["count"]
            layer2 = connection.execute(
                "SELECT COUNT(*) AS count FROM nodes WHERE project_id=? AND layer=2",
                (project["id"],),
            ).fetchone()["count"]
        self.assertEqual(layer1, 5)
        self.assertGreaterEqual(layer2, 4)

    def test_chinese_task_routes_to_concept_dependency_and_test(self) -> None:
        project, _ = self._add_and_scan()
        context = build_context(self.database, project, "修改会员升级和支付逻辑")
        self.assertIn("会员升级", context)
        self.assertIn("src/membership/upgrade.py", context)
        self.assertIn("src/payment/service.py", context)
        self.assertIn("tests/test_upgrade.py", context)

    def test_incremental_scan_only_reprocesses_changed_file(self) -> None:
        project, _ = self._add_and_scan()
        self._write(
            "src/payment/service.py",
            "def charge(amount: int) -> bool:\n    return amount >= 1\n\n"
            "def refund(amount: int) -> bool:\n    return amount > 0\n",
        )
        report = scan_project(self.database, project, full=False)
        self.assertGreaterEqual(report.changed_files, 1)
        self.assertLess(report.changed_files, report.total_files)

    def test_impact_uses_physical_and_semantic_graphs(self) -> None:
        project, _ = self._add_and_scan()
        data = impact_data(self.database, project, "src/membership/upgrade.py")
        outgoing = {(item["relation"], item["path"]) for item in data["outgoing"]}
        incoming = {(item["relation"], item["path"]) for item in data["incoming"]}
        self.assertIn(("imports", "src/payment/service.py"), outgoing)
        self.assertTrue(any(path == "tests/test_upgrade.py" for _, path in incoming))
        self.assertTrue(any(item["label"] == "会员升级" for item in data["concepts"]))

    def test_hooks_create_task_events_and_affected_concepts(self) -> None:
        session = "session-1"
        start = ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "SessionStart",
                "session_id": session,
                "cwd": str(self.project_root),
                "source": "startup",
            },
        )
        self.assertIn("AgentNavi 项目上下文", start.stdout)
        prompt = ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "UserPromptSubmit",
                "session_id": session,
                "cwd": str(self.project_root),
                "prompt": "修改会员升级支付逻辑",
            },
        )
        self.assertIsNotNone(prompt.task_id)
        ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "PostToolUse",
                "session_id": session,
                "cwd": str(self.project_root),
                "tool_name": "apply_patch",
                "tool_input": {
                    "patch": "*** Begin Patch\n*** Update File: src/membership/upgrade.py\n*** End Patch"
                },
                "tool_response": {"success": True},
            },
        )
        ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "PostToolUse",
                "session_id": session,
                "cwd": str(self.project_root),
                "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/test_upgrade.py"},
                "tool_response": {"exit_code": 0},
            },
        )
        stop = ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "Stop",
                "session_id": session,
                "cwd": str(self.project_root),
                "last_assistant_message": "已完成修改并通过测试。",
            },
        )
        self.assertEqual(stop.stdout, "{}")

        project = resolve_project(self.database, cwd=self.project_root)
        tasks = list_tasks(self.database, project["id"])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "completed")
        self.assertIn("通过测试", tasks[0]["summary"])
        task_node = self.database.node_id(project["id"], 3, "task", tasks[0]["id"])
        with self.database.connect() as connection:
            relations = {
                row["relation"]
                for row in connection.execute(
                    "SELECT relation FROM edges WHERE project_id=? AND layer=3 AND source_id=?",
                    (project["id"], task_node),
                )
            }
        self.assertIn("modified", relations)
        self.assertIn("tested", relations)
        self.assertIn("affects", relations)

    def test_obsidian_export_is_regenerable_and_external(self) -> None:
        project, _ = self._add_and_scan()
        report = export_obsidian(
            self.database,
            destination=self.base / "vault",
            project_selector=project["id"],
        )
        self.assertEqual(report.projects, 1)
        self.assertGreaterEqual(report.concepts, 3)
        index = report.destination / "AgentNavi" / "首页.md"
        self.assertTrue(index.exists())
        concept_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (report.destination / "AgentNavi" / "Concepts").rglob("*.md")
        )
        self.assertIn("src/membership/upgrade.py", concept_text)
        self.assertFalse((self.project_root / "AgentNavi").exists())

    def test_external_semantic_provider_is_vendor_neutral(self) -> None:
        provider = self.base / "provider.py"
        provider.write_text(
            "import json, sys\n"
            "payload = json.load(sys.stdin)\n"
            "print(json.dumps({\n"
            "  'concepts': [{'key': 'billing-rule', 'label': '计费规则', "
            "'files': ['src/payment/service.py'], 'confidence': 0.91}],\n"
            "  'relations': []\n"
            "}, ensure_ascii=False))\n",
            encoding="utf-8",
        )
        config = {
            "semantic_command": f"{os.environ.get('PYTHON', os.sys.executable)} {provider}",
            "obsidian_vault": str(self.base / "provider-vault"),
        }
        self.settings.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings.config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        database = ensure_database(Settings.load(self.home))
        project = add_project(database, self.project_root)
        scan_project(database, project, full=True)
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM nodes WHERE project_id=? AND layer=2 AND kind='concept' AND key='billing-rule'",
                (project["id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["label"], "计费规则")
        self.assertEqual(row["source"], "external-semantic-provider")

    def test_integration_installer_merges_idempotently(self) -> None:
        hook_path = self.base / "codex" / "hooks.json"
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "echo existing"}],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        install_integration("codex", hook_path=hook_path, install_skill=False)
        install_integration("codex", hook_path=hook_path, install_skill=False)
        data = json.loads(hook_path.read_text(encoding="utf-8"))
        groups = data["hooks"]["PostToolUse"]
        existing = [
            handler
            for group in groups
            for handler in group.get("hooks", [])
            if handler.get("command") == "echo existing"
        ]
        agentnavi = [
            handler
            for group in groups
            for handler in group.get("hooks", [])
            if "agentnavi hook ingest --agent codex" in handler.get("command", "")
        ]
        self.assertEqual(len(existing), 1)
        self.assertEqual(len(agentnavi), 1)

    def test_semantic_graph_suppresses_weaker_duplicate_relation(self) -> None:
        project, _ = self._add_and_scan()
        membership_id = self.database.node_id(project["id"], 2, "concept", "membership")
        payment_id = self.database.node_id(project["id"], 2, "concept", "payment")
        with self.database.connect() as connection:
            relations = [
                row["relation"]
                for row in connection.execute(
                    "SELECT relation FROM edges WHERE project_id=? AND layer=2 AND source_id=? AND target_id=?",
                    (project["id"], membership_id, payment_id),
                )
            ]
        self.assertEqual(relations, ["depends_on"])

    def test_full_rescan_preserves_historical_task_to_concept_edges(self) -> None:
        session = "history-session"
        ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "SessionStart",
                "session_id": session,
                "cwd": str(self.project_root),
                "source": "startup",
            },
        )
        prompt = ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "UserPromptSubmit",
                "session_id": session,
                "cwd": str(self.project_root),
                "prompt": "修改会员升级逻辑",
            },
        )
        ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "PostToolUse",
                "session_id": session,
                "cwd": str(self.project_root),
                "tool_name": "apply_patch",
                "tool_input": {
                    "patch": "*** Begin Patch\n*** Update File: src/membership/upgrade.py\n*** End Patch"
                },
            },
        )
        ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "Stop",
                "session_id": session,
                "cwd": str(self.project_root),
                "last_assistant_message": "已完成。",
            },
        )
        project = resolve_project(self.database, cwd=self.project_root)
        task_node = self.database.node_id(project["id"], 3, "task", prompt.task_id)
        with self.database.connect() as connection:
            before = connection.execute(
                "SELECT COUNT(*) AS count FROM edges WHERE project_id=? AND layer=3 AND source_id=? AND relation='affects'",
                (project["id"], task_node),
            ).fetchone()["count"]
        self.assertGreater(before, 0)

        scan_project(self.database, project, full=True)
        with self.database.connect() as connection:
            after = connection.execute(
                "SELECT COUNT(*) AS count FROM edges WHERE project_id=? AND layer=3 AND source_id=? AND relation='affects'",
                (project["id"], task_node),
            ).fetchone()["count"]
        self.assertEqual(after, before)

    def test_registry_can_resolve_nested_working_directory(self) -> None:
        project = add_project(self.database, self.project_root)
        nested = self.project_root / "src" / "membership"
        resolved = resolve_project(self.database, cwd=nested)
        self.assertEqual(resolved["id"], project["id"])
        self.assertEqual(len(list_projects(self.database)), 1)


if __name__ == "__main__":
    unittest.main()
