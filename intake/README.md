# Intake: lock files from teams

Drop files here to make sure a team's project installs offline exactly as locked
(gpygap.toml: `packages.requirements_dir`). Subdirectories are fine, e.g. `intake/team-a/uv.lock`.

| File | What gets mirrored |
| --- | --- |
| `uv.lock`, `*.lock` | Every package from a registry, at its exact locked version. |
| `pylock.toml`, `pylock.*.toml` | Same, for PEP 751 lock files. |
| `*.txt` | Each requirement line with its dependencies (options such as `--hash`, `-r` are ignored). |

Old pinned versions stay in the mirror for as long as the file is here, even after the
project leaves the top list. Remove the file when the team no longer needs it.
This README and other `.md` files are ignored.
