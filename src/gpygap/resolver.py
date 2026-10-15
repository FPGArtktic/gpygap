# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dependency resolution with ``uv pip compile``.

Each request (one requirement, one Python version) is resolved on its own.
Resolving all projects together would fail on the first version conflict
between two of them; resolving them separately lets the mirror simply hold
every version that some project needs.

Most requests are *universal* (``--universal``): one resolution covering every
platform. A request can instead target one platform (``--python-platform``);
the healing step uses that to fetch exactly what a developer on that platform
would get when the universal resolution was not enough.

Results are cached in the state directory:

* a fresh result is reused for ``max_age_hours``, so an interrupted run resumes
  without resolving everything again;
* when resolution fails, the last successful result is kept ("stale"), so a
  temporary outage never makes files look unneeded and get pruned.
"""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
import tempfile
import time
import tomllib
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gpygap.config import Config
from gpygap.console import progress
from gpygap.models import FREE_THREADED_SUFFIX, DistFile, DistKind, LockedPackage
from gpygap.platforms import PLATFORMS, Platform
from gpygap.util import normalize_name, parallel_map, read_json, write_json

log = logging.getLogger(__name__)

# Errors that will not go away by retrying.
_PERMANENT_ERRORS = (
    "No solution found",
    "unsatisfiable",
    "not found in the package registry",
    "Failed to parse",
)
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+!~-]*")


class ResolveError(Exception):
    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(frozen=True, slots=True)
class ResolveRequest:
    """One requirement (or several, newline-separated) to resolve for one Python version."""

    requirement: str
    python: str
    platform: Platform | None = None
    """``None`` resolves for every platform at once (universal)."""
    no_deps: bool = False
    """Resolve only the requirement itself, e.g. a pin from a complete lock file."""

    def __str__(self) -> str:
        target = f" {self.platform.key}" if self.platform else ""
        return f"{self.requirement} @ py{self.python}{target}"

    def to_json(self) -> dict[str, Any]:
        return {
            "requirement": self.requirement,
            "python": self.python,
            "platform": self.platform.key if self.platform else None,
            "no_deps": self.no_deps,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> ResolveRequest:
        platform = PLATFORMS[data["platform"]] if data.get("platform") else None
        return cls(data["requirement"], data["python"], platform, bool(data.get("no_deps")))


def universal_requests(
    requirements: Iterable[str], pythons: Sequence[str], *, no_deps: bool = False
) -> list[ResolveRequest]:
    return [ResolveRequest(req, py, no_deps=no_deps) for req in requirements for py in pythons]


@dataclass(frozen=True, slots=True)
class Resolution:
    """Outcome of one request.

    On failure ``packages`` holds the last successful result, if there was one.
    """

    request: ResolveRequest
    packages: tuple[LockedPackage, ...]
    resolved_at: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def stale(self) -> bool:
        return self.error is not None and bool(self.packages)

    def to_json(self) -> dict[str, Any]:
        return {
            "request": self.request.to_json(),
            "resolved_at": self.resolved_at,
            "error": self.error,
            "packages": [p.to_json() for p in self.packages],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Resolution:
        return cls(
            request=ResolveRequest.from_json(data["request"]),
            packages=tuple(LockedPackage.from_json(p) for p in data["packages"]),
            resolved_at=data["resolved_at"],
            error=data.get("error"),
        )


def parse_pylock(text: str) -> list[LockedPackage]:
    """Parse a PEP 751 ``pylock.toml`` produced by uv."""
    document = tomllib.loads(text)
    return [_parse_package(entry) for entry in document.get("packages", [])]


def _parse_package(entry: dict[str, Any]) -> LockedPackage:
    files: list[DistFile | None] = []
    if entry.get("sdist"):
        files.append(_parse_file("sdist", entry["sdist"]))
    files.extend(_parse_file("wheel", wheel) for wheel in entry.get("wheels", []))
    return LockedPackage(
        name=normalize_name(entry["name"]),
        version=str(entry.get("version", "")),
        marker=entry.get("marker"),
        files=tuple(f for f in files if f is not None),
    )


def _parse_file(kind: DistKind, entry: dict[str, Any]) -> DistFile | None:
    url = entry.get("url")
    if not url:
        return None  # a local or VCS source; nothing to mirror
    filename = urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    # The file name becomes a path on disk, so never trust it blindly.
    if not _is_safe_filename(filename):
        log.warning("Skipping file with suspicious name %r", filename)
        return None
    return DistFile(
        kind=kind,
        url=url,
        filename=filename,
        size=entry.get("size"),
        sha256=entry.get("hashes", {}).get("sha256"),
    )


def _is_safe_filename(filename: str) -> bool:
    return _SAFE_FILENAME.fullmatch(filename) is not None and ".." not in filename


class Resolver:
    """Resolves requests through uv, with caching and retries."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._cache_dir = config.state.resolutions

    def resolve_all(
        self, requests: Sequence[ResolveRequest], label: str, *, failure_level: int = logging.WARNING
    ) -> dict[ResolveRequest, Resolution]:
        """Resolve requests in parallel; failures are logged at ``failure_level``."""
        results: dict[ResolveRequest, Resolution] = {}
        failures = 0
        with progress(len(requests), label, unit="job") as bar:
            for request, resolution in parallel_map(self.resolve, requests, self._config.jobs):
                results[request] = resolution
                if not resolution.ok:
                    failures += 1
                    _log_failure(resolution, failure_level)
                bar.update()
                bar.set_postfix(failed=failures)
        return results

    def resolve(self, request: ResolveRequest) -> Resolution:
        """Resolve one request, reusing a fresh cached result when available."""
        cached = self._load(request)
        if cached is not None and cached.ok and self._is_fresh(cached):
            return cached
        try:
            packages = self._compile_with_retries(request)
        except ResolveError as exc:
            previous = cached.packages if cached is not None else ()
            resolution = Resolution(request, previous, time.time(), error=str(exc))
        else:
            resolution = Resolution(request, tuple(packages), time.time())
        self._store(resolution)
        return resolution

    def _is_fresh(self, resolution: Resolution) -> bool:
        return time.time() - resolution.resolved_at < self._config.max_age_hours * 3600

    def _compile_with_retries(self, request: ResolveRequest) -> list[LockedPackage]:
        for attempt in range(1, self._config.retries + 1):
            try:
                return self._compile(request)
            except ResolveError as exc:
                if exc.permanent or attempt == self._config.retries:
                    raise
                time.sleep(3 * attempt)
        raise AssertionError("unreachable")

    def _compile(self, request: ResolveRequest) -> list[LockedPackage]:
        self._config.state.tmp.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._config.state.tmp) as tmp:
            lock_file = Path(tmp) / "pylock.toml"
            try:
                proc = subprocess.run(
                    self._compile_command(request, lock_file),
                    input=request.requirement + "\n",
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=self._config.uv_env(),
                    timeout=self._config.resolve_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ResolveError(f"timed out after {self._config.resolve_timeout}s") from exc
            if proc.returncode != 0 or not lock_file.exists():
                message = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
                raise ResolveError(message, permanent=any(e in message for e in _PERMANENT_ERRORS))
            return parse_pylock(lock_file.read_text("utf-8"))

    def _compile_command(self, request: ResolveRequest, lock_file: Path) -> list[str]:
        # --no-config: the operator's own uv.toml (e.g. pointing at another
        # index) must not change what the mirror contains.
        target = ["--python-platform", request.platform.uv_target] if request.platform else ["--universal"]
        return [
            *(self._config.uv_bin, "pip", "compile", "-"),
            *target,
            *("--python-version", request.python.removesuffix(FREE_THREADED_SUFFIX)),
            *(["--no-deps"] if request.no_deps else []),
            *("--format", "pylock.toml"),
            *("--no-header", "--quiet", "--no-config"),
            *("--default-index", self._config.index_url),
            *("--output-file", str(lock_file)),
        ]

    def _cache_path(self, request: ResolveRequest) -> Path:
        digest = hashlib.sha1(request.requirement.encode()).hexdigest()[:10]
        readable = re.sub(r"[^A-Za-z0-9._-]+", "_", request.requirement)[:60]
        target = request.platform.key if request.platform else "universal"
        suffix = "-nodeps" if request.no_deps else ""
        return self._cache_dir / f"py{request.python}" / target / f"{readable}-{digest}{suffix}.json"

    def _load(self, request: ResolveRequest) -> Resolution | None:
        data = read_json(self._cache_path(request))
        if data is None:
            return None
        try:
            return Resolution.from_json(data)
        except (KeyError, TypeError):
            return None  # written by an older version; resolve again

    def _store(self, resolution: Resolution) -> None:
        write_json(self._cache_path(resolution.request), resolution.to_json())


def _log_failure(resolution: Resolution, level: int) -> None:
    error = resolution.error or ""
    last_line = error.strip().splitlines()[-1] if error.strip() else "unknown error"
    suffix = " (keeping the previous resolution)" if resolution.stale else ""
    log.log(level, "resolve %s: %s%s", resolution.request, last_line, suffix)
    log.debug("resolve %s full error:\n%s", resolution.request, error)
