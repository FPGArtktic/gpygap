# gpygap

[![CI](https://github.com/FPGArtktic/gpygap/actions/workflows/ci.yml/badge.svg)](https://github.com/FPGArtktic/gpygap/actions/workflows/ci.yml)
[![Docs](https://readthedocs.org/projects/gpygap/badge/?version=latest)](https://gpygap.readthedocs.io/en/latest/)
[![License: GPL v3](https://img.shields.io/badge/license-GPL--3.0--or--later-blue?logo=gnu)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-3776AB?logo=python&logoColor=white)](pyproject.toml)

Installing from the mirror is tested on every push, offline, from a real container of each
system &mdash; not asserted:

[![Debian](https://img.shields.io/badge/Debian%2012%20%7C%2013-verified%20offline-A81D33?logo=debian&logoColor=white)](https://github.com/FPGArtktic/gpygap/actions/workflows/ci.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu%2024.04%20%7C%2026.04-verified%20offline-E95420?logo=ubuntu&logoColor=white)](https://github.com/FPGArtktic/gpygap/actions/workflows/ci.yml)
[![RHEL 9](https://img.shields.io/badge/RHEL%209%20%28UBI%29-verified%20offline-EE0000?logo=redhat&logoColor=white)](https://github.com/FPGArtktic/gpygap/actions/workflows/ci.yml)
[![Arch Linux](https://img.shields.io/badge/Arch%20Linux-verified%20offline-1793D1?logo=archlinux&logoColor=white)](https://github.com/FPGArtktic/gpygap/actions/workflows/ci.yml)
[![Alpine](https://img.shields.io/badge/Alpine-verified%20offline-0D597F?logo=alpinelinux&logoColor=white)](https://github.com/FPGArtktic/gpygap/actions/workflows/ci.yml)
[![Windows](https://img.shields.io/badge/Windows-verified-0078D4?logo=windows&logoColor=white)](https://github.com/FPGArtktic/gpygap/actions/workflows/ci.yml)

An offline PyPI mirror for air-gapped networks: **the top 1000 PyPI projects plus your own
packages, with every dependency, extra and build requirement**, for Windows and Linux and
several Python versions. Developers install with `uv` (or `pip`) exactly as they would from
PyPI.

- **No compiler needed offline.** When a project publishes no wheel for a target, the
  mirror builds one, in a container, while it still has a toolchain — so installing in
  the air-gapped network is a plain binary install. See [Limitations](#limitations) for
  the one case that still is not.
- **Verified before it ships.** Every package is test-installed offline from the mirror for
  every Python version and platform (Windows is checked from Linux). Anything missing is
  fetched automatically and checked again. Each run ends with `Mirror is COMPLETE` or a
  list of what is missing.
- **Extras included**: `uvicorn[standard]`, `requests[socks]`, `celery[redis]` and so on.
- **Synchronizes like `rsync --delete`.** Monthly runs pick up new versions and the current
  top list; projects that drop out disappear with the dependencies nobody else needs.
- **Ships deltas with checksums.** Only changes cross the air gap, and a damaged transfer
  is detected before it touches the offline mirror.
- **Runs in a podman container.** Nothing is installed or built on the host.
- **Plain files.** Any web server (nginx, IIS, ...) can serve the mirror.

## Quickstart

### 1. Build the mirror (online machine)

You need Linux with [podman](https://podman.io/); rootless is fine. uv, Python and all
builds live in the container.

```bash
git clone <this repository> gpygap && cd gpygap

# Optional: list your own packages, one per line (see the file's header).
$EDITOR packages.txt

bin/gpygap sync --top 10       # smoke run: ~1 GB, ~5 minutes
bin/gpygap sync --plan-only    # size of the full mirror; downloads nothing
```

When the test run ends with `Mirror is COMPLETE`, build the real thing and export it to
the transfer medium:

```bash
rm -rf gpygap gpygap-state    # drop the test run
GPYGAP_MOUNTS=/media/usb bin/gpygap sync --export-delta /media/usb/delta-2026-10
```

The first export contains the whole mirror; later ones contain only changes.

### 2. Install it on the offline server

Python 3.9+ is all the server needs:

```bash
python3 /media/usb/delta-2026-10/gpygap_offline.py apply /media/usb/delta-2026-10 /srv/gpygap
```

Serve `/srv/gpygap` over HTTP, e.g. with nginx:

```nginx
server {
    listen 80;
    server_name pypi.example.local;
    root /srv/gpygap;
    autoindex on;
}
```

### 3. Point developers at it

Install uv from the mirror (links on `http://pypi.example.local/`), then create `uv.toml`
(`~/.config/uv/uv.toml` on Linux, `%APPDATA%\uv\uv.toml` on Windows):

```toml
[[index]]
url = "http://pypi.example.local/simple"
default = true

python-install-mirror = "http://pypi.example.local/python"
```

Everything works as usual: `uv python install 3.12`, `uv venv`, `uv pip install pandas`,
`uv add fastapi`, `uv sync`.

### 4. Check it from inside the network

```bash
curl -O http://pypi.example.local/gpygap_offline.py
python3 gpygap_offline.py smoke-test http://pypi.example.local
```

This repeats, over HTTP, every check that passed on the online side, so it also catches
web server misconfiguration.

### 5. Every month

```bash
GPYGAP_MOUNTS=/media/usb bin/gpygap sync --export-delta /media/usb/delta-2026-11
# offline:
python3 /media/usb/delta-2026-11/gpygap_offline.py apply /media/usb/delta-2026-11 /srv/gpygap
```

## Choosing what to mirror

| Source | Where | Use it for |
| --- | --- | --- |
| Top list | `packages.top` in [gpygap.toml](gpygap.toml) | The N most downloaded projects (default 1000), refreshed every run. |
| Your packages | [packages.txt](packages.txt) | Internal or niche packages, version ranges, old versions a project still needs. |
| Team lock files | [intake/](intake/README.md) | Drop a `uv.lock`, `pylock.toml` or `requirements.txt`; everything in it installs offline exactly as locked. |
| Exclusions | `packages.exclude` | Projects you never want (e.g. huge ones). The next project in the ranking takes the slot. |
| Extras | `packages.extras`, `packages.extras_exclude` | On by default for all of the above; skip extras by name. |

Every entry is mirrored with all dependencies, for every Python version and platform in
`targets`, and verified. See the size impact of any change first with `--plan-only`.

## How it works

```text
 ONLINE (podman container)                               OFFLINE (air-gapped)
 ─────────────────────────                               ────────────────────
 top list + packages.txt + intake + extras
   │  each requirement x Python version on its own:
   ▼  uv pip compile --universal ─► pylock.toml (URLs + SHA-256)
 download (verified) + build requirements of sdists
   │
 prune what nothing needs + PEP 503 index
   │
 verify: offline install of every requirement x Python x platform,      delta (USB, diode)
   │     offline build of every sdist that must be built           ─────────────────────►
   ▼  missing? ─► fetch exactly that ─► check again                     gpygap_offline.py apply
 MIRROR-SHA256SUMS, delta                                          (checksums) ─► web server
                                                                   smoke test from a dev machine
```

Each requirement is resolved **on its own**, so version conflicts between projects (say,
different `pydantic` or `click` requirements) never block anything: the mirror simply holds
several versions. Details: [docs/architecture.md](docs/architecture.md),
[docs/verification.md](docs/verification.md).

## Requirements (online side)

- Linux with podman. Nothing else on the host.
- Internet access to pypi.org, files.pythonhosted.org, ghcr.io, docker.io and github.com.
- Disk, for the top 1000 on Linux and Windows with Python 3.10–3.14 (October 2026):
  about 80 GB with all extras, 67 GB without development extras (`dev`, `test`, `docs`...),
  25 GB with `packages.extras = false`. Most of it is the ML stack (TensorFlow, PyTorch,
  CUDA libraries) pulled in by extras. `bin/gpygap sync --plan-only` gives the exact
  figure and the largest projects.

`bin/gpygap` builds its image on first use (and after code changes), mounts the
project directory at the same path, and runs as the calling user. Directories outside the
project, such as a USB drive or a mirror on another disk, go into `GPYGAP_MOUNTS`,
separated by colons.

Exit codes: `0` mirror complete; `1` something is still missing or downloads failed (see
the errors log); `2` configuration error; `130` interrupted (run again to resume).

## Configuration: `gpygap.toml`

Every setting lives in [gpygap.toml](gpygap.toml), each one documented in place. The most
common ones:

| Key | Default | Effect |
| --- | --- | --- |
| `packages.top` | `1000` | How many top projects to mirror. |
| `packages.extra_file` | `packages.txt` | Your own list of packages. |
| `packages.requirements_dir` | `intake` | Directory for team lock files. |
| `packages.exclude` | – | Projects to skip; the next one in the ranking takes the slot. |
| `packages.extras` / `extras_exclude` | `true` / – | Mirror extras; skip some by name (e.g. `dev`, `docs`). |
| `targets.python` | `3.10`–`3.14` | Python versions developers use. |
| `targets.platforms` | `linux-x86_64`, `win-amd64` | Also available: `linux-aarch64`, `win-arm64`, `macos-arm64`, `macos-x86_64`. |
| `sync.keep_runs` | `1` | `1` = pure rsync. `2` = removed versions linger one more month (recommended when teams pin versions). |
| `sync.max_prune_ratio` | `0.5` | Safety net: never delete more than half of the mirror in one run. |
| `sync.verify` / `build_check` / `heal_rounds` | `true` / `true` / `2` | Install checks, offline sdist builds, rounds of fetching what is missing. |
| `sync.with_tools` | `true` | Also mirror uv binaries and Python interpreters. |

A misspelled key is an error, not silently ignored. The command line only carries options
for a single run: `--top`, `-o`, `--plan-only`, `--export-delta`, `--full-export`,
`--force-prune`, `-v` (`bin/gpygap sync --help`).

## Monthly update

Every `sync`:

1. fetches the current top list and resolves everything again (new versions), including
   extras, `packages.txt` and intake files;
2. downloads only missing files, including the build requirements of sdists;
3. deletes files no requirement needs any more (subject to `keep_runs`);
4. writes the index and **verifies it like a developer would**: an offline install check
   for every requirement, Python version and platform, and an offline build of every sdist
   some platform has to build;
5. fetches whatever is missing but available upstream, for exactly the failing platform,
   and checks again (`heal_rounds`);
6. writes checksums of every file (`MIRROR-SHA256SUMS`).

Safety nets keep a failure from emptying the mirror:

- a package that fails to resolve keeps its previous version;
- if any download fails, nothing is deleted in that run;
- if the top list is unreachable, the previous copy is used;
- if pruning would delete more than `max_prune_ratio` of the files, it is blocked until you
  rerun with `--force-prune`.

Scheduling (cron, 03:00 on the 1st of each month):

```cron
0 3 1 * * GPYGAP_MOUNTS=/export /opt/gpygap/bin/gpygap sync --export-delta /export/delta-$(date +\%Y-\%m) >> /var/log/gpygap.log 2>&1
```

Logs live in `gpygap-state/logs/`: `<run>.log` (everything), `<run>-errors.log`
(warnings and errors only; if it does not exist, the run was clean) and `<run>-report.json`
(what was downloaded, deleted and verified). Troubleshooting:
[docs/operations.md](docs/operations.md).

## Moving the mirror across the air gap

The state directory (`gpygap-state/`) **stays** on the online machine; only the
mirror moves.

### Option A: deltas (recommended)

```bash
# online: the first delta holds the whole mirror, later ones only changes
GPYGAP_MOUNTS=/media/usb bin/gpygap sync --export-delta /media/usb/delta-2026-10

# offline (Python 3.9+, nothing to install):
python3 /media/usb/delta-2026-10/gpygap_offline.py apply /media/usb/delta-2026-10 /srv/gpygap
```

- The delta's checksums are verified before anything changes; a damaged delta is refused
  and the mirror stays untouched. Afterwards the whole mirror is checked.
- Deltas apply **in order**. Each delta names the mirror version it expects (see
  `MIRROR-VERSION`) and refuses any other.
- Lost a delta? Export a full one with `--export-delta DIR --full-export` and apply it to
  an empty directory, or with `--force`.
- The order of operations is safe for a live server: new packages first, then the index,
  deletions last.

### Option B: full copy with rsync

```bash
rsync -a --delete gpygap/ /media/usb/gpygap/          # online
rsync -a --delete /media/usb/gpygap/ /srv/gpygap/     # offline
python3 /srv/gpygap/gpygap_offline.py verify /srv/gpygap   # or: sha256sum -c MIRROR-SHA256SUMS
```

### The medium: USB, DVD/Blu-ray, or a data diode

A delta is an ordinary directory, so it travels on whatever the security policy allows.
Nothing about the format assumes a particular medium, and every route is covered by the
same two guarantees: each file carries its SHA-256, and each delta names the mirror
version it expects, so a damaged, skipped or repeated delta is refused before the first
file is written.

| Medium | How |
| --- | --- |
| **USB / removable disk** | Export straight onto it: `GPYGAP_MOUNTS=/media/usb bin/gpygap sync --export-delta /media/usb/delta-2026-10`. |
| **DVD or Blu-ray** | Burn the delta directory as a data disc (`growisofs -Z /dev/sr0 -R -J delta-2026-10/`). A delta larger than the disc is split into volumes: `tar -cf - delta-2026-10 \| split -b 23G - delta.tar.` burns one part per Blu-ray; offline, `cat delta.tar.* \| tar -xf -` puts it back. |
| **Data diode** | One-way, so there is no asking for a resend: stream the delta as a tar (`tar -cf - delta-2026-10`) and let the receiver unpack it. The checksums tell the receiver whether the stream arrived intact, and the version chain tells it whether a delta was lost on the way. If one was, the next full export (`--full-export`) resynchronizes without needing anything back from the offline side. |

Disc capacities, for sizing an export: DVD 4.7 GB, dual-layer DVD 8.5 GB, Blu-ray 25 GB,
dual-layer Blu-ray 50 GB. `bin/gpygap sync --plan-only` gives the size before you export.

### Checking the offline side

```bash
python3 gpygap_offline.py verify /srv/gpygap                     # every file vs. its checksum
python3 gpygap_offline.py smoke-test http://pypi.example.local        # top projects, over HTTP
python3 gpygap_offline.py smoke-test http://pypi.example.local --all  # plus extras, packages.txt, intake
```

The smoke test needs uv on the machine it runs on (take it from the mirror, see below).

## Serving the mirror

**nginx** (Linux): see the Quickstart; any static configuration with directory indexes works.

**IIS** (Windows): a static site pointing at the mirror directory, plus this `web.config`:

```xml
<configuration>
  <system.webServer>
    <staticContent>
      <mimeMap fileExtension=".whl" mimeType="application/octet-stream" />
      <mimeMap fileExtension=".metadata" mimeType="text/plain" />
      <mimeMap fileExtension=".sha256" mimeType="text/plain" />
    </staticContent>
    <!-- Python build archive names contain '+' -->
    <security><requestFiltering allowDoubleEscaping="true" /></security>
    <directoryBrowse enabled="true" />
  </system.webServer>
</configuration>
```

Quick test: `python3 -m http.server 8080 --directory /srv/gpygap`.

The mirror's home page (`http://pypi.example.local/`) shows its version, the top projects,
links to the uv binaries, and the client configuration.

## Setting up developer machines

### 1. uv, from the mirror

Linux:

```bash
curl -fsSLO http://pypi.example.local/tools/uv/<version>/uv-x86_64-unknown-linux-gnu.tar.gz
tar xzf uv-x86_64-unknown-linux-gnu.tar.gz
install -m 755 uv-x86_64-unknown-linux-gnu/uv uv-x86_64-unknown-linux-gnu/uvx ~/.local/bin/
```

Windows (PowerShell):

```powershell
Invoke-WebRequest http://pypi.example.local/tools/uv/<version>/uv-x86_64-pc-windows-msvc.zip -OutFile uv.zip
Expand-Archive uv.zip -DestinationPath "$env:USERPROFILE\.local\bin"
# then add %USERPROFILE%\.local\bin to PATH
```

Use the uv version from the mirror: which Python build `uv python install` asks for is
compiled into uv, so another uv version may ask for files the mirror does not have.

### 2. `uv.toml`

| System | Per user | Whole machine |
| --- | --- | --- |
| Linux | `~/.config/uv/uv.toml` | `/etc/uv/uv.toml` |
| Windows | `%APPDATA%\uv\uv.toml` | `%PROGRAMDATA%\uv\uv.toml` |

```toml
[[index]]
url = "http://pypi.example.local/simple"
default = true

python-install-mirror = "http://pypi.example.local/python"
```

Or use environment variables: `UV_DEFAULT_INDEX=http://pypi.example.local/simple` and
`UV_PYTHON_INSTALL_MIRROR=http://pypi.example.local/python`. With HTTPS and a company CA,
add `native-tls = true` so uv uses the system certificate store.

### 3. pip (optional)

`~/.config/pip/pip.conf` (Linux) or `%APPDATA%\pip\pip.ini` (Windows):

```ini
[global]
index-url = http://pypi.example.local/simple
trusted-host = pypi.example.local
```

**pip must be 23.1 or newer.** The index serves PEP 658 metadata sidecars, which let a
client resolve without downloading wheels. pip 23.0 and older compare the name in that
metadata to the requested name without normalizing, so any project whose metadata spells
its name with an underscore is rejected with *"has inconsistent Name"* — Debian 12 ships
exactly that version. Upgrading works offline, from the mirror itself:

```bash
pip install --index-url http://pypi.example.local/simple --upgrade pip
```

CI does this on every distro before installing anything, so the path is tested.

## Limitations

- The mirror holds the top projects, `packages.txt`, intake files, their extras and
  dependencies. Anything else must be added to `packages.txt`.
- CPython only: no PyPy, no GraalPy. Everything else CPython ships is a target: glibc
  (manylinux) and musl (Alpine) on x86-64 and aarch64, Windows x86-64, and the
  free-threaded builds (`3.13t`, `3.14t`) alongside the ordinary ones.
- **Windows is the one place a compiler can still be needed.** A Linux host cannot build
  a Windows wheel — a container shares the host kernel, and setuptools has no
  cross-build mode — so Windows gets the wheels the projects published, plus every
  `py3-none-any` wheel the mirror built, which is most of what would otherwise be
  missing. Whatever is left is listed by name in the run report and in
  `MIRROR-GAPS.json`, and there are two ways to close it: point `build.external_dir` at
  a directory a Windows machine drops `pip wheel` output into, or install the MSVC Build
  Tools on the developer machines (`vs_BuildTools.exe --layout` makes an offline
  installer that crosses the air gap).
- `torch` from PyPI pulls CUDA libraries on Linux (`nvidia-*`, several GB per version).
  `--plan-only` shows the size per project; exclude what you do not need. Indexes other
  than PyPI (such as download.pytorch.org) are not mirrored.
- With `keep_runs = 1`, a lock file pinned to a version that left the mirror stops
  installing. Use `keep_runs = 2`, or put the lock file into `intake/`.

## Documentation

- [docs/](docs/index.md): architecture, verification, file formats, operations, design
  decisions.
- [docs/development.md](docs/development.md): commands, layout and invariants for working
  on the code.

## Development

```bash
uv sync                                   # dev tools only (pytest, ruff, mypy)
uv run pytest -q && uv run ruff check src tests && uv run mypy
uv run --group docs mkdocs serve          # the documentation, at localhost:8000
bin/check-offline-py39                    # gpygap_offline.py on Python 3.9 (container)
bin/gpygap sync --top 10                  # end-to-end test (container)
```

See [docs/development.md](docs/development.md) for the module layout and the invariants
to keep.

## Author

[Mateusz Okulanis](https://fpgartktic.github.io/) &mdash; <https://fpgartktic.github.io/>

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).

`gpygap_offline.py`, the one file that crosses the air gap on its own, carries the full
notice in its header so it stays licensed wherever it ends up.
