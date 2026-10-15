# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Choosing which projects to mirror, and what happens when nobody answers.

The fallback here is not politeness. A run that silently mirrored zero top
projects would conclude that almost every file already in the mirror is
unneeded, and the prune would act on that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gpygap.toplist import load_top_list


def source_at(path: Path, projects: list[str], last_update: str = "2026-10-01") -> str:
    path.write_text(
        json.dumps({"last_update": last_update, "rows": [{"project": p, "downloads": 1} for p in projects]}),
        "utf-8",
    )
    return str(path)


def test_the_first_n_projects_are_taken_and_normalized(tmp_path: Path) -> None:
    source = source_at(tmp_path / "top.json", ["Zope.Interface", "urllib3", "boto3", "idna"])
    top = load_top_list([source], tmp_path / "cache.json", 3, [])
    assert top.projects == ("zope-interface", "urllib3", "boto3")
    assert top.last_update == "2026-10-01"


def test_an_excluded_project_does_not_use_up_a_slot(tmp_path: Path) -> None:
    # Otherwise excluding one project would silently shrink the mirror by one.
    source = source_at(tmp_path / "top.json", ["urllib3", "torch", "boto3", "idna"])
    top = load_top_list([source], tmp_path / "cache.json", 3, ["Torch"])
    assert top.projects == ("urllib3", "boto3", "idna")


def test_the_second_source_is_tried_when_the_first_is_unusable(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text('{"no_rows_here": true}', "utf-8")
    good = source_at(tmp_path / "good.json", ["urllib3"])
    assert load_top_list([str(broken), good], tmp_path / "cache.json", 1, []).projects == ("urllib3",)


def test_a_reachable_source_refreshes_the_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    source = source_at(tmp_path / "top.json", ["urllib3", "boto3"])
    load_top_list([source], cache, 2, [])
    assert [r["project"] for r in json.loads(cache.read_text("utf-8"))["rows"]] == ["urllib3", "boto3"]


def test_an_unreachable_source_falls_back_to_the_previous_run(tmp_path: Path) -> None:
    cache = tmp_path / "cache.json"
    source_at(cache, ["urllib3", "boto3"], last_update="2026-09-01")

    top = load_top_list([str(tmp_path / "gone.json")], cache, 2, [])

    assert top.projects == ("urllib3", "boto3")
    assert top.last_update == "2026-09-01"


def test_nothing_reachable_and_nothing_cached_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    # An empty list is the dangerous answer: it would make the whole mirror look
    # unneeded. Refusing to continue is the only safe thing left.
    with pytest.raises(RuntimeError, match="no source reachable"):
        load_top_list([str(tmp_path / "gone.json")], tmp_path / "absent.json", 10, [])


def test_asking_for_more_than_the_list_holds_returns_what_there_is(tmp_path: Path) -> None:
    source = source_at(tmp_path / "top.json", ["urllib3"])
    assert load_top_list([source], tmp_path / "cache.json", 1000, []).projects == ("urllib3",)


def test_a_duplicate_in_the_ranking_is_counted_once(tmp_path: Path) -> None:
    source = source_at(tmp_path / "top.json", ["urllib3", "URLLib3", "boto3"])
    assert load_top_list([source], tmp_path / "cache.json", 2, []).projects == ("urllib3", "boto3")
