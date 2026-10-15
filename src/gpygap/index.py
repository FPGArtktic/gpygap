# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Static PEP 503 "simple" index and the mirror's landing page.

The index is plain HTML, so any static web server (nginx, Apache, IIS,
``python -m http.server``) can serve the mirror directory as is.
"""

from __future__ import annotations

import html
import shutil
import urllib.parse
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path

from gpygap.distmeta import DistMeta, DistMetaCache
from gpygap.models import MirrorFile, MirrorInfo
from gpygap.util import human_size, write_if_changed

_SIMPLE_PAGE = """\
<!DOCTYPE html>
<html>
<head><meta name="pypi:repository-version" content="1.0"><title>{title}</title></head>
<body>
{body}
</body>
</html>
"""


def write_simple_index(root: Path, manifest: Mapping[str, MirrorFile], meta: DistMetaCache) -> int:
    """Write ``simple/`` for every package file in ``manifest``; return the project count."""
    projects = _group_by_project(root, manifest)
    simple = root / "simple"
    for project, files in projects.items():
        anchors = "\n".join(_anchor(f, meta.get(f.path)) for f in files)
        page = _SIMPLE_PAGE.format(
            title=f"Links for {project}", body=f"<h1>Links for {project}</h1>\n{anchors}"
        )
        write_if_changed(simple / project / "index.html", page)
    _remove_stale_projects(simple, projects.keys())
    links = "\n".join(f'<a href="{p}/">{p}</a><br/>' for p in projects)
    write_if_changed(simple / "index.html", _SIMPLE_PAGE.format(title="Simple index", body=links))
    return len(projects)


def _group_by_project(root: Path, manifest: Mapping[str, MirrorFile]) -> dict[str, list[MirrorFile]]:
    projects: dict[str, list[MirrorFile]] = {}
    for path in sorted(manifest):
        file = manifest[path]
        if file.project is not None and (root / path).exists():
            projects.setdefault(file.project, []).append(file)
    return dict(sorted(projects.items()))


def _anchor(file: MirrorFile, meta: DistMeta) -> str:
    filename = file.path.rsplit("/", 1)[1]
    href = f"../../packages/{file.project}/{urllib.parse.quote(filename)}"
    if file.sha256:
        href += f"#sha256={file.sha256}"
    attrs = [f'href="{href}"']
    if meta.requires_python:
        attrs.append(f'data-requires-python="{html.escape(meta.requires_python)}"')
    if meta.metadata_sha256:
        # PEP 658 name and its PEP 714 successor; clients read either.
        attrs.append(f'data-dist-info-metadata="sha256={meta.metadata_sha256}"')
        attrs.append(f'data-core-metadata="sha256={meta.metadata_sha256}"')
    return f"<a {' '.join(attrs)}>{html.escape(filename)}</a><br/>"


def _remove_stale_projects(simple: Path, projects: Collection[str]) -> None:
    if not simple.is_dir():
        return
    for directory in simple.iterdir():
        if directory.is_dir() and directory.name not in projects:
            shutil.rmtree(directory)


_LANDING_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>gpygap &mdash; offline PyPI mirror</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 860px; margin: 2rem auto; padding: 0 1rem; }}
body {{ line-height: 1.5; }}
pre {{ background: #f4f4f4; padding: .8rem; overflow-x: auto; }}
</style>
</head>
<body>
<h1>gpygap &mdash; offline PyPI mirror</h1>
<p>Version <b>{version}</b> &middot; top list from {top_update} &middot; {projects} projects &middot;
{files} files ({size}) &middot; Python {pythons} &middot; {platforms}</p>

<h2>Configure uv</h2>
<p>Linux: <code>~/.config/uv/uv.toml</code> or <code>/etc/uv/uv.toml</code><br>
Windows: <code>%APPDATA%\\uv\\uv.toml</code> or <code>%PROGRAMDATA%\\uv\\uv.toml</code></p>
<pre>[[index]]
url = "http://THIS-SERVER/simple"
default = true

python-install-mirror = "http://THIS-SERVER/python"</pre>

<p>Package index: <a href="simple/">simple/</a> &middot; Python builds: <a href="python/">python/</a></p>

<h2>uv binaries</h2>
<ul>
{uv_links}
</ul>

<h2>Top projects</h2>
<p>{top_projects}</p>
</body>
</html>
"""


def write_landing_page(root: Path, info: MirrorInfo, uv_archives: Sequence[str]) -> None:
    """Write ``index.html`` at the mirror root with setup instructions for developers."""
    uv_links = "\n".join(f'<li><a href="{p}">{html.escape(p)}</a></li>' for p in uv_archives)
    page = _LANDING_PAGE.format(
        version=info.version,
        top_update=html.escape(info.top_last_update or "?"),
        projects=info.projects,
        files=info.files,
        size=human_size(info.bytes),
        pythons=", ".join(info.python_versions),
        platforms=", ".join(info.platforms),
        uv_links=uv_links or "<li>none (sync.with_tools is off)</li>",
        top_projects=html.escape(", ".join(info.top_projects)),
    )
    write_if_changed(root / "index.html", page)
