# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Metadata read from downloaded distributions.

Wheels: the ``METADATA`` file is extracted next to the wheel as
``<wheel>.metadata`` (PEP 658), so installers can resolve without downloading
whole wheels, and ``Requires-Python`` is exposed in the index (PEP 503).

Sdists: ``PKG-INFO`` and ``build-system.requires`` from ``pyproject.toml``,
which tells which build backends the mirror must carry.

From both, ``Provides-Extra`` tells which extras (``pkg[extra]``) exist.

Archives are only read, never extracted to disk.
"""

from __future__ import annotations

import hashlib
import logging
import re
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from email.parser import BytesHeaderParser
from pathlib import Path
from typing import Any

from gpygap.util import normalize_name, read_json, write_if_changed, write_json

log = logging.getLogger(__name__)

# What pip and uv assume for an sdist without a [build-system] table (PEP 517/518).
LEGACY_BUILD_REQUIRES = ("setuptools>=40.8.0", "wheel")
_MAX_MEMBER_SIZE = 1 << 20
_WHEEL_METADATA = re.compile(r"[^/]+\.dist-info/METADATA")
# Bump when DistMeta gains fields, so stale cache entries are re-read.
_CACHE_VERSION = 2


@dataclass(frozen=True, slots=True)
class CoreMetadata:
    """The parts of core metadata (PEP 566) the mirror needs."""

    requires_python: str | None
    provides_extra: tuple[str, ...]


def parse_core_metadata(raw: bytes) -> CoreMetadata:
    headers = BytesHeaderParser().parsebytes(raw)
    requires_python = str(headers.get("Requires-Python") or "").strip()
    extras = sorted({normalize_name(str(e)) for e in headers.get_all("Provides-Extra") or []})
    return CoreMetadata(requires_python=requires_python or None, provides_extra=tuple(extras))


@dataclass(frozen=True, slots=True)
class DistMeta:
    size: int
    requires_python: str | None = None
    metadata_sha256: str | None = None
    build_requires: tuple[str, ...] = ()
    provides_extra: tuple[str, ...] = ()


def metadata_sidecar(wheel: Path) -> Path:
    return wheel.with_name(wheel.name + ".metadata")


def inspect_wheel(path: Path) -> DistMeta:
    """Extract the wheel's METADATA to its PEP 658 sidecar and summarize it."""
    with zipfile.ZipFile(path) as archive:
        names = [n for n in archive.namelist() if _WHEEL_METADATA.fullmatch(n)]
        if not names:
            return DistMeta(size=path.stat().st_size)
        raw = archive.read(names[0])
    write_if_changed(metadata_sidecar(path), raw)
    core = parse_core_metadata(raw)
    return DistMeta(
        size=path.stat().st_size,
        requires_python=core.requires_python,
        metadata_sha256=hashlib.sha256(raw).hexdigest(),
        provides_extra=core.provides_extra,
    )


def inspect_sdist(path: Path) -> DistMeta:
    members = _read_top_level_members(path, {"PKG-INFO", "pyproject.toml"})
    core = parse_core_metadata(members.get("PKG-INFO", b""))
    return DistMeta(
        size=path.stat().st_size,
        requires_python=core.requires_python,
        build_requires=_build_requires(members.get("pyproject.toml")),
        provides_extra=core.provides_extra,
    )


def _read_top_level_members(path: Path, wanted: set[str]) -> dict[str, bytes]:
    """Read files named in ``wanted`` from the sdist's top-level directory."""
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            return {
                name.split("/")[1]: archive.read(name)
                for name in archive.namelist()
                if _is_top_level(name, wanted)
            }
    found: dict[str, bytes] = {}
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            if member.isfile() and member.size <= _MAX_MEMBER_SIZE and _is_top_level(member.name, wanted):
                stream = archive.extractfile(member)
                if stream is not None:
                    found[member.name.split("/")[1]] = stream.read()
            if len(found) == len(wanted):
                break
    return found


def _is_top_level(name: str, wanted: set[str]) -> bool:
    parts = name.strip("/").split("/")
    return len(parts) == 2 and parts[1] in wanted


def _build_requires(pyproject: bytes | None) -> tuple[str, ...]:
    if pyproject is None:
        return LEGACY_BUILD_REQUIRES
    try:
        build_system = tomllib.loads(pyproject.decode("utf-8", "replace")).get("build-system", {})
    except tomllib.TOMLDecodeError:
        return LEGACY_BUILD_REQUIRES
    requires = build_system.get("requires")
    if not isinstance(requires, list):
        return LEGACY_BUILD_REQUIRES
    return tuple(str(r) for r in requires)


class DistMetaCache:
    """Per-file metadata, cached across runs and keyed by mirror path."""

    def __init__(self, root: Path, cache_file: Path) -> None:
        self._root = root
        self._cache_file = cache_file
        self._entries: dict[str, DistMeta] = {}
        data = read_json(cache_file) or {}
        if data.get("version") == _CACHE_VERSION:
            for path, entry in data["entries"].items():
                self._entries[path] = _meta_from_json(entry)

    def get(self, path: str) -> DistMeta:
        file = self._root / path
        cached = self._entries.get(path)
        if cached is not None and self._is_current(file, cached):
            return cached
        meta = self._inspect(file)
        self._entries[path] = meta
        return meta

    def save(self, keep: Iterable[str]) -> None:
        """Persist the cache, forgetting files no longer in the mirror."""
        keep = set(keep)
        entries = {p: asdict(m) for p, m in self._entries.items() if p in keep}
        write_json(self._cache_file, {"version": _CACHE_VERSION, "entries": entries})

    @staticmethod
    def _is_current(file: Path, meta: DistMeta) -> bool:
        if file.stat().st_size != meta.size:
            return False
        return not file.name.endswith(".whl") or metadata_sidecar(file).exists()

    @staticmethod
    def _inspect(file: Path) -> DistMeta:
        try:
            return inspect_wheel(file) if file.name.endswith(".whl") else inspect_sdist(file)
        except (OSError, zipfile.BadZipFile, tarfile.TarError, UnicodeDecodeError) as exc:
            log.warning("cannot read metadata of %s: %s", file.name, exc)
            return DistMeta(size=file.stat().st_size)


def _meta_from_json(entry: dict[str, Any]) -> DistMeta:
    return DistMeta(
        size=entry["size"],
        requires_python=entry.get("requires_python"),
        metadata_sha256=entry.get("metadata_sha256"),
        build_requires=tuple(entry.get("build_requires") or ()),
        provides_extra=tuple(entry.get("provides_extra") or ()),
    )


def build_requirements(meta: DistMetaCache, sdist_paths: Iterable[str], root: Path) -> set[str]:
    """Collect ``build-system.requires`` of the given sdists, whitespace-normalized."""
    requirements: set[str] = set()
    for path in sdist_paths:
        if (root / path).exists():
            requirements.update(" ".join(r.split()) for r in meta.get(path).build_requires)
    return requirements
