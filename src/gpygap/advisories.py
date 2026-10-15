# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Known vulnerabilities in what the mirror ships.

An air-gapped network is slower to patch than any other, because a fix travels
at the speed of the next delta. The least this tool can do is say, at the moment
it publishes, which of the packages it just mirrored have advisories against the
exact versions it chose -- and ship that answer across the air gap with them, so
the people who cannot query an API still have the list.

OSV publishes its whole PyPI advisory set as one archive, which is why this is
practical at all: matching a few thousand ``(project, version)`` pairs is a local
join over a 36 MB download, not thousands of API calls from a machine that is
already doing enough of them.

Two honest caveats, repeated in the published report so nobody reads it as more
than it is: it is a snapshot taken when the mirror was built, and advisory data
has both false positives and gaps.
"""

from __future__ import annotations

import json
import logging
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from gpygap.config import Config
from gpygap.models import SEVERITIES, MirrorFile
from gpygap.util import http_get, normalize_name

log = logging.getLogger(__name__)

_ALIASES = {"medium": "moderate", "important": "high"}


@dataclass(frozen=True, slots=True)
class Finding:
    """One advisory against one version the mirror actually holds."""

    project: str
    version: str
    advisory: str
    aliases: tuple[str, ...]
    severity: str
    summary: str
    fixed_in: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "version": self.version,
            "advisory": self.advisory,
            "aliases": list(self.aliases),
            "severity": self.severity,
            "summary": self.summary,
            "fixed_in": list(self.fixed_in),
        }


@dataclass(frozen=True, slots=True)
class AdvisorySummary:
    """What the scan found, and whether it should stop the run."""

    scanned_projects: int
    scanned_versions: int
    findings: tuple[Finding, ...]
    counts: Mapping[str, int]
    stale: bool = False
    """The advisory set could not be refreshed and a cached copy was used."""

    @property
    def blocking_count(self) -> int:
        return len(self.findings)

    def at_or_above(self, level: str) -> int:
        if level not in SEVERITIES or level == "unknown":
            return 0
        floor = SEVERITIES.index(level)
        return sum(n for s, n in self.counts.items() if s in SEVERITIES and SEVERITIES.index(s) >= floor)

    def to_json(self) -> dict[str, Any]:
        return {
            "scanned_projects": self.scanned_projects,
            "scanned_versions": self.scanned_versions,
            "counts": dict(self.counts),
            "stale": self.stale,
            "findings": [f.to_json() for f in self.findings],
            "caveat": (
                "A snapshot taken when this mirror was built, from the OSV PyPI "
                "advisory set. Advisory data has false positives and gaps; absence "
                "here is not evidence that a version is safe."
            ),
        }


def mirrored_versions(manifest: Mapping[str, MirrorFile]) -> dict[str, set[str]]:
    """Project -> the versions of it present in the mirror, read from the file names."""
    versions: dict[str, set[str]] = {}
    for path, file in manifest.items():
        if file.project is None:
            continue
        version = _version_of(path.rsplit("/", 1)[-1])
        if version is not None:
            versions.setdefault(normalize_name(file.project), set()).add(version)
    return versions


def _version_of(filename: str) -> str | None:
    try:
        if filename.endswith(".whl"):
            return str(parse_wheel_filename(filename)[1])
        if filename.endswith((".tar.gz", ".zip")):
            return str(parse_sdist_filename(filename)[1])
    except (InvalidWheelFilename, InvalidSdistFilename, InvalidVersion, ValueError):
        return None
    return None


def scan(archive: Path, mirrored: Mapping[str, set[str]]) -> list[Finding]:
    """Match the OSV archive against the versions the mirror holds.

    The archive is streamed and only matching advisories are kept: there are tens
    of thousands of them and no reason to hold the ones about packages nobody here
    mirrors.
    """
    findings: list[Finding] = []
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if not name.endswith(".json"):
                continue
            try:
                entry = json.loads(bundle.read(name))
            except (ValueError, zipfile.BadZipFile):
                continue
            findings += _findings_for(entry, mirrored)
    findings.sort(key=lambda f: (-SEVERITIES.index(f.severity), f.project, f.version, f.advisory))
    return findings


def _findings_for(entry: Mapping[str, Any], mirrored: Mapping[str, set[str]]) -> list[Finding]:
    if entry.get("withdrawn"):
        return []
    severity = _severity(entry)
    found = []
    for affected in entry.get("affected", ()):
        package = affected.get("package", {})
        if package.get("ecosystem") != "PyPI":
            continue
        project = normalize_name(str(package.get("name", "")))
        held = mirrored.get(project)
        if not held:
            continue
        fixed = _fixed_versions(affected)
        for version in sorted(held & _affected_versions(affected, held)):
            found.append(
                Finding(
                    project=project,
                    version=version,
                    advisory=str(entry.get("id", "?")),
                    aliases=tuple(str(a) for a in entry.get("aliases", ())),
                    severity=severity,
                    summary=str(entry.get("summary") or "").strip(),
                    fixed_in=fixed,
                )
            )
    return found


def _affected_versions(affected: Mapping[str, Any], held: Iterable[str]) -> set[str]:
    """Which of the versions we hold this advisory covers.

    The explicit ``versions`` list is preferred wherever OSV gives one: it is what
    the advisory's author enumerated, and it sidesteps every disagreement about how
    a range endpoint compares under PEP 440.
    """
    listed = affected.get("versions")
    if isinstance(listed, list) and listed:
        enumerated = {str(v) for v in listed}
        return {v for v in held if v in enumerated or _normalized_match(v, enumerated)}
    return {v for v in held if _in_ranges(v, affected.get("ranges", ()))}


def _normalized_match(version: str, enumerated: set[str]) -> bool:
    try:
        parsed = Version(version)
    except InvalidVersion:
        return False
    for candidate in enumerated:
        try:
            if Version(candidate) == parsed:
                return True
        except InvalidVersion:
            continue
    return False


def _in_ranges(version: str, ranges: Iterable[Mapping[str, Any]]) -> bool:
    try:
        parsed = Version(version)
    except InvalidVersion:
        return False
    for entry in ranges:
        if entry.get("type") not in ("ECOSYSTEM", "SEMVER"):
            continue
        inside = False
        for event in entry.get("events", ()):
            if "introduced" in event:
                introduced = str(event["introduced"])
                inside = introduced == "0" or _at_least(parsed, introduced)
            elif inside and (
                ("fixed" in event and _at_least(parsed, str(event["fixed"])))
                or ("last_affected" in event and _above(parsed, str(event["last_affected"])))
            ):
                inside = False
        if inside:
            return True
    return False


def _at_least(version: Version, bound: str) -> bool:
    try:
        return version >= Version(bound)
    except InvalidVersion:
        return False


def _above(version: Version, bound: str) -> bool:
    try:
        return version > Version(bound)
    except InvalidVersion:
        return False


def _severity(entry: Mapping[str, Any]) -> str:
    specific = entry.get("database_specific")
    if isinstance(specific, dict):
        raw = str(specific.get("severity", "")).strip().lower()
        raw = _ALIASES.get(raw, raw)
        if raw in SEVERITIES:
            return raw
    return "unknown"


def fetch_archive(config: Config) -> tuple[Path | None, bool]:
    """Refresh the OSV archive; return it and whether a cached copy had to do.

    A cached copy is used when every source is unreachable, for the same reason the
    top list falls back: a run that silently scanned nothing would report a clean
    mirror, which is worse than reporting an old answer and saying it is old.
    """
    target = config.state.osv_archive
    for url in config.osv_sources:
        try:
            raw = http_get(url, timeout=300, attempts=config.retries + 1)
        except OSError as exc:
            log.warning("advisory source %s unreachable: %s", url, exc)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        temporary.write_bytes(raw)
        temporary.replace(target)
        return target, False
    if target.is_file():
        log.warning("using the cached advisory set: no source was reachable")
        return target, True
    log.error("no advisory source was reachable and no cached copy exists; skipping the scan")
    return None, True


def run_scan(config: Config, manifest: Mapping[str, MirrorFile]) -> AdvisorySummary | None:
    """Scan the published mirror; ``None`` when advisory reporting is switched off."""
    if not config.advisories:
        return None
    mirrored = mirrored_versions(manifest)
    archive, stale = fetch_archive(config)
    if archive is None:
        return AdvisorySummary(len(mirrored), sum(len(v) for v in mirrored.values()), (), {}, stale=True)
    findings = scan(archive, mirrored)
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return AdvisorySummary(
        scanned_projects=len(mirrored),
        scanned_versions=sum(len(v) for v in mirrored.values()),
        findings=tuple(findings),
        counts=counts,
        stale=stale,
    )


def affected_projects(summary: AdvisorySummary, limit: int) -> Sequence[str]:
    """The worst findings, formatted for a console line each."""
    return [
        f"{f.project} {f.version}: {f.advisory} ({f.severity})"
        + (f", fixed in {f.fixed_in[0]}" if f.fixed_in else "")
        for f in summary.findings[:limit]
    ]


def _fixed_versions(affected: Mapping[str, Any]) -> tuple[str, ...]:
    fixed: list[str] = []
    for entry in affected.get("ranges", ()):
        for event in entry.get("events", ()):
            if "fixed" in event:
                fixed.append(str(event["fixed"]))
    return tuple(dict.fromkeys(fixed))
