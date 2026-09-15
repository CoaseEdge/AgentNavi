from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentnavi.config import Settings
from agentnavi.database import ensure_database
from agentnavi.mcp.errors import AgentNaviMCPError, to_public_error
from agentnavi.mcp.project_resolver import resolve_project
from agentnavi.utils import utc_now


class ProjectResolverTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.database = ensure_database(Settings.load(self.base / "agentnavi-home"))

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _add_project(self, project_id: str, name: str, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO projects(id, name, root, kind, created_at, updated_at)
                VALUES (?, ?, ?, 'software', ?, ?)
                """,
                (project_id, name, str(root.resolve()), now, now),
            )
            connection.commit()

    def test_explicit_project_id_has_priority_over_workspace(self) -> None:
        first_root = self.base / "first"
        second_root = self.base / "second"
        self._add_project("first-id", "first", first_root)
        self._add_project("second-id", "second", second_root)

        project = resolve_project(
            self.database,
            project_id="first-id",
            workspace=second_root,
        )

        self.assertEqual(project["id"], "first-id")

    def test_workspace_uses_deepest_containing_project(self) -> None:
        outer = self.base / "workspace"
        inner = outer / "packages" / "service"
        self._add_project("outer", "outer", outer)
        self._add_project("inner", "inner", inner)

        project = resolve_project(
            self.database,
            workspace=inner / "src" / "feature",
        )

        self.assertEqual(project["id"], "inner")

    def test_unique_registered_project_is_the_final_fallback(self) -> None:
        root = self.base / "only-project"
        self._add_project("only", "display-name", root)

        project = resolve_project(
            self.database,
            workspace=self.base / "outside",
        )

        self.assertEqual(project["id"], "only")

    def test_explicit_selector_is_strictly_id_only(self) -> None:
        root = self.base / "project"
        self._add_project("stable-id", "display-name", root)

        for selector in ("display-name", str(root.resolve())):
            with self.subTest(selector=selector):
                with self.assertRaises(AgentNaviMCPError) as captured:
                    resolve_project(self.database, project_id=selector, workspace=root)
                self.assertEqual(captured.exception.code, "PROJECT_NOT_FOUND")
                self.assertTrue(captured.exception.retryable)

    def test_zero_or_multiple_candidates_require_a_project(self) -> None:
        with self.assertRaises(AgentNaviMCPError) as zero:
            resolve_project(self.database, workspace=self.base / "outside")
        self.assertEqual(zero.exception.code, "PROJECT_REQUIRED")
        self.assertEqual(zero.exception.details, {"candidateCount": 0})

        self._add_project("one", "one", self.base / "one")
        self._add_project("two", "two", self.base / "two")
        with self.assertRaises(AgentNaviMCPError) as multiple:
            resolve_project(self.database, workspace=self.base / "outside")
        self.assertEqual(multiple.exception.code, "PROJECT_REQUIRED")
        self.assertEqual(multiple.exception.details, {"candidateCount": 2})

    def test_public_errors_never_expose_internal_paths_or_exception_details(self) -> None:
        private_path = str((self.base / "private" / "agentnavi.db").resolve())
        public = to_public_error(RuntimeError(f"database failed at {private_path}"))

        payload = public.to_dict()
        self.assertEqual(payload["code"], "INTERNAL_ERROR")
        self.assertNotIn(private_path, str(payload))
        self.assertNotIn("database failed", str(payload))

    def test_error_details_are_a_deep_snapshot_of_mutable_input(self) -> None:
        details = {
            "selection": {
                "candidates": ["registered-project"],
            }
        }
        error = AgentNaviMCPError("PROJECT_REQUIRED", details=details)
        original_payload = error.to_dto().to_dict()

        details["selection"]["candidates"][0] = str(
            (self.base / "private" / "agentnavi.db").resolve()
        )
        details["selection"]["candidates"].append("another-project")

        self.assertEqual(error.to_dto().to_dict(), original_payload)
        self.assertEqual(
            original_payload["details"],
            {"selection": {"candidates": ["registered-project"]}},
        )
        self.assertTrue(error.retryable)


if __name__ == "__main__":
    unittest.main()
