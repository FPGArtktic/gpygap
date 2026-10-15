# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from pathlib import Path

from gpygap.manifest import RunHistory
from gpygap.prune import prune_tree


def _touch(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def test_prune_removes_unreferenced_files_with_their_sidecars(tmp_path: Path) -> None:
    for relative in (
        "packages/a/a-1.whl",
        "packages/a/a-1.whl.metadata",
        "packages/b/b-1.whl",
        "packages/b/b-1.whl.metadata",
        "packages/c/c-1.tar.gz",
    ):
        _touch(tmp_path, relative)
    keep = {"packages/a/a-1.whl", "packages/c/c-1.tar.gz"}
    result = prune_tree(tmp_path, keep, ["packages"], max_ratio=1.0, force=False)
    assert result.files == 2
    assert not (tmp_path / "packages/b").exists()
    assert (tmp_path / "packages/a/a-1.whl.metadata").exists()


def test_prune_is_blocked_above_the_safety_ratio(tmp_path: Path) -> None:
    for name in ("a", "b", "c", "d"):
        _touch(tmp_path, f"packages/{name}/{name}-1.whl")
    result = prune_tree(tmp_path, {"packages/a/a-1.whl"}, ["packages"], max_ratio=0.5, force=False)
    assert result.blocked
    assert len(list((tmp_path / "packages").rglob("*.whl"))) == 4


def test_run_history_retains_files_of_the_last_runs(tmp_path: Path) -> None:
    history = RunHistory(tmp_path)
    history.record("20260101-000000", ["old"])
    history.record("20260201-000000", ["mid"])
    history.record("20260301-000000", ["new"])
    assert history.retained(1) == {"new"}
    assert history.retained(2) == {"mid", "new"}
