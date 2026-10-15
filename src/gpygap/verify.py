# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Proof that the published mirror is complete, before it leaves the online side.

Install check
    For every requirement, Python version and platform, uv resolves against the
    mirror's ``file://`` index, offline, with an empty cache: exactly what a
    developer's uv does, minus the network. ``--python-platform`` makes uv pick
    distributions for the *target* platform, so Windows installs are checked
    from Linux. First with ``--no-build`` (wheels only); if that fails, with
    builds allowed; if that fails too, the same request is tried upstream to
    tell a gap in the mirror from a project that does not support the target.

Build check
    Every sdist some target must build is built offline against the mirror.
    This catches build requirements that cannot be read statically (requested
    by the build backend at build time). Builds run inside the container, which
    has no compilers on purpose: dependency resolution happens before
    compilation, so a later compiler error is not a mirror problem and is fast.

Both checks report what is missing so ``sync`` can fetch it and check again.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from gpygap.config import Config
from gpygap.console import progress
from gpygap.models import FREE_THREADED_SUFFIX
from gpygap.platforms import Platform
from gpygap.util import parallel_map

log = logging.getLogger(__name__)

_ERROR_TAIL = 2000
_TIMED_OUT = "timed out"
# uv: "No solution found when resolving: `setuptools>=61`, `cython<3`"
_UNRESOLVED_LINE = re.compile(r"No solution found when resolving: (.+)")
_BACKTICKED = re.compile(r"`([^`]+)`")
_RESOLUTION_FAILURE = ("Failed to resolve requirements", "not found in the package registry")


class InstallOutcome(StrEnum):
    WHEELS = "wheels"
    """Installs from wheels only."""
    BUILD = "build"
    """Needs a build from sdist, as it would upstream; the build check covers it."""
    MISSING = "missing"
    """Fails from the mirror but works upstream: the mirror lacks something."""
    UNAVAILABLE = "unavailable"
    """Fails upstream too: the project does not support this target."""


@dataclass(frozen=True, slots=True)
class InstallCheck:
    requirement: str
    python: str
    platform: Platform
    no_deps: bool = False

    def __str__(self) -> str:
        return f"{self.requirement} @ py{self.python} {self.platform.key}"


@dataclass(frozen=True, slots=True)
class InstallResult:
    check: InstallCheck
    outcome: InstallOutcome
    error: str = ""


class BuildOutcome(StrEnum):
    OK = "ok"
    MISSING_DEPS = "missing-deps"
    """A build requirement cannot be resolved from the mirror."""
    FAILED = "failed"
    """Failed after resolving its requirements (compiler, Python version…): not a mirror problem."""
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class BuildResult:
    sdist: str
    outcome: BuildOutcome
    missing: tuple[str, ...] = ()
    """Requirement strings uv could not resolve from the mirror."""
    error: str = ""


def unresolved_requirements(error: str) -> tuple[str, ...]:
    """Requirement strings from uv's ``No solution found when resolving: `a`, `b``` line."""
    requirements: list[str] = []
    for line in _UNRESOLVED_LINE.findall(error):
        requirements += _BACKTICKED.findall(line)
    return tuple(dict.fromkeys(requirements))


class Verifier:
    def __init__(self, config: Config) -> None:
        self._config = config
        self._index_url = (config.output / "simple").resolve().as_uri()
        self._cache = config.state.verify_cache

    def reset_cache(self) -> None:
        """Start from an empty cache so nothing fetched earlier can mask a gap."""
        shutil.rmtree(self._cache, ignore_errors=True)

    def check_installs(self, checks: Sequence[InstallCheck]) -> list[InstallResult]:
        results: list[InstallResult] = []
        with progress(len(checks), "Checking installs offline", unit="check") as bar:
            for _, result in parallel_map(self._check_install, checks, self._config.jobs):
                results.append(result)
                bar.update()
                if result.outcome is InstallOutcome.MISSING:
                    log.warning("install %s: missing from mirror", result.check)
                    log.debug("install %s offline error:\n%s", result.check, result.error)
        return results

    def check_builds(self, sdists: Sequence[str]) -> list[BuildResult]:
        self._config.state.tmp.mkdir(parents=True, exist_ok=True)
        results: list[BuildResult] = []
        with progress(len(sdists), "Building sdists offline", unit="sdist") as bar:
            for _, result in parallel_map(self._check_build, sdists, self._config.jobs):
                results.append(result)
                bar.update()
                if result.outcome is BuildOutcome.MISSING_DEPS:
                    missing = ", ".join(result.missing) or "see debug log"
                    log.warning("build %s: missing build requirements: %s", result.sdist, missing)
                elif result.outcome is not BuildOutcome.OK:
                    log.debug("build %s: %s\n%s", result.sdist, result.outcome, result.error)
        return results

    def _check_install(self, check: InstallCheck) -> InstallResult:
        error = self._compile(check, offline=True, wheels_only=True)
        if error is None:
            return InstallResult(check, InstallOutcome.WHEELS)
        if self._compile(check, offline=True, wheels_only=False) is None:
            return InstallResult(check, InstallOutcome.BUILD)
        if self._compile(check, offline=False, wheels_only=False) is None:
            return InstallResult(check, InstallOutcome.MISSING, error)
        return InstallResult(check, InstallOutcome.UNAVAILABLE, error)

    def _compile(self, check: InstallCheck, *, offline: bool, wheels_only: bool) -> str | None:
        """Return ``None`` if the requirement resolves, else uv's error output."""
        command = [self._config.uv_bin, "pip", "compile", "-", "--no-header", "--quiet", "--no-config"]
        version = check.python.removesuffix(FREE_THREADED_SUFFIX)
        command += ["--python-version", version, "--python-platform", check.platform.uv_target]
        command += ["--no-deps"] if check.no_deps else []
        command += ["--no-build"] if wheels_only else []
        if offline:
            command += ["--offline", "--default-index", self._index_url]
            env = self._config.uv_env(cache_dir=self._cache)
        else:
            command += ["--default-index", self._config.index_url]
            env = self._config.uv_env()
        return _run(command, env, check.requirement + "\n", self._config.resolve_timeout)

    def _check_build(self, sdist: str) -> BuildResult:
        with tempfile.TemporaryDirectory(dir=self._config.state.tmp) as out_dir:
            command = [
                *(self._config.uv_bin, "build", "--wheel", "--no-config", "--offline"),
                *("--default-index", self._index_url, "--out-dir", out_dir),
                str(self._config.output / sdist),
            ]
            env = self._config.uv_env(cache_dir=self._cache)
            error = _run(command, env, None, self._config.build_timeout)
        if error is None:
            return BuildResult(sdist, BuildOutcome.OK)
        if error == _TIMED_OUT:
            return BuildResult(sdist, BuildOutcome.TIMEOUT)
        if any(marker in error for marker in _RESOLUTION_FAILURE):
            return BuildResult(sdist, BuildOutcome.MISSING_DEPS, unresolved_requirements(error), error)
        return BuildResult(sdist, BuildOutcome.FAILED, error=error)


def _run(command: list[str], env: dict[str, str], stdin: str | None, timeout: int) -> str | None:
    """Run uv; return ``None`` on success, else the tail of its error output."""
    try:
        proc = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _TIMED_OUT
    return None if proc.returncode == 0 else (proc.stderr or proc.stdout).strip()[-_ERROR_TAIL:]
