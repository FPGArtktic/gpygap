# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""The files a developer and a web server actually see: the index, the landing
page, the advisory report and the checksum list."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

from gpygap.advisories import AdvisorySummary, Finding
from gpygap.checksums import write_checksums
from gpygap.distmeta import DistMetaCache, build_requirements, inspect_sdist, parse_core_metadata
from gpygap.index import write_advisory_page, write_landing_page, write_simple_index
from gpygap.models import MirrorFile, MirrorInfo
from gpygap.util import human_size, normalize_name, read_json, remove_empty_dirs, write_if_changed

INFO = MirrorInfo(
    version="20261015-120000",
    created="2026-10-15T12:00:00+00:00",
    top_last_update="2026-10-01",
    top_projects=("urllib3", "boto3"),
    extra_requirements=("pip",),
    python_versions=("3.12", "3.13t"),
    platforms=("linux-x86_64", "win-amd64"),
    projects=2,
    files=3,
    bytes=4096,
)


def wheel_at(root: Path, project: str, filename: str, *, requires_python: str = ">=3.9") -> MirrorFile:
    path = f"packages/{project}/{filename}"
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    name, version = filename.split("-")[:2]
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            f"{name}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            f"Requires-Python: {requires_python}\nProvides-Extra: Socks\n",
        )
    return MirrorFile(path=path, url=f"https://example.invalid/{filename}", project=project, sha256="ab" * 32)


# --------------------------------------------------------------------- index


def test_the_simple_index_lists_each_project_with_its_hash_and_metadata(tmp_path: Path) -> None:
    manifest = {f.path: f for f in [wheel_at(tmp_path, "urllib3", "urllib3-2.0.0-py3-none-any.whl")]}
    meta = DistMetaCache(tmp_path, tmp_path / "meta.json")

    assert write_simple_index(tmp_path, manifest, meta) == 1

    page = (tmp_path / "simple" / "urllib3" / "index.html").read_text("utf-8")
    assert "urllib3-2.0.0-py3-none-any.whl" in page
    assert "#sha256=" + "ab" * 32 in page
    assert 'data-requires-python="&gt;=3.9"' in page
    # Both spellings, because clients read one or the other.
    assert "data-dist-info-metadata=" in page and "data-core-metadata=" in page
    assert "urllib3" in (tmp_path / "simple" / "index.html").read_text("utf-8")


def test_a_file_in_the_manifest_but_not_on_disk_is_not_advertised(tmp_path: Path) -> None:
    # A failed download must not become a link that 404s for every developer.
    present = wheel_at(tmp_path, "urllib3", "urllib3-2.0.0-py3-none-any.whl")
    missing = MirrorFile(path="packages/ghost/ghost-1.0-py3-none-any.whl", url="x", project="ghost")
    meta = DistMetaCache(tmp_path, tmp_path / "meta.json")

    assert write_simple_index(tmp_path, {present.path: present, missing.path: missing}, meta) == 1
    assert not (tmp_path / "simple" / "ghost").exists()


def test_a_project_that_left_the_mirror_loses_its_index_page(tmp_path: Path) -> None:
    first = wheel_at(tmp_path, "gone", "gone-1.0-py3-none-any.whl")
    meta = DistMetaCache(tmp_path, tmp_path / "meta.json")
    write_simple_index(tmp_path, {first.path: first}, meta)
    assert (tmp_path / "simple" / "gone").is_dir()

    kept = wheel_at(tmp_path, "stays", "stays-1.0-py3-none-any.whl")
    write_simple_index(tmp_path, {kept.path: kept}, meta)
    assert not (tmp_path / "simple" / "gone").exists()


def test_the_landing_page_links_the_advisory_report_only_once_it_exists(tmp_path: Path) -> None:
    write_landing_page(tmp_path, INFO, ["tools/uv/0.12.9/uv-x86_64-unknown-linux-gnu.tar.gz"])
    assert "advisories.html" not in (tmp_path / "index.html").read_text("utf-8")

    (tmp_path / "advisories.html").write_text("placeholder", "utf-8")
    write_landing_page(tmp_path, INFO, [])
    page = (tmp_path / "index.html").read_text("utf-8")
    assert "advisories.html" in page
    assert "none (sync.with_tools is off)" in page


# ---------------------------------------------------------- advisory report


def test_a_clean_report_still_says_what_it_does_not_prove(tmp_path: Path) -> None:
    summary = AdvisorySummary(scanned_projects=12, scanned_versions=14, findings=(), counts={})
    write_advisory_page(tmp_path, INFO, summary)
    page = (tmp_path / "advisories.html").read_text("utf-8")
    assert "No advisory matches any version held" in page
    assert "has not been shown to be free of defects" in page
    assert "4. Limitations" in page


def test_findings_are_numbered_and_grouped_worst_first(tmp_path: Path) -> None:
    findings = (
        Finding("pyyaml", "5.3", "GHSA-a", ("CVE-1",), "critical", "bad", ("5.3.1",)),
        Finding("urllib3", "1.26.5", "GHSA-b", ("CVE-2",), "high", "worse", ()),
        Finding("urllib3", "1.26.5", "GHSA-c", (), "high", "", ("2.0",)),
    )
    summary = AdvisorySummary(2, 2, findings, {"critical": 1, "high": 2})
    write_advisory_page(tmp_path, INFO, summary)
    page = (tmp_path / "advisories.html").read_text("utf-8")

    assert "Critical &mdash; 1 finding" in page and "High &mdash; 2 findings" in page
    assert page.index("F-001") < page.index("F-002") < page.index("F-003")
    assert page.index("Critical &mdash;") < page.index("High &mdash;")
    assert "no fix listed" not in page and "none published" in page
    assert "3 findings across 2 projects" in page


def test_a_cached_advisory_set_is_declared_on_the_report(tmp_path: Path) -> None:
    summary = AdvisorySummary(1, 1, (), {}, stale=True)
    write_advisory_page(tmp_path, INFO, summary)
    assert "cached" in (tmp_path / "advisories.html").read_text("utf-8")


# ----------------------------------------------------------------- checksums


def test_checksums_cover_every_file_and_prefer_the_recorded_hash(tmp_path: Path) -> None:
    known = wheel_at(tmp_path, "urllib3", "urllib3-2.0.0-py3-none-any.whl")
    (tmp_path / "index.html").write_text("landing", "utf-8")
    (tmp_path / "packages" / "urllib3" / "partial.whl.part").write_text("junk", "utf-8")

    count = write_checksums(tmp_path, {known.path: known}, tmp_path / "cache.json")

    lines = (tmp_path / "MIRROR-SHA256SUMS").read_text("utf-8").splitlines()
    assert count == len(lines) == 2
    assert f"{'ab' * 32}  {known.path}" in lines
    # Temporary names never make it into the list a developer verifies against.
    assert not any(".part" in line for line in lines)
    landing = next(line for line in lines if line.endswith("index.html"))
    assert landing.startswith(hashlib.sha256(b"landing").hexdigest())


def test_an_unchanged_file_keeps_its_hash_from_the_cache(tmp_path: Path) -> None:
    # The cache lives in the state directory, outside the mirror, or it would end
    # up hashing itself.
    root = tmp_path / "mirror"
    root.mkdir()
    (root / "a.txt").write_text("content", "utf-8")
    cache = tmp_path / "state" / "checksum-cache.json"
    write_checksums(root, {}, cache)
    first = read_json(cache)
    write_checksums(root, {}, cache)
    assert read_json(cache) == first
    assert first is not None and list(first) == ["a.txt"]


# ------------------------------------------------------------------ distmeta


def test_core_metadata_keeps_requires_python_and_normalizes_extras() -> None:
    parsed = parse_core_metadata(
        b"Metadata-Version: 2.1\nName: x\nRequires-Python: >=3.9\n"
        b"Provides-Extra: Socks\nProvides-Extra: use_chardet\n"
    )
    assert parsed.requires_python == ">=3.9"
    assert parsed.provides_extra == ("socks", "use-chardet")


def test_an_sdist_build_backend_is_read_without_unpacking_the_archive(tmp_path: Path) -> None:
    sdist = tmp_path / "packages" / "spam" / "spam-1.0.tar.gz"
    sdist.parent.mkdir(parents=True)
    pyproject = b'[build-system]\nrequires = ["setuptools>=61", "cython"]\n'
    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo("spam-1.0/pyproject.toml")
        info.size = len(pyproject)
        archive.addfile(info, io.BytesIO(pyproject))

    assert inspect_sdist(sdist).build_requires == ("setuptools>=61", "cython")


def test_an_sdist_without_pyproject_falls_back_to_the_legacy_backend(tmp_path: Path) -> None:
    sdist = tmp_path / "packages" / "old" / "old-1.0.tar.gz"
    sdist.parent.mkdir(parents=True)
    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo("old-1.0/setup.py")
        info.size = 0
        archive.addfile(info, io.BytesIO(b""))

    assert inspect_sdist(sdist).build_requires == ("setuptools>=40.8.0", "wheel")


def test_build_requirements_are_collected_and_whitespace_normalized(tmp_path: Path) -> None:
    sdist = tmp_path / "packages" / "spam" / "spam-1.0.tar.gz"
    sdist.parent.mkdir(parents=True)
    pyproject = b'[build-system]\nrequires = ["setuptools  >=  61"]\n'
    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo("spam-1.0/pyproject.toml")
        info.size = len(pyproject)
        archive.addfile(info, io.BytesIO(pyproject))
    meta = DistMetaCache(tmp_path, tmp_path / "meta.json")

    found = build_requirements(
        meta, ["packages/spam/spam-1.0.tar.gz", "packages/ghost/ghost-1.tar.gz"], tmp_path
    )
    assert found == {"setuptools >= 61"}


# ---------------------------------------------------------------------- util


def test_write_if_changed_leaves_an_identical_file_alone(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b.txt"
    assert write_if_changed(target, "same") is True
    before = target.stat().st_mtime_ns
    assert write_if_changed(target, "same") is False
    assert target.stat().st_mtime_ns == before
    assert write_if_changed(target, "different") is True


def test_empty_directories_are_removed_but_the_root_stays(tmp_path: Path) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "file.txt").write_text("x", "utf-8")
    (tmp_path / "drop" / "deeper").mkdir(parents=True)

    remove_empty_dirs(tmp_path)

    assert (tmp_path / "keep" / "file.txt").is_file()
    assert not (tmp_path / "drop").exists()
    assert tmp_path.is_dir()


def test_names_are_normalized_the_way_pep_503_says() -> None:
    assert normalize_name("Zope.Interface") == "zope-interface"
    assert normalize_name("ruamel__yaml") == "ruamel-yaml"


def test_sizes_read_the_way_an_operator_expects() -> None:
    assert human_size(0).startswith("0")
    assert "KB" in human_size(2048) or "KiB" in human_size(2048)
    assert human_size(5 * 1024**3).startswith("5")


def test_unreadable_json_is_none_rather_than_an_exception(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", "utf-8")
    assert read_json(broken) is None
    assert read_json(tmp_path / "absent.json") is None
