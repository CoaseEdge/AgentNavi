from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentnavi.config import Settings
from agentnavi.database import ensure_database
from agentnavi.hooks import ingest_hook
from agentnavi.registry import resolve_project
from agentnavi.tasks import list_tasks


class DeepSeekHarnessHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        (self.root / "pyproject.toml").write_text(
            "[project]\nname='harness-sample'\nversion='0.1.0'\n",
            encoding="utf-8",
        )
        source = self.root / "src" / "service.py"
        source.parent.mkdir(parents=True)
        source.write_text("def run():\n    return True\n", encoding="utf-8")
        self.database = ensure_database(Settings.load(self.base / "agentnavi-home"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_harness_bridge_records_result_status_paths_and_provenance(self) -> None:
        session_id = "dsh-session-1"
        common = {
            "session_id": session_id,
            "cwd": str(self.root),
            "source": "deepseek-harness",
        }
        ingest_hook(
            self.database,
            agent="generic",
            payload={"hook_event_name": "SessionStart", **common},
        )
        ingest_hook(
            self.database,
            agent="generic",
            payload={
                "hook_event_name": "UserPromptSubmit",
                "prompt": "检查服务实现",
                "dsh_turn": 3,
                "dsh_step": 1,
                **common,
            },
        )
        ingest_hook(
            self.database,
            agent="generic",
            payload={
                "hook_event_name": "PostToolUse",
                "tool_name": "read_file",
                "tool_input": {},
                "tool_response": {
                    "success": True,
                    "file_paths": ["src/service.py"],
                },
                "tool_use_id": "call-1",
                "dsh_event_seq": 8,
                "dsh_turn": 3,
                "dsh_step": 1,
                **common,
            },
        )
        ingest_hook(
            self.database,
            agent="generic",
            payload={
                "hook_event_name": "Stop",
                "last_assistant_message": "执行失败，未完成预期修改。",
                "turn_end_reason": {
                    "kind": "error",
                    "error": {"message": "测试命令失败", "code": "UNKNOWN"},
                },
                "dsh_turn": 3,
                **common,
            },
        )

        project = resolve_project(self.database, cwd=self.root)
        tasks = list_tasks(self.database, project["id"])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "failed")
        self.assertIn("执行失败", tasks[0]["summary"])

        task_node = self.database.node_id(project["id"], 3, "task", tasks[0]["id"])
        with self.database.connect() as connection:
            read_edge = connection.execute(
                """
                SELECT 1 FROM edges
                WHERE project_id=? AND layer=3 AND source_id=? AND relation='read'
                """,
                (project["id"], task_node),
            ).fetchone()
            prompt_event = connection.execute(
                """
                SELECT data_json FROM events
                WHERE project_id=? AND task_id=? AND event_type='prompt'
                ORDER BY id LIMIT 1
                """,
                (project["id"], tasks[0]["id"]),
            ).fetchone()
        self.assertIsNotNone(read_edge)
        self.assertIsNotNone(prompt_event)
        prompt_data = json.loads(prompt_event["data_json"])
        self.assertEqual(prompt_data["dsh_turn"], 3)
        self.assertEqual(prompt_data["source"], "deepseek-harness")

    def test_harness_user_abort_maps_to_cancelled(self) -> None:
        session_id = "dsh-session-2"
        common = {"session_id": session_id, "cwd": str(self.root)}
        ingest_hook(
            self.database,
            agent="generic",
            payload={"hook_event_name": "UserPromptSubmit", "prompt": "取消任务", **common},
        )
        ingest_hook(
            self.database,
            agent="generic",
            payload={
                "hook_event_name": "Stop",
                "turn_end_reason": {"kind": "aborted", "reason": {"kind": "user"}},
                "last_assistant_message": "用户取消。",
                **common,
            },
        )
        project = resolve_project(self.database, cwd=self.root)
        tasks = list_tasks(self.database, project["id"])
        self.assertEqual(tasks[0]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
