# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from gpygap import offline
from gpygap.offline import (
    DELTA_FILE,
    PAYLOAD_DIR,
    SCRIPT_NAME,
    SUMS_FILE,
    VERSION_FILE,
    apply_delta,
    export_delta,
    sha256_file,
    verify_mirror,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def _tree(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text("utf-8") for p in root.rglob("*") if p.is_file()}


def test_full_export_then_incremental_delta(tmp_path: Path) -> None:
    online, offline, snapshot = tmp_path / "online", tmp_path / "offline", tmp_path / "snapshot.json"
    _write(online, "packages/a/a-1.whl", "a1")
    _write(online, "packages/b/b-1.whl", "b1")
    _write(online, "simple/a/index.html", "index a1")
    _write(online, VERSION_FILE, "v1\n")

    first = export_delta(online, tmp_path / "d1", snapshot)
    assert first.base_version is None
    assert (tmp_path / "d1" / SCRIPT_NAME).exists()
    apply_delta(tmp_path / "d1", offline)
    assert _tree(offline) == _tree(online)

    (online / "packages/b/b-1.whl").unlink()
    _write(online, "packages/a/a-2.whl", "a2")
    _write(online, "simple/a/index.html", "index a1 a2")
    _write(online, VERSION_FILE, "v2\n")
    os.utime(online / "simple/a/index.html", ns=(1, 1))  # force a different mtime

    second = export_delta(online, tmp_path / "d2", snapshot)
    assert (second.base_version, second.new_version, second.deleted) == ("v1", "v2", 1)
    apply_delta(tmp_path / "d2", offline)
    assert _tree(offline) == _tree(online)


def test_out_of_order_delta_is_refused(tmp_path: Path) -> None:
    online = tmp_path / "online"
    _write(online, VERSION_FILE, "v1\n")
    export_delta(online, tmp_path / "d1", tmp_path / "snapshot.json")
    _write(online, VERSION_FILE, "v2\n")
    export_delta(online, tmp_path / "d2", tmp_path / "snapshot.json")
    with pytest.raises(RuntimeError, match="expects mirror version"):
        apply_delta(tmp_path / "d2", tmp_path / "offline")


def test_path_traversal_in_deletions_is_refused(tmp_path: Path) -> None:
    delta = tmp_path / "delta"
    (delta / "payload").mkdir(parents=True)
    (delta / DELTA_FILE).write_text(
        json.dumps({"base_version": None, "new_version": "v1", "files": {}, "deleted": ["../outside.txt"]}),
        "utf-8",
    )
    (tmp_path / "outside.txt").write_text("keep me", "utf-8")
    with pytest.raises(ValueError, match="unsafe path"):
        apply_delta(delta, tmp_path / "mirror")
    assert (tmp_path / "outside.txt").exists()


def test_damaged_delta_is_refused_before_touching_the_mirror(tmp_path: Path) -> None:
    online, offline = tmp_path / "online", tmp_path / "offline"
    _write(online, "packages/a/a-1.whl", "a1")
    _write(online, VERSION_FILE, "v1\n")
    export_delta(online, tmp_path / "d1", tmp_path / "snapshot.json")
    (tmp_path / "d1" / PAYLOAD_DIR / "packages/a/a-1.whl").write_text("bit rot", "utf-8")
    with pytest.raises(RuntimeError, match="damaged"):
        apply_delta(tmp_path / "d1", offline)
    assert not offline.exists() or not any(offline.rglob("*.whl"))


def test_verify_mirror_reports_missing_and_corrupted_files(tmp_path: Path) -> None:
    _write(tmp_path, "packages/a/a-1.whl", "a1")
    _write(tmp_path, "packages/b/b-1.whl", "b1")
    sums = "".join(
        f"{sha256_file(tmp_path / p)}  {p}\n" for p in ("packages/a/a-1.whl", "packages/b/b-1.whl")
    )
    _write(tmp_path, SUMS_FILE, sums + f"{'0' * 64}  packages/c/c-1.whl\n")
    assert verify_mirror(tmp_path) == ["missing: packages/c/c-1.whl"]
    _write(tmp_path, "packages/a/a-1.whl", "tampered")
    assert verify_mirror(tmp_path) == ["corrupted: packages/a/a-1.whl", "missing: packages/c/c-1.whl"]


def test_offline_module_only_imports_the_standard_library() -> None:
    tree = ast.parse(Path(offline.__file__).read_text("utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported - {"__future__"} <= sys.stdlib_module_names
