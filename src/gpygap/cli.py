# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-FileContributor: Mateusz Chudzik <matchupikchu99@gmail.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line interface.

The mirror itself is configured in ``gpygap.toml``; the command line only holds
options that change a single run.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from gpygap import offline
from gpygap.config import DEFAULT_CONFIG_FILE, Config, ConfigError, load_config
from gpygap.console import ROOT_LOGGER, setup_console_logging
from gpygap.scan import (
    GENERATED_HEADER,
    ScanResult,
    fetch_submodules,
    render_requirements,
    scan_project,
)
from gpygap.sync import SyncOptions, run_sync
from gpygap.util import normalize_name, write_if_changed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpygap",
        description="Offline PyPI mirror of the top projects and their dependencies.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="build or update the mirror (online side)")
    sync.add_argument(
        "-c", "--config", type=Path, help=f"configuration file (default: ./{DEFAULT_CONFIG_FILE} if present)"
    )
    sync.add_argument("--top", type=int, help="override packages.top, e.g. --top 10 for a quick test")
    sync.add_argument("-o", "--output", help="override mirror.output")
    sync.add_argument("--plan-only", action="store_true", help="resolve and report sizes; change nothing")
    sync.add_argument(
        "--export-delta", type=Path, metavar="DIR", help="write the changes since the last export to DIR"
    )
    sync.add_argument("--full-export", action="store_true", help="export the whole mirror, not a delta")
    sync.add_argument("--force-prune", action="store_true", help="ignore sync.max_prune_ratio for this run")
    sync.add_argument("-v", "--verbose", action="store_true")

    scan = commands.add_parser(
        "scan", help="work out what a project needs and write it as an intake requirements file"
    )
    scan.add_argument("path", type=Path, metavar="PATH", help="project directory to scan")
    scan.add_argument(
        "-c", "--config", type=Path, help=f"configuration file (default: ./{DEFAULT_CONFIG_FILE} if present)"
    )
    scan.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="FILE",
        help="where to write (default: <packages.requirements_dir>/<project>-scan.txt)",
    )
    scan.add_argument("--stdout", action="store_true", help="print the file instead of writing it")
    scan.add_argument("--no-imports", action="store_true", help="only read declared dependencies")
    scan.add_argument(
        "--init-submodules",
        action="store_true",
        help="run `git submodule update --init --recursive` in the project first; "
        "this clones from the hosts the project declares, so it is off by default",
    )
    scan.add_argument(
        "--check-index",
        action="store_true",
        help="confirm guessed names against upstream.index_url; report what it does not have",
    )
    scan.add_argument("--force", action="store_true", help="overwrite an output file this tool did not write")
    scan.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 if an import matched no distribution, or a declared submodule has no content",
    )
    scan.add_argument("-v", "--verbose", action="store_true")

    commands.add_parser(
        "offline",
        help="offline-side commands: apply, verify, smoke-test (same as gpygap_offline.py)",
        add_help=False,
    )
    return parser


def _config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    default = Path(DEFAULT_CONFIG_FILE)
    return default if default.exists() else None


def _overrides(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if args.top is not None:
        overrides["packages.top"] = args.top
    if args.output is not None:
        overrides["mirror.output"] = args.output
    return overrides


def _sync(args: argparse.Namespace) -> int:
    config = load_config(_config_path(args.config), _overrides(args))
    options = SyncOptions(
        plan_only=args.plan_only,
        force_prune=args.force_prune,
        export_delta=args.export_delta,
        full_export=args.full_export,
        verbose=args.verbose,
    )
    return run_sync(config, options)


def _scan(args: argparse.Namespace) -> int:
    config = load_config(_config_path(args.config), {})
    root = args.path.resolve()
    if not root.is_dir():
        print(f"not a directory: {args.path}", file=sys.stderr)
        return 2
    setup_console_logging(verbose=args.verbose)
    if args.init_submodules and (failure := fetch_submodules(root)) is not None:
        # Not fatal. The scan still describes the tree it can read, and whatever
        # stayed empty is named below and fails --strict, which is a more useful
        # answer than refusing to look at the project at all.
        print(failure, file=sys.stderr)
    result = scan_project(
        root,
        follow_imports=not args.no_imports,
        index_url=config.index_url if args.check_index else None,
    )
    rendered = render_requirements(result)
    if args.stdout:
        print(rendered, end="")
    elif (written := _write_scan(result, rendered, args, config)) is not None:
        print(f"{len(result.requirements)} requirements -> {written}")
    else:
        return 2
    _report_scan(result)
    return 1 if args.strict and (result.unmatched or result.missing_submodules) else 0


def _write_scan(result: ScanResult, rendered: str, args: argparse.Namespace, config: Config) -> Path | None:
    """Write the generated file, or explain to stderr why it was not written."""
    destination: Path | None = args.output
    if destination is None:
        if config.requirements_dir is None:
            print(
                "nowhere to write: set packages.requirements_dir in the configuration, "
                "or pass -o FILE or --stdout",
                file=sys.stderr,
            )
            return None
        destination = config.requirements_dir / f"{normalize_name(result.root.name)}-scan.txt"
    if _is_handwritten(destination) and not args.force:
        print(f"{destination} was not written by gpygap scan; pass --force to replace it", file=sys.stderr)
        return None
    write_if_changed(destination, rendered)
    return destination


def _is_handwritten(destination: Path) -> bool:
    """Whether a file exists that this tool did not generate, so replacing it loses work."""
    try:
        with destination.open("r", encoding="utf-8") as handle:
            return not handle.readline().startswith(GENERATED_HEADER)
    except OSError:
        return False
    except UnicodeDecodeError:
        return True


def _report_scan(result: ScanResult) -> None:
    for module in result.missing_submodules:
        print(
            f"submodule has no content, so its requirements are missing from this scan: "
            f"{module.path} (run `git submodule update --init --recursive`)",
            file=sys.stderr,
        )
    for lock in result.lock_files:
        print(f"lock file, hand it to the intake directory unchanged: {lock}")
    for item in result.unmatched:
        print(f"no distribution for import {item.module!r}: {item.reason} ({', '.join(item.origins[:3])})")


def main(argv: list[str] | None = None) -> int:
    args, rest = _build_parser().parse_known_args(argv)
    if args.command == "offline":
        return offline.main(rest)
    if rest:
        print(f"unrecognized arguments: {' '.join(rest)}", file=sys.stderr)
        return 2
    try:
        return _scan(args) if args.command == "scan" else _sync(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except (RuntimeError, FileExistsError) as exc:
        logging.getLogger(ROOT_LOGGER).error("%s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted; run the same command again to resume", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
