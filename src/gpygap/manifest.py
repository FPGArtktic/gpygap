# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""What the mirror contains, and what recent runs required.

The manifest maps every published file to its upstream origin. The run history
keeps, per run, the list of files that run required; files required by none
of the last ``keep_runs`` runs are pruned. With ``keep_runs = 1`` the mirror
behaves like ``rsync --delete`` against the current top list.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from gpygap.models import MirrorFile
from gpygap.util import read_json, write_json

Manifest = dict[str, MirrorFile]

# Run files kept for diagnostics, beyond what retention needs.
_MIN_HISTORY = 5


def load_manifest(path: Path) -> Manifest:
    data = read_json(path) or {}
    return {p: MirrorFile.from_json(entry) for p, entry in data.items()}


def save_manifest(path: Path, manifest: Mapping[str, MirrorFile]) -> None:
    write_json(path, {p: f.to_json() for p, f in sorted(manifest.items())})


class RunHistory:
    """Per-run lists of required mirror paths, stored as ``<run_id>.json``."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def record(self, run_id: str, paths: Iterable[str]) -> None:
        write_json(self._directory / f"{run_id}.json", sorted(paths))

    def retained(self, keep_runs: int) -> set[str]:
        """Paths required by any of the last ``keep_runs`` runs."""
        keep: set[str] = set()
        for run_file in self._run_files()[-keep_runs:]:
            keep.update(read_json(run_file) or [])
        return keep

    def trim(self, keep_runs: int) -> None:
        """Delete run files that retention no longer needs."""
        for run_file in self._run_files()[: -max(keep_runs, _MIN_HISTORY)]:
            run_file.unlink()

    def _run_files(self) -> list[Path]:
        # Run ids are timestamps, so name order is chronological order.
        return sorted(self._directory.glob("*.json"))
