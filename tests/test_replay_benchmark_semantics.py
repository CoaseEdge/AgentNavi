from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentnavi.benchmark import compare_suite, evaluate_retrieval_suite, record_observed_run
from agentnavi.config import Settings
from agentnavi.database import Database, ensure_database
from agentnavi.engine import scan_project
from agentnavi.eventlog import backfill_event_log, replay_event_log, verify_event_log
from agentnavi.hooks import ingest_hook
from agentnavi.registry import add_project, list_projects, remove_project, resolve_project
from agentnavi.semantic_overlays import (
    add_correction,
    decide_review_candidate,
    list_corrections,
    list_review_candidates,
    verify_semantic_overlay_log,
)
from agentnavi.tasks import close_task, create_task, record_event


class DurableContextTestCase(unittest.TestCase):
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
        project = add_project(
            self.database,
            self.project_root,
            project_id="sample-project",
        )
        report = scan_project(self.database, project, full=True)
        return resolve_project(self.database, project["id"]), report

    def _delete_database(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.settings.database_path}{suffix}")
            if path.exists():
                path.unlink()

    def _run_hook_task(self, session: str = "replay-session") -> str:
        ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "SessionStart",
                "session_id": session,
                "cwd": str(self.project_root),
            },
        )
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
        ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "Stop",
                "session_id": session,
                "cwd": str(self.project_root),
                "last_assistant_message": "已完成修改并通过测试。",
            },
        )
        ingest_hook(
            self.database,
            agent="codex",
            payload={
                "hook_event_name": "SessionEnd",
                "session_id": session,
                "cwd": str(self.project_root),
            },
        )
        assert prompt.task_id is not None
        return prompt.task_id

    def test_l3_event_log_rebuilds_after_complete_database_loss(self) -> None:
        task_id = self._run_hook_task()
        verification = verify_event_log(self.settings.event_log_path)
        self.assertEqual(verification.invalid, 0)
        self.assertGreaterEqual(verification.valid, 7)

        self._delete_database()
        self.database = ensure_database(Settings.load(self.home))
        report = replay_event_log(self.database, reset=True, strict=True)
        self.assertEqual(report.invalid, 0)
        self.assertEqual(report.applied, verification.valid)

        with self.database.connect() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            self.assertIsNotNone(task)
            self.assertEqual(task["status"], "completed")
            self.assertIn("通过测试", task["summary"])
            relations = {
                row["relation"]
                for row in connection.execute(
                    "SELECT relation FROM edges WHERE project_id=? AND layer=3",
                    ("sample-project",),
                )
            }
            self.assertTrue({"modified", "tested", "affects"}.issubset(relations))
            session = connection.execute(
                "SELECT * FROM sessions WHERE session_key='codex:replay-session'"
            ).fetchone()
            self.assertIsNotNone(session)
            self.assertIsNotNone(session["ended_at"])

        second = replay_event_log(self.database, strict=True)
        self.assertEqual(second.applied, 0)
        self.assertEqual(second.skipped, verification.valid)

    def test_legacy_sqlite_l3_can_be_backfilled_then_replayed(self) -> None:
        task_id = self._run_hook_task("legacy-session")
        self.settings.event_log_path.write_text("", encoding="utf-8")
        with self.database.connect() as connection:
            connection.execute("DELETE FROM applied_log_events")
            connection.commit()

        backfill = backfill_event_log(self.database)
        self.assertGreater(backfill.events_written, 0)
        self.assertEqual(verify_event_log(self.settings.event_log_path).invalid, 0)

        self._delete_database()
        self.database = ensure_database(Settings.load(self.home))
        replay = replay_event_log(self.database, reset=True, strict=True)
        self.assertGreater(replay.applied, 0)
        with self.database.connect() as connection:
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            self.assertIsNotNone(task)
            self.assertEqual(task["status"], "completed")

    def test_benchmark_reports_savings_only_when_quality_is_preserved(self) -> None:
        for index in range(24):
            self._write(f"docs/unrelated-{index:02d}.txt", "无关材料" * 3000)
        project, _ = self._add_and_scan()
        cases = self.base / "benchmark-cases.json"
        expected = [
            "src/membership/upgrade.py",
            "src/payment/service.py",
            "tests/test_upgrade.py",
        ]
        cases.write_text(
            json.dumps(
                [
                    {
                        "id": "membership-upgrade",
                        "task": "修改会员升级和支付逻辑，并运行对应测试",
                        "expected_files": expected,
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        results = evaluate_retrieval_suite(
            self.database,
            project,
            cases_path=cases,
            suite="retrieval-proof",
        )
        full = next(item for item in results if item["mode"] == "full-scan")
        navi = next(item for item in results if item["mode"] == "agentnavi")
        self.assertEqual(navi["metrics"]["recall"], 1.0)
        self.assertLess(
            navi["metrics"]["estimated_context_tokens"],
            full["metrics"]["estimated_context_tokens"],
        )
        comparison = compare_suite(
            self.database,
            project["id"],
            suite="retrieval-proof",
            run_kind="retrieval",
        )
        full_pair = next(
            item for item in comparison["comparisons"] if item["baseline"] == "full-scan"
        )
        self.assertEqual(full_pair["eligible_cases"], 1)
        self.assertGreater(full_pair["reduction"], 0.5)

        for mode, tokens, paths in (
            ("baseline", 10000, expected + ["docs/unrelated-00.txt"]),
            ("agentnavi", 2500, expected),
        ):
            task = create_task(
                self.database,
                project_id=project["id"],
                title=f"benchmark-{mode}",
                prompt="修改会员升级和支付逻辑",
            )
            for path in paths:
                record_event(
                    self.database,
                    project=project,
                    agent="manual",
                    task_id=task["id"],
                    event_type="read",
                    paths=[path],
                )
            close_task(self.database, task["id"], summary="完成")
            record_observed_run(
                self.database,
                project,
                suite="observed-proof",
                case_key="membership-upgrade",
                mode=mode,
                task_id=task["id"],
                expected_files=expected,
                exploration_tokens=tokens,
                success=True,
            )

        observed = compare_suite(
            self.database,
            project["id"],
            suite="observed-proof",
            run_kind="observed",
        )["comparisons"][0]
        self.assertEqual(observed["eligible_cases"], 1)
        self.assertAlmostEqual(observed["reduction"], 0.75)

    def test_human_overlay_survives_rescan_and_database_rebuild(self) -> None:
        project, _ = self._add_and_scan()
        add_correction(
            self.database,
            project_id=project["id"],
            action="rename_concept",
            subject_key="membership",
            value={"label": "会员升级体系"},
            note="人工确认名称",
        )
        # 同一重命名槽位后写覆盖前写，不能积累成相互竞争的两条规则。
        add_correction(
            self.database,
            project_id=project["id"],
            action="rename_concept",
            subject_key="membership",
            value={"label": "会员与升级"},
            note="采用最终名称",
        )
        scan_project(self.database, project, full=True)

        candidates = list_review_candidates(
            self.database,
            project["id"],
            limit=200,
            include_reviewed=True,
        )
        edge_candidate = next(
            item
            for item in candidates
            if item["kind"] == "edge"
            and item["subject_key"] == "membership"
            and item["relation"] == "depends_on"
            and item["object_key"] == "payment"
        )
        decide_review_candidate(
            self.database,
            project_id=project["id"],
            candidate_id=edge_candidate["id"],
            decision="reject",
            note="代码引用不代表稳定业务依赖",
        )
        scan_project(self.database, project, full=True)

        membership_id = Database.node_id(project["id"], 2, "concept", "membership")
        payment_id = Database.node_id(project["id"], 2, "concept", "payment")
        with self.database.connect() as connection:
            membership = connection.execute(
                "SELECT * FROM nodes WHERE id=?", (membership_id,)
            ).fetchone()
            self.assertEqual(membership["label"], "会员与升级")
            edge_count = connection.execute(
                """
                SELECT COUNT(*) AS count FROM edges
                WHERE source_id=? AND relation='depends_on' AND target_id=?
                """,
                (membership_id, payment_id),
            ).fetchone()["count"]
            self.assertEqual(edge_count, 0)
        rename_rows = [
            row
            for row in list_corrections(self.database, project["id"])
            if row["action"] == "rename_concept" and row["subject_key"] == "membership"
        ]
        self.assertEqual(len(rename_rows), 1)
        self.assertEqual(verify_semantic_overlay_log(self.settings.semantic_overlay_log_path).invalid, 0)

        self._delete_database()
        self.database = ensure_database(Settings.load(self.home))
        project = add_project(
            self.database,
            self.project_root,
            project_id="sample-project",
        )
        scan_project(self.database, project, full=True)
        with self.database.connect() as connection:
            membership = connection.execute(
                "SELECT * FROM nodes WHERE id=?", (membership_id,)
            ).fetchone()
            self.assertEqual(membership["label"], "会员与升级")
            edge_count = connection.execute(
                """
                SELECT COUNT(*) AS count FROM edges
                WHERE source_id=? AND relation='depends_on' AND target_id=?
                """,
                (membership_id, payment_id),
            ).fetchone()["count"]
            self.assertEqual(edge_count, 0)

        reviewed = list_review_candidates(
            self.database,
            project["id"],
            limit=200,
            include_reviewed=True,
        )
        rejected = next(item for item in reviewed if item["id"] == edge_candidate["id"])
        self.assertEqual(rejected["decision"], "rejected")
        decide_review_candidate(
            self.database,
            project_id=project["id"],
            candidate_id=rejected["id"],
            decision="accept",
        )
        scan_project(self.database, project, full=True)
        with self.database.connect() as connection:
            edge = connection.execute(
                """
                SELECT * FROM edges
                WHERE source_id=? AND relation='depends_on' AND target_id=?
                """,
                (membership_id, payment_id),
            ).fetchone()
            self.assertIsNotNone(edge)
            self.assertEqual(edge["source"], "human-overlay")

    def test_project_removal_does_not_immediately_reappear_from_overlay_log(self) -> None:
        project, _ = self._add_and_scan()
        add_correction(
            self.database,
            project_id=project["id"],
            action="rename_concept",
            subject_key="membership",
            value={"label": "会员升级体系"},
        )
        remove_project(self.database, project["id"])
        self.database = ensure_database(Settings.load(self.home))
        self.assertEqual(list_projects(self.database), [])


if __name__ == "__main__":
    unittest.main()
