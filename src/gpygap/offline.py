# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""The air-gapped side: applying updates and proving the mirror works there.

Commands (``python3 gpygap_offline.py <command> --help``):

``apply DELTA MIRROR``
    Apply one exported delta. The payload is checked against the checksums in
    ``DELTA.json`` before anything is touched; afterwards the whole mirror is
    checked against ``MIRROR-SHA256SUMS``. Each delta names the mirror version
    it expects, so a skipped or repeated delta is refused.

``verify MIRROR``
    Check every file of the mirror against ``MIRROR-SHA256SUMS``.

``smoke-test URL``
    From any machine with uv: resolve what the online side verified, against
    the *served* mirror over HTTP. Proves the web server setup too.

The export (``export_delta``) lives here as well, next to the format it writes.

This module must only import the standard library (Python 3.9+) and nothing
from this package: it is copied into every delta and into the mirror root as
``gpygap_offline.py``, so the offline side needs no installation. That is why
the full licence notice is repeated here: this file travels on its own.

Copyright (C) 2026 Mateusz Okulanis.

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. It is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details. You
should have received a copy of the GNU General Public License along with this
program. If not, see <https://www.gnu.org/licenses/>.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

VERSION_FILE = "MIRROR-VERSION"
SUMS_FILE = "MIRROR-SHA256SUMS"
VERIFIED_FILE = "MIRROR-VERIFIED.json"
DELTA_FILE = "DELTA.json"
PAYLOAD_DIR = "payload"
SCRIPT_NAME = "gpygap_offline.py"
_TEMPORARY_SUFFIXES = (".part", ".tmp")
_TREES = ("packages", "simple", "tools", "python")
_CHUNK = 1 << 20

Snapshot = dict[str, list[int]]
"""Mirror path -> ``[size, mtime_ns]``"""


# --------------------------------------------------------------------------- checksums


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sums(path: Path) -> dict[str, str]:
    """Parse a ``sha256sum``-style file into ``{path: sha256}``."""
    sums = {}
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            digest, name = line.split(None, 1)
            sums[name.strip().lstrip("*")] = digest
    return sums


def verify_mirror(root: Path) -> list[str]:
    """Check every file listed in ``MIRROR-SHA256SUMS``; return the problems found."""
    sums_file = root / SUMS_FILE
    if not sums_file.exists():
        return [f"{SUMS_FILE} not found in {root}"]
    problems = []
    sums = read_sums(sums_file)
    for count, (relative, expected) in enumerate(sorted(sums.items()), 1):
        path = _inside(root, relative)
        if not path.is_file():
            problems.append(f"missing: {relative}")
        elif sha256_file(path) != expected:
            problems.append(f"corrupted: {relative}")
        if count % 5000 == 0:
            log.info("checked %d/%d files", count, len(sums))
    return problems


# --------------------------------------------------------------------------- export (online side)


@dataclass(frozen=True)
class DeltaSummary:
    base_version: str | None
    new_version: str
    changed: int
    bytes: int
    deleted: int


def take_snapshot(root: Path) -> Snapshot:
    snapshot: Snapshot = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.name.endswith(_TEMPORARY_SUFFIXES):
            stat = path.stat()
            snapshot[path.relative_to(root).as_posix()] = [stat.st_size, stat.st_mtime_ns]
    return snapshot


def export_delta(root: Path, dest: Path, snapshot_file: Path, *, full: bool = False) -> DeltaSummary:
    """Write the changes since the last export (or everything, if ``full``) to ``dest``."""
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"delta directory {dest} is not empty")
    previous = {"version": None, "files": {}} if full else _read_snapshot(snapshot_file)
    current = take_snapshot(root)
    changed = sorted(p for p, sig in current.items() if previous["files"].get(p) != sig)
    deleted = sorted(p for p in previous["files"] if p not in current)
    checksums = {path: _copy_and_hash(root / path, dest / PAYLOAD_DIR / path) for path in changed}
    summary = DeltaSummary(
        base_version=previous["version"],
        new_version=(root / VERSION_FILE).read_text("utf-8").strip(),
        changed=len(changed),
        bytes=sum(current[p][0] for p in changed),
        deleted=len(deleted),
    )
    _write_json(
        dest / DELTA_FILE,
        {
            "base_version": summary.base_version,
            "new_version": summary.new_version,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": checksums,
            "deleted": deleted,
        },
    )
    shutil.copy2(Path(__file__), dest / SCRIPT_NAME)
    _write_json(snapshot_file, {"version": summary.new_version, "files": current})
    return summary


def _copy_and_hash(source: Path, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with source.open("rb") as reader, dest.open("wb") as writer:
        for chunk in iter(lambda: reader.read(_CHUNK), b""):
            digest.update(chunk)
            writer.write(chunk)
    shutil.copystat(source, dest)
    return digest.hexdigest()


# --------------------------------------------------------------------------- apply (offline side)


def apply_delta(delta: Path, target: Path, *, force: bool = False, verify: bool = True) -> list[str]:
    """Apply one exported delta to ``target``; return problems found by the final check."""
    info = json.loads((delta / DELTA_FILE).read_text("utf-8"))
    current = _mirror_version(target)
    if info["base_version"] != current and not force:
        raise RuntimeError(
            f"delta expects mirror version {info['base_version']!r} but {target} has {current!r}; "
            "apply the missing deltas in order, or export a full one (--full-export)"
        )
    payload = delta / PAYLOAD_DIR
    corrupted = _check_payload(payload, info["files"])
    if corrupted:
        raise RuntimeError(f"delta is damaged, nothing was changed: {', '.join(corrupted[:10])}")
    files = sorted(info["files"], key=_copy_order)
    for count, path in enumerate(files, 1):
        _install(payload / path, _inside(target, path))
        if count % 1000 == 0:
            log.info("copied %d/%d files", count, len(files))
    removed = _delete(target, info["deleted"])
    log.info("applied %s -> %s: %d copied, %d deleted", current, info["new_version"], len(files), removed)
    return verify_mirror(target) if verify else []


def _check_payload(payload: Path, checksums: dict[str, str]) -> list[str]:
    log.info("checking %d delta files", len(checksums))
    bad = []
    for path, expected in checksums.items():
        file = _inside(payload, path)
        if not file.is_file() or sha256_file(file) != expected:
            bad.append(path)
    return bad


def _copy_order(path: str) -> tuple[int, str]:
    """Package files first, then the index pointing at them, the version marker last."""
    if path == VERSION_FILE:
        return 2, path
    if path.startswith("simple/") or "/" not in path:
        return 1, path
    return 0, path


def _install(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copy2(source, tmp)
    tmp.replace(dest)


def _delete(target: Path, paths: list[str]) -> int:
    removed = 0
    for path in paths:
        file = _inside(target, path)
        if file.is_file():
            file.unlink()
            removed += 1
    for tree in _TREES:
        _remove_empty_dirs(target / tree)
    return removed


# --------------------------------------------------------------------------- smoke test


def smoke_test(base_url: str, *, uv: str, include_all: bool, jobs: int) -> list[str]:
    """Re-run the online install checks against the served mirror; return failures."""
    base_url = base_url.rstrip("/")
    with urllib.request.urlopen(f"{base_url}/{VERIFIED_FILE}", timeout=60) as response:
        checks = json.loads(response.read())
    if not include_all:
        checks = [c for c in checks if c.get("top")]
    log.info("smoke test: %d checks against %s/simple", len(checks), base_url)
    with tempfile.TemporaryDirectory() as cache:
        env = dict(os.environ, UV_CACHE_DIR=cache, UV_NO_PROGRESS="1")

        def run(check: dict[str, Any]) -> str | None:
            return _smoke_check(check, f"{base_url}/simple", uv, env)

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            failures = [f for f in pool.map(run, checks) if f is not None]
    return failures


def _smoke_check(check: dict[str, Any], index_url: str, uv: str, env: dict[str, str]) -> str | None:
    command = [uv, "pip", "compile", "-", "--no-header", "--quiet", "--no-config"]
    command += ["--default-index", index_url, "--python-version", check["python"]]
    command += ["--python-platform", check["uv_target"]]
    command += ["--no-build"] if check["outcome"] == "wheels" else []
    command += ["--no-deps"] if check.get("no_deps") else []
    proc = subprocess.run(
        command, input=check["requirement"] + "\n", capture_output=True, text=True, env=env, check=False
    )
    if proc.returncode == 0:
        return None
    last_line = (proc.stderr.strip().splitlines() or ["?"])[-1]
    return f"{check['requirement']} @ py{check['python']} {check['platform']}: {last_line}"


# --------------------------------------------------------------------------- helpers


def _inside(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root``, refusing anything that escapes it."""
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe path: {relative!r}")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes the mirror: {relative!r}")
    return resolved


def _mirror_version(root: Path) -> str | None:
    try:
        return (root / VERSION_FILE).read_text("utf-8").strip()
    except FileNotFoundError:
        return None


def _read_snapshot(path: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(path.read_text("utf-8")))
    except (OSError, ValueError):
        return {"version": None, "files": {}}


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), "utf-8")
    tmp.replace(path)


def _remove_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    directories = sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts))
    for directory in reversed(directories):
        if not any(directory.iterdir()):
            directory.rmdir()


# --------------------------------------------------------------------------- command line


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=SCRIPT_NAME, description="Offline side of gpygap.")
    commands = parser.add_subparsers(dest="command", required=True)

    apply = commands.add_parser("apply", help="apply a delta to the offline mirror")
    apply.add_argument("delta", type=Path, help="delta directory (contains DELTA.json)")
    apply.add_argument("mirror", type=Path, help="offline mirror directory")
    apply.add_argument("--force", action="store_true", help="skip the base version check")
    apply.add_argument("--no-verify", action="store_true", help="skip checking the whole mirror afterwards")

    verify = commands.add_parser("verify", help="check the mirror against MIRROR-SHA256SUMS")
    verify.add_argument("mirror", type=Path)

    smoke = commands.add_parser("smoke-test", help="resolve verified requirements against the served mirror")
    smoke.add_argument("url", help="mirror base URL, e.g. http://pypi.example.local")
    smoke.add_argument("--all", action="store_true", help="check extras and pins too, not only top projects")
    smoke.add_argument("--uv", default="uv", help="uv executable")
    smoke.add_argument("--jobs", type=int, default=8)
    return parser


def _report(problems: list[str], what: str) -> int:
    for problem in problems[:50]:
        log.error("  %s", problem)
    if len(problems) > 50:
        log.error("  ... and %d more", len(problems) - 50)
    log.info("%s: %s", what, f"{len(problems)} problem(s)" if problems else "OK")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "apply":
            problems = apply_delta(args.delta, args.mirror, force=args.force, verify=not args.no_verify)
            return _report(problems, "mirror check")
        if args.command == "verify":
            return _report(verify_mirror(args.mirror), "mirror check")
        failures = smoke_test(args.url, uv=args.uv, include_all=args.all, jobs=args.jobs)
        return _report(failures, "smoke test")
    except (OSError, ValueError, RuntimeError) as exc:
        log.error("error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
