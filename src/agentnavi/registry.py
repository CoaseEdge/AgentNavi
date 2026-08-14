from __future__ import annotations

import sqlite3
from pathlib import Path

from .database import Database
from .utils import detect_git_root, slugify, stable_id, utc_now

SOFTWARE_MARKERS = (
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "composer.json",
    "Gemfile",
)

VIDEO_EXTENSIONS = {".prproj", ".drp", ".fcpxml", ".aep", ".veg"}


class ProjectNotFoundError(LookupError):
    pass


def detect_project_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    if not candidate.exists():
        raise FileNotFoundError(f"项目路径不存在：{candidate}")
    return detect_git_root(candidate) or candidate


def detect_project_kind(root: Path) -> str:
    if any((root / marker).exists() for marker in SOFTWARE_MARKERS):
        return "software"

    markdown_count = 0
    video_count = 0
    inspected = 0
    for child in root.rglob("*"):
        if inspected >= 500:
            break
        if not child.is_file():
            continue
        inspected += 1
        suffix = child.suffix.lower()
        if suffix in {".md", ".mdx", ".rst", ".txt"}:
            markdown_count += 1
        if suffix in VIDEO_EXTENSIONS or suffix in {".mp4", ".mov", ".mkv", ".wav", ".mp3"}:
            video_count += 1
    if video_count >= 3:
        return "video"
    if markdown_count >= 5:
        return "writing"
    return "generic"


def _unique_project_id(connection: sqlite3.Connection, preferred: str, root: Path) -> str:
    base = slugify(preferred, fallback="project")
    row = connection.execute("SELECT root FROM projects WHERE id=?", (base,)).fetchone()
    if row is None or Path(row["root"]).resolve() == root:
        return base
    return f"{base}-{stable_id(str(root))[:8]}"


def add_project(
    database: Database,
    path: str | Path,
    *,
    project_id: str | None = None,
    name: str | None = None,
    kind: str | None = None,
) -> sqlite3.Row:
    root = detect_project_root(path)
    project_name = name or root.name
    with database.connect() as connection:
        existing = connection.execute("SELECT * FROM projects WHERE root=?", (str(root),)).fetchone()
        if existing is not None:
            return existing
        resolved_id = _unique_project_id(connection, project_id or project_name, root)
        resolved_kind = kind or detect_project_kind(root)
        now = utc_now()
        connection.execute(
            """
            INSERT INTO projects(id, name, root, kind, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (resolved_id, project_name, str(root), resolved_kind, now, now),
        )
        connection.commit()
        row = connection.execute("SELECT * FROM projects WHERE id=?", (resolved_id,)).fetchone()
        assert row is not None
        return row


def list_projects(database: Database) -> list[sqlite3.Row]:
    with database.connect() as connection:
        return list(connection.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE, id"))


def remove_project(database: Database, selector: str) -> sqlite3.Row:
    with database.connect() as connection:
        project = resolve_project_in_connection(connection, selector=selector)
        connection.execute("DELETE FROM projects WHERE id=?", (project["id"],))
        connection.commit()
        return project


def resolve_project_in_connection(
    connection: sqlite3.Connection,
    *,
    selector: str | None = None,
    cwd: str | Path | None = None,
) -> sqlite3.Row:
    if selector:
        row = connection.execute(
            "SELECT * FROM projects WHERE id=? OR name=? OR root=?",
            (selector, selector, str(Path(selector).expanduser().resolve()) if Path(selector).expanduser().exists() else selector),
        ).fetchone()
        if row is not None:
            return row

        # 允许用项目路径内的任意子路径定位。
        candidate = Path(selector).expanduser()
        if candidate.exists():
            candidate = candidate.resolve()
            matches: list[sqlite3.Row] = []
            for project in connection.execute("SELECT * FROM projects"):
                root = Path(project["root"]).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    continue
                matches.append(project)
            if matches:
                return max(matches, key=lambda item: len(Path(item["root"]).parts))
        raise ProjectNotFoundError(f"找不到项目：{selector}")

    current = Path(cwd or Path.cwd()).expanduser().resolve()
    matches = []
    for project in connection.execute("SELECT * FROM projects"):
        root = Path(project["root"]).resolve()
        try:
            current.relative_to(root)
        except ValueError:
            continue
        matches.append(project)
    if not matches:
        raise ProjectNotFoundError(f"当前目录未关联任何项目：{current}")
    return max(matches, key=lambda item: len(Path(item["root"]).parts))


def resolve_project(
    database: Database,
    selector: str | None = None,
    *,
    cwd: str | Path | None = None,
) -> sqlite3.Row:
    with database.connect() as connection:
        return resolve_project_in_connection(connection, selector=selector, cwd=cwd)


def ensure_project_for_cwd(database: Database, cwd: str | Path) -> sqlite3.Row:
    try:
        return resolve_project(database, cwd=cwd)
    except ProjectNotFoundError:
        return add_project(database, cwd)
