# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Choosing which upstream files the mirror needs.

A universal resolution lists every wheel of every pinned version, for every
platform and interpreter. Only wheels installable on a configured platform with
a configured CPython version are kept; packages whose environment marker
excludes all targets (e.g. macOS-only dependencies) are dropped entirely.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

from packaging.markers import InvalidMarker, Marker, UndefinedComparison, UndefinedEnvironmentName
from packaging.tags import Tag
from packaging.utils import InvalidWheelFilename, parse_wheel_filename

from gpygap.config import SdistPolicy
from gpygap.models import BuildTarget, DistFile, LockedPackage, MirrorFile
from gpygap.platforms import Platform
from gpygap.resolver import Resolution

_INTERPRETER = re.compile(r"(?P<impl>py|cp)(?P<major>\d)(?P<minor>\d*)")


def parse_python(version: str) -> tuple[int, int]:
    major, minor = version.split(".")
    return int(major), int(minor)


def interpreter_compatible(interpreter: str, abi: str, python: tuple[int, int]) -> bool:
    """Return whether a wheel tag's interpreter/ABI pair runs on CPython ``python``.

    Non-CPython builds (``pp``, ``graalpy``) and free-threaded builds (``cp313t``)
    are rejected; stable-ABI wheels (``abi3``) run on their version and later.
    """
    match = _INTERPRETER.fullmatch(interpreter)
    if match is None or int(match["major"]) != python[0]:
        return False
    minor = int(match["minor"]) if match["minor"] else None
    if match["impl"] == "py" or abi in ("abi3", "none"):
        return minor is None or minor <= python[1]
    return minor == python[1] and not abi.endswith("t")


@functools.cache
def _wheel_tags(filename: str) -> frozenset[Tag] | None:
    try:
        return parse_wheel_filename(filename)[3]
    except InvalidWheelFilename:
        return None


def wheel_compatible(filename: str, platform: Platform, python: tuple[int, int]) -> bool:
    """Return whether the wheel installs on ``platform`` with CPython ``python``.

    Wheels with unparsable names are kept: mirroring a useless file is cheaper
    than missing a needed one.
    """
    tags = _wheel_tags(filename)
    if tags is None:
        return True
    return any(
        platform.accepts_wheel_platform(tag.platform)
        and interpreter_compatible(tag.interpreter, tag.abi, python)
        for tag in tags
    )


@functools.cache
def _parse_marker(marker: str) -> Marker | None:
    try:
        return Marker(marker)
    except InvalidMarker:
        return None


def _marker_environments(platform: Platform, python: str) -> Iterator[dict[str, str]]:
    # The patch version is unknown, so try both ends of the range.
    for patch in ("0", "99"):
        full = f"{python}.{patch}"
        yield {
            **platform.marker_env,
            "python_version": python,
            "python_full_version": full,
            "implementation_name": "cpython",
            "implementation_version": full,
            "platform_python_implementation": "CPython",
            "platform_release": "",
            "platform_version": "",
            "extra": "",
        }


def marker_applies(marker: str | None, platform: Platform, python: str) -> bool:
    """Return whether a dependency with ``marker`` is needed on ``platform``/``python``.

    Markers that cannot be evaluated count as applying, for the same reason as
    unparsable wheel names.
    """
    if not marker:
        return True
    parsed = _parse_marker(marker)
    if parsed is None:
        return True
    try:
        return any(parsed.evaluate(env) for env in _marker_environments(platform, python))
    except (UndefinedComparison, UndefinedEnvironmentName):
        return True


@dataclass(frozen=True, slots=True)
class Selection:
    files: tuple[DistFile, ...]
    build_sdist: DistFile | None
    """The sdist, if some target has no wheel and must build from source."""
    needs_build: tuple[BuildTarget, ...] = ()
    """Exactly which platform and Python version have no wheel.

    A single boolean would say "build it" without saying what for; the wheel builder
    needs the list, because one build serves one interpreter on one platform (unless
    it turns out to be ``py3-none-any``, which serves all of them).
    """


def select_files(
    package: LockedPackage, python: str, platforms: Sequence[Platform], policy: SdistPolicy
) -> Selection:
    """Pick the files of ``package`` needed for ``python`` on ``platforms``."""
    targets = [p for p in platforms if marker_applies(package.marker, p, python)]
    if not targets:
        return Selection((), None)
    version = parse_python(python)
    wheels = [f for f in package.files if f.kind == "wheel"]
    sdists = [f for f in package.files if f.kind == "sdist"]

    def has_wheel(platform: Platform) -> bool:
        return any(wheel_compatible(w.filename, platform, version) for w in wheels)

    kept = [w for w in wheels if any(wheel_compatible(w.filename, p, version) for p in targets)]
    uncovered = tuple(BuildTarget(p.key, python) for p in targets if not has_wheel(p))
    if policy is SdistPolicy.ALL or (policy is SdistPolicy.NEEDED and uncovered):
        kept += sdists
    build_sdist = sdists[0] if sdists and uncovered else None
    return Selection(tuple(kept), build_sdist, uncovered if build_sdist else ())


@dataclass(slots=True)
class MirrorPlan:
    """Everything the mirror should contain after this run."""

    files: dict[str, MirrorFile] = field(default_factory=dict)
    build_sdists: dict[str, set[BuildTarget]] = field(default_factory=dict)
    """Sdist path -> the targets with no upstream wheel for it.

    Their build requirements are mirrored, and the wheel builder turns each target
    into a wheel so that nothing has to be compiled on a developer machine.
    """
    by_requirement: dict[str, set[str]] = field(default_factory=dict)
    """Paths each requirement pulls in, for size reports."""

    def size_of(self, paths: Iterable[str]) -> int:
        return sum(self.files[p].size or 0 for p in paths)


def package_path(project: str, filename: str) -> str:
    return f"packages/{project}/{filename}"


def build_plan(
    resolutions: Iterable[Resolution], platforms: Sequence[Platform], policy: SdistPolicy
) -> MirrorPlan:
    """Turn resolutions into the set of files to mirror.

    Stale resolutions contribute their last successful packages, so a failure
    keeps already mirrored files instead of dropping them. A resolution made for
    one platform only contributes files for that platform.
    """
    plan = MirrorPlan()
    for resolution in resolutions:
        request = resolution.request
        targets = (request.platform,) if request.platform else tuple(platforms)
        paths = plan.by_requirement.setdefault(request.requirement, set())
        for package in resolution.packages:
            selection = select_files(package, request.python, targets, policy)
            for dist in selection.files:
                path = package_path(package.name, dist.filename)
                plan.files[path] = _mirror_file(path, package.name, dist)
                paths.add(path)
            if selection.build_sdist is not None:
                sdist_path = package_path(package.name, selection.build_sdist.filename)
                plan.build_sdists.setdefault(sdist_path, set()).update(selection.needs_build)
    return plan


def _mirror_file(path: str, project: str, dist: DistFile) -> MirrorFile:
    return MirrorFile(path=path, url=dist.url, size=dist.size, sha256=dist.sha256, project=project)
