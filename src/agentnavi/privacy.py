"""Core 与 Presentation 共用的本地路径隐私检查。"""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit


_URI_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9+.-])([A-Z][A-Z0-9+.-]*:[^\s<>\"']*)"
)
_WINDOWS_ABSOLUTE_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\[^\\/]+[\\/])"
)
_UNC_FORWARD_RE = re.compile(r"(?<!:)//[A-Za-z0-9._~-]+(?:/|\b)")
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _contains_private_uri(value: str) -> bool:
    """识别本地文件 URI，同时明确放行普通 HTTP(S) URL。"""

    for match in _URI_TOKEN_RE.finditer(value):
        token = match.group(1).rstrip(".,;!?)]}，。；！？）】")
        parsed = urlsplit(token)
        scheme = parsed.scheme.lower()
        if scheme in {"http", "https"}:
            continue
        if parsed.netloc.lower() == "file":
            return True
        if scheme != "file":
            continue
        path = parsed.path
        if (
            path.startswith(("/", "\\", "~/"))
            or PureWindowsPath(path).is_absolute()
            or bool(PureWindowsPath(path).drive)
            or bool(parsed.netloc)
        ):
            return True
    return False


def contains_posix_absolute(value: str) -> bool:
    for index, character in enumerate(value):
        if character != "/" or index + 1 >= len(value):
            continue
        if index == 0:
            return True
        previous = value[index - 1]
        if previous == ":" and value[index + 1] == "/":
            continue
        if previous.isalnum() or previous in "._~-/":
            continue
        return True
    return False


def contains_private_path(value: str) -> bool:
    return bool(
        _contains_private_uri(value)
        or "~/" in value
        or _UNC_FORWARD_RE.search(value)
        or _WINDOWS_ABSOLUTE_RE.search(value)
        or contains_posix_absolute(value)
        or value == "/"
    )


def is_canonical_relative_path(value: str) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or "\\" in value
        or _URI_SCHEME_RE.match(value)
    ):
        return False
    posix_path = PurePosixPath(value)
    return not (
        posix_path.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or bool(PureWindowsPath(value).drive)
        or value.startswith("~")
        or ".." in posix_path.parts
        or str(posix_path) in {"", "."}
        or str(posix_path) != value
    )


__all__ = [
    "contains_posix_absolute",
    "contains_private_path",
    "is_canonical_relative_path",
]
