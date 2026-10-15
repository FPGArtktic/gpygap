# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from pathlib import Path

import pytest

from gpygap.config import ConfigError, SdistPolicy, load_config


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "gpygap.toml"
    path.write_text(text, "utf-8")
    return path


def test_defaults_resolve_paths_against_config_directory(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, ""))
    assert config.output == tmp_path / "gpygap"
    assert config.state_dir == tmp_path / "gpygap-state"
    assert config.uv_cache_dir == tmp_path / "gpygap-state" / "uv-cache"
    assert config.top == 1000
    assert config.sdists is SdistPolicy.ALL
    assert [p.key for p in config.platforms] == ["linux-x86_64", "win-amd64"]


def test_overrides_and_extra_file(tmp_path: Path) -> None:
    (tmp_path / "company.txt").write_text("internal-lib==1.2  # pinned\n\n# comment\nrequests\n", "utf-8")
    path = _write(tmp_path, '[packages]\nextra = ["requests"]\nextra_file = "company.txt"\n')
    config = load_config(path, {"packages.top": 10})
    assert config.top == 10
    assert config.extra_requirements == ("requests", "internal-lib==1.2")


@pytest.mark.parametrize(
    "text",
    [
        "[packages]\ntopp = 10\n",
        "[pakages]\ntop = 10\n",
        '[packages]\ntop = "10"\n',
        "[sync]\nprune = 1\n",
        '[targets]\nplatforms = ["beos-x86"]\n',
        '[targets]\npython = ["3"]\n',
        "[sync]\nmax_prune_ratio = 0\n",
    ],
)
def test_invalid_configuration_is_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, text))
