# Verification and healing

The goal: nothing a developer asks for (within scope) may turn out to be missing after the
mirror crossed the air gap. "Within scope" means: the top projects, `packages.txt` and
`packages.extra`, intake files, the extras of all of these, and the build backends, for
every configured Python version and platform.

## Layers of proof

| Where | What | Catches |
| --- | --- | --- |
| online, `verify.Verifier.check_installs` | uv resolves each requirement x Python x platform offline against the `file://` index, empty cache | missing wheels, versions or dependencies; wrong markers; index bugs |
| online, `verify.Verifier.check_builds` | `uv build --offline` of every sdist some target must build | build requirements missing from the mirror, including dynamic ones |
| offline, `gpygap_offline.py apply` | SHA-256 of every delta file before applying; whole mirror against `MIRROR-SHA256SUMS` after | transfer damage, partial copies |
| offline, `gpygap_offline.py smoke-test URL` | repeats every passed install check over HTTP against the served mirror | web server setup (MIME types, `+` in URLs, paths) |

## Install check outcomes (`InstallOutcome`)

Each check runs up to three `uv pip compile` calls, stopping at the first success:

1. offline, `--no-build` → `wheels`: installs from wheels only.
2. offline, builds allowed → `build`: needs an sdist build, which is also true upstream
   (the mirror keeps all compatible wheels of a version, so a version without a wheel here
   has none upstream). The build check covers these sdists.
3. upstream (PyPI) → if it works there: `missing`, the mirror has a gap; if not:
   `unavailable`, the project does not support this target (e.g. pywin32 on Linux).

`--python-platform` makes uv choose distributions for the target, so Windows is checked
from Linux. Checks use a cache that is emptied before each round, so nothing downloaded
earlier can hide a gap.

## Build check outcomes (`BuildOutcome`)

- `ok`: built.
- `missing-deps`: uv reported `Failed to resolve requirements` / `No solution found when
  resolving: `a`, `b``. The backticked requirements are parsed (`unresolved_requirements`)
  and fetched.
- `failed`: failed after its requirements resolved, e.g. no compiler (the container has
  none on purpose) or an unsupported Python. Not a mirror problem.
- `timeout`: `performance.build_timeout` exceeded.

## Healing

After each verification round (`sync._verify_and_heal`):

- every `missing` install check becomes a *targeted* request: the same requirement and
  Python, resolved for exactly the failing platform (`--python-platform`), which is what a
  developer there would get upstream;
- every `missing-deps` build adds its unresolved build requirements as universal requests.

The new requests are fetched (with their own build requirements), the mirror is
re-published, and only the failed checks run again. This repeats up to
`sync.heal_rounds` times. Whatever still fails is listed as `still missing`, the report
says `Mirror is INCOMPLETE`, and the exit code is 1.

Fixes that worked are saved to `targeted-resolutions.json` in the state directory and
fetched at the start of later runs, so they are not pruned and healed again every month.
A platform fix is dropped once its requirement leaves the scope; a build fix once the sdist
that needed it is no longer built.

## What verification cannot prove

- That a build *succeeds* on a developer machine (compilers, system libraries).
- Requirements outside the scope (a project not in top-N, `packages.txt` or intake).
- Python patch versions other than the newest mirrored build per minor version.
- Clients using a different uv version than the mirrored one for `uv python install`.
