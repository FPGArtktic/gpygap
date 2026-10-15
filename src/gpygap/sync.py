# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""One synchronization run, from the top list to a verified, published mirror.

Steps, in order:

1. load the top list and the teams' intake (lock files, requirements files);
2. resolve and download the requirements, their extras, and the build
   requirements of sdists that some target must build from source;
3. download uv and Python builds;
4. record what this run requires and prune what no recent run requires;
5. publish: simple index, landing page, version files;
6. verify like a developer would, offline: install checks for every
   requirement x Python x platform, and offline builds of needed sdists;
7. heal: whatever the checks found missing is resolved for exactly the failing
   target, downloaded, published and checked again (``sync.heal_rounds``);
8. write the list of verified checks and the checksums of every file;
9. optionally export a delta for the air-gapped side.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gpygap import offline
from gpygap.advisories import run_scan
from gpygap.checksums import write_checksums
from gpygap.config import Config
from gpygap.console import setup_logging
from gpygap.distmeta import DistMetaCache
from gpygap.downloader import DownloadResult, download_missing
from gpygap.extras import extra_requirements
from gpygap.fetcher import Fetcher
from gpygap.index import write_advisory_page, write_landing_page, write_simple_index
from gpygap.intake import Intake, load_intake
from gpygap.manifest import Manifest, RunHistory, load_manifest, save_manifest
from gpygap.models import MirrorFile, MirrorInfo
from gpygap.prune import PruneResult, prune_tree
from gpygap.report import RunReport, VerificationSummary, log_plan, top_statuses
from gpygap.resolver import Resolution, ResolveRequest, universal_requests
from gpygap.tools import plan_tools, uv_version
from gpygap.toplist import TopList, load_top_list
from gpygap.util import read_json, write_if_changed, write_json
from gpygap.verify import (
    BuildOutcome,
    BuildResult,
    InstallCheck,
    InstallOutcome,
    InstallResult,
    Verifier,
)
from gpygap.wheelbuild import BuildGap, BuildSummary, WheelFactory

log = logging.getLogger(__name__)

# Build backends, and what they install on demand, mirrored unconditionally:
# any sdist a developer builds offline will need one of them.
SEED_BUILD_REQUIREMENTS = (
    "setuptools",
    "wheel",
    "hatchling",
    "hatch-vcs",
    "flit-core",
    "poetry-core",
    "pdm-backend",
    "setuptools-scm",
    "scikit-build-core",
    "meson-python",
    "maturin",
    "cython",
    "cmake",
    "ninja",
    "pybind11",
)


@dataclass(frozen=True, slots=True)
class SyncOptions:
    """Options of a single run, as opposed to the mirror's configuration."""

    plan_only: bool = False
    force_prune: bool = False
    export_delta: Path | None = None
    full_export: bool = False
    verbose: bool = False


@dataclass(frozen=True, slots=True)
class _Scope:
    """What this run must make installable."""

    top: TopList
    intake: Intake
    extras: tuple[str, ...]
    seeds: tuple[str, ...]
    extra_requirements: tuple[str, ...]

    @property
    def requirements(self) -> list[str]:
        """Requirements resolved with their dependencies."""
        groups = (
            self.top.projects,
            self.extra_requirements,
            self.intake.requirements,
            self.extras,
            self.seeds,
        )
        return list(dict.fromkeys(r for group in groups for r in group))

    @property
    def checked(self) -> set[str]:
        """Everything the install checks cover, pins included."""
        return {*self.requirements, *self.intake.pins}


@dataclass(slots=True)
class _Verification:
    installs: dict[InstallCheck, InstallResult] = field(default_factory=dict)
    builds: dict[str, BuildResult] = field(default_factory=dict)
    build_fixes: dict[ResolveRequest, str] = field(default_factory=dict)
    """Build requirements fetched because of a build check, mapped to the sdist that needed them."""
    heal_rounds: int = 0


def run_sync(config: Config, options: SyncOptions) -> int:
    """Run one synchronization; return the process exit code."""
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    config.output.mkdir(parents=True, exist_ok=True)
    errors_log = setup_logging(config.state.logs, run_id, verbose=options.verbose)
    started = time.monotonic()
    _log_header(config, run_id)

    meta = DistMetaCache(config.output, config.state.dist_meta)
    fetcher = Fetcher(config, meta, download=not options.plan_only)
    scope = _fetch_scope(config, fetcher, meta)
    if options.plan_only:
        log_plan(fetcher.plan, config.output, load_manifest(config.state.manifest))
        return 0

    tools = _fetch_tools(config, fetcher.downloads)
    # Nothing is pruned yet. The wheels the last run built are not in the plan -- only
    # the builder knows about them, and it has not run -- so pruning here would delete
    # the very files the build cache is about to reuse.
    manifest, _ = _commit(config, run_id, fetcher, tools, force_prune=False, prune=False)
    info = _publish(config, run_id, scope, manifest, meta)

    # Wheels are built against the published index, so this cannot run earlier; what
    # it produces then has to be committed and published again to reach the index.
    factory = WheelFactory(config)
    _build_wheels(config, factory, fetcher)
    manifest, pruned = _commit(config, run_id, fetcher, tools, factory.files, force_prune=options.force_prune)
    info = _publish(config, run_id, scope, manifest, meta, built=factory.files, gaps=factory.gaps)

    verification = _verify_and_heal(
        config, run_id, scope, fetcher=fetcher, meta=meta, tools=tools, factory=factory
    )
    if verification.heal_rounds:
        manifest, _ = _commit(config, run_id, fetcher, tools, factory.files, force_prune=False)
        info = _publish(config, run_id, scope, manifest, meta, built=factory.files, gaps=factory.gaps)
    advisories = run_scan(config, manifest)
    if advisories is not None:
        write_json(config.output / offline.ADVISORIES_FILE, advisories.to_json())
        write_advisory_page(config.output, info, advisories)
        # The landing page links the report only once it exists.
        write_landing_page(config.output, info, _uv_archives(manifest))
    _save_fixes(config, fetcher, scope, verification)
    _write_verified(config, verification, scope)
    shutil.copy2(offline.__file__, config.output / offline.SCRIPT_NAME)
    write_checksums(config.output, manifest, config.state.checksums)
    delta = _export(config, options)

    report = RunReport(
        run_id=run_id,
        duration_s=round(time.monotonic() - started),
        info=info,
        downloads=fetcher.downloads,
        pruned=pruned,
        top=top_statuses(fetcher.resolutions, scope.top.projects, config.python_versions),
        verification=_summarize(config, verification),
        delta=delta,
        advisories=advisories,
        advisories_fail_on=config.advisories_fail_on,
    )
    report_path = config.state.logs / f"{run_id}-report.json"
    report.write(report_path)
    report.log_summary(report_path, errors_log)
    return report.exit_code


def _log_header(config: Config, run_id: str) -> None:
    if shutil.which(config.uv_bin) is None:
        raise RuntimeError(f"uv not found ({config.uv_bin!r}); install it: https://docs.astral.sh/uv/")
    log.info("Run %s: mirror %s, state %s", run_id, config.output, config.state_dir)
    log.info("uv %s, UV_CACHE_DIR=%s", uv_version(config), config.uv_cache_dir)
    log.info("Python %s on %s", ", ".join(config.python_versions), ", ".join(p.key for p in config.platforms))


# --------------------------------------------------------------------------- scope


def _fetch_scope(config: Config, fetcher: Fetcher, meta: DistMetaCache) -> _Scope:
    """Resolve and download everything this run must make installable."""
    pythons = config.python_versions
    top = load_top_list(config.top_sources, config.state.top_list, config.top, config.exclude)
    intake = load_intake(config.requirements_dir)
    seeds = SEED_BUILD_REQUIREMENTS if config.build_deps_rounds > 0 else ()
    scope = _Scope(top, intake, (), seeds, config.extra_requirements)
    log.info(
        "Scope: %d top projects, %d extra requirements, %d intake requirements, %d pins, %d build backends",
        len(top.projects),
        len(config.extra_requirements),
        len(intake.requirements),
        len(intake.pins),
        len(seeds),
    )
    fetcher.fetch(universal_requests(scope.requirements, pythons), "Resolving")
    fetcher.fetch(universal_requests(intake.pins, pythons, no_deps=True), "Resolving intake pins")

    if config.extras:
        # Extras of everything requested on purpose; build backends are tools, not libraries.
        wanted = [*top.projects, *config.extra_requirements, *intake.requirements]
        extras = extra_requirements(wanted, fetcher.resolutions, meta, config.output, config.extras_exclude)
        log.info("Extras: %d, each resolved on its own", len(extras))
        # Some extras legitimately fail (unsupported Python, conflicting pins
        # upstream); the install checks report the ones that matter.
        fetcher.fetch(universal_requests(extras, pythons), "Resolving extras", failure_level=logging.DEBUG)
        scope = _Scope(top, intake, tuple(extras), seeds, config.extra_requirements)

    fixes = _load_fixes(config, scope, fetcher)
    if fixes:
        fetcher.fetch(fixes, "Resolving fixes found by earlier runs")
    return scope


# --------------------------------------------------------------------------- tools, commit, publish


def _fetch_tools(config: Config, downloads: DownloadResult) -> list[MirrorFile]:
    if not config.with_tools:
        return []
    try:
        tools = plan_tools(config)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        log.error("cannot list uv and Python downloads: %s", exc)
        return []
    downloads.merge(
        download_missing(
            config.output,
            tools,
            workers=config.download_jobs,
            attempts=config.retries + 2,
            label="Downloading uv and Python",
        )
    )
    return tools


def _commit(
    config: Config,
    run_id: str,
    fetcher: Fetcher,
    tools: Sequence[MirrorFile],
    built: Sequence[MirrorFile] = (),
    *,
    force_prune: bool,
    prune: bool = True,
) -> tuple[Manifest, PruneResult]:
    """Record this run's requirements, prune, and return the manifest of what remains.

    Locally built wheels are passed in separately because the plan is rebuilt from the
    resolutions on every round and would drop them. Listing them here is what puts them
    in the manifest, in the simple index and in the run history -- and therefore what
    stops the prune from deleting a wheel nothing can re-download.
    """
    required = [*fetcher.plan.files.values(), *tools, *built]
    present = {f.path: f for f in required if (config.output / f.path).exists()}
    history = RunHistory(config.state.runs)
    history.record(run_id, present)
    pruned = _prune(config, history, fetcher.downloads, force=force_prune) if prune else PruneResult()
    candidates = load_manifest(config.state.manifest) | present
    manifest = {path: f for path, f in candidates.items() if (config.output / path).exists()}
    save_manifest(config.state.manifest, manifest)
    history.trim(config.keep_runs)
    return manifest, pruned


def _build_wheels(config: Config, factory: WheelFactory, fetcher: Fetcher) -> BuildSummary:
    """Build a wheel for every target an sdist has no upstream wheel for."""
    hashes = {
        path: file.sha256
        for path, file in fetcher.plan.files.items()
        if file.sha256 and path in fetcher.plan.build_sdists
    }
    # Wheels built on another machine first: a Windows host or CI runner can cover
    # what no container here can, and an adopted wheel means one less build to run.
    factory.ingest(fetcher.plan.build_sdists)
    summary = factory.run(fetcher.plan.build_sdists, hashes)
    if summary.built or summary.gaps:
        log.info(
            "Built %d wheels (%d reused, %d builds run), %d targets left without one",
            len(summary.built),
            summary.cached,
            summary.attempted,
            len(summary.gaps),
        )
    for gap in summary.unbuildable:
        # Not a failure: no container on a Linux host builds a Windows or macOS wheel.
        log.debug("no builder for %s (%s)", gap.target, gap.sdist)
    return summary


def _prune(config: Config, history: RunHistory, downloads: DownloadResult, *, force: bool) -> PruneResult:
    if not config.prune:
        log.info("Pruning disabled (sync.prune = false)")
        return PruneResult()
    if downloads.failed:
        # A failed download of a new version must not take the old one away.
        log.warning("Pruning skipped: %d downloads failed, previous files stay", len(downloads.failed))
        return PruneResult()
    subdirs = ["packages", "tools", "python"] if config.with_tools else ["packages"]
    keep = history.retained(config.keep_runs)
    return prune_tree(config.output, keep, subdirs, max_ratio=config.max_prune_ratio, force=force)


def _publish(
    config: Config,
    run_id: str,
    scope: _Scope,
    manifest: Manifest,
    meta: DistMetaCache,
    *,
    built: Sequence[MirrorFile] = (),
    gaps: Sequence[BuildGap] = (),
) -> MirrorInfo:
    projects = write_simple_index(config.output, manifest, meta)
    meta.save(manifest)
    info = MirrorInfo(
        version=run_id,
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        top_last_update=scope.top.last_update,
        top_projects=scope.top.projects,
        extra_requirements=config.extra_requirements,
        python_versions=config.python_versions,
        platforms=tuple(p.key for p in config.platforms),
        projects=projects,
        files=len(manifest),
        bytes=sum((config.output / path).stat().st_size for path in manifest),
        # Published so the offline side can tell at a glance which wheels did not come
        # from PyPI: they were compiled here, from the sdist next to them.
        built_wheels=tuple(sorted(f.path for f in built if f.path in manifest)),
    )
    write_json(config.output / "MIRROR-INFO.json", info.to_json())
    write_json(config.output / offline.GAPS_FILE, _gaps_document(gaps))
    write_landing_page(config.output, info, _uv_archives(manifest))
    write_if_changed(config.output / offline.VERSION_FILE, run_id + "\n")
    return info


def _gaps_document(gaps: Sequence[BuildGap]) -> dict[str, Any]:
    """What still has no wheel, in a form another machine can act on.

    A Windows builder reads this, runs ``pip wheel`` for each entry and drops the
    result where ``build.external_dir`` points; the next run folds it in.
    """
    return {
        "version": 1,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "gaps": [
            {
                "project": gap.sdist.split("/")[1],
                "sdist": gap.sdist,
                "platform": gap.target.platform,
                "python": gap.target.python,
                "reason": gap.reason,
            }
            for gap in gaps
        ],
    }


def _uv_archives(manifest: Mapping[str, MirrorFile]) -> list[str]:
    return sorted(p for p in manifest if p.startswith("tools/uv/") and not p.endswith(".sha256"))


# --------------------------------------------------------------------------- verify and heal


def _verify_and_heal(
    config: Config,
    run_id: str,
    scope: _Scope,
    *,
    fetcher: Fetcher,
    meta: DistMetaCache,
    tools: Sequence[MirrorFile],
    factory: WheelFactory,
) -> _Verification:
    """Check the published mirror; fetch what is missing and check again."""
    result = _Verification()
    if not config.verify:
        return result
    verifier = Verifier(config)
    checks = _install_checks(config, fetcher.resolutions, scope)
    sdists = _sdists_to_build(config, fetcher, done=set())
    for heal_round in range(config.heal_rounds + 1):
        verifier.reset_cache()
        result.installs |= {r.check: r for r in verifier.check_installs(checks)}
        result.builds |= {r.sdist: r for r in verifier.check_builds(sdists)}
        heal = [r for r in _heal_requests(config, result) if r not in fetcher.resolutions]
        if not heal or heal_round == config.heal_rounds:
            break
        log.info(
            "Healing round %d: resolving %d requests the checks found missing", heal_round + 1, len(heal)
        )
        fetcher.fetch(heal, "Resolving what the checks found missing")
        result.heal_rounds += 1
        # Healing can pull in sdists nobody has a wheel for; build those too, or the
        # next round would report them missing with nothing having tried to fix it.
        _build_wheels(config, factory, fetcher)
        manifest, _ = _commit(config, run_id, fetcher, tools, factory.files, force_prune=False)
        _publish(config, run_id, scope, manifest, meta, built=factory.files, gaps=factory.gaps)
        checks = [c for c, r in result.installs.items() if r.outcome is InstallOutcome.MISSING]
        sdists = [s for s, r in result.builds.items() if r.outcome is BuildOutcome.MISSING_DEPS]
        sdists += _sdists_to_build(config, fetcher, done=set(result.builds))
    return result


def _install_checks(
    config: Config, resolutions: Mapping[ResolveRequest, Resolution], scope: _Scope
) -> list[InstallCheck]:
    """One check per universal request of the scope and per target platform.

    Requests that failed to resolve universally are checked too: a project may
    still install on one platform, and the check will then heal it.
    """
    wanted = scope.checked
    return [
        InstallCheck(request.requirement, request.python, platform, request.no_deps)
        for request in resolutions
        if request.platform is None and request.requirement in wanted
        for platform in config.platforms
    ]


def _sdists_to_build(config: Config, fetcher: Fetcher, *, done: set[str]) -> list[str]:
    if not config.build_check:
        return []
    return sorted(s for s in set(fetcher.plan.build_sdists) - done if (config.output / s).exists())


def _heal_requests(config: Config, verification: _Verification) -> list[ResolveRequest]:
    requests = [
        ResolveRequest(r.check.requirement, r.check.python, r.check.platform, r.check.no_deps)
        for r in verification.installs.values()
        if r.outcome is InstallOutcome.MISSING
    ]
    for build in verification.builds.values():
        if build.outcome is BuildOutcome.MISSING_DEPS:
            for request in universal_requests(build.missing, config.python_versions):
                verification.build_fixes.setdefault(request, build.sdist)
                requests.append(request)
    return requests


# --------------------------------------------------------------------------- persisted fixes


def _load_fixes(config: Config, scope: _Scope, fetcher: Fetcher) -> list[ResolveRequest]:
    """Fixes found by earlier runs that still apply, so they are not pruned and healed again.

    A platform-specific fix stays while its requirement is in scope; a build
    requirement fix stays while the sdist that needed it is still built.
    """
    requests = []
    for entry in read_json(config.state.targeted) or []:
        request = ResolveRequest.from_json(entry["request"])
        origin = entry.get("origin")
        if (request.platform is not None and request.requirement in scope.checked) or (
            origin in fetcher.plan.build_sdists
        ):
            requests.append(request)
    return requests


def _save_fixes(config: Config, fetcher: Fetcher, scope: _Scope, verification: _Verification) -> None:
    entries = [
        {"request": request.to_json(), "origin": request.requirement}
        for request, resolution in fetcher.resolutions.items()
        if request.platform is not None and request.requirement in scope.checked and resolution.packages
    ]
    entries += [
        {"request": request.to_json(), "origin": sdist}
        for request, sdist in verification.build_fixes.items()
        if fetcher.resolutions.get(request) is not None and fetcher.resolutions[request].packages
    ]
    write_json(config.state.targeted, entries)


def _write_verified(config: Config, verification: _Verification, scope: _Scope) -> None:
    """List the checks that passed, for the offline smoke test to repeat over HTTP."""
    top = set(scope.top.projects)
    entries = [
        {
            "requirement": check.requirement,
            "python": check.python,
            "platform": check.platform.key,
            "uv_target": check.platform.uv_target,
            "no_deps": check.no_deps,
            "outcome": result.outcome.value,
            "top": check.requirement in top,
        }
        for check, result in sorted(verification.installs.items(), key=lambda item: str(item[0]))
        if result.outcome in (InstallOutcome.WHEELS, InstallOutcome.BUILD)
    ]
    write_json(config.output / offline.VERIFIED_FILE, entries)


def _summarize(config: Config, verification: _Verification) -> VerificationSummary | None:
    if not config.verify:
        return None
    return VerificationSummary.from_results(
        verification.installs.values(), verification.builds.values(), verification.heal_rounds
    )


# --------------------------------------------------------------------------- export


def _export(config: Config, options: SyncOptions) -> offline.DeltaSummary | None:
    if options.export_delta is None:
        return None
    return offline.export_delta(
        config.output, options.export_delta.resolve(), config.state.export_snapshot, full=options.full_export
    )
