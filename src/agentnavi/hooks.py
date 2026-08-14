from __future__ import annotations

import json
import re
import shlex
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .database import Database
from .engine import scan_project
from .query import build_context
from .registry import ensure_project_for_cwd, resolve_project
from .scanning import parse_patch_paths
from .tasks import (
    active_task,
    close_task,
    create_task,
    end_session,
    record_event,
    upsert_session,
)

_PATH_KEYS = {
    "path",
    "file_path",
    "filepath",
    "notebook_path",
    "target_path",
    "source_path",
    "destination_path",
}
_PATH_LIST_KEYS = {"paths", "files", "file_paths"}


@dataclass(slots=True)
class HookResult:
    """Hook 适配器的输出。

    ``stdout`` 会直接返回给 Agent。对于 SessionStart 和 UserPromptSubmit，
    这是可注入的项目上下文；对于 Stop 等事件，默认为空 JSON，避免影响 Agent。
    """

    stdout: str = ""
    event: str = ""
    project_id: str | None = None
    task_id: str | None = None


def _event_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("hook_event_name")
        or payload.get("event_name")
        or payload.get("event")
        or ""
    ).strip()


def _session_id(payload: dict[str, Any]) -> str:
    value = payload.get("session_id") or payload.get("conversation_id") or payload.get("thread_id")
    return str(value or "unknown-session")


def _cwd(payload: dict[str, Any]) -> Path:
    value = payload.get("cwd") or payload.get("working_directory") or Path.cwd()
    return Path(str(value)).expanduser().resolve()


def _prompt(payload: dict[str, Any]) -> str:
    value = payload.get("prompt") or payload.get("user_prompt") or payload.get("message") or ""
    if isinstance(value, dict):
        value = value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _summary(payload: dict[str, Any]) -> str:
    value = (
        payload.get("last_assistant_message")
        or payload.get("assistant_message")
        or payload.get("result")
        or payload.get("response")
        or ""
    )
    if isinstance(value, dict):
        value = value.get("text") or value.get("content") or json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    return text[:8000]


def _tool_name(payload: dict[str, Any]) -> str:
    return str(payload.get("tool_name") or payload.get("tool") or "").strip()


def _tool_input(payload: dict[str, Any]) -> Any:
    return payload.get("tool_input") or payload.get("input") or {}


def _collect_path_candidates(value: Any, *, key: str | None = None) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            lowered = str(child_key).lower()
            if lowered in _PATH_KEYS and isinstance(child_value, str):
                candidates.append(child_value)
                continue
            if lowered in _PATH_LIST_KEYS and isinstance(child_value, list):
                candidates.extend(str(item) for item in child_value if isinstance(item, (str, Path)))
                continue
            candidates.extend(_collect_path_candidates(child_value, key=lowered))
    elif isinstance(value, list):
        for item in value:
            candidates.extend(_collect_path_candidates(item, key=key))
    elif isinstance(value, str):
        if key in _PATH_KEYS:
            candidates.append(value)
        if "*** Begin Patch" in value or "*** Update File:" in value or "*** Add File:" in value:
            candidates.extend(parse_patch_paths(value))
    return list(dict.fromkeys(path for path in candidates if path.strip()))


def _extract_paths(payload: dict[str, Any]) -> list[str]:
    candidates = _collect_path_candidates(_tool_input(payload))
    candidates.extend(_collect_path_candidates(payload.get("tool_response", {})))
    return list(dict.fromkeys(candidates))


def _extract_command(payload: dict[str, Any]) -> str:
    tool_input = _tool_input(payload)
    if isinstance(tool_input, dict):
        for key in ("command", "cmd", "script"):
            value = tool_input.get(key)
            if isinstance(value, str):
                return value
        commands = tool_input.get("commands")
        if isinstance(commands, list):
            return "\n".join(str(item) for item in commands)
    if isinstance(tool_input, str):
        return tool_input
    return ""


def _paths_from_command(command: str, cwd: Path) -> list[str]:
    """从常见 shell 命令中保守提取显式文件参数。

    这里只记录明确存在于项目内的路径；不尝试完整解析 shell 语法，也不把普通单词误判为路径。
    """

    if not command:
        return []
    candidates: list[str] = []
    candidates.extend(parse_patch_paths(command))
    try:
        tokens = shlex.split(command, comments=False, posix=True)
    except ValueError:
        tokens = re.findall(r"(?:[^\s'\"]|\"[^\"]*\"|'[^']*')+", command)
    for token in tokens:
        token = token.strip("'\"")
        if not token or token.startswith("-") or token in {".", "..", "|", "&&", ";"}:
            continue
        if any(symbol in token for symbol in ("$", "*", "?", "{", "}", "<", ">")):
            continue
        candidate = Path(token).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            if candidate.exists() and candidate.is_file():
                candidates.append(str(candidate.resolve()))
        except OSError:
            continue
    return list(dict.fromkeys(candidates))


def _classify_tool_event(tool_name: str, command: str) -> str:
    name = tool_name.lower()
    if any(part in name for part in ("write", "edit", "patch", "notebookedit", "delete")):
        return "modify"
    if any(part in name for part in ("read", "fetch_file", "open_file")):
        return "read"
    if any(part in name for part in ("grep", "glob", "search", "find")):
        return "search"
    if any(part in name for part in ("bash", "shell", "terminal", "exec", "command")):
        lowered = command.lower()
        if re.search(r"(?:^|[;&|\s])(?:pytest|unittest|npm\s+test|pnpm\s+test|yarn\s+test|go\s+test|cargo\s+test|mvn\s+test|gradle\s+test)(?:\s|$)", lowered):
            return "test"
        if re.search(r"(?:^|[;&|\s])(?:rg|grep|find|fd|ag)(?:\s|$)", lowered):
            return "search"
        return "command"
    return "tool"


def _compact_event_data(payload: dict[str, Any], *, command: str = "") -> dict[str, Any]:
    data: dict[str, Any] = {}
    if command:
        data["command"] = command[:4000]
    for key in ("tool_use_id", "permission_mode", "source", "reason"):
        if key in payload:
            data[key] = payload[key]
    response = payload.get("tool_response")
    if isinstance(response, dict):
        compact: dict[str, Any] = {}
        for key in ("success", "exit_code", "status", "error"):
            if key in response:
                compact[key] = response[key]
        if compact:
            data["response"] = compact
    return data


def _ensure_index(database: Database, project: sqlite3.Row) -> None:
    try:
        scan_project(database, project, full=project["last_scan_at"] is None)
    except (OSError, sqlite3.Error, RuntimeError):
        # Hook 必须 fail-open。项目索引失败不应阻止主 Agent 工作。
        return


def _current_project(database: Database, cwd: Path) -> sqlite3.Row:
    project = ensure_project_for_cwd(database, cwd)
    # ensure_project_for_cwd 返回的行可能早于本轮扫描；调用方如需新时间戳再重新读取。
    return project


def ingest_hook(database: Database, *, agent: str, payload: dict[str, Any]) -> HookResult:
    """接收 Codex / Claude Code 等 Agent 的 Hook JSON 并写入三层图谱。"""

    event = _event_name(payload)
    cwd = _cwd(payload)
    external_session_id = _session_id(payload)
    project = _current_project(database, cwd)
    project_id = project["id"]

    if event == "SessionStart":
        upsert_session(
            database,
            project_id=project_id,
            agent=agent,
            external_session_id=external_session_id,
        )
        _ensure_index(database, project)
        project = resolve_project(database, project_id)
        context = build_context(database, project, "")
        return HookResult(stdout=context, event=event, project_id=project_id)

    if event == "UserPromptSubmit":
        upsert_session(
            database,
            project_id=project_id,
            agent=agent,
            external_session_id=external_session_id,
        )
        previous = active_task(database, agent=agent, external_session_id=external_session_id)
        if previous and previous["status"] == "running":
            close_task(database, previous["id"], status="interrupted", summary="收到新的用户任务，上一任务未正常结束。")
        prompt = _prompt(payload)
        title = prompt.splitlines()[0][:160] if prompt else "Agent 任务"
        task = create_task(
            database,
            project_id=project_id,
            title=title,
            prompt=prompt,
            agent=agent,
            external_session_id=external_session_id,
        )
        record_event(
            database,
            project=project,
            agent=agent,
            external_session_id=external_session_id,
            task_id=task["id"],
            event_type="prompt",
            data={"prompt": prompt[:8000]},
        )
        _ensure_index(database, project)
        project = resolve_project(database, project_id)
        context = build_context(database, project, prompt)
        return HookResult(stdout=context, event=event, project_id=project_id, task_id=task["id"])

    if event in {"PostToolUse", "PostToolUseFailure"}:
        tool_name = _tool_name(payload)
        command = _extract_command(payload)
        paths = _extract_paths(payload)
        paths.extend(_paths_from_command(command, cwd))
        event_type = _classify_tool_event(tool_name, command)
        if event == "PostToolUseFailure":
            event_type = f"{event_type}_failed"
        record_event(
            database,
            project=project,
            agent=agent,
            external_session_id=external_session_id,
            event_type=event_type,
            tool_name=tool_name,
            paths=list(dict.fromkeys(paths)),
            data=_compact_event_data(payload, command=command),
        )
        return HookResult(event=event, project_id=project_id)

    if event in {"Stop", "SubagentStop", "TaskCompleted"}:
        task = active_task(database, agent=agent, external_session_id=external_session_id)
        if task:
            summary = _summary(payload)
            record_event(
                database,
                project=project,
                agent=agent,
                external_session_id=external_session_id,
                task_id=task["id"],
                event_type="result",
                data={"summary": summary[:8000]},
            )
            _ensure_index(database, project)
            close_task(database, task["id"], status="completed", summary=summary)
            return HookResult(stdout="{}", event=event, project_id=project_id, task_id=task["id"])
        return HookResult(stdout="{}", event=event, project_id=project_id)

    if event == "SessionEnd":
        # Codex 对 SessionEnd 命令的超时上限很短；重建已在 Stop 完成，这里只收尾会话。
        end_session(database, agent=agent, external_session_id=external_session_id)
        return HookResult(event=event, project_id=project_id)

    # 未识别事件仍留原始审计事件，但不干扰 Agent。
    record_event(
        database,
        project=project,
        agent=agent,
        external_session_id=external_session_id,
        event_type=event or "unknown",
        data={"payload_keys": sorted(payload.keys())},
    )
    return HookResult(event=event, project_id=project_id)


def ingest_stdin(database: Database, *, agent: str) -> HookResult:
    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    if not isinstance(payload, dict):
        raise ValueError("Hook 输入必须是 JSON 对象")
    return ingest_hook(database, agent=agent, payload=payload)
