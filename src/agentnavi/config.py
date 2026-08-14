from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_IGNORED_DIRECTORIES = (
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "coverage",
    ".next",
    ".nuxt",
    "target",
)

DEFAULT_IGNORED_FILES = (
    ".DS_Store",
    "Thumbs.db",
)


@dataclass(frozen=True, slots=True)
class Settings:
    """运行配置。

    AgentNavi 的所有持久化状态默认位于 ``~/.agentnavi``，不会向被索引项目写入文件。
    """

    home: Path
    database_path: Path
    config_path: Path
    obsidian_vault: Path
    max_file_bytes: int = 4 * 1024 * 1024
    ignored_directories: tuple[str, ...] = field(default_factory=lambda: DEFAULT_IGNORED_DIRECTORIES)
    ignored_files: tuple[str, ...] = field(default_factory=lambda: DEFAULT_IGNORED_FILES)
    semantic_command: str | None = None

    @classmethod
    def load(cls, home: str | Path | None = None) -> "Settings":
        raw_home = home or os.environ.get("AGENTNAVI_HOME") or "~/.agentnavi"
        home_path = Path(raw_home).expanduser().resolve()
        config_path = home_path / "config.json"
        data: dict[str, Any] = {}
        if config_path.exists():
            try:
                loaded = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"无法读取 AgentNavi 配置：{config_path}: {exc}") from exc
            if not isinstance(loaded, dict):
                raise RuntimeError(f"AgentNavi 配置必须是 JSON 对象：{config_path}")
            data = loaded

        obsidian_raw = data.get("obsidian_vault", str(home_path / "obsidian-vault"))
        obsidian_path = Path(str(obsidian_raw)).expanduser()
        if not obsidian_path.is_absolute():
            obsidian_path = home_path / obsidian_path

        semantic_command = os.environ.get("AGENTNAVI_SEMANTIC_COMMAND")
        if semantic_command is None:
            value = data.get("semantic_command")
            semantic_command = str(value) if value else None

        ignored_dirs = tuple(
            dict.fromkeys((*DEFAULT_IGNORED_DIRECTORIES, *map(str, data.get("ignored_directories", []))))
        )
        ignored_files = tuple(
            dict.fromkeys((*DEFAULT_IGNORED_FILES, *map(str, data.get("ignored_files", []))))
        )

        return cls(
            home=home_path,
            database_path=home_path / "agentnavi.db",
            config_path=config_path,
            obsidian_vault=obsidian_path.resolve(),
            max_file_bytes=int(data.get("max_file_bytes", 4 * 1024 * 1024)),
            ignored_directories=ignored_dirs,
            ignored_files=ignored_files,
            semantic_command=semantic_command,
        )

    def ensure_layout(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.obsidian_vault.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(
                json.dumps(
                    {
                        "max_file_bytes": self.max_file_bytes,
                        "obsidian_vault": str(self.obsidian_vault),
                        "semantic_command": None,
                        "ignored_directories": [],
                        "ignored_files": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
