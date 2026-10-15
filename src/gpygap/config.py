# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mirror configuration, loaded from a human-edited TOML file (``gpygap.toml``).

Every key is optional and falls back to the default in ``_SCHEMA``. Unknown
sections and keys are rejected so that a typo fails loudly instead of being
silently ignored. Relative paths are resolved against the config file's directory.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from gpygap.platforms import PLATFORMS, Platform, get_platforms
from gpygap.util import normalize_name

DEFAULT_CONFIG_FILE = "gpygap.toml"
DEFAULT_INDEX_URL = "https://pypi.org/simple"
DEFAULT_TOP_SOURCES = (
    "https://hugovk.github.io/top-pypi-packages/top-pypi-packages.min.json",
    "https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/top-pypi-packages.min.json",
)


class ConfigError(ValueError):
    """The configuration file is invalid."""


class SdistPolicy(StrEnum):
    """Which source distributions to mirror."""

    ALL = "all"
    """Every sdist of every mirrored version."""
    NEEDED = "needed"
    """Only sdists of versions lacking a wheel for some target platform."""
    NONE = "none"


_SCHEMA: dict[str, dict[str, Any]] = {
    "mirror": {
        "output": "gpygap",
        "state_dir": None,
        "uv_cache_dir": None,
        "uv": "uv",
    },
    "packages": {
        "top": 1000,
        "top_sources": list(DEFAULT_TOP_SOURCES),
        "extra": [],
        "extra_file": None,
        "exclude": [],
        "extras": True,
        "extras_exclude": [],
        "requirements_dir": None,
        "sdists": SdistPolicy.ALL.value,
    },
    "targets": {
        "python": ["3.10", "3.11", "3.12", "3.13", "3.14"],
        "platforms": ["linux-x86_64", "win-amd64"],
    },
    "upstream": {
        "index_url": DEFAULT_INDEX_URL,
    },
    "sync": {
        "keep_runs": 1,
        "max_prune_ratio": 0.5,
        "prune": True,
        "build_deps_rounds": 3,
        "with_tools": True,
        "verify": True,
        "build_check": True,
        "heal_rounds": 2,
    },
    "build": {
        "wheels": True,
        "external_dir": None,
        "engine": "auto",
        "container_root": None,
        "host_root": None,
        "images": [],
        "exclude": [],
        "jobs": 2,
        "timeout": 1800,
    },
    "performance": {
        "jobs": 8,
        "download_jobs": 16,
        "retries": 2,
        "resolve_timeout": 900,
        "build_timeout": 600,
        "max_age_hours": 20.0,
    },
}


@dataclass(frozen=True, slots=True)
class StatePaths:
    """Layout of the state directory (caches, logs, history); never shipped offline."""

    root: Path

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def resolutions(self) -> Path:
        return self.root / "resolutions"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    @property
    def verify_cache(self) -> Path:
        return self.root / "verify-cache"

    @property
    def top_list(self) -> Path:
        return self.root / "top-pypi-packages.json"

    @property
    def targeted(self) -> Path:
        return self.root / "targeted-resolutions.json"

    @property
    def checksums(self) -> Path:
        return self.root / "checksum-cache.json"

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def dist_meta(self) -> Path:
        return self.root / "dist-meta.json"

    @property
    def export_snapshot(self) -> Path:
        return self.root / "export-snapshot.json"

    @property
    def built_wheels(self) -> Path:
        return self.root / "built-wheels.json"

    @property
    def build_tmp(self) -> Path:
        return self.root / "build"


@dataclass(frozen=True, slots=True)
class Config:
    """Validated configuration of one mirror."""

    output: Path
    state_dir: Path
    uv_cache_dir: Path
    uv_bin: str
    top: int
    top_sources: tuple[str, ...]
    extra_requirements: tuple[str, ...]
    exclude: frozenset[str]
    extras: bool
    extras_exclude: frozenset[str]
    requirements_dir: Path | None
    sdists: SdistPolicy
    python_versions: tuple[str, ...]
    platforms: tuple[Platform, ...]
    index_url: str
    keep_runs: int
    max_prune_ratio: float
    prune: bool
    build_deps_rounds: int
    with_tools: bool
    verify: bool
    build_check: bool
    heal_rounds: int
    build_wheels: bool
    build_external_dir: Path | None
    build_engine: str
    build_container_root: str | None
    build_host_root: str | None
    build_images: Mapping[str, str]
    build_exclude: frozenset[str]
    build_jobs: int
    wheel_build_timeout: int
    jobs: int
    download_jobs: int
    retries: int
    resolve_timeout: int
    build_timeout: int
    max_age_hours: float

    @property
    def state(self) -> StatePaths:
        return StatePaths(self.state_dir)

    def uv_env(self, cache_dir: Path | None = None) -> dict[str, str]:
        """Environment for uv subprocesses, pinned to the mirror's own uv cache."""
        return os.environ | {
            "UV_CACHE_DIR": str(cache_dir or self.uv_cache_dir),
            "UV_NO_PROGRESS": "1",
        }


def load_config(path: Path | None, overrides: dict[str, Any] | None = None) -> Config:
    """Load ``path`` (if given), apply ``overrides`` (``{"section.key": value}``) and validate."""
    raw: dict[str, Any] = {}
    if path is not None:
        try:
            raw = tomllib.loads(path.read_text("utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc
    base_dir = path.parent.resolve() if path is not None else Path.cwd()
    values = _merge_with_defaults(raw)
    for dotted, value in (overrides or {}).items():
        section, key = dotted.split(".", 1)
        values[section][key] = value
    return _build(values, base_dir)


def _merge_with_defaults(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = {section: dict(defaults) for section, defaults in _SCHEMA.items()}
    for section, table in raw.items():
        if section not in _SCHEMA:
            raise ConfigError(f"unknown section [{section}]; known: {', '.join(_SCHEMA)}")
        if not isinstance(table, dict):
            raise ConfigError(f"[{section}] must be a table")
        for key, value in table.items():
            if key not in _SCHEMA[section]:
                raise ConfigError(f"unknown key {section}.{key}; known: {', '.join(_SCHEMA[section])}")
            _check_type(f"{section}.{key}", value, _SCHEMA[section][key])
            values[section][key] = value
    return values


def _check_type(name: str, value: Any, default: Any) -> None:
    if default is None:
        expected: type | tuple[type, ...] = str
    elif isinstance(default, bool):
        expected = bool
    elif isinstance(default, float):
        expected = (int, float)
    else:
        expected = type(default)
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ConfigError(f"{name} has the wrong type: {value!r}")
    if isinstance(value, list) and not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{name} must be a list of strings")


def _build(v: dict[str, dict[str, Any]], base_dir: Path) -> Config:
    mirror, packages, targets, sync, build, perf = (
        v["mirror"],
        v["packages"],
        v["targets"],
        v["sync"],
        v["build"],
        v["performance"],
    )
    output = (base_dir / mirror["output"]).resolve()
    state_dir = _optional_path(base_dir, mirror["state_dir"]) or output.with_name(output.name + "-state")
    try:
        platforms = get_platforms(targets["platforms"])
        sdists = SdistPolicy(packages["sdists"])
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    config = Config(
        output=output,
        state_dir=state_dir,
        uv_cache_dir=_optional_path(base_dir, mirror["uv_cache_dir"]) or state_dir / "uv-cache",
        uv_bin=mirror["uv"],
        top=packages["top"],
        top_sources=tuple(packages["top_sources"]),
        extra_requirements=_extra_requirements(packages, base_dir),
        exclude=frozenset(packages["exclude"]),
        extras=packages["extras"],
        extras_exclude=frozenset(packages["extras_exclude"]),
        requirements_dir=_optional_path(base_dir, packages["requirements_dir"]),
        sdists=sdists,
        python_versions=tuple(targets["python"]),
        platforms=platforms,
        index_url=v["upstream"]["index_url"],
        keep_runs=sync["keep_runs"],
        max_prune_ratio=float(sync["max_prune_ratio"]),
        prune=sync["prune"],
        build_deps_rounds=sync["build_deps_rounds"],
        with_tools=sync["with_tools"],
        verify=sync["verify"],
        build_check=sync["build_check"],
        heal_rounds=sync["heal_rounds"],
        build_wheels=build["wheels"],
        build_external_dir=_optional_path(base_dir, build["external_dir"]),
        build_engine=build["engine"],
        build_container_root=build["container_root"],
        build_host_root=build["host_root"],
        build_images=_build_images(build["images"]),
        build_exclude=frozenset(normalize_name(p) for p in build["exclude"]),
        build_jobs=build["jobs"],
        wheel_build_timeout=build["timeout"],
        jobs=perf["jobs"],
        download_jobs=perf["download_jobs"],
        retries=perf["retries"],
        resolve_timeout=perf["resolve_timeout"],
        build_timeout=perf["build_timeout"],
        max_age_hours=float(perf["max_age_hours"]),
    )
    _validate(config)
    return config


def _build_images(entries: list[str]) -> Mapping[str, str]:
    """Parse ``build.images`` overrides, each ``platform-key=image``.

    Defaults live on the platforms themselves; this only exists so an operator can
    point a target at a mirrored or newer builder image without editing the code.
    """
    images: dict[str, str] = {}
    for entry in entries:
        key, separator, image = entry.partition("=")
        if not separator or not key.strip() or not image.strip():
            raise ConfigError(f"build.images: {entry!r} is not 'platform-key=image'")
        if key.strip() not in PLATFORMS:
            raise ConfigError(f"build.images: unknown platform {key.strip()!r}")
        images[key.strip()] = image.strip()
    return MappingProxyType(images)


def _optional_path(base_dir: Path, value: str | None) -> Path | None:
    return (base_dir / value).resolve() if value else None


def _extra_requirements(packages: dict[str, Any], base_dir: Path) -> tuple[str, ...]:
    """Inline ``extra`` plus the lines of ``extra_file`` (comments and blanks skipped)."""
    requirements = list(packages["extra"])
    if packages["extra_file"]:
        path = base_dir / packages["extra_file"]
        try:
            lines = path.read_text("utf-8").splitlines()
        except OSError as exc:
            raise ConfigError(f"packages.extra_file: {exc}") from exc
        requirements += [ln.split("#", 1)[0].strip() for ln in lines]
    return tuple(dict.fromkeys(r for r in requirements if r))


def _validate(config: Config) -> None:
    checks = [
        (config.top >= 0, "packages.top must be >= 0"),
        (bool(config.python_versions), "targets.python must not be empty"),
        (bool(config.platforms), "targets.platforms must not be empty"),
        (config.keep_runs >= 1, "sync.keep_runs must be >= 1"),
        (0 < config.max_prune_ratio <= 1, "sync.max_prune_ratio must be in (0, 1]"),
        (config.build_deps_rounds >= 0, "sync.build_deps_rounds must be >= 0"),
        (config.heal_rounds >= 0, "sync.heal_rounds must be >= 0"),
        (min(config.jobs, config.download_jobs, config.retries) >= 1, "performance values must be >= 1"),
        (config.build_jobs >= 1, "build.jobs must be >= 1"),
        (config.wheel_build_timeout >= 1, "build.timeout must be >= 1"),
        (
            bool(config.build_container_root) == bool(config.build_host_root),
            "build.container_root and build.host_root must be set together",
        ),
    ]
    for ok, message in checks:
        if not ok:
            raise ConfigError(message)
    for version in config.python_versions:
        if not re.fullmatch(r"3\.\d+t?", version):
            raise ConfigError(
                f"targets.python: {version!r} is not a 'major.minor' version like '3.12', "
                "optionally with a 't' suffix for a free-threaded build like '3.13t'"
            )
