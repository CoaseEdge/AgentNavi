"""Generate MCP host configuration without modifying third-party files."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


TARGETS = {"claude", "vscode", "codex", "generic"}


def _command(home: str | Path | None) -> list[str]:
    command = [sys.executable, "-m", "agentnavi", "mcp", "--transport", "stdio"]
    if home is not None:
        command[3:3] = ["--home", str(home)]
    return command


def build_setup_config(*, target: str, home: str | Path | None = None) -> dict[str, Any]:
    """Return a host-specific example; callers must print it, never write it."""

    if target not in TARGETS:
        raise ValueError(f"unsupported MCP setup target: {target}")
    command = _command(home)
    server = {"command": command[0], "args": command[1:]}
    if target == "generic":
        return {"mcpServers": {"agentnavi": server}}
    if target == "claude":
        return {"mcpServers": {"agentnavi": server}}
    if target == "vscode":
        return {"servers": {"agentnavi": server}}
    return {"servers": {"agentnavi": server}}


def render_setup_config(*, target: str, home: str | Path | None = None) -> str:
    return json.dumps(build_setup_config(target=target, home=home), ensure_ascii=False, indent=2)


__all__ = ["TARGETS", "build_setup_config", "render_setup_config"]
