from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTEXT_FIRST_SKILL = """---
name: context-first
description: 在大型或陌生项目中工作时，先用 AgentNavi 按“任务→概念→文件”定位上下文，再读取和修改真实文件，减少无目的全仓库扫描。
---

# 上下文优先工作法

处理大型、陌生或多文件项目时，遵守以下顺序：

1. 在项目目录执行 `agentnavi context \"<当前用户任务的简短描述>\"`，用当前任务查找相关概念、文件和历史任务。
2. 优先读取 AgentNavi 返回的真实源文件；图谱只是导航，不是事实来源。
3. 需要评估修改范围时，执行 `agentnavi impact <相对文件路径>`。
4. 需要追溯过去为何这样设计时，执行 `agentnavi history \"关键词\"`。
5. 只有索引结果明显不足时，才扩大搜索范围；不要默认扫描整个仓库。
6. 对低置信度语义关系必须核对源文件、测试和 Git 差异。
7. 工作完成后正常结束任务；生命周期 Hook 会记录任务、增量更新图谱并沉淀历史。

始终沿以下路径下钻：

`当前任务 → 业务或内容概念 → 相关真实文件 → 必要的一跳依赖`

不要把 Obsidian 导出文件当作项目源文件，也不要向被索引项目写入 `.agentnavi`、`_graph` 或 `.obsidian`。
"""


def _handler(agent: str, *, timeout: int, status: str | None = None, context_limit: int | None = None) -> dict[str, Any]:
    handler: dict[str, Any] = {
        "type": "command",
        "command": f"agentnavi hook ingest --agent {agent}",
        "timeout": timeout,
    }
    if status:
        handler["statusMessage"] = status
    if context_limit is not None:
        handler["additionalContextLimit"] = context_limit
    return handler


CODEX_HOOKS: dict[str, Any] = {
    "description": "AgentNavi：外置三层项目上下文与任务记忆",
    "hooks": {
        "SessionStart": [
            {
                "matcher": "startup|resume|clear|compact",
                "hooks": [_handler("codex", timeout=120, status="加载 AgentNavi 项目上下文", context_limit=8000)],
            }
        ],
        "UserPromptSubmit": [
            {
                "hooks": [_handler("codex", timeout=120, status="定位任务相关概念与文件", context_limit=8000)]
            }
        ],
        "PostToolUse": [
            {
                "matcher": "*",
                "hooks": [_handler("codex", timeout=15)],
            }
        ],
        "Stop": [
            {
                "hooks": [_handler("codex", timeout=120, status="沉淀任务图并增量更新索引")]
            }
        ],
        "SessionEnd": [
            {
                "hooks": [_handler("codex", timeout=3)]
            }
        ],
    },
}

CLAUDE_HOOKS: dict[str, Any] = {
    "hooks": {
        "SessionStart": [
            {
                "matcher": "startup|resume|clear|compact|fork",
                "hooks": [{"type": "command", "command": "agentnavi hook ingest --agent claude", "timeout": 120}],
            }
        ],
        "UserPromptSubmit": [
            {
                "hooks": [{"type": "command", "command": "agentnavi hook ingest --agent claude", "timeout": 120}]
            }
        ],
        "PostToolUse": [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": "agentnavi hook ingest --agent claude", "timeout": 15}],
            }
        ],
        "PostToolUseFailure": [
            {
                "matcher": "*",
                "hooks": [{"type": "command", "command": "agentnavi hook ingest --agent claude", "timeout": 15}],
            }
        ],
        "Stop": [
            {
                "hooks": [{"type": "command", "command": "agentnavi hook ingest --agent claude", "timeout": 120}]
            }
        ],
        "SessionEnd": [
            {
                "hooks": [{"type": "command", "command": "agentnavi hook ingest --agent claude", "timeout": 5}]
            }
        ],
    }
}


@dataclass(frozen=True, slots=True)
class IntegrationInstallReport:
    agent: str
    hook_path: Path
    skill_path: Path | None
    backup_path: Path | None


def hook_template(agent: str) -> dict[str, Any]:
    if agent == "codex":
        return CODEX_HOOKS
    if agent == "claude":
        return CLAUDE_HOOKS
    raise ValueError(f"不支持的 Agent：{agent}")


def default_hook_path(agent: str) -> Path:
    if agent == "codex":
        return Path("~/.codex/hooks.json").expanduser()
    if agent == "claude":
        return Path("~/.claude/settings.json").expanduser()
    raise ValueError(f"不支持的 Agent：{agent}")


def default_skill_path(agent: str) -> Path:
    if agent == "codex":
        return Path("~/.agents/skills/context-first/SKILL.md").expanduser()
    if agent == "claude":
        return Path("~/.claude/skills/context-first/SKILL.md").expanduser()
    raise ValueError(f"不支持的 Agent：{agent}")


def _contains_agentnavi(group: Any, agent: str) -> bool:
    if not isinstance(group, dict):
        return False
    for handler in group.get("hooks", []):
        if not isinstance(handler, dict):
            continue
        command = str(handler.get("command", ""))
        if f"agentnavi hook ingest --agent {agent}" in command:
            return True
    return False


def merge_hook_configuration(existing: dict[str, Any], template: dict[str, Any], *, agent: str) -> dict[str, Any]:
    result = json.loads(json.dumps(existing))
    if agent == "codex" and template.get("description") and not result.get("description"):
        result["description"] = template["description"]
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("现有配置中的 hooks 必须是 JSON 对象")
    for event, groups in template["hooks"].items():
        current = hooks.setdefault(event, [])
        if not isinstance(current, list):
            raise ValueError(f"现有配置中的 hooks.{event} 必须是数组")
        current[:] = [group for group in current if not _contains_agentnavi(group, agent)]
        current.extend(json.loads(json.dumps(groups)))
    return result


def install_integration(
    agent: str,
    *,
    hook_path: str | Path | None = None,
    install_skill: bool = True,
) -> IntegrationInstallReport:
    destination = Path(hook_path).expanduser() if hook_path else default_hook_path(agent)
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    backup: Path | None = None
    if destination.exists():
        try:
            loaded = json.loads(destination.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"现有配置不是合法 JSON：{destination}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"现有配置必须是 JSON 对象：{destination}")
        existing = loaded
        backup = destination.with_suffix(destination.suffix + ".agentnavi.bak")
        shutil.copy2(destination, backup)

    merged = merge_hook_configuration(existing, hook_template(agent), agent=agent)
    destination.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    skill_path: Path | None = None
    if install_skill:
        skill_path = default_skill_path(agent)
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(CONTEXT_FIRST_SKILL, encoding="utf-8")
    return IntegrationInstallReport(agent, destination, skill_path, backup)
