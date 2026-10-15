# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parallel, verified downloads into the mirror.

A file only appears under its final name after its size and SHA-256 match the
values from the lock file, so a file that exists is always complete.
"""

from __future__ import annotations

import functools
import hashlib
import http.client
import logging
import threading
import time
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tqdm import tqdm

from gpygap.console import progress
from gpygap.models import MirrorFile
from gpygap.util import USER_AGENT, human_size, parallel_map

log = logging.getLogger(__name__)

_CHUNK = 1 << 20
_TIMEOUT = 120


@dataclass(slots=True)
class DownloadResult:
    files: int = 0
    bytes: int = 0
    failed: list[str] = field(default_factory=list)

    def merge(self, other: DownloadResult) -> None:
        self.files += other.files
        self.bytes += other.bytes
        self.failed += other.failed


class _ByteCounter:
    """Thread-safe adapter feeding byte counts into a progress bar."""

    def __init__(self, bar: tqdm[Any]) -> None:
        self._bar = bar
        self._lock = threading.Lock()

    def add(self, count: int) -> None:
        with self._lock:
            self._bar.update(count)


def is_present(root: Path, file: MirrorFile) -> bool:
    """Return whether ``file`` exists in the mirror with the expected size."""
    try:
        size = (root / file.path).stat().st_size
    except FileNotFoundError:
        return False
    return file.size is None or size == file.size


def download_missing(
    root: Path, files: Iterable[MirrorFile], *, workers: int, attempts: int, label: str
) -> DownloadResult:
    """Download every file not yet present under ``root``; failures are logged and returned."""
    missing = [f for f in files if not is_present(root, f)]
    result = DownloadResult()
    if not missing:
        return result
    sizes = [f.size for f in missing]
    total = None if None in sizes else sum(s or 0 for s in sizes)
    log.info("%s: %d files, %s", label, len(missing), "size unknown" if total is None else human_size(total))
    with progress(total, label, as_bytes=True) as bar:
        fetch = functools.partial(
            _fetch_with_retries, root=root, counter=_ByteCounter(bar), attempts=attempts
        )
        for file, error in parallel_map(fetch, missing, workers):
            if error is None:
                result.files += 1
                result.bytes += (root / file.path).stat().st_size
            else:
                result.failed.append(file.path)
                log.error("download %s: %s", file.path, error)
    return result


def _fetch_with_retries(file: MirrorFile, *, root: Path, counter: _ByteCounter, attempts: int) -> str | None:
    """Download ``file``; return ``None`` on success or the last error message."""
    error = ""
    for attempt in range(1, attempts + 1):
        try:
            _fetch(file, root / file.path, counter)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            error = str(exc) or type(exc).__name__
            if attempt < attempts:
                time.sleep(2**attempt)
        else:
            return None
    return error


def _fetch(file: MirrorFile, dest: Path, counter: _ByteCounter) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    received = 0
    request = urllib.request.Request(file.url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response, partial.open("wb") as out:
            while chunk := response.read(_CHUNK):
                out.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                counter.add(len(chunk))
        _check(file, received, digest.hexdigest())
    except BaseException:
        counter.add(-received)
        partial.unlink(missing_ok=True)
        raise
    partial.replace(dest)


def _check(file: MirrorFile, size: int, sha256: str) -> None:
    if file.size is not None and size != file.size:
        raise ValueError(f"size {size} != expected {file.size}")
    if file.sha256 is not None and sha256 != file.sha256:
        raise ValueError("SHA-256 mismatch")
