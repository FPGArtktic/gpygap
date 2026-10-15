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

from gpygap.advisories import AdvisorySummary
from gpygap.distmeta import DistMeta, DistMetaCache
from gpygap.models import SEVERITIES, MirrorFile, MirrorInfo
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

<p>Package index: <a href="simple/">simple/</a> &middot;
Python builds: <a href="python/">python/</a>{security_link}</p>

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
        security_link=(
            ' &middot; Known vulnerabilities: <a href="advisories.html">advisories.html</a>'
            if (root / ADVISORY_PAGE).exists()
            else ""
        ),
    )
    write_if_changed(root / "index.html", page)


ADVISORY_PAGE = "advisories.html"

_ADVISORY_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Security Advisory Report &mdash; gpygap mirror {version}</title>
<style>
:root {{
  --ink: #16191d; --muted: #5b6672; --rule: #d8dde3; --paper: #ffffff;
  --panel: #f6f8fa; --accent: #1f4e79;
  --critical: #8b0000; --high: #b3301c; --moderate: #9a6700; --low: #4a5560; --unknown: #6b7682;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --ink: #e8eaed; --muted: #9aa4b0; --rule: #333941; --paper: #14171a;
    --panel: #1b1f24; --accent: #7fb3e0;
    --critical: #ff7a7a; --high: #ff9b7a; --moderate: #ffc166; --low: #aeb6c0; --unknown: #8e97a3;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  font-family: "Charter", "Georgia", "Times New Roman", serif;
  color: var(--ink); background: var(--paper);
  max-width: 62rem; margin: 0 auto; padding: 3rem 1.5rem 5rem; line-height: 1.6;
}}
header.doc {{ border-bottom: 3px double var(--rule); padding-bottom: 1.5rem; margin-bottom: 2rem; }}
.eyebrow {{
  font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
  font-size: .72rem; letter-spacing: .18em; text-transform: uppercase;
  color: var(--muted); margin: 0 0 .4rem;
}}
h1 {{ font-size: 2rem; margin: 0 0 .3rem; letter-spacing: -.01em; }}
.subtitle {{ color: var(--muted); margin: 0; font-size: 1.05rem; }}
h2 {{
  font-size: 1.15rem; margin: 2.6rem 0 .8rem; padding-bottom: .3rem;
  border-bottom: 1px solid var(--rule); letter-spacing: .01em;
}}
h3 {{ font-size: 1rem; margin: 1.8rem 0 .6rem; color: var(--accent); }}
p {{ margin: .6rem 0; }}
table {{ border-collapse: collapse; width: 100%; margin: .8rem 0 1.4rem; font-size: .9rem; }}
caption {{
  caption-side: top; text-align: left; font-size: .78rem; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted); padding-bottom: .4rem;
}}
th, td {{ text-align: left; padding: .45rem .7rem; vertical-align: top; }}
th, td {{ border-bottom: 1px solid var(--rule); }}
thead th {{
  background: var(--panel); font-size: .74rem; letter-spacing: .09em;
  text-transform: uppercase; color: var(--muted); font-weight: 600;
}}
tbody tr:last-child td {{ border-bottom: 2px solid var(--rule); }}
code, .mono {{ font-family: ui-monospace, "SFMono-Regular", Menlo, monospace; font-size: .86em; }}
dl.control {{
  display: grid; grid-template-columns: minmax(10rem, max-content) 1fr;
  gap: .35rem 1.4rem; margin: 1.2rem 0 0; font-size: .9rem;
}}
dl.control dt {{
  font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
  font-size: .74rem; letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
  padding-top: .15rem;
}}
dl.control dd {{ margin: 0; }}
.sev {{ font-weight: 700; white-space: nowrap; font-variant-caps: all-small-caps; letter-spacing: .06em; }}
.sev.critical {{ color: var(--critical); }}
.sev.high {{ color: var(--high); }}
.sev.moderate {{ color: var(--moderate); }}
.sev.low {{ color: var(--low); }}
.sev.unknown {{ color: var(--unknown); }}
.callout {{ border-left: 3px solid var(--accent); background: var(--panel); }}
.callout {{ padding: .9rem 1.2rem; margin: 1.2rem 0; }}
.callout.clear {{ border-left-color: #2e7d32; }}
.callout p:first-child {{ margin-top: 0; }}
.callout p:last-child {{ margin-bottom: 0; }}
.total {{ font-weight: 700; }}
footer.doc {{
  margin-top: 3.5rem; padding-top: 1rem; border-top: 1px solid var(--rule);
  color: var(--muted); font-size: .82rem; display: flex; justify-content: space-between; gap: 1rem;
}}
@media print {{
  body {{ max-width: none; padding: 0; font-size: 10.5pt; }}
  h2 {{ page-break-after: avoid; }}
  table {{ page-break-inside: auto; }}
  tr {{ page-break-inside: avoid; }}
  footer.doc {{ position: fixed; bottom: 0; width: 100%; }}
}}
</style>
</head>
<body>
<header class="doc">
<p class="eyebrow">Security advisory report</p>
<h1>Known vulnerabilities in mirrored packages</h1>
<p class="subtitle">Offline PyPI mirror &mdash; produced by gpygap</p>
<dl class="control">
<dt>Mirror version</dt><dd class="mono">{version}</dd>
<dt>Mirror built</dt><dd class="mono">{created}</dd>
<dt>Advisory source</dt><dd>OSV, PyPI ecosystem{stale_note}</dd>
<dt>Scope</dt><dd>{projects} projects, {versions} distinct versions published by this mirror</dd>
<dt>Result</dt><dd class="total">{verdict}</dd>
</dl>
</header>

<h2>1. Summary</h2>
{summary_table}

<h2>2. Scope and method</h2>
<p>Every distribution file published by this mirror was reduced to a
<span class="mono">(project, version)</span> pair and matched against the OSV advisory
set for the PyPI ecosystem. Where an advisory enumerates affected versions, that list
was used; where it does not, its version ranges were evaluated under PEP&nbsp;440
ordering. Withdrawn advisories and advisories for other ecosystems were excluded.</p>
<p>The scan covers what the mirror <em>contains</em>. It does not consider which of
those packages any particular project installs, nor how they are used, so a finding
here is not by itself an exposure.</p>

<h2>3. Findings</h2>
{findings}

<h2>4. Limitations</h2>
<div class="callout">
<p><strong>This is a snapshot.</strong> It reflects the advisory set as it stood when
this mirror was built. Advisories published afterwards are not represented; re-running
the mirror refreshes both the packages and this report.</p>
<p><strong>Advisory data is incomplete and imperfect.</strong> It contains false
positives, and it does not contain every vulnerability that exists. A package absent
from this report has not been shown to be free of defects.</p>
<p><strong>Severity is the publisher's.</strong> Ratings are taken from the advisory
database unchanged and have not been reassessed against this environment.</p>
</div>

<footer class="doc">
<span>gpygap &middot; mirror {version}</span>
<span>Machine-readable: <a href="MIRROR-ADVISORIES.json">MIRROR-ADVISORIES.json</a>
&middot; <a href="index.html">mirror index</a></span>
</footer>
</body>
</html>
"""


def write_advisory_page(root: Path, info: MirrorInfo, summary: AdvisorySummary) -> None:
    """Write a browsable advisory report next to the index.

    The people who most need this list are the ones who cannot query an API, so it is
    served from the mirror itself rather than printed once on the online side. It is
    laid out as a report because that is how it gets read: by somebody deciding
    whether to let a package into a network they cannot easily patch.
    """
    findings = len(summary.findings)
    page = _ADVISORY_PAGE.format(
        version=html.escape(info.version),
        created=html.escape(info.created),
        projects=summary.scanned_projects,
        versions=summary.scanned_versions,
        stale_note=" (cached copy; no source was reachable)" if summary.stale else "",
        verdict=(
            "No advisory matches any version held"
            if not findings
            else f"{findings} finding{'s' if findings != 1 else ''} across "
            f"{len({f.project for f in summary.findings})} projects"
        ),
        summary_table=_summary_table(summary),
        findings=_findings_sections(summary),
    )
    write_if_changed(root / ADVISORY_PAGE, page)


def _summary_table(summary: AdvisorySummary) -> str:
    rows = []
    for severity in reversed(SEVERITIES):
        count = summary.counts.get(severity, 0)
        projects = len({f.project for f in summary.findings if f.severity == severity})
        rows.append(f'<tr><td class="sev {severity}">{severity}</td><td>{count}</td><td>{projects}</td></tr>')
    total_projects = len({f.project for f in summary.findings})
    return (
        "<table><caption>Findings by severity</caption>"
        "<thead><tr><th>Severity</th><th>Findings</th><th>Projects</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        f'<tfoot><tr><td class="total">Total</td><td class="total">{len(summary.findings)}</td>'
        f'<td class="total">{total_projects}</td></tr></tfoot></table>'
    )


def _findings_sections(summary: AdvisorySummary) -> str:
    if not summary.findings:
        return (
            '<div class="callout clear"><p>No advisory in the OSV PyPI set matches any '
            "version this mirror holds. See section 4 before reading that as an "
            "assurance.</p></div>"
        )
    sections = []
    index = 0
    for severity in reversed(SEVERITIES):
        group = [f for f in summary.findings if f.severity == severity]
        if not group:
            continue
        rows = []
        for finding in group:
            index += 1
            rows.append(
                "<tr>"
                f'<td class="mono">F-{index:03d}</td>'
                f"<td><code>{html.escape(finding.project)}</code></td>"
                f"<td><code>{html.escape(finding.version)}</code></td>"
                f'<td class="mono">{html.escape(finding.advisory)}</td>'
                f'<td class="mono">{html.escape(", ".join(finding.aliases) or "—")}</td>'
                f'<td class="mono">{html.escape(", ".join(finding.fixed_in) or "none published")}</td>'
                f"<td>{html.escape(finding.summary) or '&mdash;'}</td>"
                "</tr>"
            )
        sections.append(
            f"<h3>{severity.capitalize()} &mdash; {len(group)} "
            f"finding{'s' if len(group) != 1 else ''}</h3>"
            "<table><thead><tr><th>Ref</th><th>Project</th><th>Version</th>"
            "<th>Advisory</th><th>Aliases</th><th>Fixed in</th><th>Summary</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )
    return "\n".join(sections)
