"""Core 与 Presentation 共用的本地路径隐私检查。"""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath


_LOCAL_FILE_URI_RE = re.compile(
    r"(?i)(?:file:(?=/{1,3}|\\|~|[A-Z]:[\\/])|vscode(?:-insiders)?://file/)"
)
_WINDOWS_ABSOLUTE_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\[^\\/]+[\\/])"
)
_UNC_FORWARD_RE = re.compile(r"(?<!:)//[A-Za-z0-9._~-]+(?:/|\b)")
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


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
        _LOCAL_FILE_URI_RE.search(value)
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
