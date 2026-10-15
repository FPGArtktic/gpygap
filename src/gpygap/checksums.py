# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""``MIRROR-SHA256SUMS``: a checksum for every file of the published mirror.

The offline side checks the mirror against it after each delta (and on demand
with ``gpygap_offline.py verify``, or ``sha256sum -c`` on Linux), so a file
damaged in transfer is found by the operator, not by a developer's install.

Package files reuse the SHA-256 from the lock file, already verified when they
were downloaded; other files are hashed once and cached by size and mtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from gpygap.models import MirrorFile
from gpygap.offline import SUMS_FILE, sha256_file
from gpygap.util import read_json, write_if_changed, write_json

_SKIPPED_SUFFIXES = (".part", ".tmp")


def write_checksums(root: Path, manifest: Mapping[str, MirrorFile], cache_file: Path) -> int:
    """Write ``MIRROR-SHA256SUMS`` for every file under ``root``; return the file count."""
    cache: dict[str, list[object]] = read_json(cache_file) or {}
    fresh_cache: dict[str, list[object]] = {}
    lines = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or relative == SUMS_FILE or relative.endswith(_SKIPPED_SUFFIXES):
            continue
        known = manifest.get(relative)
        if known is not None and known.sha256:
            digest = known.sha256
        else:
            digest = _cached_digest(path, cache.get(relative))
            stat = path.stat()
            fresh_cache[relative] = [stat.st_size, stat.st_mtime_ns, digest]
        lines.append(f"{digest}  {relative}")
    write_if_changed(root / SUMS_FILE, "\n".join(lines) + "\n")
    write_json(cache_file, fresh_cache)
    return len(lines)


def _cached_digest(path: Path, entry: list[object] | None) -> str:
    stat = path.stat()
    if entry is not None and entry[:2] == [stat.st_size, stat.st_mtime_ns]:
        return str(entry[2])
    return sha256_file(path)
