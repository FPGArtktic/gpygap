# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keeps the mirror's files in step with a growing set of resolve requests.

Every ``fetch`` resolves the new requests, rebuilds the plan from all
resolutions so far, downloads what is missing, and then repeats for the build
requirements of sdists that some target platform has to build.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from gpygap.config import Config
from gpygap.distmeta import DistMetaCache, build_requirements
from gpygap.downloader import DownloadResult, download_missing
from gpygap.resolver import Resolution, Resolver, ResolveRequest, universal_requests
from gpygap.selection import MirrorPlan, build_plan


class Fetcher:
    def __init__(self, config: Config, meta: DistMetaCache, *, download: bool) -> None:
        self._config = config
        self._meta = meta
        self._download = download
        self._resolver = Resolver(config)
        self.resolutions: dict[ResolveRequest, Resolution] = {}
        self.plan = MirrorPlan()
        self.downloads = DownloadResult()

    def fetch(
        self, requests: Iterable[ResolveRequest], label: str, *, failure_level: int = logging.WARNING
    ) -> None:
        """Resolve and download ``requests``, then the build requirements they bring in."""
        self._resolve(requests, label, failure_level)
        for round_no in range(1, self._config.build_deps_rounds + 1):
            build = universal_requests(self._build_requirements(), self._config.python_versions)
            if not self._resolve(build, f"Resolving build requirements ({round_no})", logging.WARNING):
                break

    def _resolve(self, requests: Iterable[ResolveRequest], label: str, failure_level: int) -> bool:
        """Resolve requests not seen before; return whether there were any."""
        new = [r for r in dict.fromkeys(requests) if r not in self.resolutions]
        if not new:
            return False
        self.resolutions |= self._resolver.resolve_all(new, label, failure_level=failure_level)
        self.plan = build_plan(self.resolutions.values(), self._config.platforms, self._config.sdists)
        if self._download:
            self.downloads.merge(
                download_missing(
                    self._config.output,
                    self.plan.files.values(),
                    workers=self._config.download_jobs,
                    attempts=self._config.retries + 2,
                    label="Downloading",
                )
            )
        return True

    def _build_requirements(self) -> set[str]:
        # Build requirements are read from downloaded sdists; plan-only mode has none.
        if not self._download:
            return set()
        return build_requirements(self._meta, self.plan.build_sdists.keys(), self._config.output)
