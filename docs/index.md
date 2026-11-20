<div class="hero" markdown>

# gpygap

<p class="tagline" markdown>
An offline PyPI mirror for air-gapped networks. The top 1000 projects plus your own,
with every dependency, extra and build requirement, for Linux and Windows across five
Python versions — and the wheels upstream never published, built here so that installing
on the other side of the gap needs no compiler.
</p>

[Read the pipeline](pipeline.md){ .md-button .md-button--primary }
[Run it](operations.md){ .md-button }
[What CI proves](ci.md){ .md-button }

</div>

<div class="grid cards" markdown>

-   :material-sitemap:{ .lg .middle } **How it works**

    ---

    One run from end to end, the modules that perform it, and how the mirror proves
    itself complete before it ships.

    [:octicons-arrow-right-24: The pipeline, end to end](pipeline.md) ·
    [Architecture](architecture.md) ·
    [Verification](verification.md)

-   :material-package-variant-closed:{ .lg .middle } **Wheels we build**

    ---

    What happens when a project publishes no wheel for a target, why Windows is the
    exception, and how a Windows machine hands one back.

    [:octicons-arrow-right-24: Wheels we build](wheels.md)

-   :material-server:{ .lg .middle } **Running it**

    ---

    The monthly routine, moving deltas by USB, DVD or a data diode, and what to do when
    a run fails.

    [:octicons-arrow-right-24: Where to run it](setups.md) ·
    [Operations](operations.md) ·
    [Scanning a project](scan.md) ·
    [On Windows](online-on-windows.md)

-   :material-shield-alert:{ .lg .middle } **Known vulnerabilities**

    ---

    Every version the mirror holds, matched against the OSV advisory set, in a report
    that travels with it.

    [:octicons-arrow-right-24: Advisories](advisories.md)

-   :material-file-tree:{ .lg .middle } **Files and formats**

    ---

    Everything the tool writes: the mirror layout, the delta format, the state
    directory.

    [:octicons-arrow-right-24: Files and formats](file-formats.md)

-   :material-wrench:{ .lg .middle } **Working on it**

    ---

    Commands, module layout, the invariants worth reading twice, and what CI proves on
    every push.

    [:octicons-arrow-right-24: Development](development.md) ·
    [Continuous integration](ci.md) ·
    [Design decisions](decisions.md)

</div>

## In the repository

| File | What it is |
| --- | --- |
| `README.md` | User guide with Quickstart, for the people running the mirror and for developers configuring their machines. |
| `gpygap.toml` | Every configuration key, documented inline. |
| `packages.txt` | Our own packages, mirrored on top of the top list. |
| `intake/` | Team lock files, mirrored exactly as locked. |
| `CHANGELOG.md` | What changed in each release, and what it means for an existing mirror. |
| `CONTRIBUTING.md` | How to propose a change: setup, what a finished change looks like, commit style. |
| `LICENSE` | GPL-3.0-or-later. |

## Author

[Mateusz Okulanis](https://fpgartktic.github.io/) &mdash; <https://fpgartktic.github.io/>.
Licensed GPL-3.0-or-later.

## Contributors

- **Mateusz Chudzik** &mdash; <matchupikchu99@gmail.com>
    - `gpygap scan <directory>` (1.1.0): works out what a project needs from its own
      directory, from declared dependencies and from every `import` parsed with `ast`, with
      `--check-index`, `--strict` and `--force`; see [Scanning a project](scan.md).
    - `intake.requirement_lines()` made public, so `scan` and `intake/` share one
      requirements parser.
    - Submodules in `gpygap scan` (1.2.0): the scan reads `.gitmodules` instead of
      trusting the directory, so a clone made without `--recurse-submodules` is no longer
      reported as complete. Populated submodules are scanned with the submodule named in
      each provenance comment; empty ones are listed in an `INCOMPLETE` block and fail
      `--strict`; and `--init-submodules` fills them on request. See
      [Scanning a project](scan.md).
    - Wheel builds probe the container engine only when a target needs one (1.2.0), so a
      Windows host without a running Docker daemon still builds its native `win-amd64`
      wheels.
    - The opt-in `.githooks/pre-commit` hook, which refuses a commit that adds a path from
      the committer's own machine.
    - The changelog and the release notes for 1.1.0 and 1.2.0.
    - CI: the unit suite on Windows for every supported Python, and retries for the
      consume image build when a distribution mirror is briefly unreachable.

## Glossary

| Term | Meaning |
| --- | --- |
| online side | The machine with internet access that runs `gpygap sync`. |
| offline side | The air-gapped network: a web server serving the mirror, developer machines. |
| mirror | The published directory (`gpygap/`): packages, `simple/` index, tools. Only this crosses the air gap. |
| state | `gpygap-state/`: caches, logs, run history. Never leaves the online side. |
| request | One `uv pip compile` job: a requirement, a Python version, optionally one platform (`ResolveRequest`). |
| universal resolution | `--universal`: one resolution valid for all platforms. The default for requests. |
| targeted resolution | `--python-platform X`: what a developer on platform X gets. Used for healing. |
| install check | Offline `uv pip compile` of one requirement x Python x platform against the mirror. |
| build check | Offline `uv build` of an sdist against the mirror, to prove its build requirements are there. |
| healing | Fetching what a check found missing, then checking again. |
| fix | A healing request persisted for later runs (`targeted-resolutions.json`). |
| packages.txt | Our own requirements on top of the top list (`packages.extra_file`); mirrored with dependencies and extras. |
| intake | Lock and requirements files from teams in `intake/` (`packages.requirements_dir`). |
| scan | `gpygap scan <dir>`: an intake requirements file derived from a project's manifests and imports, for review. |
| scope | Everything a run must make installable: top-N, `packages.extra`, packages.txt, intake, their extras, build backends. |
| pin | `name==version` from an intake lock file, resolved `--no-deps`. |
| delta | Export of changes since the last export, applied offline with `gpygap_offline.py apply`. |
| target | One `(platform, Python version)` pair a wheel has to exist for (`BuildTarget`). |
| built wheel | A wheel this mirror compiled because upstream published none; `origin = "built"`, no URL. |
| universal wheel | `py3-none-any`: pure Python, installs on every target whatever machine built it. |
| gap | A target still without a wheel after building, listed in `MIRROR-GAPS.json`. |
| finding | One advisory against one version the mirror holds (`MIRROR-ADVISORIES.json`, `advisories.html`). |
| external wheels | Wheels built on another machine (a Windows host) and adopted from `build.external_dir`. |
