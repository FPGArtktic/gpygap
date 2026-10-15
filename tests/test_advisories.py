# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from gpygap.advisories import AdvisorySummary, Finding, mirrored_versions, scan
from gpygap.models import MirrorFile


def advisory(
    identifier: str,
    name: str,
    *,
    versions: list[str] | None = None,
    ranges: list[dict[str, Any]] | None = None,
    severity: str = "HIGH",
    withdrawn: str | None = None,
) -> dict[str, Any]:
    affected: dict[str, Any] = {"package": {"name": name, "ecosystem": "PyPI"}}
    if versions is not None:
        affected["versions"] = versions
    if ranges is not None:
        affected["ranges"] = ranges
    entry: dict[str, Any] = {
        "id": identifier,
        "aliases": [f"CVE-{identifier}"],
        "summary": f"{name} is unhappy",
        "database_specific": {"severity": severity},
        "affected": [affected],
    }
    if withdrawn:
        entry["withdrawn"] = withdrawn
    return entry


def archive_of(tmp_path: Path, *entries: dict[str, Any]) -> Path:
    path = tmp_path / "osv.zip"
    with zipfile.ZipFile(path, "w") as bundle:
        for entry in entries:
            bundle.writestr(f"{entry['id']}.json", json.dumps(entry))
        bundle.writestr("NOT-JSON.txt", "ignored")
    return path


def test_an_enumerated_version_is_matched(tmp_path: Path) -> None:
    archive = archive_of(tmp_path, advisory("A", "urllib3", versions=["1.26.4", "1.26.5"]))
    findings = scan(archive, {"urllib3": {"1.26.5", "2.0.0"}})
    assert [(f.project, f.version, f.severity) for f in findings] == [("urllib3", "1.26.5", "high")]
    assert findings[0].aliases == ("CVE-A",)


def test_a_range_is_evaluated_when_no_versions_are_listed(tmp_path: Path) -> None:
    archive = archive_of(
        tmp_path,
        advisory(
            "B", "spam", ranges=[{"type": "ECOSYSTEM", "events": [{"introduced": "1.0"}, {"fixed": "2.0"}]}]
        ),
    )
    findings = scan(archive, {"spam": {"0.9", "1.5", "2.0", "2.1"}})
    assert sorted(f.version for f in findings) == ["1.5"]
    assert findings[0].fixed_in == ("2.0",)


def test_an_open_ended_range_covers_everything_after_it(tmp_path: Path) -> None:
    archive = archive_of(
        tmp_path, advisory("C", "spam", ranges=[{"type": "ECOSYSTEM", "events": [{"introduced": "0"}]}])
    )
    findings = scan(archive, {"spam": {"0.1", "9.9"}})
    assert sorted(f.version for f in findings) == ["0.1", "9.9"]


def test_last_affected_is_inclusive(tmp_path: Path) -> None:
    archive = archive_of(
        tmp_path,
        advisory(
            "D",
            "spam",
            ranges=[{"type": "ECOSYSTEM", "events": [{"introduced": "1.0"}, {"last_affected": "1.9"}]}],
        ),
    )
    findings = scan(archive, {"spam": {"1.9", "2.0"}})
    assert sorted(f.version for f in findings) == ["1.9"]


def test_a_withdrawn_advisory_is_ignored(tmp_path: Path) -> None:
    archive = archive_of(tmp_path, advisory("E", "spam", versions=["1.0"], withdrawn="2026-01-01T00:00:00Z"))
    assert scan(archive, {"spam": {"1.0"}}) == []


def test_another_ecosystem_is_ignored(tmp_path: Path) -> None:
    entry = advisory("F", "spam", versions=["1.0"])
    entry["affected"][0]["package"]["ecosystem"] = "npm"
    assert scan(archive_of(tmp_path, entry), {"spam": {"1.0"}}) == []


def test_project_names_are_matched_after_normalization(tmp_path: Path) -> None:
    archive = archive_of(tmp_path, advisory("G", "Zope.Interface", versions=["5.0"]))
    assert len(scan(archive, {"zope-interface": {"5.0"}})) == 1


def test_versions_are_compared_not_string_matched(tmp_path: Path) -> None:
    # 1.0 and 1.0.0 are the same release under PEP 440; an advisory that spells it
    # one way must still match a file name that spells it the other.
    archive = archive_of(tmp_path, advisory("H", "spam", versions=["1.0"]))
    assert len(scan(archive, {"spam": {"1.0.0"}})) == 1


def test_findings_come_back_worst_first(tmp_path: Path) -> None:
    archive = archive_of(
        tmp_path,
        advisory("I", "spam", versions=["1.0"], severity="LOW"),
        advisory("J", "spam", versions=["1.0"], severity="CRITICAL"),
        advisory("K", "spam", versions=["1.0"], severity="MODERATE"),
    )
    assert [f.severity for f in scan(archive, {"spam": {"1.0"}})] == ["critical", "moderate", "low"]


def test_an_unknown_severity_does_not_crash_the_sort(tmp_path: Path) -> None:
    archive = archive_of(tmp_path, advisory("L", "spam", versions=["1.0"], severity="made-up"))
    assert [f.severity for f in scan(archive, {"spam": {"1.0"}})] == ["unknown"]


def test_versions_come_from_the_file_names_and_tools_are_skipped() -> None:
    manifest = {
        "packages/urllib3/urllib3-1.26.5-py2.py3-none-any.whl": MirrorFile(
            path="a", url="", project="urllib3"
        ),
        "packages/docopt/docopt-0.6.2.tar.gz": MirrorFile(path="b", url="", project="docopt"),
        "packages/odd/not-a-package.txt": MirrorFile(path="c", url="", project="odd"),
        "tools/uv/0.12.9/uv-x86_64-unknown-linux-gnu.tar.gz": MirrorFile(path="d", url="", project=None),
    }
    assert mirrored_versions(manifest) == {"urllib3": {"1.26.5"}, "docopt": {"0.6.2"}}


def test_at_or_above_walks_up_the_ladder() -> None:
    summary = AdvisorySummary(
        scanned_projects=1,
        scanned_versions=1,
        findings=(),
        counts={"low": 3, "moderate": 2, "high": 1, "critical": 1},
    )
    assert summary.at_or_above("critical") == 1
    assert summary.at_or_above("high") == 2
    assert summary.at_or_above("low") == 7
    # "unknown" is not a threshold anyone can mean.
    assert summary.at_or_above("unknown") == 0


def test_the_published_report_says_what_it_is_not() -> None:
    summary = AdvisorySummary(
        scanned_projects=1,
        scanned_versions=1,
        findings=(Finding("spam", "1.0", "GHSA-x", ("CVE-1",), "high", "bad", ("1.1",)),),
        counts={"high": 1},
    )
    document = summary.to_json()
    assert "false positives" in document["caveat"]
    assert document["findings"][0]["fixed_in"] == ["1.1"]
