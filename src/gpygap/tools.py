# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""uv binaries and Python interpreters for the offline network.

Developers behind the air gap need uv itself and, unless they have one, a
Python interpreter. Both are mirrored:

* ``tools/uv/<version>/uv-<target>.<ext>`` - uv release archives, matching the
  uv version that builds the mirror;
* ``python/<release>/<file>`` - python-build-standalone archives in the layout
  uv expects from ``python-install-mirror``.

Clients should use the mirrored uv version: which Python build uv requests is
compiled into uv, so a different uv version may ask for files not mirrored.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
from collections.abc import Sequence
from typing import Any

from gpygap.config import Config
from gpygap.models import MirrorFile
from gpygap.platforms import Platform

_UV_RELEASES = "https://github.com/astral-sh/uv/releases/download"
_FINAL_RELEASE = re.compile(r"\d+\.\d+\.\d+")


def uv_version(config: Config) -> str:
    proc = subprocess.run(
        [config.uv_bin, "--version"], capture_output=True, text=True, env=config.uv_env(), check=True
    )
    return proc.stdout.split()[1]


def plan_tools(config: Config) -> list[MirrorFile]:
    """Files for uv and Python on every configured platform."""
    return [*_uv_archives(uv_version(config), config.platforms), *_python_builds(config)]


def _uv_archives(version: str, platforms: Sequence[Platform]) -> list[MirrorFile]:
    files = []
    for platform in platforms:
        url = f"{_UV_RELEASES}/{version}/{platform.uv_archive}"
        path = f"tools/uv/{version}/{platform.uv_archive}"
        files.append(MirrorFile(path=path, url=url))
        files.append(MirrorFile(path=path + ".sha256", url=url + ".sha256"))
    return files


def _python_builds(config: Config) -> list[MirrorFile]:
    # --only-downloads: otherwise versions installed on this machine replace
    # their download entries and silently go missing from the mirror.
    proc = subprocess.run(
        [
            config.uv_bin,
            "python",
            "list",
            "--only-downloads",
            "--all-platforms",
            "--all-arches",
            "--output-format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=config.uv_env(),
        check=True,
    )
    downloads = pick_python_builds(json.loads(proc.stdout), config.platforms, config.python_versions)
    return [_python_build_file(d["url"]) for d in downloads]


def pick_python_builds(
    available: list[dict[str, Any]], platforms: Sequence[Platform], pythons: Sequence[str]
) -> list[dict[str, Any]]:
    """Newest final CPython release of each (platform, minor version) pair."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in available:
        if not _is_standard_cpython(entry):
            continue
        parts = entry["version_parts"]
        python = f"{parts['major']}.{parts['minor']}"
        for platform in platforms:
            if python not in pythons or (entry["os"], entry["arch"], entry["libc"]) != platform.python_build:
                continue
            key = (platform.key, python)
            if key not in best or parts["patch"] > best[key]["version_parts"]["patch"]:
                best[key] = entry
    return list(best.values())


def _is_standard_cpython(entry: dict[str, Any]) -> bool:
    return (
        entry.get("implementation") == "cpython"
        and entry.get("variant") == "default"
        and bool(entry.get("url"))
        and _FINAL_RELEASE.fullmatch(entry.get("version", "")) is not None
    )


def _python_build_file(url: str) -> MirrorFile:
    # .../releases/download/<release>/<file>  ->  python/<release>/<file>
    release, quoted_name = urllib.parse.urlsplit(url).path.split("/")[-2:]
    return MirrorFile(path=f"python/{release}/{urllib.parse.unquote(quoted_name)}", url=url)
