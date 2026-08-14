from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def stable_id(*parts: object, prefix: str = "") -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8", errors="surrogatepass")
    digest = hashlib.sha1(payload).hexdigest()
    return f"{prefix}{digest}" if prefix else digest


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def slugify(value: str, *, fallback: str = "item") -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    normalized = re.sub(r"\s+", "-", normalized)
    normalized = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", normalized, flags=re.UNICODE)
    normalized = re.sub(r"-+", "-", normalized).strip("-_")
    return normalized or fallback


def humanize_identifier(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = value.replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", value) or "未命名"


def run_git(root: Path, *args: str, timeout: float = 5.0) -> subprocess.CompletedProcess[str] | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed


def detect_git_root(path: Path) -> Path | None:
    completed = run_git(path, "rev-parse", "--show-toplevel")
    if completed and completed.returncode == 0:
        candidate = completed.stdout.strip()
        if candidate:
            return Path(candidate).expanduser().resolve()
    return None


def path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def unique_preserving_order(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))
