# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plain data types shared across the package."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

DistKind = Literal["sdist", "wheel"]
FileOrigin = Literal["upstream", "built"]


@dataclass(frozen=True, slots=True, order=True)
class BuildTarget:
    """One platform and CPython version a wheel has to exist for.

    Used both ways round: selection reports the targets an sdist has no wheel for,
    and the wheel builder reports the targets a wheel it produced satisfies.
    """

    platform: str
    """A ``platforms.PLATFORMS`` key, e.g. ``linux-x86_64``."""
    python: str
    """``major.minor``, with a ``t`` suffix for free-threaded builds: ``3.13t``."""

    def __str__(self) -> str:
        return f"{self.platform}/py{self.python}"


@dataclass(frozen=True, slots=True)
class DistFile:
    """A single distribution file as listed in a lock file."""

    kind: DistKind
    url: str
    filename: str
    size: int | None
    sha256: str | None


@dataclass(frozen=True, slots=True)
class LockedPackage:
    """One package pinned by a resolution, with all its upstream files."""

    name: str
    version: str
    marker: str | None
    files: tuple[DistFile, ...]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> LockedPackage:
        return cls(
            name=data["name"],
            version=data["version"],
            marker=data.get("marker"),
            files=tuple(DistFile(**f) for f in data["files"]),
        )


@dataclass(frozen=True, slots=True)
class MirrorFile:
    """A file the mirror should contain, addressed by its POSIX path inside the mirror.

    ``project`` is set for package files and ``None`` for tools (uv, Python builds),
    which are not listed in the simple index.

    ``origin`` says where the file came from. ``built`` means this mirror compiled it
    from an sdist because upstream ships no wheel for some target; such a file has no
    ``url`` and cannot be re-downloaded, only rebuilt.
    """

    path: str
    url: str
    size: int | None = None
    sha256: str | None = None
    project: str | None = None
    origin: FileOrigin = "upstream"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> MirrorFile:
        return cls(**data)


@dataclass(frozen=True, slots=True)
class MirrorInfo:
    """Description of a published mirror version (written to ``MIRROR-INFO.json``)."""

    version: str
    created: str
    top_last_update: str | None
    top_projects: tuple[str, ...]
    extra_requirements: tuple[str, ...]
    python_versions: tuple[str, ...]
    platforms: tuple[str, ...]
    projects: int
    files: int
    bytes: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
