# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run reports: a JSON file per run and a short console summary."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from gpygap.downloader import DownloadResult, is_present
from gpygap.manifest import Manifest
from gpygap.models import MirrorInfo
from gpygap.offline import DeltaSummary
from gpygap.prune import PruneResult
from gpygap.resolver import Resolution, ResolveRequest
from gpygap.selection import MirrorPlan
from gpygap.util import human_size, write_json
from gpygap.verify import BuildOutcome, BuildResult, InstallOutcome, InstallResult

log = logging.getLogger(__name__)

_PLAN_TOP_HEAVIEST = 25
_LISTED_PROBLEMS = 20


@dataclass(frozen=True, slots=True)
class TopStatus:
    """Python versions for which a top project resolved, kept a stale result, or failed."""

    ok: tuple[str, ...]
    stale: tuple[str, ...]
    failed: tuple[str, ...]


def top_statuses(
    resolutions: Mapping[ResolveRequest, Resolution], projects: Iterable[str], pythons: Sequence[str]
) -> dict[str, TopStatus]:
    statuses = {}
    for project in projects:
        results = [resolutions[ResolveRequest(project, py)] for py in pythons]
        statuses[project] = TopStatus(
            ok=tuple(r.request.python for r in results if r.ok),
            stale=tuple(r.request.python for r in results if r.stale),
            failed=tuple(r.request.python for r in results if not r.ok and not r.stale),
        )
    return statuses


@dataclass(frozen=True, slots=True)
class VerificationSummary:
    installs: dict[str, int]
    """Install checks per outcome."""
    builds: dict[str, int]
    """Build checks per outcome."""
    heal_rounds: int
    missing: tuple[str, ...]
    """Install checks still failing from the mirror while working upstream."""
    missing_build_deps: tuple[str, ...]
    """Sdists whose build requirements are still not resolvable offline."""

    @classmethod
    def from_results(
        cls, installs: Iterable[InstallResult], builds: Iterable[BuildResult], heal_rounds: int
    ) -> VerificationSummary:
        install_list, build_list = list(installs), list(builds)
        return cls(
            installs=dict(Counter(r.outcome.value for r in install_list)),
            builds=dict(Counter(r.outcome.value for r in build_list)),
            heal_rounds=heal_rounds,
            missing=tuple(sorted(str(r.check) for r in install_list if r.outcome is InstallOutcome.MISSING)),
            missing_build_deps=tuple(
                sorted(
                    f"{r.sdist}: {', '.join(r.missing) or 'unparsed, see log'}"
                    for r in build_list
                    if r.outcome is BuildOutcome.MISSING_DEPS
                )
            ),
        )

    @property
    def complete(self) -> bool:
        return not self.missing and not self.missing_build_deps


@dataclass(slots=True)
class RunReport:
    run_id: str
    duration_s: int
    info: MirrorInfo
    downloads: DownloadResult
    pruned: PruneResult
    top: dict[str, TopStatus]
    verification: VerificationSummary | None
    """``None`` when verification is disabled."""
    delta: DeltaSummary | None

    @property
    def exit_code(self) -> int:
        incomplete = self.verification is not None and not self.verification.complete
        return 1 if incomplete or self.downloads.failed else 0

    def write(self, path: Path) -> None:
        write_json(path, self._to_json())

    def _to_json(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "duration_s": self.duration_s,
            "info": self.info.to_json(),
            "downloads": asdict(self.downloads),
            "pruned": asdict(self.pruned),
            "top_incomplete": {k: asdict(v) for k, v in self.top.items() if v.stale or v.failed},
            "verification": asdict(self.verification) if self.verification else None,
            "delta": asdict(self.delta) if self.delta else None,
        }

    def log_summary(self, report_path: Path, errors_log: Path) -> None:
        info = self.info
        log.info("=" * 72)
        log.info(
            "Mirror %s: %d projects, %d files, %s",
            info.version,
            info.projects,
            info.files,
            human_size(info.bytes),
        )
        log.info(
            "Downloaded %d files (%s), %d failed",
            self.downloads.files,
            human_size(self.downloads.bytes),
            len(self.downloads.failed),
        )
        if self.pruned.blocked:
            log.info("Pruning: BLOCKED by the safety limit (see the errors log)")
        else:
            log.info("Pruned %d files (%s)", self.pruned.files, human_size(self.pruned.bytes))
        self._log_top()
        if self.verification is not None:
            _log_verification(self.verification)
        if self.delta is not None:
            log.info(
                "Delta %s -> %s: %d files (%s), %d deletions",
                self.delta.base_version or "(full)",
                self.delta.new_version,
                self.delta.changed,
                human_size(self.delta.bytes),
                self.delta.deleted,
            )
        log.info("Took %ds; report: %s", self.duration_s, report_path)
        if errors_log.exists():
            log.info("Warnings and errors: %s", errors_log)

    def _log_top(self) -> None:
        complete = sum(1 for s in self.top.values() if not s.stale and not s.failed)
        unresolved = sum(1 for s in self.top.values() if not s.ok and not s.stale)
        log.info(
            "Top projects resolved on every Python: %d/%d, partially: %d, not at all: %d",
            complete,
            len(self.top),
            len(self.top) - complete - unresolved,
            unresolved,
        )


def _log_verification(summary: VerificationSummary) -> None:
    installs = summary.installs
    log.info(
        "Install checks: %d from wheels, %d need a build, %d unsupported upstream too, %d MISSING",
        installs.get(InstallOutcome.WHEELS, 0),
        installs.get(InstallOutcome.BUILD, 0),
        installs.get(InstallOutcome.UNAVAILABLE, 0),
        len(summary.missing),
    )
    builds = summary.builds
    if builds:
        log.info(
            "Build checks: %d ok, %d failed for non-mirror reasons, %d timed out, %d MISSING build deps",
            builds.get(BuildOutcome.OK, 0),
            builds.get(BuildOutcome.FAILED, 0),
            builds.get(BuildOutcome.TIMEOUT, 0),
            len(summary.missing_build_deps),
        )
    if summary.heal_rounds:
        log.info("Healing rounds used: %d", summary.heal_rounds)
    for problem in [*summary.missing, *summary.missing_build_deps][:_LISTED_PROBLEMS]:
        log.error("still missing: %s", problem)
    log.info("Mirror is %s", "COMPLETE" if summary.complete else "INCOMPLETE (see above)")


def log_plan(plan: MirrorPlan, root: Path, previous: Manifest) -> None:
    """Describe what a sync would download and delete, without doing it."""
    missing = [f for f in plan.files.values() if not is_present(root, f)]
    removed = [p for p in previous if p not in plan.files]
    log.info("Plan: mirror would hold %d files (%s)", len(plan.files), human_size(plan.size_of(plan.files)))
    log.info(
        "Plan: download %d files (%s), delete about %d (build requirements are not counted in plan mode)",
        len(missing),
        human_size(sum(f.size or 0 for f in missing)),
        len(removed),
    )
    # Files shared by many requirements are counted once here: this is where the space goes.
    by_project: Counter[str] = Counter()
    versions: dict[str, set[str]] = {}
    for file in plan.files.values():
        project = file.project or "?"
        by_project[project] += file.size or 0
        versions.setdefault(project, set()).add(file.path.rsplit("/", 1)[1].split("-")[1])
    log.info("Largest projects (all mirrored versions, each file counted once):")
    for project, size in by_project.most_common(_PLAN_TOP_HEAVIEST):
        log.info("  %10s  %s (%d versions)", human_size(size), project, len(versions[project]))
    heaviest = sorted(
        ((plan.size_of(paths), req) for req, paths in plan.by_requirement.items()), reverse=True
    )
    log.info("Heaviest requirements, including dependencies (shared files counted for each):")
    for size, requirement in heaviest[:_PLAN_TOP_HEAVIEST]:
        log.info("  %10s  %s", human_size(size), requirement)
