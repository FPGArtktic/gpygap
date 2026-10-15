# Development

How to work on gpygap itself. For what it does and how to run it, start at
`README.md` in the repository root; for why it is built this way, [decisions.md](decisions.md).

## Ground rules

- **Nothing runs on the host but the unit tests and linters.** Every `sync`, every sdist
  build and every integration test runs in podman, through `bin/gpygap` or another
  container. Building an sdist executes code downloaded from the internet, and the host
  must not accumulate build tools or packages.
- **Never run a full `sync`** (top 1000, 25+ GB) while developing. Use `--top 10` for an
  end-to-end run and `--plan-only` for sizes.

## Commands

```bash
uv sync                                    # dev environment (pytest, ruff, mypy)
uv run pytest -q                           # unit tests: no network, no subprocesses
uv run ruff format src tests && uv run ruff check src tests
uv run mypy                                # strict
uv run --group docs mkdocs serve           # documentation at localhost:8000
uv run --group docs mkdocs build --strict  # what Read the Docs runs
bin/check-offline-py39                     # offline.py on Python 3.9, in a container
bin/gpygap sync --top 10                   # end-to-end in the container (~1 GB, ~5 min)
bin/gpygap sync --plan-only                # sizes only, downloads nothing
```

Set `GPYGAP_ENGINE=docker` if you have docker rather than podman; both `bin/` scripts
honour it.

**A change is done when** `ruff format`, `ruff check`, `mypy` and `pytest` pass;
`bin/check-offline-py39` passes if `offline.py` changed; and a `--top 10` run ends with
`Mirror is COMPLETE` if the change touches resolution, selection, download, index or
verification.

## Layout (`src/gpygap/`)

| Module | Responsibility |
| --- | --- |
| `cli.py` | argparse; per-run options only; the `offline` subcommand delegates to `offline.main`. |
| `config.py` | `_SCHEMA` (defaults), TOML loading and validation, `Config`, `StatePaths`. |
| `sync.py` | Orchestrates one run; reads top to bottom as the list of steps. |
| `fetcher.py` | Resolve → plan → download → build-requirement rounds, for any set of requests. |
| `resolver.py` | `ResolveRequest` (universal or one platform, optional `--no-deps`), `uv pip compile --format pylock.toml`, cache, retries, stale fallback. |
| `selection.py` | Wheel tag and marker filtering per platform; builds the `MirrorPlan`; records which targets have no wheel. |
| `wheelbuild.py` | Builds the wheels upstream never published, in sibling containers; caches results across runs. |
| `extras.py` | One requirement per extra (`Provides-Extra`, local file or remote PEP 658) of the top projects, `packages.txt` and intake requirements; keeps version specifiers. |
| `intake.py` | Team files in `intake/`: `uv.lock`, `pylock*.toml` (pins), `*.txt` (requirements); `.md` and dotfiles ignored. |
| `toplist.py` | Top-N list (hugovk/top-pypi-packages), cached copy as fallback. |
| `downloader.py` | Parallel downloads, size + SHA-256 verified before the final rename. |
| `distmeta.py` | PEP 658 sidecars, Requires-Python, Provides-Extra, sdist build requirements. |
| `manifest.py` | Published-file manifest and per-run history (retention). |
| `prune.py` | Deletes files no retained run requires, behind a ratio safety limit. |
| `index.py` | Static `simple/` pages and the landing `index.html`. |
| `verify.py` | Install checks and build checks against the `file://` index, offline. |
| `advisories.py` | Matches everything published against the OSV advisory set. |
| `checksums.py` | `MIRROR-SHA256SUMS`. |
| `tools.py` | uv release archives and python-build-standalone builds. |
| `offline.py` | **Standalone, stdlib only, Python 3.9.** Delta export/apply, mirror verify, smoke test. Shipped as `gpygap_offline.py`. |
| `report.py` | Per-run JSON report, console summary, `--plan-only` output. |
| `platforms.py` | `Platform`: one OS/arch across wheel tags, markers, uv targets, Python builds. |
| `models.py`, `util.py`, `console.py` | Shared dataclasses, small helpers, logging and progress. |

Outside `src/`: `Containerfile` (the image, uv version pinned), `bin/gpygap` (podman
wrapper), `bin/check-offline-py39` with `tests/offline_py39_check.py`, `gpygap.toml` (every
setting, documented), `packages.txt` (`packages.extra_file`), `intake/`
(`packages.requirements_dir`).

## Invariants

Breaking one of these breaks the guarantee the mirror exists to give.

- **`offline.py` imports only the standard library and runs on Python 3.9.** Enforced by
  `test_offline_module_only_imports_the_standard_library` and `bin/check-offline-py39`.
  Ruff has `per-file-target-version = py39` for it; do not "modernize" it. It also carries
  its own licence notice, because it travels alone.
- **Each requirement is resolved separately, per Python version.** Never resolve the whole
  scope as one set: a single conflict would fail everything.
- **Nothing is pruned after a failure that could make files look unneeded.** Stale
  resolutions keep their last good result, failed downloads skip pruning, an unreachable
  top list falls back to the cached copy, and `max_prune_ratio` blocks mass deletion.
- **"Complete" means no install check `missing` and no build check `missing-deps` after
  healing.** Never weaken a check to make a run pass; heal the gap or report it.
- **Every in-scope requirement installs from wheels alone on every buildable target**, or
  the gap is named in the report. Never relax `--only-binary` to make a run pass.
- **A built wheel is recorded with `origin = "built"`** and never overwrites an upstream
  wheel of the same name. It reaches the manifest only because `sync._commit` is handed
  the builder's files; without that the prune would delete a file nothing can
  re-download.
- **A wheel build is reproducible from `(sdist sha256, platform, python, builder image)`**
  -- that tuple is the cache key, and a cached failure is a result, not a reason to
  retry. A cache entry whose wheel is gone is dropped and rebuilt.
- **Verification caches start empty each round.** A check must never see the online uv
  cache, or it will pass on a file the mirror does not have.
- **A file under its final name in the mirror is complete and hash-verified.** Downloads
  land on a temporary name and are renamed only after size and SHA-256 match.
- **File names from lock files become paths**, so they are validated: `resolver._parse_file`
  for mirror paths, `offline._inside` for delta paths.
- **Generated files go through `write_if_changed`**, so an unchanged file keeps its mtime
  and deltas stay small.
- **uv runs with `--no-config` and the mirror's own caches.** The operator's uv
  configuration must not be able to change what the mirror contains.
- **The uv version is pinned in `Containerfile`.** Mirrored uv binaries and Python builds
  come from that version; bumping it is a deliberate change.

## Changing configuration

A new setting needs all of: a default in `config._SCHEMA`; a field on `Config`, set in
`config._build` and range-checked in `config._validate` if it has bounds; a documented
entry in `gpygap.toml`; a mention in the repository `README.md`; and a paragraph here or in
another `docs/` page if it changes behaviour described there. Options that change a single
run go to `cli.py` and `SyncOptions` instead, not to the config file.

## Style

- Small functions with one job. A module docstring says why the module exists.
- Typed dataclasses (`frozen=True, slots=True` where possible) rather than dicts crossing
  module boundaries; JSON conversion through `to_json` / `from_json`.
- Log through `logging.getLogger(__name__)`. Warnings and errors also land in
  `<state>/logs/<run>-errors.log`, whose existence means "needs attention" — so log
  *expected* failures, such as an unsupported extra, at DEBUG. `print` belongs in `cli.py`.
- Catch specific exceptions. A broad `except` needs a comment saying why.
- Tests cover pure logic and filesystem behaviour with `tmp_path`. No network and no
  subprocesses in `tests/test_*.py`.
- Every source file starts with the two SPDX lines; the project is GPL-3.0-or-later.
- Record design decisions, with their reasons, in [decisions.md](decisions.md).
