"""MCP 请求的确定性项目解析器。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..database import Database
from .errors import AgentNaviMCPError


def _workspace_match(
    projects: list[sqlite3.Row],
    workspace: str | Path,
) -> sqlite3.Row | None:
    current = Path(workspace).expanduser().resolve()
    matches: list[sqlite3.Row] = []
    for project in projects:
        root = Path(project["root"]).expanduser().resolve()
        try:
            current.relative_to(root)
        except ValueError:
            continue
        matches.append(project)
    if not matches:
        return None
    return max(
        matches,
        key=lambda project: (len(Path(project["root"]).parts), project["id"]),
    )


def resolve_project(
    database: Database,
    *,
    project_id: str | None = None,
    workspace: str | Path | None = None,
) -> sqlite3.Row:
    """按 ID、workspace、唯一项目的固定顺序解析，不自动注册项目。"""

    with database.connect() as connection:
        if project_id is not None:
            project = Database.fetch_project(connection, project_id)
            if project is None:
                raise AgentNaviMCPError("PROJECT_NOT_FOUND")
            return project

        projects = list(connection.execute("SELECT * FROM projects ORDER BY id"))
        if workspace is not None:
            project = _workspace_match(projects, workspace)
            if project is not None:
                return project

        if len(projects) == 1:
            return projects[0]

        raise AgentNaviMCPError(
            "PROJECT_REQUIRED",
            details={"candidateCount": len(projects)},
        )


__all__ = ["resolve_project"]
