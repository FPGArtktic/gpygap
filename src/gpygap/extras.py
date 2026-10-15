# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Discovery of optional features ("extras") of mirrored requirements.

``pip install uvicorn[standard]`` needs more than ``uvicorn``. Each extra that
a requirement's project declares (``Provides-Extra``) becomes its own
requirement, resolved separately: extras of one project may conflict with each
other (``cpu`` vs ``cuda``), so resolving them together could fail where each
alone works. Version constraints are kept: ``django>=4.2`` yields
``django[argon2]>=4.2``.

Extras are read from the mirrored file when it is already downloaded, otherwise
from the index's PEP 658 metadata (``<wheel url>.metadata``), which keeps
``--plan-only`` free of large downloads.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Collection, Iterable, Mapping
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from gpygap.distmeta import DistMetaCache, parse_core_metadata
from gpygap.models import DistFile, LockedPackage
from gpygap.resolver import Resolution, ResolveRequest
from gpygap.selection import package_path
from gpygap.util import http_get, normalize_name, parallel_map

log = logging.getLogger(__name__)

_REMOTE_WORKERS = 16


def extra_requirements(
    requirements: Iterable[str],
    resolutions: Mapping[ResolveRequest, Resolution],
    meta: DistMetaCache,
    root: Path,
    exclude: Collection[str],
) -> list[str]:
    """Return one requirement per extra of every resolved version of ``requirements``.

    Requirements that already name extras, or point at a URL, are left alone.
    """
    parsed = _plain_requirements(requirements)
    sources: dict[str, tuple[Requirement, DistFile]] = {}
    for requirement, package in _resolved_versions(parsed, resolutions):
        dist = _metadata_source(package)
        if dist is not None:
            sources[package_path(package.name, dist.filename)] = (requirement, dist)
    missing = [dist for path, (_, dist) in sources.items() if not (root / path).exists()]
    remote = _remote_extras(missing)

    result: set[str] = set()
    for path, (requirement, dist) in sources.items():
        extras = meta.get(path).provides_extra if (root / path).exists() else remote.get(dist.url, ())
        result.update(with_extra(requirement, extra) for extra in extras if extra not in exclude)
    return sorted(result)


def with_extra(requirement: Requirement, extra: str) -> str:
    """``requirement`` with exactly one extra, keeping its version and marker."""
    variant = copy.copy(requirement)
    variant.extras = {extra}
    return str(variant)


def _plain_requirements(requirements: Iterable[str]) -> dict[str, Requirement]:
    parsed = {}
    for text in requirements:
        try:
            requirement = Requirement(text)
        except InvalidRequirement:
            continue
        if not requirement.extras and not requirement.url:
            parsed[text] = requirement
    return parsed


def _resolved_versions(
    requirements: Mapping[str, Requirement], resolutions: Mapping[ResolveRequest, Resolution]
) -> list[tuple[Requirement, LockedPackage]]:
    """Distinct versions of each requirement's project as resolved for it, across Python versions."""
    seen: dict[tuple[str, str, str], tuple[Requirement, LockedPackage]] = {}
    for request, resolution in resolutions.items():
        requirement = requirements.get(request.requirement)
        if requirement is None:
            continue
        name = normalize_name(requirement.name)
        for package in resolution.packages:
            if package.name == name:
                seen.setdefault((request.requirement, package.name, package.version), (requirement, package))
    return list(seen.values())


def _metadata_source(package: LockedPackage) -> DistFile | None:
    """A wheel if there is one (its metadata is exact), else the sdist."""
    for kind in ("wheel", "sdist"):
        for dist in package.files:
            if dist.kind == kind:
                return dist
    return None


def _remote_extras(dists: list[DistFile]) -> dict[str, tuple[str, ...]]:
    """Fetch PEP 658 metadata of wheels not downloaded yet; sdists are skipped."""
    wheels = [d for d in dists if d.kind == "wheel"]
    extras: dict[str, tuple[str, ...]] = {}
    for dist, provided in parallel_map(_fetch_extras, wheels, _REMOTE_WORKERS):
        if provided is not None:
            extras[dist.url] = provided
    return extras


def _fetch_extras(dist: DistFile) -> tuple[str, ...] | None:
    try:
        return parse_core_metadata(http_get(dist.url + ".metadata")).provides_extra
    except OSError as exc:
        log.debug("no remote metadata for %s: %s", dist.filename, exc)
        return None
