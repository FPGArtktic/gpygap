# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Removal of mirror files that no recent run requires."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from gpygap.util import remove_empty_dirs

log = logging.getLogger(__name__)

_SIDECAR_SUFFIX = ".metadata"
_TEMPORARY_SUFFIXES = (".part", ".tmp")


@dataclass(frozen=True, slots=True)
class PruneResult:
    files: int = 0
    bytes: int = 0
    blocked: bool = False


def prune_tree(
    root: Path, keep: set[str], subdirs: Sequence[str], *, max_ratio: float, force: bool
) -> PruneResult:
    """Delete files under ``root/<subdirs>`` whose path is not in ``keep``.

    A wheel's ``.metadata`` sidecar lives and dies with the wheel. As a safety
    net against a broken run wiping the mirror, nothing is deleted when more
    than ``max_ratio`` of the files would go, unless ``force`` is set.
    """
    victims, total = _unreferenced_files(root, keep, subdirs)
    if not victims:
        return PruneResult()
    ratio = len(victims) / total
    if ratio > max_ratio and not force:
        log.error(
            "prune blocked: %d of %d files (%.0f%%) would be deleted, above sync.max_prune_ratio=%.0f%%; "
            "check the logs, then rerun with --force-prune if this is intended",
            len(victims),
            total,
            ratio * 100,
            max_ratio * 100,
        )
        return PruneResult(blocked=True)
    freed = 0
    for path in victims:
        freed += path.stat().st_size
        path.unlink()
        log.debug("pruned %s", path.relative_to(root).as_posix())
    for subdir in subdirs:
        remove_empty_dirs(root / subdir)
    return PruneResult(files=len(victims), bytes=freed)


def _unreferenced_files(root: Path, keep: set[str], subdirs: Sequence[str]) -> tuple[list[Path], int]:
    victims: list[Path] = []
    total = 0
    for subdir in subdirs:
        for path in sorted((root / subdir).rglob("*")):
            if not path.is_file():
                continue
            total += 1
            relative = path.relative_to(root).as_posix()
            if relative.endswith(_TEMPORARY_SUFFIXES) or relative.removesuffix(_SIDECAR_SUFFIX) not in keep:
                victims.append(path)
    return victims, total
