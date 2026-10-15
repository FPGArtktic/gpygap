# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from pathlib import Path

import pytest
from packaging.requirements import Requirement

from gpygap.distmeta import parse_core_metadata
from gpygap.extras import with_extra
from gpygap.intake import load_intake
from gpygap.verify import unresolved_requirements

UV_LOCK = """
version = 1
requires-python = ">=3.12"

[[package]]
name = "app"
version = "0.1.0"
source = { editable = "." }

[[package]]
name = "requests"
version = "2.31.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "certifi"
version = "2024.2.2"
source = { registry = "https://pypi.org/simple" }
"""

REQUIREMENTS = """\
# team requirements
-r base.txt
--index-url https://example.invalid/simple
django==4.2.11 \\
    --hash=sha256:aaaa
celery[redis]>=5.3  # broker
-e ./local
"""


def test_load_intake_reads_lock_files_and_requirements(tmp_path: Path) -> None:
    (tmp_path / "team-a").mkdir()
    (tmp_path / "team-a" / "uv.lock").write_text(UV_LOCK, "utf-8")
    (tmp_path / "requirements.txt").write_text(REQUIREMENTS, "utf-8")
    intake = load_intake(tmp_path)
    assert intake.pins == ("requests==2.31.0", "certifi==2024.2.2")
    assert intake.requirements == ("django==4.2.11", "celery[redis]>=5.3")


def test_unresolved_requirements_are_parsed_from_uv_errors() -> None:
    error = (
        "error: Failed to build `fake-1.0.tar.gz`\n"
        "  Caused by: Failed to resolve requirements from `build-system.requires`\n"
        "  Caused by: No solution found when resolving: `setuptools>=61`, `cython<0.20`\n"
        "  Caused by: Because only cython==3.3.0 is available ...\n"
    )
    assert unresolved_requirements(error) == ("setuptools>=61", "cython<0.20")


def test_core_metadata_extras_are_normalized() -> None:
    raw = (
        b"Metadata-Version: 2.1\nName: x\nRequires-Python: >=3.9\n"
        b"Provides-Extra: Socks\nProvides-Extra: use_chardet\n"
    )
    core = parse_core_metadata(raw)
    assert core.requires_python == ">=3.9"
    assert core.provides_extra == ("socks", "use-chardet")


def test_intake_ignores_documentation(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# drop lock files here\n", "utf-8")
    (tmp_path / ".gitkeep").write_text("", "utf-8")
    assert load_intake(tmp_path) == load_intake(None)


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ("uvicorn", "uvicorn[standard]"),
        ("django>=4.2,<5", "django[standard]<5,>=4.2"),
        ("pywin32-ctypes; sys_platform == 'win32'", 'pywin32-ctypes[standard]; sys_platform == "win32"'),
    ],
)
def test_with_extra_keeps_version_and_marker(requirement: str, expected: str) -> None:
    assert with_extra(Requirement(requirement), "standard") == expected
