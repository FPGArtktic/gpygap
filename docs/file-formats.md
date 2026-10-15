# Files and formats

## Mirror (`mirror.output`, default `gpygap/`): what crosses the air gap

| Path | Content |
| --- | --- |
| `packages/<project>/<file>` | Wheels and sdists, byte-identical to PyPI. |
| `packages/<project>/<wheel>.metadata` | The wheel's METADATA (PEP 658), so clients need not download wheels to resolve. |
| `simple/index.html`, `simple/<project>/index.html` | PEP 503 index: `#sha256=`, `data-requires-python`, `data-dist-info-metadata` / `data-core-metadata`. Links are relative (`../../packages/...`). |
| `python/<release>/<file>` | python-build-standalone archives in the layout of uv's `python-install-mirror`. |
| `tools/uv/<version>/uv-<target>.<ext>` (+ `.sha256`) | uv release archives. |
| `index.html` | Landing page with client configuration. |
| `MIRROR-VERSION` | Run id of the published version; deltas check it. |
| `MIRROR-INFO.json` | `MirrorInfo`: version, top list date, projects, targets, counts. |
| `MIRROR-VERIFIED.json` | Install checks that passed online: requirement, python, platform, uv_target, no_deps, outcome (`wheels`/`build`), top. Input of the smoke test. |
| `MIRROR-SHA256SUMS` | `sha256sum` format, every file except itself. `sha256sum -c` compatible. |
| `gpygap_offline.py` | Copy of `src/gpygap/offline.py`. |

Generated files are written only when their content changes (`util.write_if_changed`),
so their mtime, and therefore deltas, stay small.

## Delta (`--export-delta DIR`)

| Path | Content |
| --- | --- |
| `payload/<mirror path>` | New or changed files (by size + mtime since the last export). |
| `DELTA.json` | `base_version` (expected `MIRROR-VERSION`, `null` = full), `new_version`, `created`, `files` (`{path: sha256}` of the payload), `deleted` (mirror paths). |
| `gpygap_offline.py` | The script to apply it. |

Apply order: package files, then `simple/` and root files, `MIRROR-VERSION` last; then
deletions; then the whole-mirror checksum check.

## State (`mirror.state_dir`, default `gpygap-state/`): stays online

| Path | Content |
| --- | --- |
| `resolutions/py<X>/<universal or platform>/<requirement>-<sha1>[-nodeps].json` | `Resolution`: request, resolved_at, error, packages (with every file URL/hash). |
| `uv-cache/` | uv's cache for resolution (`UV_CACHE_DIR`). |
| `verify-cache/` | uv cache for checks; emptied before each verification round. |
| `runs/<run_id>.json` | Mirror paths required by that run; drives retention (`keep_runs`). |
| `manifest.json` | `{path: MirrorFile}` of everything published. |
| `dist-meta.json` | `{"version": N, "entries": {path: DistMeta}}`: Requires-Python, metadata hash, build requirements, extras. |
| `targeted-resolutions.json` | Fixes from healing: `[{request, origin}]`. |
| `checksum-cache.json` | `{path: [size, mtime_ns, sha256]}` for files not hashed by the lock file. |
| `export-snapshot.json` | `{version, files: {path: [size, mtime_ns]}}` at the last export. |
| `top-pypi-packages.json` | Last fetched top list (fallback when the source is unreachable). |
| `logs/<run>.log`, `<run>-errors.log`, `<run>-report.json` | Full log; warnings and errors only (exists = needs attention); summary. |
| `tmp/` | Scratch space for uv. |

Deleting the state directory is safe: the next run resolves and hashes again, and the
next export is a full one. The mirror itself is never rebuilt from scratch, only
re-synchronized.
