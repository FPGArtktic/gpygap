# Design decisions

Each entry: the decision, why, and what would have to change to revisit it.

## Static PEP 503 index, not the uv cache

The first idea was to fill uv's cache and copy it. The cache is per user, holds only the
current platform's wheels, is tied to uv's cache format version, and cannot be served over
HTTP. A static index of real files works for every uv and pip version, every OS, and any
web server. (`uv pip install --download-only` does not exist.)

## One resolution per requirement and Python version

Resolving the whole top list together fails on the first conflict between two projects.
Separate resolutions never conflict; the mirror simply holds several versions of shared
dependencies. Cost: many `uv pip compile` calls, made cheap by uv's cache and the
resolution cache.

## Universal resolution plus targeted healing

`--universal` gives one answer for all platforms per request: half the work of resolving
each platform separately. It can differ from what a developer on one platform gets; the
install checks detect that and heal with a targeted (`--python-platform`) resolution. Fixes
are persisted so the difference is not re-healed every month.

## `pylock.toml` as the resolver output

PEP 751 lock files from `uv pip compile --format pylock.toml` list every file of every
pinned version with URL, size and SHA-256, without needing a matching interpreter (unlike
`uv lock`, which wants one satisfying `requires-python`).

## Extras resolved one by one

Extras of one project can conflict with each other (`cpu` vs `cuda`). Resolving
`project[extra]` separately mirrors everything any single extra needs. Extras are taken
for every requirement someone asked for on purpose (top list, `packages.txt`, intake
requirements files), keeping its version specifier (`django>=4.2` gives
`django[argon2]>=4.2`); not for intake pins (a lock file already lists what its project
uses) nor for build backends.

## Verification by resolving, not installing

`uv pip compile --python-platform X --no-build` fails exactly when an install on X would
need a missing wheel, without unpacking gigabytes of wheels, and works for Windows from
Linux. Real installs are exercised by the offline smoke test and by the build check.

## Build check in a container without compilers

Building sdists runs arbitrary code from the internet, so it happens only inside the
container. Without compilers, C extensions fail right after their build requirements were
resolved, which is all the check needs, and fast.

## Everything runs in a container

The host must not accumulate Python packages, build tools or caches. `bin/gpygap`
runs the tool in podman with only the project directory (and `GPYGAP_MOUNTS`) mounted
at identical paths, so paths in `gpygap.toml` mean the same inside and out.

## `offline.py` is standalone, stdlib only, Python 3.9

It is copied to the air-gapped side as `gpygap_offline.py`, where nothing can be
installed and servers may run old distributions. `bin/check-offline-py39` runs it on
Python 3.9; ruff is pinned to `py39` for that file, but linters miss newer stdlib APIs
(`datetime.UTC` slipped through once), so the container check is the real guard.

## rsync semantics with retention and safety nets

Each run records the files it requires; files no retained run (`keep_runs`) requires are
deleted. Safety nets make failures never look like "not needed": stale resolutions keep
old results, failed downloads skip pruning, an unreachable top list falls back to the
cached copy, and `max_prune_ratio` blocks mass deletion.

## Deltas with a version chain and checksums

Physical transfer media are slow and error-prone. Deltas carry only changes, name the
version they apply to (refusing out-of-order application), and carry checksums verified
before anything is modified. `MIRROR-SHA256SUMS` lets the offline side check the whole
mirror with standard tools.

## Configuration in `gpygap.toml`, run options on the command line

Humans maintain the mirror's scope in one documented, validated file; typos are errors.
The command line only carries what changes per run (`--top` for tests, `--plan-only`,
`--export-delta`, `--force-prune`).

## Python, not Rust or Go

The heavy lifting is uv (Rust). The tool orchestrates it, downloads and writes HTML; the
bottleneck is the network. Python has `packaging`, the reference implementation of wheel
tags and markers, which would otherwise have to be reimplemented.

## Two ways to add packages: `packages.txt` and `intake/`

`packages.txt` is a short, human-curated list ("we also need these"), resolved to the
newest matching versions with dependencies and extras. `intake/` takes whole lock files
from teams and mirrors every locked version exactly, without re-resolving, so a project
that works online installs offline unchanged. Keeping them apart keeps the curated list
readable and makes it obvious which entries can be dropped when a team leaves.

## Wheels are built here, not on the developer machine

A package with no wheel for a target used to be shipped as an sdist and compiled at
install time, which in an air-gapped network means every developer machine needs a
compiler, headers and sometimes Rust. The online side has all of that already, so it
builds the wheel and the offline side installs a binary. The rule became testable in one
line: every in-scope requirement must install with `--only-binary :all:`. To revisit:
only if building turns out to produce wheels that differ meaningfully from what the
project would have published.

## Wheel builds run in sibling containers, not nested

The mirror's own image deliberately has no compilers, and the pypa manylinux images have
the libc floor, the interpreters and auditwheel. So builds run as siblings, launched
through a container engine socket the wrapper passes in, rather than inside the mirror's
container or in a nested engine. Nested rootless podman needs privileged flags and the
vfs storage driver, and is slower; a sibling is exactly as isolated as the gpygap
container itself. Cost: the container can start containers, which on a single-user build
host is the authority it already had. `GPYGAP_NO_BUILD_SOCKET=1` withholds it.

## podman is driven by its own client, not the Docker-compatible one

podman serves a Docker-compatible API, and one client for both engines would be simpler.
It does not work: a container created through that layer comes up unable to fork
("Resource temporarily unavailable") while the identical container created through
podman's own API is fine. The image therefore carries both clients and picks the one
matching the socket it was given. To revisit: when the compatibility layer stops doing
this.

## No Windows or macOS builder, and the list of routes that do not work

Container images of Windows need a Windows host, because a container shares the host
kernel. xwin and clang-cl solve the toolchain but not the build system: setuptools takes
its target from the running interpreter, so on Linux it emits a `.so` and a
`linux_x86_64` tag; `crossenv` targets Unix hosts. .NET's cross-compilation shares no
machinery with CPython extensions. Wine with MSVC does work, because the interpreter that
runs is really Windows Python, and was rejected as fragile and licence-encumbered. What
covers Windows instead: the `py3-none-any` wheels built here, a Windows machine dropping
`pip wheel` output into `build.external_dir`, and the compiler on the developer machine
for the remainder, which the report names. To revisit: if a Windows build host becomes
available, it plugs into `build.external_dir` with no code change.

## Every mirror file records where it came from

`MirrorFile.origin` is `upstream` or `built`. A built wheel has no URL: nothing can
re-download it, so losing it means rebuilding it, and the prune has to be told it exists.
Publishing the list in `MIRROR-INFO.json` also lets the offline side see which files did
not come from PyPI, which is a question an auditor will eventually ask.

## The published artifact keeps the `MIRROR-` prefix

The tool is `gpygap`, but the files it publishes are `MIRROR-VERSION`,
`MIRROR-SHA256SUMS`, `MIRROR-VERIFIED.json` and `MIRROR-INFO.json`, and the dataclasses are
`MirrorFile`, `MirrorPlan`, `MirrorInfo`. Those names describe the artifact — a mirror —
and stay meaningful to someone holding a copy on the offline side who has never heard of
the tool that produced it. Renaming them would also change the on-disk wire format that
`gpygap_offline.py` reads. To revisit: only if a second tool ever writes the same layout.

## English everywhere

Code, comments, docstrings, logs, `docs/` and README.md are in English, so one language
serves the operators, the developers and future maintainers (human or AI).
