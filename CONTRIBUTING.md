# Contributing to gpygap

Thanks for looking. gpygap builds an offline PyPI mirror for air-gapped networks, so the
bar for a change is slightly unusual: **a mistake here is discovered by somebody with no
way to download a fix.** That shapes most of what follows.

## Before you start

- Read [docs/development.md](docs/development.md) — the commands, the module layout and
  the invariants. The invariants are the part worth reading twice.
- Read [docs/decisions.md](docs/decisions.md) before changing a design choice. Each entry
  says why the decision was made and what would have to change to revisit it. Revisiting
  one is welcome; doing it without knowing it was a decision is what the file prevents.
- For anything larger than a bug fix, open an issue first. The scope of this tool is
  deliberately narrow and it is cheaper to agree on that before you write code.

## Setting up

```bash
uv sync --all-groups
uv run pytest -q
```

That is the whole development environment: pytest, ruff, mypy and mkdocs. Everything
else — building the mirror, building sdists, the integration tests — runs in a container,
because building an sdist executes code downloaded from the internet and the host that
builds a mirror must stay clean. You need podman (or docker, with `GPYGAP_ENGINE=docker`).

## What a finished change looks like

```bash
uv run ruff format src tests && uv run ruff check src tests
uv run mypy
uv run pytest -q
bin/check-offline-py39                            # if you touched offline.py
uv run --group docs mkdocs build --strict         # if you touched docs/
bin/gpygap sync --config .github/ci/gpygap.toml   # if you touched the pipeline
```

The last one takes about a minute and roughly 140 MB, and must end with
`Mirror is COMPLETE`. Never run a full `sync`: it is the top 1000 projects and 25+ GB.

## Tests

- `tests/test_*.py` must not touch the network and must not start a subprocess. The whole
  suite runs in well under a second, and it stays that way. If a change needs a container
  or a real uv, it belongs in a CI job, not in pytest. Mock at the seam
  (`monkeypatch.setattr(factory, "_run_container", ...)`) rather than at the boundary of
  the operating system.
- Test names are sentences about behaviour, not about functions:
  `test_a_cached_failure_is_a_result_not_a_reason_to_retry`.
- Coverage runs automatically with pytest. Raising it is welcome; a test written only to
  raise it is not.

## Commits and pull requests

- Conventional prefix, scoped where it helps: `feat(selection): ...`, `fix(prune): ...`,
  `docs:`, `ci:`, `build:`, `test:`, `refactor:`, `chore:`.
- Imperative subject, under about 50 characters.
- The body explains **why**, wrapped at 72 characters. The diff already says what
  changed; it cannot say what you were worried about.
- One logical change per commit, usually one module. If a feature needs a data-model
  change first, that change is its own commit.
- Rebase rather than merge. A pull request that needs five commits should have five
  commits that each build.

## Style

Written out in [docs/development.md](docs/development.md); the short version:

- Small functions with one job; the module docstring says why the module exists.
- Frozen dataclasses across module boundaries, never dicts.
- Catch specific exceptions. A broad `except` needs a comment saying why.
- Comments explain reasoning, not mechanics. If a line needs a comment to say what it
  does, rewrite the line.
- Everything is in English: code, comments, logs, docs.
- Every file starts with the two SPDX lines. The project is GPL-3.0-or-later, and by
  contributing you agree your work is licensed that way.

## Documentation

`docs/` describes the software from end to end, with Mermaid diagrams for anything
structural. A page added to `docs/` needs an entry in `mkdocs.yml` under `nav:`, or the
strict build fails. A diagram that no longer matches the code is worse than no diagram —
update it in the same commit.

## Reporting a problem with a mirror

If a run did not end with `Mirror is COMPLETE`, the useful attachments are
`<state>/logs/<run>-report.json` and `<state>/logs/<run>-errors.log`. Neither contains
anything about your network beyond package names and the index URL you configured.
