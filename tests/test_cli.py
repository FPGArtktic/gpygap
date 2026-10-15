# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""The command line: what it accepts, and what it returns to a cron job.

Exit codes are the only part of this tool a scheduler reads, so they are worth
pinning: 0 shipped, 1 something is wrong with the mirror, 2 the operator's
input is wrong, 130 interrupted.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from gpygap import cli, offline
from gpygap.config import ConfigError, load_config


def parse(*argv: str) -> argparse.Namespace:
    args, rest = cli._build_parser().parse_known_args(list(argv))
    assert not rest, rest
    return args


def test_the_config_file_is_found_next_to_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli._config_path(None) is None

    (tmp_path / "gpygap.toml").write_text("", "utf-8")
    assert cli._config_path(None) == Path("gpygap.toml")

    elsewhere = tmp_path / "other.toml"
    assert cli._config_path(elsewhere) == elsewhere


def test_only_the_switches_that_were_given_become_overrides() -> None:
    assert cli._overrides(parse("sync")) == {}
    assert cli._overrides(parse("sync", "--top", "10")) == {"packages.top": 10}
    assert cli._overrides(parse("sync", "-o", "/srv/mirror")) == {"mirror.output": "/srv/mirror"}


def test_the_run_switches_reach_the_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(config: object, options: object) -> int:
        captured["options"] = options
        return 0

    monkeypatch.chdir(tmp_path)
    (tmp_path / "gpygap.toml").write_text("", "utf-8")
    monkeypatch.setattr(cli, "run_sync", fake_run)

    assert cli.main(["sync", "--plan-only", "--force-prune", "--full-export", "-v"]) == 0

    options = captured["options"]
    assert options.plan_only and options.force_prune and options.full_export and options.verbose  # type: ignore[attr-defined]
    assert options.export_delta is None  # type: ignore[attr-defined]


def test_a_bad_configuration_is_the_operators_mistake_not_the_mirrors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gpygap.toml").write_text("[packages]\ntpo = 10\n", "utf-8")
    # Exit 2, not 1: nothing is wrong with the mirror, the input was wrong.
    assert cli.main(["sync"]) == 2


def test_a_mirror_problem_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gpygap.toml").write_text("", "utf-8")

    def boom(config: object, options: object) -> int:
        raise RuntimeError("top list unavailable")

    monkeypatch.setattr(cli, "run_sync", boom)
    assert cli.main(["sync"]) == 1


def test_an_interrupted_run_says_it_can_be_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gpygap.toml").write_text("", "utf-8")

    def interrupted(config: object, options: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_sync", interrupted)
    assert cli.main(["sync"]) == 130
    assert "resume" in capsys.readouterr().err


def test_an_unknown_switch_is_refused_rather_than_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gpygap.toml").write_text("", "utf-8")
    monkeypatch.setattr(cli, "run_sync", lambda config, options: 0)

    assert cli.main(["sync", "--no-such-option"]) == 2
    assert "unrecognized" in capsys.readouterr().err


def test_the_offline_subcommand_hands_everything_after_it_to_the_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_offline(argv: list[str]) -> int:
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(offline, "main", fake_offline)
    assert cli.main(["offline", "verify", "/srv/gpygap"]) == 0
    # Everything after the subcommand is the script's, including switches the
    # sync parser would otherwise have claimed.
    assert seen["argv"] == ["verify", "/srv/gpygap"]


def test_a_typo_in_the_configuration_names_the_key_and_the_alternatives(tmp_path: Path) -> None:
    # Silently ignoring an unknown key is how an operator discovers, a month later,
    # that the setting they thought they changed was never read.
    config = tmp_path / "gpygap.toml"
    config.write_text("[packages]\ntpo = 10\n", "utf-8")
    with pytest.raises(ConfigError, match="tpo"):
        load_config(config, {})
