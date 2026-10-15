# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Requirements handed in by teams, so their projects install offline.

Teams drop files into ``packages.requirements_dir``:

* ``uv.lock`` / ``*.lock`` (uv) and ``pylock*.toml`` (PEP 751): complete lock
  files. Every locked registry package is mirrored at exactly its locked
  version; dependencies are already in the lock, so pins resolve ``--no-deps``.
* ``*.txt`` (requirements files): each line is mirrored with its dependencies.

Old pinned versions that dropped out of the top list stay available this way.
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_LINE_CONTINUATION = re.compile(r"\\\s*\n")
_OPTION = re.compile(r"\s--?[a-z].*$")


@dataclass(frozen=True, slots=True)
class Intake:
    requirements: tuple[str, ...] = ()
    """Requirements resolved with their dependencies."""
    pins: tuple[str, ...] = ()
    """``name==version`` pins from lock files, resolved without dependencies."""


def load_intake(directory: Path | None) -> Intake:
    if directory is None:
        return Intake()
    if not directory.is_dir():
        log.warning("packages.requirements_dir %s does not exist", directory)
        return Intake()
    requirements: dict[str, None] = {}
    pins: dict[str, None] = {}
    for path in sorted(p for p in directory.rglob("*") if p.is_file() and not _is_documentation(p)):
        found_requirements, found_pins = _read(path)
        requirements.update(dict.fromkeys(found_requirements))
        pins.update(dict.fromkeys(found_pins))
        log.info("Intake %s: %d requirements, %d pins", path.name, len(found_requirements), len(found_pins))
    return Intake(tuple(requirements), tuple(pins))


def _is_documentation(path: Path) -> bool:
    """README files and dotfiles may live next to the intake files; they are not requirements."""
    return path.name.startswith(".") or path.suffix.lower() in (".md", ".rst")


def _read(path: Path) -> tuple[list[str], list[str]]:
    try:
        if path.suffix == ".lock":
            return [], list(_uv_lock_pins(tomllib.loads(path.read_text("utf-8"))))
        if path.name.startswith("pylock") and path.suffix == ".toml":
            return [], list(_pylock_pins(tomllib.loads(path.read_text("utf-8"))))
        if path.suffix == ".txt":
            return list(_requirement_lines(path.read_text("utf-8"))), []
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        log.warning("Intake %s skipped: %s", path, exc)
        return [], []
    log.info("Intake %s skipped: unknown file type", path.name)
    return [], []


def _uv_lock_pins(lock: dict[str, Any]) -> Iterator[str]:
    for package in lock.get("package", []):
        if "registry" in package.get("source", {}) and package.get("version"):
            yield f"{package['name']}=={package['version']}"


def _pylock_pins(lock: dict[str, Any]) -> Iterator[str]:
    for package in lock.get("packages", []):
        from_index = package.get("index") or package.get("sdist") or package.get("wheels")
        if from_index and package.get("version"):
            yield f"{package['name']}=={package['version']}"


def _requirement_lines(text: str) -> Iterator[str]:
    """Requirement lines of a requirements file; options (``-r``, ``--hash``…) are dropped."""
    for raw in _LINE_CONTINUATION.sub(" ", text).splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        yield _OPTION.sub("", line).strip()
