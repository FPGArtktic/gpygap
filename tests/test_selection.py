# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import pytest

from gpygap.config import SdistPolicy
from gpygap.models import DistFile, LockedPackage
from gpygap.platforms import PLATFORMS
from gpygap.resolver import Resolution, ResolveRequest
from gpygap.selection import (
    build_plan,
    interpreter_compatible,
    marker_applies,
    select_files,
    wheel_compatible,
)

LINUX = PLATFORMS["linux-x86_64"]
WINDOWS = PLATFORMS["win-amd64"]


@pytest.mark.parametrize(
    ("interpreter", "abi", "python", "expected"),
    [
        ("py3", "none", (3, 12), True),
        ("py2", "none", (3, 12), False),
        ("cp312", "cp312", (3, 12), True),
        ("cp312", "cp312", (3, 13), False),
        ("cp313", "cp313t", (3, 13), False),
        ("cp38", "abi3", (3, 12), True),
        ("cp313", "abi3", (3, 12), False),
        ("pp310", "pypy310_pp73", (3, 10), False),
    ],
)
def test_interpreter_compatible(interpreter: str, abi: str, python: tuple[int, int], expected: bool) -> None:
    assert interpreter_compatible(interpreter, abi, python) is expected


@pytest.mark.parametrize(
    ("filename", "platform_key", "expected"),
    [
        ("six-1.17.0-py2.py3-none-any.whl", "linux-x86_64", True),
        ("pc-2.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", "linux-x86_64", True),
        ("pc-2.0-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl", "linux-x86_64", False),
        ("pc-2.0-cp312-cp312-musllinux_1_1_x86_64.whl", "linux-x86_64", False),
        ("pc-2.0-cp312-cp312-win_amd64.whl", "win-amd64", True),
        ("pc-2.0-cp312-cp312-win32.whl", "win-amd64", False),
        ("pc-2.0-cp312-cp312-macosx_11_0_arm64.whl", "win-amd64", False),
    ],
)
def test_wheel_compatible(filename: str, platform_key: str, expected: bool) -> None:
    assert wheel_compatible(filename, PLATFORMS[platform_key], (3, 12)) is expected


def test_marker_applies() -> None:
    assert marker_applies("sys_platform == 'win32'", WINDOWS, "3.12")
    assert not marker_applies("sys_platform == 'win32'", LINUX, "3.12")
    assert not marker_applies("python_version < '3.11'", LINUX, "3.12")
    assert marker_applies("python_full_version >= '3.12.4'", LINUX, "3.12")
    assert marker_applies(None, LINUX, "3.12")


def _wheel(filename: str) -> DistFile:
    return DistFile("wheel", f"https://example.invalid/{filename}", filename, 1, "0" * 64)


def _sdist(filename: str) -> DistFile:
    return DistFile("sdist", f"https://example.invalid/{filename}", filename, 1, "0" * 64)


def test_select_files_requests_build_when_a_platform_lacks_wheels() -> None:
    package = LockedPackage(
        name="winonly",
        version="1.0",
        marker=None,
        files=(_sdist("winonly-1.0.tar.gz"), _wheel("winonly-1.0-cp312-cp312-win_amd64.whl")),
    )
    selection = select_files(package, "3.12", (LINUX, WINDOWS), SdistPolicy.NEEDED)
    assert {f.filename for f in selection.files} == {
        "winonly-1.0.tar.gz",
        "winonly-1.0-cp312-cp312-win_amd64.whl",
    }
    assert selection.build_sdist is not None


def test_select_files_skips_packages_excluded_by_marker() -> None:
    package = LockedPackage(
        "macdep", "1.0", "sys_platform == 'darwin'", (_wheel("macdep-1.0-py3-none-any.whl"),)
    )
    selection = select_files(package, "3.12", (LINUX, WINDOWS), SdistPolicy.ALL)
    assert selection.files == ()


def test_build_plan_keeps_files_of_stale_resolutions() -> None:
    package = LockedPackage("six", "1.17.0", None, (_wheel("six-1.17.0-py2.py3-none-any.whl"),))
    stale = Resolution(ResolveRequest("six", "3.12"), (package,), 0.0, error="network down")
    plan = build_plan([stale], (LINUX,), SdistPolicy.ALL)
    assert set(plan.files) == {"packages/six/six-1.17.0-py2.py3-none-any.whl"}
