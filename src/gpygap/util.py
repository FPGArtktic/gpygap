# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small filesystem, network and concurrency helpers."""

from __future__ import annotations

import json
import re
import time
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, TypeVar

USER_AGENT = "gpygap/1.0 (+https://github.com/FPGArtktic/gpygap)"

T = TypeVar("T")
R = TypeVar("R")


def normalize_name(name: str) -> str:
    """Normalize a project name as required by PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def read_json(path: Path) -> Any | None:
    """Return the decoded JSON document at ``path``, or ``None`` if missing or invalid."""
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: Any) -> None:
    """Atomically write ``data`` as JSON."""
    _atomic_write(path, json.dumps(data, indent=1, ensure_ascii=False).encode())


def write_if_changed(path: Path, data: str | bytes) -> bool:
    """Write ``data`` only if it differs from the current content.

    Keeping mtimes stable for unchanged files keeps transfer deltas small.
    Returns ``True`` if the file was written.
    """
    raw = data.encode() if isinstance(data, str) else data
    try:
        if path.read_bytes() == raw:
            return False
    except OSError:
        pass
    _atomic_write(path, raw)
    return True


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)


def remove_empty_dirs(root: Path) -> None:
    """Remove empty directories below ``root`` (deepest first), keeping ``root`` itself."""
    if not root.is_dir():
        return
    dirs = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts))
    for directory in reversed(dirs):
        if not any(directory.iterdir()):
            directory.rmdir()


def human_size(size: float) -> str:
    """Format a byte count for humans, e.g. ``1.5 GB``."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def http_get(url: str, *, timeout: float = 60, attempts: int = 3) -> bytes:
    """Fetch ``url`` into memory, retrying with exponential backoff."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body: bytes = response.read()
                return body
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def parallel_map(func: Callable[[T], R], items: Iterable[T], workers: int) -> Iterator[tuple[T, R]]:
    """Run ``func`` over ``items`` in a thread pool, yielding ``(item, result)`` as tasks finish.

    Tasks that have not started yet are cancelled when the consumer stops early,
    for example on Ctrl+C, so an interrupted run exits promptly.
    """
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(func, item): item for item in items}
        for future in as_completed(futures):
            yield futures[future], future.result()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
