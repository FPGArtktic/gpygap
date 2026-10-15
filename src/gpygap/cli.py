# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
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
from gpygap.config import DEFAULT_CONFIG_FILE, ConfigError, load_config
from gpygap.console import ROOT_LOGGER
from gpygap.sync import SyncOptions, run_sync


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


def main(argv: list[str] | None = None) -> int:
    args, rest = _build_parser().parse_known_args(argv)
    if args.command == "offline":
        return offline.main(rest)
    if rest:
        print(f"unrecognized arguments: {' '.join(rest)}", file=sys.stderr)
        return 2
    try:
        return _sync(args)
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
