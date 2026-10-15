# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Target platforms the mirror can serve.

A platform ties together the different names the same OS/CPU pair has in each
tool: wheel platform tags, PEP 508 marker values, uv's ``--python-platform``,
python-build-standalone archives and uv release archives.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True, eq=False)
class Platform:
    """A target operating system and CPU architecture."""

    key: str
    wheel_platform: re.Pattern[str]
    """Wheel platform tags (other than ``any``) installable on this platform."""
    uv_target: str
    """Value for ``uv pip compile --python-platform``."""
    marker_env: Mapping[str, str] = field(repr=False)
    """PEP 508 marker variables that depend on the platform."""
    python_build: tuple[str, str, str]
    """``(os, arch, libc)`` as reported by ``uv python list --output-format json``."""
    uv_archive: str
    """File name of the uv release archive for this platform."""
    build_image: str | None = None
    """Container image that can build a wheel for this platform, if any.

    ``None`` means the mirror cannot produce wheels for it and must ship the sdist:
    a Linux kernel does not run Windows or macOS toolchains, and cross-compiling
    Python extensions is not something setuptools supports. Those targets get the
    ``py3-none-any`` wheels built elsewhere and, failing that, a reported gap.
    """
    wheel_tag_platform: str | None = None
    """Platform tag ``auditwheel`` should retag a built wheel to."""

    def accepts_wheel_platform(self, tag: str) -> bool:
        return tag == "any" or self.wheel_platform.fullmatch(tag) is not None

    @property
    def buildable(self) -> bool:
        return self.build_image is not None


def _markers(sys_platform: str, system: str, os_name: str, machine: str) -> Mapping[str, str]:
    return MappingProxyType(
        {
            "sys_platform": sys_platform,
            "platform_system": system,
            "os_name": os_name,
            "platform_machine": machine,
        }
    )


_MANYLINUX = r"(manylinux1|manylinux2010|manylinux2014|manylinux_\d+_\d+)"
_MUSLLINUX = r"musllinux_\d+_\d+"

# The pypa images carry every CPython the mirror targets (including the free-threaded
# builds) plus auditwheel, which is what retags a fresh wheel to a manylinux/musllinux
# tag. Pinned by tag for the same reason uv is pinned: the glibc/musl floor a wheel is
# built against must not drift between runs.
_MANYLINUX_IMAGE = "quay.io/pypa/manylinux_2_28_{arch}:2026.10.10-1"
_MUSLLINUX_IMAGE = "quay.io/pypa/musllinux_1_2_{arch}:2026.10.10-1"

PLATFORMS: Mapping[str, Platform] = MappingProxyType(
    {
        p.key: p
        for p in (
            Platform(
                key="linux-x86_64",
                wheel_platform=re.compile(_MANYLINUX + "_x86_64"),
                uv_target="x86_64-manylinux_2_31",
                marker_env=_markers("linux", "Linux", "posix", "x86_64"),
                python_build=("linux", "x86_64", "gnu"),
                uv_archive="uv-x86_64-unknown-linux-gnu.tar.gz",
                build_image=_MANYLINUX_IMAGE.format(arch="x86_64"),
                wheel_tag_platform="manylinux_2_28_x86_64",
            ),
            Platform(
                key="linux-aarch64",
                wheel_platform=re.compile(_MANYLINUX + "_aarch64"),
                uv_target="aarch64-manylinux_2_31",
                marker_env=_markers("linux", "Linux", "posix", "aarch64"),
                python_build=("linux", "aarch64", "gnu"),
                uv_archive="uv-aarch64-unknown-linux-gnu.tar.gz",
                build_image=_MANYLINUX_IMAGE.format(arch="aarch64"),
                wheel_tag_platform="manylinux_2_28_aarch64",
            ),
            Platform(
                key="linux-musl-x86_64",
                wheel_platform=re.compile(_MUSLLINUX + "_x86_64"),
                uv_target="x86_64-unknown-linux-musl",
                marker_env=_markers("linux", "Linux", "posix", "x86_64"),
                python_build=("linux", "x86_64", "musl"),
                uv_archive="uv-x86_64-unknown-linux-musl.tar.gz",
                build_image=_MUSLLINUX_IMAGE.format(arch="x86_64"),
                wheel_tag_platform="musllinux_1_2_x86_64",
            ),
            Platform(
                key="linux-musl-aarch64",
                wheel_platform=re.compile(_MUSLLINUX + "_aarch64"),
                uv_target="aarch64-unknown-linux-musl",
                marker_env=_markers("linux", "Linux", "posix", "aarch64"),
                python_build=("linux", "aarch64", "musl"),
                uv_archive="uv-aarch64-unknown-linux-musl.tar.gz",
                build_image=_MUSLLINUX_IMAGE.format(arch="aarch64"),
                wheel_tag_platform="musllinux_1_2_aarch64",
            ),
            Platform(
                key="win-amd64",
                wheel_platform=re.compile(r"win_amd64"),
                uv_target="x86_64-pc-windows-msvc",
                marker_env=_markers("win32", "Windows", "nt", "AMD64"),
                python_build=("windows", "x86_64", "none"),
                uv_archive="uv-x86_64-pc-windows-msvc.zip",
            ),
            Platform(
                key="win-arm64",
                wheel_platform=re.compile(r"win_arm64"),
                uv_target="aarch64-pc-windows-msvc",
                marker_env=_markers("win32", "Windows", "nt", "ARM64"),
                python_build=("windows", "aarch64", "none"),
                uv_archive="uv-aarch64-pc-windows-msvc.zip",
            ),
            Platform(
                key="macos-arm64",
                wheel_platform=re.compile(r"macosx_\d+_\d+_(arm64|universal2)"),
                uv_target="aarch64-apple-darwin",
                marker_env=_markers("darwin", "Darwin", "posix", "arm64"),
                python_build=("macos", "aarch64", "none"),
                uv_archive="uv-aarch64-apple-darwin.tar.gz",
            ),
            Platform(
                key="macos-x86_64",
                wheel_platform=re.compile(r"macosx_\d+_\d+_(x86_64|intel|universal|universal2)"),
                uv_target="x86_64-apple-darwin",
                marker_env=_markers("darwin", "Darwin", "posix", "x86_64"),
                python_build=("macos", "x86_64", "none"),
                uv_archive="uv-x86_64-apple-darwin.tar.gz",
            ),
        )
    }
)


def get_platforms(keys: list[str]) -> tuple[Platform, ...]:
    """Look up platforms by key, raising ``ValueError`` for unknown ones."""
    unknown = [k for k in keys if k not in PLATFORMS]
    if unknown:
        raise ValueError(f"unknown platform(s) {unknown}; available: {', '.join(PLATFORMS)}")
    return tuple(PLATFORMS[k] for k in keys)
