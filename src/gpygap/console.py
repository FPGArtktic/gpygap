# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Console output: logging that cooperates with tqdm progress bars."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from tqdm import tqdm

ROOT_LOGGER = "gpygap"
_FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_CONSOLE_FORMAT = "%(levelname).1s %(message)s"


class _TqdmHandler(logging.Handler):
    """Write records through tqdm so they do not break active progress bars."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record), file=sys.stderr)
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)


def setup_logging(log_dir: Path, run_id: str, *, verbose: bool) -> Path:
    """Log to the console, to a full per-run log and to a warnings-only log.

    Returns the path of the warnings log; it is only created once something is
    written to it, so its existence means the run needs attention.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    errors_log = log_dir / f"{run_id}-errors.log"
    handlers: list[tuple[logging.Handler, int, str]] = [
        (_TqdmHandler(), logging.DEBUG if verbose else logging.INFO, _CONSOLE_FORMAT),
        (logging.FileHandler(log_dir / f"{run_id}.log", encoding="utf-8"), logging.DEBUG, _FILE_FORMAT),
        (logging.FileHandler(errors_log, encoding="utf-8", delay=True), logging.WARNING, _FILE_FORMAT),
    ]
    root = logging.getLogger(ROOT_LOGGER)
    root.setLevel(logging.DEBUG)
    for handler, level, fmt in handlers:
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(handler)
    return errors_log


def progress(total: int | None, label: str, *, unit: str = "it", as_bytes: bool = False) -> tqdm[Any]:
    """Create a progress bar with the package's common settings."""
    return tqdm(
        total=total,
        desc=label,
        unit="B" if as_bytes else unit,
        unit_scale=as_bytes,
        unit_divisor=1024,
        dynamic_ncols=True,
    )
