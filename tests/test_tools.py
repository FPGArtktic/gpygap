# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Choosing which python-build-standalone archives to mirror.

A developer behind the air gap runs `uv python install` and gets whatever uv asks
for. If the mirror guessed differently, the request 404s on a machine with no way
to fetch the real thing, so the selection has to match uv's own vocabulary
exactly: implementation, variant, libc and all.
"""

from __future__ import annotations

from typing import Any

from gpygap.platforms import PLATFORMS
from gpygap.tools import pick_python_builds

LINUX = PLATFORMS["linux-x86_64"]
MUSL = PLATFORMS["linux-musl-x86_64"]
WINDOWS = PLATFORMS["win-amd64"]


def build(
    version: str,
    *,
    os_name: str = "linux",
    arch: str = "x86_64",
    libc: str = "gnu",
    variant: str = "default",
    implementation: str = "cpython",
    url: str = "https://example.invalid/python.tar.gz",
) -> dict[str, Any]:
    major, minor, patch = (int(p) for p in version.split("."))
    return {
        "implementation": implementation,
        "variant": variant,
        "url": url,
        "version": version,
        "version_parts": {"major": major, "minor": minor, "patch": patch},
        "os": os_name,
        "arch": arch,
        "libc": libc,
    }


def versions(picked: list[dict[str, Any]]) -> list[str]:
    return sorted(f"{p['version']}-{p['variant']}-{p['libc']}" for p in picked)


def test_the_newest_patch_of_each_minor_version_wins() -> None:
    available = [build("3.12.1"), build("3.12.7"), build("3.12.3")]
    assert versions(pick_python_builds(available, [LINUX], ["3.12"])) == ["3.12.7-default-gnu"]


def test_a_version_nobody_asked_for_is_not_mirrored() -> None:
    available = [build("3.12.7"), build("3.9.18")]
    assert versions(pick_python_builds(available, [LINUX], ["3.12"])) == ["3.12.7-default-gnu"]


def test_other_implementations_are_skipped() -> None:
    available = [build("3.12.7", implementation="pypy"), build("3.12.7", implementation="graalpy")]
    assert pick_python_builds(available, [LINUX], ["3.12"]) == []


def test_a_prerelease_is_not_what_a_developer_should_be_handed() -> None:
    available = [
        {**build("3.14.0"), "version": "3.14.0rc2"},
        build("3.14.0"),
    ]
    assert versions(pick_python_builds(available, [LINUX], ["3.14"])) == ["3.14.0-default-gnu"]


def test_an_entry_without_a_url_cannot_be_downloaded_so_it_is_skipped() -> None:
    assert pick_python_builds([build("3.12.7", url="")], [LINUX], ["3.12"]) == []


def test_musl_and_glibc_are_different_builds_for_different_platforms() -> None:
    available = [build("3.12.7", libc="gnu"), build("3.12.7", libc="musl")]

    assert versions(pick_python_builds(available, [LINUX], ["3.12"])) == ["3.12.7-default-gnu"]
    assert versions(pick_python_builds(available, [MUSL], ["3.12"])) == ["3.12.7-default-musl"]
    # Both platforms configured: both builds are mirrored, and neither stands in
    # for the other.
    assert len(pick_python_builds(available, [LINUX, MUSL], ["3.12"])) == 2


def test_a_free_threaded_target_gets_the_free_threaded_build() -> None:
    # python-build-standalone ships these as a variant of the same version rather
    # than a separate version, so asking for 3.13t must not quietly hand back the
    # build with the GIL.
    available = [build("3.13.5"), build("3.13.5", variant="freethreaded")]

    assert versions(pick_python_builds(available, [LINUX], ["3.13t"])) == ["3.13.5-freethreaded-gnu"]
    assert versions(pick_python_builds(available, [LINUX], ["3.13"])) == ["3.13.5-default-gnu"]


def test_both_abis_are_mirrored_when_both_are_targeted() -> None:
    available = [build("3.13.5"), build("3.13.5", variant="freethreaded")]
    assert versions(pick_python_builds(available, [LINUX], ["3.13", "3.13t"])) == [
        "3.13.5-default-gnu",
        "3.13.5-freethreaded-gnu",
    ]


def test_an_unknown_variant_is_not_mirrored() -> None:
    # Debug and other variants exist upstream; handing one to a developer who asked
    # for 3.13 would be a surprise they cannot undo offline.
    assert pick_python_builds([build("3.13.5", variant="debug")], [LINUX], ["3.13"]) == []


def test_windows_builds_are_matched_by_their_own_triple() -> None:
    available = [build("3.12.7", os_name="windows", arch="x86_64", libc="none"), build("3.12.7")]
    picked = pick_python_builds(available, [WINDOWS], ["3.12"])
    assert len(picked) == 1 and picked[0]["os"] == "windows"
