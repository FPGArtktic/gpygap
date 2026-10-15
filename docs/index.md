# gpygap documentation

Read in this order when you are new to the project:

| Document | Answers |
| --- | --- |
| [architecture.md](architecture.md) | What happens in a run, which module does what, how data flows. |
| [verification.md](verification.md) | How the tool proves the offline mirror is complete, and how it heals gaps. |
| [file-formats.md](file-formats.md) | Every file the tool writes: mirror layout, deltas, state directory. |
| [operations.md](operations.md) | Running it (container), monthly routine, transfer, what to do when a run fails. |
| [development.md](development.md) | Working on the code: commands, module layout, invariants, style. |
| [decisions.md](decisions.md) | Why things are the way they are; read before changing a design choice. |

In the repository root, next to these pages:

| File | What it is |
| --- | --- |
| `README.md` | User guide with Quickstart, for the people running the mirror and for developers configuring their machines. |
| `gpygap.toml` | Every configuration key, documented inline. |
| `packages.txt` | Our own packages, mirrored on top of the top list. |
| `intake/` | Team lock files, mirrored exactly as locked. |
| `LICENSE` | GPL-3.0-or-later. |

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
| scope | Everything a run must make installable: top-N, `packages.extra`, packages.txt, intake, their extras, build backends. |
| pin | `name==version` from an intake lock file, resolved `--no-deps`. |
| delta | Export of changes since the last export, applied offline with `gpygap_offline.py apply`. |
