# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""What a run concludes, and the exit code it concludes it with.

These are the decisions an operator automates against: a non-zero exit stops a
monthly pipeline, and a mirror called COMPLETE gets shipped across an air gap.
"""

from __future__ import annotations

from pathlib import Path

from gpygap.advisories import AdvisorySummary, Finding
from gpygap.downloader import DownloadResult, is_present
from gpygap.models import LockedPackage, MirrorFile, MirrorInfo
from gpygap.platforms import PLATFORMS, get_platforms
from gpygap.prune import PruneResult
from gpygap.report import RunReport, TopStatus, VerificationSummary, top_statuses
from gpygap.resolver import Resolution, ResolveRequest
from gpygap.verify import (
    BuildOutcome,
    BuildResult,
    InstallCheck,
    InstallOutcome,
    InstallResult,
    unresolved_requirements,
)

LINUX = PLATFORMS["linux-x86_64"]
INFO = MirrorInfo(
    version="20261015-120000",
    created="2026-10-15T12:00:00+00:00",
    top_last_update="2026-10-01",
    top_projects=("urllib3",),
    extra_requirements=(),
    python_versions=("3.12",),
    platforms=("linux-x86_64",),
    projects=1,
    files=1,
    bytes=10,
)


def report(**kwargs: object) -> RunReport:
    defaults: dict[str, object] = {
        "run_id": "20261015-120000",
        "duration_s": 1,
        "info": INFO,
        "downloads": DownloadResult(),
        "pruned": PruneResult(),
        "top": {},
        "verification": None,
        "delta": None,
    }
    defaults.update(kwargs)
    return RunReport(**defaults)  # type: ignore[arg-type]


# ------------------------------------------------------------------ outcomes


def test_a_mirror_with_nothing_missing_is_complete() -> None:
    summary = VerificationSummary.from_results(
        [
            InstallResult(InstallCheck("urllib3", "3.12", LINUX), InstallOutcome.WHEELS),
            InstallResult(InstallCheck("spam", "3.12", LINUX), InstallOutcome.BUILD),
            InstallResult(InstallCheck("win-only", "3.12", LINUX), InstallOutcome.UNAVAILABLE),
        ],
        [BuildResult("packages/spam/spam-1.tar.gz", BuildOutcome.FAILED)],
        heal_rounds=1,
    )
    # A build that needs a compiler, and a project that does not support the target,
    # are both facts about the world rather than holes in the mirror.
    assert summary.complete
    assert summary.installs == {"wheels": 1, "build": 1, "unavailable": 1}


def test_a_missing_install_makes_the_mirror_incomplete() -> None:
    summary = VerificationSummary.from_results(
        [InstallResult(InstallCheck("ghost", "3.12", LINUX), InstallOutcome.MISSING)], [], heal_rounds=2
    )
    assert not summary.complete
    assert summary.missing == ("ghost @ py3.12 linux-x86_64",)


def test_unresolvable_build_requirements_make_the_mirror_incomplete() -> None:
    summary = VerificationSummary.from_results(
        [],
        [BuildResult("packages/spam/spam-1.tar.gz", BuildOutcome.MISSING_DEPS, ("cython<3",))],
        heal_rounds=0,
    )
    assert not summary.complete
    assert summary.missing_build_deps == ("packages/spam/spam-1.tar.gz: cython<3",)


def test_a_build_gap_with_nothing_parsed_still_names_the_sdist() -> None:
    summary = VerificationSummary.from_results(
        [], [BuildResult("packages/spam/spam-1.tar.gz", BuildOutcome.MISSING_DEPS)], heal_rounds=0
    )
    assert summary.missing_build_deps == ("packages/spam/spam-1.tar.gz: unparsed, see log",)


# ----------------------------------------------------------------- exit code


def test_a_clean_run_exits_zero() -> None:
    assert report().exit_code == 0


def test_an_incomplete_mirror_exits_non_zero() -> None:
    summary = VerificationSummary.from_results(
        [InstallResult(InstallCheck("ghost", "3.12", LINUX), InstallOutcome.MISSING)], [], heal_rounds=0
    )
    assert report(verification=summary).exit_code == 1


def test_a_failed_download_exits_non_zero_even_if_verification_passed() -> None:
    # Pruning is skipped after a failed download, so the mirror is intact; the run
    # still has to say something went wrong or a cron job will never notice.
    assert report(downloads=DownloadResult(files=1, bytes=1, failed=["packages/a/a.whl"])).exit_code == 1


def test_advisories_do_not_fail_a_run_unless_asked() -> None:
    findings = (Finding("spam", "1.0", "GHSA-x", (), "critical", "bad", ()),)
    summary = AdvisorySummary(1, 1, findings, {"critical": 1})
    assert report(advisories=summary).exit_code == 0
    assert report(advisories=summary, advisories_fail_on="critical").exit_code == 1
    assert report(advisories=summary, advisories_fail_on="high").exit_code == 1


def test_a_finding_below_the_threshold_does_not_fail_the_run() -> None:
    findings = (Finding("spam", "1.0", "GHSA-x", (), "low", "meh", ()),)
    summary = AdvisorySummary(1, 1, findings, {"low": 1})
    assert report(advisories=summary, advisories_fail_on="high").exit_code == 0


def test_the_json_report_carries_every_section() -> None:
    document = report(advisories=AdvisorySummary(1, 1, (), {}))._to_json()
    assert set(document) >= {"run_id", "info", "downloads", "pruned", "verification", "advisories"}
    assert document["verification"] is None
    assert document["advisories"]["scanned_projects"] == 1


# -------------------------------------------------------------- top statuses


def test_top_status_tells_stale_apart_from_failed() -> None:
    # The distinction is the whole safety net: a resolution that failed but kept its
    # last good packages still contributes files, so nothing gets pruned under it.
    kept = (LockedPackage(name="urllib3", version="2.0.0", marker=None, files=()),)
    good = Resolution(ResolveRequest("urllib3", "3.12"), (), 0.0)
    stale = Resolution(ResolveRequest("urllib3", "3.13"), kept, 0.0, "upstream blew up")
    failed = Resolution(ResolveRequest("urllib3", "3.14"), (), 0.0, "upstream blew up")

    statuses = top_statuses(
        {r.request: r for r in (good, stale, failed)}, ["urllib3"], ["3.12", "3.13", "3.14"]
    )

    assert statuses["urllib3"].ok == ("3.12",)
    assert statuses["urllib3"].stale == ("3.13",)
    assert statuses["urllib3"].failed == ("3.14",)


def test_top_status_is_a_plain_record() -> None:
    status = TopStatus(ok=("3.12",), stale=(), failed=("3.13",))
    assert status.ok == ("3.12",) and status.failed == ("3.13",)


# ------------------------------------------------------- uv error scraping


def test_requirements_are_scraped_out_of_uvs_error() -> None:
    error = (
        "error: Failed to resolve requirements\n"
        "  No solution found when resolving: `setuptools>=61`, `cython<3`\n"
    )
    assert unresolved_requirements(error) == ("setuptools>=61", "cython<3")


def test_the_same_requirement_mentioned_twice_is_reported_once() -> None:
    error = (
        "No solution found when resolving: `cython`\nNo solution found when resolving: `cython`, `ninja`\n"
    )
    assert unresolved_requirements(error) == ("cython", "ninja")


def test_an_error_uv_phrased_differently_scrapes_to_nothing() -> None:
    # Not a crash and not a guess: the sdist is still reported, with "unparsed".
    assert unresolved_requirements("error: something else entirely") == ()


# ------------------------------------------------------------------ presence


def test_a_file_of_the_wrong_size_counts_as_absent(tmp_path: Path) -> None:
    (tmp_path / "a.whl").write_bytes(b"12345")
    assert is_present(tmp_path, MirrorFile(path="a.whl", url="x", size=5))
    assert not is_present(tmp_path, MirrorFile(path="a.whl", url="x", size=99))
    assert not is_present(tmp_path, MirrorFile(path="missing.whl", url="x", size=5))
    # No recorded size: presence is all that can be checked.
    assert is_present(tmp_path, MirrorFile(path="a.whl", url="x"))


def test_download_results_add_up() -> None:
    total = DownloadResult(files=1, bytes=10, failed=["a"])
    total.merge(DownloadResult(files=2, bytes=20, failed=["b"]))
    assert (total.files, total.bytes, total.failed) == (3, 30, ["a", "b"])


# ----------------------------------------------------------------- platforms


def test_an_unknown_platform_names_the_ones_that_exist() -> None:
    try:
        get_platforms(["linux-x86_64", "solaris-sparc"])
    except ValueError as exc:
        assert "solaris-sparc" in str(exc) and "linux-musl-x86_64" in str(exc)
    else:
        raise AssertionError("an unknown platform must not be accepted silently")
