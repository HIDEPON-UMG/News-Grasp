"""Benchmark artifact valuesを安全な単一パス要素へ制限する。"""
from __future__ import annotations

import re
from typing import Any


_SAFE_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,127}")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_path_component(value: Any, *, field: str) -> str:
    raw = str(value)
    if any(char in raw for char in ("/", "\\", ":")) or raw in {".", ".."}:
        raise ValueError(f"unsafe path component for {field}: {raw!r}")
    if raw != raw.strip() or raw.endswith((".", " ")) or not _SAFE_COMPONENT_RE.fullmatch(raw):
        raise ValueError(f"unsafe path component for {field}: {raw!r}")
    if raw.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"unsafe path component for {field}: {raw!r}")
    return raw
