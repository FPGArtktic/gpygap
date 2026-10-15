# Architecture

## The problem

Developers in an air-gapped network use uv (and sometimes pip) on Windows and Linux. They
need the most popular PyPI projects, with every dependency, extra and build requirement,
for several Python versions. Once a month the online side refreshes the selection: new
versions arrive, projects that left the top list disappear, and only the difference is
carried across the air gap.

## One run (`gpygap sync`)

`sync.run_sync` reads top to bottom as the list of steps:

```text
load top list ─────────┐
load packages.txt ─────┤   (packages.extra_file, plus packages.extra)
load intake/ ──────────┤
                       ▼
 Fetcher.fetch(universal requests: top + packages.txt + intake requirements + build backends)
 Fetcher.fetch(intake pins, --no-deps)
 extras of top + packages.txt + intake requirements ─► Fetcher.fetch(one request per extra)
 fixes from earlier runs ─────────► Fetcher.fetch(...)
               │      every fetch: resolve ─► plan ─► download ─► build requirements of
               │      sdists some target must build ─► resolve ─► … (build_deps_rounds)
               ▼
 tools: uv binaries + Python builds
 commit: record run's required files, prune what no retained run requires
 publish: simple/ index, landing page, MIRROR-INFO.json, MIRROR-VERSION
               ▼
 verify ── install checks (requirement × Python × platform) + build checks (sdists)
   │  missing? ─► heal: targeted resolutions / missing build requirements ─► fetch ─►
   │              commit ─► publish ─► re-check only what failed  (heal_rounds)
   ▼
 save fixes, MIRROR-VERIFIED.json, gpygap_offline.py, MIRROR-SHA256SUMS
 export delta (optional) ─► report
```

`--plan-only` stops after the fetch phase without downloading; extras are then discovered
from remote PEP 658 metadata, and build requirements are not computed.

## Modules (`src/gpygap/`)

Dependencies point downwards; nothing imports `sync` or `cli`.

| Layer | Modules |
| --- | --- |
| entry | `cli` (argparse) → `sync` (orchestration) |
| phases | `fetcher`, `wheelbuild`, `verify`, `prune`, `index`, `checksums`, `tools`, `report` |
| domain | `resolver`, `selection`, `extras`, `intake`, `toplist`, `distmeta`, `manifest`, `downloader` |
| base | `config`, `platforms`, `models`, `util`, `console` |
| standalone | `offline` (stdlib only; also the offline-side script, see [decisions](decisions.md)) |

Key types:

- `Config` (`config.py`): validated `gpygap.toml`; `Config.state` gives the state layout.
- `Platform` (`platforms.py`): one OS/arch across vocabularies: wheel tags, PEP 508 marker
  values, uv `--python-platform`, python-build-standalone, uv release archive.
- `ResolveRequest` / `Resolution` (`resolver.py`): a uv job and its cached result.
  A failed resolution keeps the last good packages (`stale`).
- `LockedPackage` / `DistFile` (`models.py`): what a `pylock.toml` lists.
- `MirrorPlan` (`selection.py`): files the mirror must hold, sdists that need building.
- `BuildTarget` (`models.py`): one `(platform, python)` a wheel must exist for.
- `MirrorFile` (`models.py`): a file in the mirror and where it came from -- `origin` is
  `upstream` or `built`.
- `WheelFactory` / `BuiltWheel` / `BuildGap` (`wheelbuild.py`): the builds, what they
  cover, and what stayed uncovered. See [wheels](wheels.md).
- `InstallCheck` / `InstallResult`, `BuildResult` (`verify.py`): verification outcomes.

## How a requirement becomes files

1. `Resolver` runs `uv pip compile - --universal --python-version X --format pylock.toml`
   with the requirement on stdin. The lock lists every file of every pinned version for
   every platform.
2. `selection.select_files` keeps, per configured platform: packages whose marker applies,
   wheels whose tags install there with that CPython, and sdists per `packages.sdists`. If
   some target has no wheel, the sdist is marked as needing a build.
3. `downloader` fetches missing files (`.part`, size + SHA-256 check, rename).
4. `distmeta` reads `build-system.requires` from such sdists; those requirements are
   resolved in the next round.

## Where state lives

- Mirror (`gpygap/`): only published content. See [file-formats](file-formats.md).
- State (`gpygap-state/`): resolution cache, uv cache, run history, manifest,
  logs, checksums cache, export snapshot.
- Container: everything executable (uv, Python, sdist builds). The host only sees the two
  directories above, mounted by `bin/gpygap`.
