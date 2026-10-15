# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""The list of most-downloaded PyPI projects (format of hugovk/top-pypi-packages)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gpygap.util import http_get, normalize_name, read_json, write_json

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TopList:
    projects: tuple[str, ...]
    last_update: str | None


def load_top_list(sources: Sequence[str], cache_file: Path, count: int, exclude: Iterable[str]) -> TopList:
    """Return the ``count`` most popular projects, skipping ``exclude``.

    Excluded projects do not count towards ``count``: the next project in the
    ranking takes their place. If no source is reachable, the copy saved by the
    previous run is used, which keeps the mirror unchanged instead of pruning it.
    """
    data = _fetch_first(sources)
    if data is not None:
        write_json(cache_file, data)
    else:
        data = read_json(cache_file)
        if data is None:
            raise RuntimeError("top list unavailable: no source reachable and no cached copy")
        log.warning("Using the top list saved by the previous run; nothing will drop out")
    excluded = {normalize_name(name) for name in exclude}
    return TopList(_pick(data["rows"], count, excluded), data.get("last_update"))


def _fetch_first(sources: Sequence[str]) -> dict[str, Any] | None:
    for source in sources:
        try:
            data = _load_source(source)
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Top list source %s failed: %s", source, exc)
            continue
        log.info("Top list from %s (last_update: %s)", source, data.get("last_update", "?"))
        return data
    return None


def _load_source(source: str) -> dict[str, Any]:
    raw = http_get(source) if re.match(r"https?://", source) else Path(source).read_bytes()
    data = json.loads(raw)
    if not isinstance(data.get("rows"), list):
        raise ValueError("missing 'rows' list")
    return dict(data)


def _pick(rows: list[dict[str, Any]], count: int, excluded: set[str]) -> tuple[str, ...]:
    picked: dict[str, None] = {}
    for row in rows:
        if len(picked) >= count:
            break
        name = normalize_name(row["project"])
        if name not in excluded:
            picked[name] = None
    return tuple(picked)
