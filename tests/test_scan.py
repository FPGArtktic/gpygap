# SPDX-FileCopyrightText: 2026 Mateusz Chudzik <matchupikchu99@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading a project tree and saying what it needs from an index.

The scanner's output becomes an intake file, so a mistake here becomes a missing
package on the offline side. Two mistakes matter most and both are pinned below:
claiming the project's own modules are PyPI projects, and walking into a virtual
environment, where the imports describe somebody's machine rather than a project.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from gpygap import scan
from gpygap.scan import Source, render_requirements, scan_project


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


def test_a_module_next_to_the_testbench_is_not_a_requirement(tmp_path: Path) -> None:
    # A cocotb testbench does `from commons import *`; mirroring a PyPI project
    # called "commons" would be worse than useless.
    write(tmp_path / "commons.py", "import cocotb\n")
    write(tmp_path / "test_main.py", "from commons import *\nfrom tests import run\n")
    write(tmp_path / "tests.py", "from commons import *\n")

    assert scan_project(tmp_path).requirements == ("cocotb",)


def test_a_package_of_the_project_is_not_a_requirement_either(tmp_path: Path) -> None:
    write(tmp_path / "mylib" / "__init__.py", "")
    write(tmp_path / "mylib" / "core.py", "import httpx\n")
    write(tmp_path / "run.py", "import mylib\nfrom mylib.core import go\n")

    assert scan_project(tmp_path).requirements == ("httpx",)


def test_a_virtual_environment_in_the_tree_is_not_the_projects_dependencies(tmp_path: Path) -> None:
    # The venv is the dangerous one: its site-packages imports every transitive
    # dependency of everything installed there.
    write(tmp_path / "sim.py", "import cocotb\n")
    write(tmp_path / "venv" / "pyvenv.cfg", "version = 3.10.12\n")
    write(
        tmp_path / "venv" / "lib" / "python3.10" / "site-packages" / "numpy" / "__init__.py", "import mkl\n"
    )
    write(tmp_path / "unnamed-env" / "pyvenv.cfg", "version = 3.11.0\n")
    write(tmp_path / "unnamed-env" / "lib" / "whatever.py", "import tensorflow\n")

    assert scan_project(tmp_path).requirements == ("cocotb",)


def test_an_import_name_becomes_its_distribution_name(tmp_path: Path) -> None:
    write(tmp_path / "app.py", "import cocotb_bus.drivers.avalon\nimport yaml\nfrom PIL import Image\n")

    result = scan_project(tmp_path)

    assert set(result.requirements) == {"cocotb-bus", "pyyaml", "pillow"}
    sources = {d.requirement: d.source for d in result.discoveries}
    # Underscores are punctuation a normalization handles; "yaml" is not.
    assert sources["cocotb-bus"] is Source.GUESSED
    assert sources["pyyaml"] is Source.MAPPED


def test_the_standard_library_and_relative_imports_are_not_requirements(tmp_path: Path) -> None:
    write(tmp_path / "pkg" / "__init__.py", "")
    write(
        tmp_path / "pkg" / "thing.py",
        "import os\nimport subprocess\nfrom collections import OrderedDict\n"
        "from . import sibling\nfrom .deeper import other\nimport requests\n",
    )

    assert scan_project(tmp_path).requirements == ("requests",)


def test_an_import_inside_a_function_counts_as_much_as_one_at_the_top(tmp_path: Path) -> None:
    # Deferred imports are how optional dependencies are usually written, and the
    # offline side needs them just as much.
    write(tmp_path / "late.py", "def go():\n    import paramiko\n    return paramiko\n")

    assert scan_project(tmp_path).requirements == ("paramiko",)


def test_a_file_that_does_not_parse_does_not_fail_the_scan(tmp_path: Path) -> None:
    write(tmp_path / "python2.py", "print 'hello'\n")
    write(tmp_path / "fine.py", "import click\n")

    assert scan_project(tmp_path).requirements == ("click",)


def test_declared_dependencies_keep_their_version_specifiers(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "thing"\ndependencies = ["requests>=2.31", "click"]\n\n'
        '[project.optional-dependencies]\npdf = ["pypdf==4.2.0"]\n\n'
        '[dependency-groups]\ndev = ["pytest>=8"]\n\n'
        '[build-system]\nrequires = ["hatchling"]\n',
    )

    requirements = scan_project(tmp_path).requirements

    assert set(requirements) == {"requests>=2.31", "click", "pypdf==4.2.0", "pytest>=8", "hatchling"}


def test_a_requirements_file_and_a_setup_cfg_are_read_as_declared(tmp_path: Path) -> None:
    write(tmp_path / "requirements.txt", "# ours\n-r base.txt\ndjango==4.2.11  # pinned\n")
    write(
        tmp_path / "setup.cfg",
        "[options]\ninstall_requires =\n    celery>=5.3\n\n[options.extras_require]\nredis =\n    redis\n",
    )

    result = scan_project(tmp_path)

    assert set(result.requirements) == {"django==4.2.11", "celery>=5.3", "redis"}
    assert {d.source for d in result.discoveries} == {Source.DECLARED}


def test_poetry_keeps_the_name_when_its_specifier_is_not_pep_508(tmp_path: Path) -> None:
    # Rewriting "^1.2" would be a guess about the upper bound; the name is what
    # decides whether the mirror has the project at all.
    write(
        tmp_path / "pyproject.toml",
        '[tool.poetry.dependencies]\npython = "^3.11"\nrequests = "^2.31"\n',
    )

    assert scan_project(tmp_path).requirements == ("requests",)


def test_a_lock_file_is_reported_rather_than_flattened(tmp_path: Path) -> None:
    # intake/ reads lock files exactly; rewriting one into loose requirements
    # would throw away the pinning that made it worth having.
    write(tmp_path / "uv.lock", 'version = 1\n\n[[package]]\nname = "requests"\nversion = "2.31.0"\n')
    write(tmp_path / "app.py", "import flask\n")

    result = scan_project(tmp_path)

    assert result.lock_files == ("uv.lock",)
    assert result.requirements == ("flask",)
    assert "uv.lock" in render_requirements(result)


def test_an_import_the_index_does_not_have_is_reported_not_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path / "app.py", "import realpkg\nimport nosuchpkg\n")
    monkeypatch.setattr(scan, "_on_index", lambda name, index_url: name == "realpkg")

    result = scan_project(tmp_path, index_url="https://pypi.org/simple")

    assert result.requirements == ("realpkg",)
    assert [u.module for u in result.unmatched] == ["nosuchpkg"]
    assert "nosuchpkg" in render_requirements(result)


def test_without_the_index_a_guess_is_written_and_marked_as_one(tmp_path: Path) -> None:
    write(tmp_path / "app.py", "import nosuchpkg\n")

    result = scan_project(tmp_path)

    assert result.requirements == ("nosuchpkg",)
    assert "guessed" in render_requirements(result)


def test_declared_only_leaves_the_imports_alone(tmp_path: Path) -> None:
    write(tmp_path / "requirements.txt", "django\n")
    write(tmp_path / "app.py", "import flask\n")

    assert scan_project(tmp_path, follow_imports=False).requirements == ("django",)


def test_a_line_that_is_not_a_requirement_is_dropped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write(tmp_path / "requirements.txt", "django==4.2\nnot a requirement!!\n")

    with caplog.at_level("WARNING", logger="gpygap.scan"):
        result = scan_project(tmp_path, follow_imports=False)

    assert result.requirements == ("django==4.2",)
    assert "not a requirement" in caplog.text


def test_the_rendered_file_says_where_every_requirement_came_from(tmp_path: Path) -> None:
    write(tmp_path / "sim" / "bench.py", "import cocotb\n")

    rendered = render_requirements(scan_project(tmp_path), today=date(2026, 11, 18))

    assert rendered.startswith(scan.GENERATED_HEADER)
    assert "2026-11-18" in rendered
    # The provenance is the point: a month later nobody remembers which lines a
    # human declared and which a parser guessed.
    assert "cocotb" in rendered
    assert "sim/bench.py" in rendered
    assert rendered.endswith("\n")
