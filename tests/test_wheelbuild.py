# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gpygap.config import load_config
from gpygap.models import BuildTarget
from gpygap.platforms import PLATFORMS
from gpygap.wheelbuild import (
    BuildGap,
    BuildSummary,
    BuiltWheel,
    WheelFactory,
    _cache_key,
    python_executable,
    targets_satisfied,
)

LINUX = BuildTarget("linux-x86_64", "3.12")
MUSL = BuildTarget("linux-musl-x86_64", "3.12")
WINDOWS = BuildTarget("win-amd64", "3.12")
LINUX_313 = BuildTarget("linux-x86_64", "3.13")
ALL = {LINUX, MUSL, WINDOWS, LINUX_313}


def config_at(tmp_path: Path, body: str = "") -> object:
    path = tmp_path / "gpygap.toml"
    path.write_text(body, "utf-8")
    return load_config(path, {})


@pytest.mark.parametrize(
    ("python", "expected"),
    [
        ("3.12", "/opt/python/cp312-cp312/bin/python"),
        ("3.9", "/opt/python/cp39-cp39/bin/python"),
        ("3.13t", "/opt/python/cp313-cp313t/bin/python"),
        ("3.14t", "/opt/python/cp314-cp314t/bin/python"),
    ],
)
def test_python_executable_maps_the_version_to_the_image_layout(python: str, expected: str) -> None:
    assert python_executable(python) == expected


def test_a_universal_wheel_satisfies_every_target_including_the_unbuildable_ones() -> None:
    # This is what lets a build on Linux close a Windows gap: most projects that
    # publish no wheel at all are pure Python.
    assert targets_satisfied("docopt-0.6.2-py3-none-any.whl", ALL) == ALL


def test_a_platform_wheel_satisfies_only_its_own_platform_and_interpreter() -> None:
    name = "pysendfile-2.0.1-cp312-cp312-manylinux_2_28_x86_64.whl"
    assert targets_satisfied(name, ALL) == {LINUX}


def test_a_musllinux_wheel_does_not_satisfy_a_glibc_target() -> None:
    name = "pysendfile-2.0.1-cp312-cp312-musllinux_1_2_x86_64.whl"
    assert targets_satisfied(name, ALL) == {MUSL}


def test_an_unknown_platform_key_is_ignored_rather_than_raising() -> None:
    assert targets_satisfied("x-1-py3-none-any.whl", {BuildTarget("solaris-sparc", "3.12")}) == set()


def test_built_wheel_reports_provenance_and_universality() -> None:
    wheel = BuiltWheel(
        path="packages/docopt/docopt-0.6.2-py3-none-any.whl",
        sha256="ab" * 32,
        size=12,
        project="docopt",
        sdist="packages/docopt/docopt-0.6.2.tar.gz",
        targets=(LINUX,),
    )
    assert wheel.universal
    mirror_file = wheel.mirror_file()
    assert mirror_file.origin == "built"
    # No URL: nothing can re-download it, which is why pruning has to be told it exists.
    assert mirror_file.url == ""
    assert mirror_file.sha256 == "ab" * 32


def test_summary_separates_what_failed_from_what_nothing_could_build() -> None:
    summary = BuildSummary(
        gaps=[
            BuildGap("packages/a/a.tar.gz", WINDOWS, "no-builder"),
            BuildGap("packages/a/a.tar.gz", LINUX, "build-failed", "compiler said no"),
        ]
    )
    assert [g.target for g in summary.unbuildable] == [WINDOWS]
    assert [g.target for g in summary.failures] == [LINUX]
    assert summary.to_json()["gaps"][0]["target"] == "win-amd64/py3.12"


def test_building_is_a_no_op_when_switched_off(tmp_path: Path) -> None:
    config = config_at(tmp_path, "[build]\nwheels = false\n")
    factory = WheelFactory(config)  # type: ignore[arg-type]
    summary = factory.run({"packages/a/a.tar.gz": {LINUX}}, {})
    assert summary.built == [] and summary.gaps == []


def test_a_missing_engine_turns_every_target_into_a_reported_gap(tmp_path: Path) -> None:
    # Silence here would be the worst outcome: the run would look complete while
    # every package without an upstream wheel had quietly been left out.
    config = config_at(tmp_path, '[build]\nengine = "definitely-not-installed"\n')
    sdist = tmp_path / "gpygap" / "packages" / "a" / "a-1.tar.gz"
    sdist.parent.mkdir(parents=True)
    sdist.write_text("x", "utf-8")
    factory = WheelFactory(config)  # type: ignore[arg-type]
    summary = factory.run({"packages/a/a-1.tar.gz": {LINUX, WINDOWS}}, {})
    assert {g.reason for g in summary.gaps} == {"no-engine"}
    assert len(summary.gaps) == 2


def test_an_excluded_project_is_never_built(tmp_path: Path) -> None:
    config = config_at(tmp_path, '[build]\nexclude = ["Spam"]\n')
    sdist = tmp_path / "gpygap" / "packages" / "spam" / "spam-1.tar.gz"
    sdist.parent.mkdir(parents=True)
    sdist.write_text("x", "utf-8")
    factory = WheelFactory(config)  # type: ignore[arg-type]
    summary = factory._build_sdist("packages/spam/spam-1.tar.gz", [LINUX], {})
    assert [g.reason for g in summary.gaps] == ["excluded"]


def test_windows_has_no_builder_and_says_so(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    factory = WheelFactory(config)  # type: ignore[arg-type]
    sdist = tmp_path / "gpygap" / "packages" / "a" / "a-1.tar.gz"
    sdist.parent.mkdir(parents=True)
    sdist.write_text("x", "utf-8")
    summary = factory._build_sdist("packages/a/a-1.tar.gz", [WINDOWS], {"packages/a/a-1.tar.gz": "cd" * 32})
    assert [g.reason for g in summary.gaps] == ["no-builder"]


def test_registering_the_same_wheel_twice_merges_the_targets_it_covers(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    factory = WheelFactory(config)  # type: ignore[arg-type]
    wheel = tmp_path / "gpygap" / "packages" / "docopt" / "docopt-0.6.2-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_text("wheel", "utf-8")
    factory._register("packages/docopt/docopt-0.6.2.tar.gz", "docopt", wheel.name, {LINUX})
    merged = factory._register("packages/docopt/docopt-0.6.2.tar.gz", "docopt", wheel.name, {WINDOWS})
    assert set(merged.targets) == {LINUX, WINDOWS}
    assert len(factory.files) == 1


def test_a_cached_entry_is_dropped_when_its_wheel_is_no_longer_there(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    factory = WheelFactory(config)  # type: ignore[arg-type]
    gone = factory._from_cache({"wheel": "a-1-py3-none-any.whl"}, "packages/a/a-1.tar.gz", "a", {LINUX})
    assert gone is None


def test_the_cache_survives_a_round_trip(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    factory = WheelFactory(config)  # type: ignore[arg-type]
    factory._cache["key"] = {"wheel": "a-1-py3-none-any.whl"}
    factory._save_cache()
    reloaded = WheelFactory(config)  # type: ignore[arg-type]
    assert reloaded._cache == {"key": {"wheel": "a-1-py3-none-any.whl"}}


def test_a_cache_from_another_format_version_is_ignored(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    state = Path(str(config.state.built_wheels))  # type: ignore[attr-defined]
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"version": 999, "entries": {"k": {"wheel": "w.whl"}}}), "utf-8")
    assert WheelFactory(config)._cache == {}  # type: ignore[arg-type]


def test_an_upstream_wheel_of_the_same_name_is_never_overwritten(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    factory = WheelFactory(config)  # type: ignore[arg-type]
    upstream = tmp_path / "gpygap" / "packages" / "a" / "a-1-py3-none-any.whl"
    upstream.parent.mkdir(parents=True)
    upstream.write_text("the file the project published", "utf-8")
    fresh = tmp_path / "a-1-py3-none-any.whl"
    fresh.write_text("ours", "utf-8")
    factory._publish(fresh, "packages/a/a-1.tar.gz")
    assert upstream.read_text("utf-8") == "the file the project published"


def test_an_image_override_wins_over_the_platform_default(tmp_path: Path) -> None:
    config = config_at(tmp_path, '[build]\nimages = ["linux-x86_64=example.invalid/img:1"]\n')
    factory = WheelFactory(config)  # type: ignore[arg-type]
    assert factory.image_for(PLATFORMS["linux-x86_64"]) == "example.invalid/img:1"
    assert factory.image_for(PLATFORMS["linux-aarch64"]) == PLATFORMS["linux-aarch64"].build_image
    assert factory.image_for(PLATFORMS["win-amd64"]) is None


def test_a_wheel_built_elsewhere_is_adopted(tmp_path: Path) -> None:
    external = tmp_path / "from-windows"
    external.mkdir()
    (external / "spam-1.0-cp312-cp312-win_amd64.whl").write_text("wheel", "utf-8")
    config = config_at(tmp_path, '[build]\nexternal_dir = "from-windows"\n')
    factory = WheelFactory(config)  # type: ignore[arg-type]

    adopted = factory.ingest({"packages/spam/spam-1.0.tar.gz": {WINDOWS, LINUX}})

    assert [w.path for w in adopted] == ["packages/spam/spam-1.0-cp312-cp312-win_amd64.whl"]
    # Only the target it actually installs on, even though two were offered.
    assert adopted[0].targets == (WINDOWS,)
    assert adopted[0].mirror_file().origin == "built"
    assert (tmp_path / "gpygap" / adopted[0].path).is_file()


def test_a_pure_python_wheel_covers_linux_whatever_machine_built_it(tmp_path: Path) -> None:
    # The symmetry that makes the Windows builder worth having in both directions:
    # py3-none-any carries no trace of where it was produced, so a wheel a Windows
    # machine built from a pure-Python sdist serves the Linux targets as well.
    external = tmp_path / "from-windows"
    external.mkdir()
    (external / "docopt-0.6.2-py2.py3-none-any.whl").write_text("wheel", "utf-8")
    config = config_at(tmp_path, '[build]\nexternal_dir = "from-windows"\n')
    factory = WheelFactory(config)  # type: ignore[arg-type]

    adopted = factory.ingest({"packages/docopt/docopt-0.6.2.tar.gz": {WINDOWS, LINUX, MUSL}})

    assert set(adopted[0].targets) == {WINDOWS, LINUX, MUSL}


def test_an_unrelated_wheel_in_the_external_directory_is_left_alone(tmp_path: Path) -> None:
    external = tmp_path / "from-windows"
    external.mkdir()
    (external / "something-else-1.0-py3-none-any.whl").write_text("wheel", "utf-8")
    (external / "notawheel.txt").write_text("x", "utf-8")
    config = config_at(tmp_path, '[build]\nexternal_dir = "from-windows"\n')
    factory = WheelFactory(config)  # type: ignore[arg-type]

    assert factory.ingest({"packages/spam/spam-1.0.tar.gz": {WINDOWS}}) == []


def test_ingest_is_a_no_op_without_a_directory(tmp_path: Path) -> None:
    factory = WheelFactory(config_at(tmp_path))  # type: ignore[arg-type]
    assert factory.ingest({"packages/spam/spam-1.0.tar.gz": {WINDOWS}}) == []


def test_one_sdists_universal_wheel_does_not_erase_another_sdists_gap(tmp_path: Path) -> None:
    # The bug this pins: docopt builds to py3-none-any and covers win-amd64 for
    # docopt. llist is a C extension and still has nothing for win-amd64. Comparing
    # targets without their sdist made the first hide the second, and the file a
    # Windows builder reads came out empty while the run reported four missing.
    config = config_at(tmp_path)
    factory = WheelFactory(config)  # type: ignore[arg-type]
    factory._gaps[("packages/llist/llist-0.7.1.tar.gz", WINDOWS)] = BuildGap(
        "packages/llist/llist-0.7.1.tar.gz", WINDOWS, "no-builder"
    )
    universal = tmp_path / "gpygap" / "packages" / "docopt" / "docopt-0.6.2-py3-none-any.whl"
    universal.parent.mkdir(parents=True)
    universal.write_text("w", "utf-8")
    factory._register("packages/docopt/docopt-0.6.2.tar.gz", "docopt", universal.name, {WINDOWS})

    assert [g.sdist for g in factory.gaps] == ["packages/llist/llist-0.7.1.tar.gz"]


def test_gaps_drop_out_once_something_covers_them(tmp_path: Path) -> None:
    config = config_at(tmp_path)
    factory = WheelFactory(config)  # type: ignore[arg-type]
    factory._gaps[("packages/a/a-1.tar.gz", WINDOWS)] = BuildGap(
        "packages/a/a-1.tar.gz", WINDOWS, "no-builder"
    )
    assert len(factory.gaps) == 1

    wheel = tmp_path / "gpygap" / "packages" / "a" / "a-1-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_text("w", "utf-8")
    factory._register("packages/a/a-1.tar.gz", "a", wheel.name, {WINDOWS})
    # A universal wheel closed it, so it is no longer a gap to report.
    assert factory.gaps == []


def test_a_cache_hit_whose_wheel_vanished_triggers_a_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pruning or a stray rm must not turn into a permanent gap: the entry is dropped
    # and the wheel is built again. Reporting a gap for a file we know how to
    # recreate would be a lie that survived every later run.
    config = config_at(tmp_path)
    factory = WheelFactory(config)  # type: ignore[arg-type]
    image = PLATFORMS["linux-x86_64"].build_image or ""
    key = _cache_key("ab" * 32, LINUX, image)
    factory._cache[key] = {"wheel": "a-1-py3-none-any.whl"}

    rebuilt = []

    def fake_build(sdist: str, target: BuildTarget, img: str) -> tuple[str | None, str]:
        rebuilt.append((sdist, target))
        wheel = tmp_path / "gpygap" / "packages" / "a" / "a-1-py3-none-any.whl"
        wheel.parent.mkdir(parents=True, exist_ok=True)
        wheel.write_text("rebuilt", "utf-8")
        return wheel.name, ""

    monkeypatch.setattr(factory, "_run_container", fake_build)
    wheel, cached, _ = factory._wheel_for(
        "packages/a/a-1.tar.gz", "a", LINUX, image=image, digest="ab" * 32, candidates={LINUX}
    )

    assert rebuilt == [("packages/a/a-1.tar.gz", LINUX)]
    assert wheel is not None and not cached
    assert factory._cache[key] == {"wheel": "a-1-py3-none-any.whl"}


def test_a_cached_failure_is_a_result_not_a_reason_to_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = config_at(tmp_path)
    factory = WheelFactory(config)  # type: ignore[arg-type]
    image = PLATFORMS["linux-x86_64"].build_image or ""
    factory._cache[_cache_key("cd" * 32, LINUX, image)] = {"wheel": "", "error": "no compiler"}

    def never(sdist: str, target: BuildTarget, img: str) -> tuple[str | None, str]:
        raise AssertionError("a cached failure must not be rebuilt")

    monkeypatch.setattr(factory, "_run_container", never)
    wheel, cached, error = factory._wheel_for(
        "packages/a/a-1.tar.gz", "a", LINUX, image=image, digest="cd" * 32, candidates={LINUX}
    )
    assert wheel is None and cached and error == "no compiler"
