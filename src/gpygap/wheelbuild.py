# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Building the wheels upstream never published.

A package with no wheel for a target used to mean the developer compiled it at
install time, on a machine that then needed a compiler. In an air-gapped network
that is the difference between ``pip install`` working and a ticket, so the
online side builds the wheel instead, while it still has a toolchain and the
mirror to resolve build requirements from.

Builds run in a **sibling** container, launched through the container engine the
``bin/gpygap`` wrapper exposes, not inside the mirror's own image: the pypa
manylinux and musllinux images carry the right libc floor, every CPython the
mirror targets, and ``auditwheel``, none of which belong in an image whose job is
to never have a compiler. The sibling gets ``--network none`` and sees only the
mirror (read-only) and one output directory.

What cannot be built is said out loud rather than papered over. A Linux kernel
does not run the Windows or macOS toolchains, and setuptools has no notion of a
cross build, so those targets keep whatever upstream published. The one thing
that rescues them is that a pure-Python sdist builds to ``py3-none-any``, which
installs everywhere -- and most projects shipping no wheel at all are pure
Python.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packaging.utils import InvalidWheelFilename, parse_wheel_filename

from gpygap.config import Config
from gpygap.console import progress
from gpygap.models import BuildTarget, MirrorFile
from gpygap.platforms import PLATFORMS, Platform
from gpygap.selection import package_path, parse_python, wheel_compatible
from gpygap.util import normalize_name, parallel_map, read_json, write_json

log = logging.getLogger(__name__)

_CACHE_VERSION = 1
_ERROR_TAIL = 2000
_TIMED_OUT = "timed out"

# 1980-01-01, the earliest timestamp a zip entry can carry. Pinning it keeps two
# builds of the same sdist byte-identical, so a monthly resync that rebuilds
# nothing also rewrites nothing and the delta stays small.
_SOURCE_DATE_EPOCH = "315532800"

# $0 is a label, the rest are the arguments below. pip resolves build requirements
# from the mirror's own index with the network off; auditwheel then retags the
# result to the platform tag the mirror advertises. A wheel auditwheel refuses is a
# failed build, never a wheel quietly tagged linux_x86_64 that no client installs.
_BUILD_SCRIPT = """
set -euo pipefail
python_bin=$1; sdist=$2; index_url=$3; out=$4; plat_tag=$5
raw="$out/raw"
mkdir -p "$raw"
"$python_bin" -m pip wheel --no-deps --no-cache-dir --disable-pip-version-check \
    --wheel-dir "$raw" --index-url "$index_url" "$sdist"
wheel=$(find "$raw" -name '*.whl' -print -quit)
test -n "$wheel"
case "$wheel" in
    *-none-any.whl) mv "$wheel" "$out/" ;;
    *) auditwheel repair --plat "$plat_tag" --wheel-dir "$out" "$wheel" ;;
esac
# The builder is root inside its own container and the caller is not, so without
# this the caller cannot even delete the directory it created and handed over.
# Permissions rather than chown: the uid that owns these files is not necessarily
# mapped into the builder's user namespace, so chown is not portable here.
chmod -R a+rwX "$out"
"""


@dataclass(frozen=True, slots=True)
class BuiltWheel:
    """A wheel this mirror compiled, and the targets it turned out to satisfy."""

    path: str
    sha256: str
    size: int
    project: str
    sdist: str
    targets: tuple[BuildTarget, ...]

    @property
    def universal(self) -> bool:
        return self.path.endswith("-none-any.whl")

    def mirror_file(self) -> MirrorFile:
        return MirrorFile(
            path=self.path,
            url="",
            size=self.size,
            sha256=self.sha256,
            project=self.project,
            origin="built",
        )


@dataclass(frozen=True, slots=True)
class BuildGap:
    """A target left without a wheel, and why."""

    sdist: str
    target: BuildTarget
    reason: str
    detail: str = ""


@dataclass(slots=True)
class BuildSummary:
    built: list[BuiltWheel] = field(default_factory=list)
    gaps: list[BuildGap] = field(default_factory=list)
    cached: int = 0
    attempted: int = 0

    @property
    def unbuildable(self) -> list[BuildGap]:
        """Gaps on platforms no container on this machine can build for."""
        return [g for g in self.gaps if g.reason == "no-builder"]

    @property
    def failures(self) -> list[BuildGap]:
        return [g for g in self.gaps if g.reason != "no-builder"]

    def to_json(self) -> dict[str, Any]:
        return {
            "built": [w.path for w in self.built],
            "universal": [w.path for w in self.built if w.universal],
            "cached": self.cached,
            "attempted": self.attempted,
            "gaps": [{"sdist": g.sdist, "target": str(g.target), "reason": g.reason} for g in self.gaps],
        }


def python_executable(python: str) -> str:
    """Path of the CPython ``python`` interpreter inside a pypa builder image.

    ``3.13t`` is the free-threaded build, which the images ship as ``cp313-cp313t``.
    """
    base = python.removesuffix("t").replace(".", "")
    suffix = "t" if python.endswith("t") else ""
    return f"/opt/python/cp{base}-cp{base}{suffix}/bin/python"


def targets_satisfied(filename: str, candidates: Iterable[BuildTarget]) -> set[BuildTarget]:
    """Of ``candidates``, the targets a freshly built wheel actually installs on.

    One build can cover more than it was asked for: ``py3-none-any`` covers every
    platform and interpreter, which is what lets a Linux build close a Windows gap.
    """
    satisfied = set()
    for target in candidates:
        platform = PLATFORMS.get(target.platform)
        if platform is None:
            continue
        if wheel_compatible(filename, platform, parse_python(target.python.removesuffix("t"))):
            satisfied.add(target)
    return satisfied


def _cache_key(sdist_sha256: str, target: BuildTarget, image: str) -> str:
    raw = f"{sdist_sha256}|{target.platform}|{target.python}|{image}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _wheel_project(filename: str) -> str:
    """Normalized project name of a wheel file, or an empty string if unreadable."""
    try:
        return normalize_name(parse_wheel_filename(filename)[0])
    except (InvalidWheelFilename, ValueError):
        return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class WheelFactory:
    """Builds missing wheels and remembers, across runs, what it already tried.

    The cache key is the sdist hash, the target and the builder image, so a monthly
    resync rebuilds only what genuinely changed. Failures are cached too: a package
    that cannot be built does not get retried every month, and the operator sees the
    same named gap until something about the inputs changes.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, str]] = {}
        self._wheels: dict[str, BuiltWheel] = {}
        self._gaps: dict[tuple[str, BuildTarget], BuildGap] = {}
        self._load_cache()

    @property
    def gaps(self) -> list[BuildGap]:
        """Targets still without a wheel, across every round of this run.

        Published as ``MIRROR-GAPS.json`` so the offline side knows which packages
        a developer machine still has to compile -- and so a Windows builder knows
        exactly what to build.
        """
        # Per sdist, not globally: docopt's py3-none-any wheel covers win-amd64 for
        # docopt and for nothing else. Comparing targets alone made one universal
        # wheel erase every Windows gap in the mirror, and the file a Windows
        # builder reads came out empty while the run said four were missing.
        covered = {(wheel.sdist, t) for wheel in self._wheels.values() for t in wheel.targets}
        return sorted(
            (gap for key, gap in self._gaps.items() if key not in covered),
            key=lambda g: (g.sdist, g.target),
        )

    @property
    def files(self) -> list[MirrorFile]:
        """Every wheel built so far, as mirror files to commit and index."""
        return [w.mirror_file() for w in self._wheels.values()]

    @property
    def engine(self) -> str:
        """The client that matches the socket we were handed.

        ``auto`` resolves from the environment bin/gpygap sets up, because the
        operator configures *whether* to build, not which client happens to be on
        the other end of the socket. podman must be driven by its own client: a
        container created through its Docker-compatible API comes up unable to fork.
        """
        configured = self._config.build_engine
        if configured != "auto":
            return configured
        if os.environ.get("CONTAINER_HOST"):
            return "podman"
        return "docker"

    def host_path(self, path: Path) -> str:
        """Translate a path this process sees into one the container engine sees.

        On Linux the wrapper mounts the project directory at the same absolute path
        inside and out, so this is the identity and nothing needs configuring. It is
        not the identity when the engine lives on another kind of host: with Docker
        Desktop on Windows the mirror is ``/work/gpygap`` in here and ``C:\\gpygap``
        out there, and a sibling container is created by the engine, which only
        understands the second one.
        """
        container_root = self._config.build_container_root
        host_root = self._config.build_host_root
        if not container_root or not host_root:
            return str(path)
        text = str(path)
        if not text.startswith(container_root):
            return text
        relative = text[len(container_root) :].lstrip("/")
        separator = "\\" if "\\" in host_root else "/"
        return host_root.rstrip("/\\") + separator + relative.replace("/", separator)

    def image_for(self, platform: Platform) -> str | None:
        return self._config.build_images.get(platform.key) or platform.build_image

    # ---------------------------------------------------------------- ingest

    def ingest(self, build_sdists: Mapping[str, set[BuildTarget]]) -> list[BuiltWheel]:
        """Adopt wheels built somewhere this machine cannot build for.

        A Linux host cannot produce a Windows wheel, but a Windows machine can, and
        so can a Windows CI runner. Point ``build.external_dir`` at where those land
        and they are folded into the mirror like anything else built here: hashed,
        indexed, recorded with ``origin = "built"``, and counted against the targets
        they cover. This is the whole of the Windows story, and it needs no code on
        the Windows side beyond ``pip wheel``.
        """
        directory = self._config.build_external_dir
        if directory is None or not directory.is_dir():
            return []
        by_project: dict[str, tuple[str, set[BuildTarget]]] = {}
        for sdist, targets in build_sdists.items():
            by_project.setdefault(normalize_name(sdist.split("/")[1]), (sdist, set()))[1].update(targets)

        adopted = []
        for wheel in sorted(directory.glob("*.whl")):
            entry = by_project.get(_wheel_project(wheel.name))
            if entry is None:
                log.debug("external wheel %s matches no sdist the mirror needs built", wheel.name)
                continue
            sdist, targets = entry
            filename, _ = self._publish(wheel, sdist)
            if filename is None:
                continue
            adopted.append(self._register(sdist, sdist.split("/")[1], filename, targets))
        if adopted:
            log.info("Adopted %d wheels built elsewhere from %s", len(adopted), directory)
        return adopted

    # ----------------------------------------------------------------- running

    def run(
        self, build_sdists: Mapping[str, set[BuildTarget]], sdist_hashes: Mapping[str, str]
    ) -> BuildSummary:
        """Build what is missing for every sdist in ``build_sdists``."""
        summary = BuildSummary()
        if not self._config.build_wheels:
            return summary
        jobs = [
            (sdist, sorted(targets))
            for sdist, targets in sorted(build_sdists.items())
            if targets and (self._config.output / sdist).is_file()
        ]
        if not jobs:
            return summary
        if not self._engine_available():
            for sdist, targets in jobs:
                summary.gaps += [BuildGap(sdist, t, "no-engine") for t in targets]
            return summary

        self._config.state.build_tmp.mkdir(parents=True, exist_ok=True)

        def one(job: tuple[str, list[BuildTarget]]) -> BuildSummary:
            return self._build_sdist(job[0], job[1], sdist_hashes)

        with progress(len(jobs), "Building missing wheels", unit="sdist") as bar:
            for _, partial in parallel_map(one, jobs, self._config.build_jobs):
                summary.built += partial.built
                summary.gaps += partial.gaps
                summary.cached += partial.cached
                summary.attempted += partial.attempted
                bar.update()
                for gap in partial.gaps:
                    self._gaps[(gap.sdist, gap.target)] = gap
                for gap in partial.failures:
                    log.warning("could not build %s for %s", gap.sdist, gap.target)
        self._save_cache()
        return summary

    def _build_sdist(
        self, sdist: str, targets: Sequence[BuildTarget], sdist_hashes: Mapping[str, str]
    ) -> BuildSummary:
        """Build one sdist for the targets that need it.

        Serial on purpose: the first build often turns out to be ``py3-none-any``,
        which satisfies every remaining target, so there is nothing left to do.
        Parallelism happens across sdists instead.
        """
        summary = BuildSummary()
        project = sdist.split("/")[1]
        if project in self._config.build_exclude:
            summary.gaps += [BuildGap(sdist, t, "excluded") for t in targets]
            return summary

        digest = sdist_hashes.get(sdist) or _sha256(self._config.output / sdist)
        remaining = set(targets)
        for target in targets:
            if target not in remaining:
                continue
            platform = PLATFORMS[target.platform]
            image = self.image_for(platform)
            if image is None:
                summary.gaps.append(BuildGap(sdist, target, "no-builder"))
                remaining.discard(target)
                continue
            wheel, cached, error = self._wheel_for(
                sdist, project, target, image=image, digest=digest, candidates=remaining
            )
            summary.attempted += 0 if cached else 1
            summary.cached += 1 if cached else 0
            if wheel is None:
                summary.gaps.append(BuildGap(sdist, target, "build-failed", error))
                remaining.discard(target)
                continue
            summary.built.append(wheel)
            remaining -= set(wheel.targets)
        return summary

    def _wheel_for(
        self,
        sdist: str,
        project: str,
        target: BuildTarget,
        *,
        image: str,
        digest: str,
        candidates: set[BuildTarget],
    ) -> tuple[BuiltWheel | None, bool, str]:
        key = _cache_key(digest, target, image)
        with self._lock:
            entry = self._cache.get(key)
        if entry is not None:
            cached = self._from_cache(entry, sdist, project, candidates)
            if cached is not None or entry.get("error"):
                return cached, True, entry.get("error", "")
            # The entry promised a wheel that is no longer on disk -- pruned, or
            # removed by hand. Forget it and build again: reporting a gap for a file
            # we know how to recreate would be a lie that survives every later run.
            log.debug("rebuilding %s for %s: the cached wheel is gone", sdist, target)
            with self._lock:
                self._cache.pop(key, None)

        filename, error = self._run_container(sdist, target, image)
        with self._lock:
            self._cache[key] = (
                {"wheel": filename} if filename else {"wheel": "", "error": error[-_ERROR_TAIL:]}
            )
        if filename is None:
            log.debug("build failed: %s for %s: %s", sdist, target, error)
            return None, False, error
        return self._register(sdist, project, filename, candidates), False, ""

    def _from_cache(
        self, entry: Mapping[str, str], sdist: str, project: str, candidates: set[BuildTarget]
    ) -> BuiltWheel | None:
        filename = entry.get("wheel") or ""
        if not filename:
            return None
        if not (self._config.output / package_path(project, filename)).is_file():
            return None
        return self._register(sdist, project, filename, candidates)

    def _register(self, sdist: str, project: str, filename: str, candidates: set[BuildTarget]) -> BuiltWheel:
        path = package_path(project, filename)
        absolute = self._config.output / path
        existing = self._wheels.get(path)
        satisfied = targets_satisfied(filename, candidates)
        if existing is not None:
            merged = BuiltWheel(
                path=existing.path,
                sha256=existing.sha256,
                size=existing.size,
                project=project,
                sdist=sdist,
                targets=tuple(sorted(set(existing.targets) | satisfied)),
            )
            self._wheels[path] = merged
            return merged
        wheel = BuiltWheel(
            path=path,
            sha256=_sha256(absolute),
            size=absolute.stat().st_size,
            project=project,
            sdist=sdist,
            targets=tuple(sorted(satisfied)),
        )
        with self._lock:
            self._wheels[path] = wheel
        return wheel

    # -------------------------------------------------------------- the engine

    def _engine_command(self) -> list[str]:
        engine = self.engine
        command = [engine]
        # Inside the mirror's own container the engine is reached over the API
        # socket bin/gpygap passes in; podman needs to be told to use it.
        if "podman" in engine and os.environ.get("CONTAINER_HOST"):
            command.append("--remote")
        return command

    def _engine_available(self) -> bool:
        if shutil.which(self.engine) is None:
            log.error(
                "build.wheels is on but %r is not on PATH: no wheels will be built and every "
                "target without an upstream wheel stays a gap",
                self.engine,
            )
            return False
        # `version` rather than `info`: its template is the same shape on the docker
        # and podman clients, and the client in the image talks to either engine.
        probe = subprocess.run(
            [*self._engine_command(), "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if probe.returncode == 0:
            log.debug("wheel builds will run on %s %s", self.engine, probe.stdout.strip())
        if probe.returncode != 0:
            log.error(
                "cannot reach the container engine for wheel builds (%s). Inside the gpygap "
                "container this needs the API socket bin/gpygap passes in. %s",
                self.engine,
                (probe.stderr or probe.stdout).strip()[-_ERROR_TAIL:],
            )
            return False
        return True

    def _run_container(self, sdist: str, target: BuildTarget, image: str) -> tuple[str | None, str]:
        """Build one wheel in a sibling container; return its file name or the error."""
        mirror = self._config.output
        platform = PLATFORMS[target.platform]
        assert platform.wheel_tag_platform is not None  # buildable platforms carry a tag
        self._config.state.build_tmp.mkdir(parents=True, exist_ok=True)
        # A leftover temporary directory is untidy; failing a whole sync over one
        # would be worse, and the build itself has already succeeded by then.
        with tempfile.TemporaryDirectory(dir=self._config.state.build_tmp, ignore_cleanup_errors=True) as out:
            out_dir = Path(out)
            command = [
                *self._engine_command(),
                *("run", "--rm", "--network", "none"),
                # The engine resolves these on its own host, which is not always
                # this container's filesystem; the paths inside stay as they are.
                *("--volume", f"{self.host_path(mirror)}:{mirror}:ro"),
                *("--volume", f"{self.host_path(out_dir)}:{out_dir}"),
                *("--env", f"SOURCE_DATE_EPOCH={_SOURCE_DATE_EPOCH}"),
                *("--workdir", str(out_dir)),
                image,
                *("bash", "-c", _BUILD_SCRIPT, "gpygap-build"),
                python_executable(target.python),
                str(mirror / sdist),
                (mirror / "simple").as_uri(),
                str(out_dir),
                platform.wheel_tag_platform,
            ]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=self._config.wheel_build_timeout,
                )
            except subprocess.TimeoutExpired:
                return None, _TIMED_OUT
            if result.returncode != 0:
                return None, (result.stderr or result.stdout).strip()[-_ERROR_TAIL:]
            wheels = sorted(out_dir.glob("*.whl"))
            if not wheels:
                return None, "the build produced no wheel"
            return self._publish(wheels[0], sdist)

    def _publish(self, wheel: Path, sdist: str) -> tuple[str | None, str]:
        """Move a freshly built wheel next to the sdist it came from."""
        project = sdist.split("/")[1]
        destination = self._config.output / package_path(project, wheel.name)
        if destination.exists():
            # An upstream wheel of the same name always wins: it is the artifact the
            # project published, and its hash is the one the lock file recorded.
            return wheel.name, ""
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part")
        shutil.copy2(wheel, temporary)
        temporary.replace(destination)
        return wheel.name, ""

    # --------------------------------------------------------------- the cache

    def _load_cache(self) -> None:
        data = read_json(self._config.state.built_wheels)
        if isinstance(data, dict) and data.get("version") == _CACHE_VERSION:
            entries = data.get("entries")
            if isinstance(entries, dict):
                self._cache = entries

    def _save_cache(self) -> None:
        with self._lock:
            payload = {"version": _CACHE_VERSION, "entries": self._cache}
        write_json(self._config.state.built_wheels, payload)
