# Operations

## Running

Always through the container, so nothing is installed or built on the host:

```bash
bin/gpygap sync --top 10                       # quick end-to-end test (~1 GB, ~5 min)
bin/gpygap sync --plan-only                    # sizes only, nothing downloaded
GPYGAP_MOUNTS=/media/usb bin/gpygap sync --export-delta /media/usb/delta-2026-11
```

`bin/gpygap` builds `localhost/gpygap:latest` from `Containerfile` when sources
changed, mounts the project directory at the same path, runs as the calling user
(`--userns=keep-id`), and passes all arguments to `gpygap`. Directories outside the
project (USB drive, mirror on another disk) go into `GPYGAP_MOUNTS`, colon-separated.

Exit codes: `0` complete; `1` incomplete (still missing after healing, or downloads
failed); `2` configuration error; `130` interrupted (rerun to resume: resolutions younger
than `max_age_hours` are reused, downloaded files are kept).

## Monthly routine

1. `bin/gpygap sync --export-delta <dir>` (cron/systemd timer on the 1st).
2. Read the end of the output or `<state>/logs/<run>-report.json`: `Mirror is COMPLETE`.
   If `<run>-errors.log` exists, read it.
3. Carry `<dir>` across. Offline: `python3 <dir>/gpygap_offline.py apply <dir> /srv/gpygap`.
4. From a developer machine: `python3 gpygap_offline.py smoke-test http://pypi.example.local`.

Deltas must be applied in order. A lost delta: `sync --export-delta DIR --full-export`,
applied with `--force` on top of the existing mirror (or into an empty directory).

## When something goes wrong

| Symptom | Meaning | Action |
| --- | --- | --- |
| `prune blocked: N of M files...` | More than `max_prune_ratio` would be deleted. | Check the top list and resolution errors in the log; if intended, rerun with `--force-prune`. |
| `Pruning skipped: N downloads failed` | Network trouble during download. | Rerun; previous files stay meanwhile. |
| `still missing: <req> @ pyX platform` | Healing could not close a gap. | Read the check's error in `<run>.log` (debug). Often a project that needs a newer uv or an index outside PyPI. Exclude it, or add a working requirement to `packages.txt`. |
| `still missing: <sdist>: <reqs>` | Build requirements unresolvable offline. | Usually resolves after another `heal_rounds`; otherwise add the requirement to `packages.txt`. |
| `resolve X: ... (keeping the previous resolution)` | Upstream resolution failed; old files kept. | Transient: ignore. Persistent: the project changed upstream; check manually. |
| offline `delta is damaged` | Payload checksum mismatch; nothing was changed. | Copy the delta again. |
| offline `mirror check: N problem(s)` | Files missing or corrupted in the served mirror. | Re-apply the last delta with `--force`, or copy a full export. |
| smoke test fails but online check passed | Web server problem. | MIME types for `.whl`/`.metadata`, `+` in URLs (IIS: `allowDoubleEscaping`), directory index. |

## Changing scope

- More/fewer projects: `packages.top`, `packages.exclude`.
- Company packages, niche packages, version ranges or old versions: one line each in
  `packages.txt` (mirrored with dependencies and extras).
- A team's whole project, exactly as locked: drop its `uv.lock` / `pylock.toml` /
  `requirements.txt` into `intake/`; remove it when no longer needed.
- New platform or Python: `targets.platforms`, `targets.python` (then expect a large
  first download).
- Size estimate before any of these: `bin/gpygap sync --plan-only`.

## Windows developer machines

Almost everything a Windows developer installs comes from a wheel: the ones the projects
published, plus every `py3-none-any` wheel the mirror built from an sdist that had none.
What is left is a compiled extension with no Windows wheel upstream, which a Linux host
cannot build. Those are listed by name in the run report and in `MIRROR-GAPS.json`, so
the list is short and known rather than discovered at `pip install` time.

Two ways to close it, and they combine:

**A Windows builder.** Any Windows machine with the MSVC Build Tools and a copy of the
mirror can produce the missing wheels; it needs no gpygap of its own.

```powershell
# on the Windows machine, against a copy of the mirror
python .github\ci\build-windows-wheels.py D:\mirror D:\wheels
```

Copy `D:\wheels` to where `build.external_dir` points on the online machine. The next
`sync` adopts them, indexes them and counts them against the targets they cover. This is
what CI does between its two mirror passes, so the path is exercised on every push.

**The compiler on the developer machine.** For an air-gapped network, Microsoft's
installer makes an offline cache that crosses the air gap like the mirror:

```powershell
vs_BuildTools.exe --layout C:\vs-layout --add Microsoft.VisualStudio.Workload.VCTools --lang en-US
```

Carry `C:\vs-layout` across and install from it. After that `pip` and `uv` build the
remaining sdists themselves, with every build requirement already in the mirror.
