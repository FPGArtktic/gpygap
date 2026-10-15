# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install from a finished mirror the way an air-gapped developer would.

Replays the install checks the online side recorded in ``MIRROR-VERIFIED.json``,
but with this machine's own pip and uv, against the mirror's ``file://`` index,
and with ``--only-binary :all:`` so a source build is a failure rather than a
silent success. Run inside a distribution container with no network, or on a
Windows runner; it is what proves the mirror is usable on a system gpygap has
never seen.

    python3 consume.py MIRROR_DIR PLATFORM_KEY [pip,uv]

Both installers are exercised because developers use both and they disagree:
pip reads the PEP 658 sidecars one way and uv another, and only one of them has
ever rejected a mirror this project produced.

Standard library only, and Python 3.9, so it runs on whatever a distribution
happens to ship.
"""

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

SUMS_FILE = "MIRROR-SHA256SUMS"
VERIFIED_FILE = "MIRROR-VERIFIED.json"


def checks_for(mirror, platform_key, python):
    """The recorded checks this machine should be able to reproduce from wheels.

    Top projects, plus whatever the configuration added by hand. The second half
    matters more than it looks: those are the packages chosen to have no upstream
    wheel, so they are the ones a locally built wheel has to carry.
    """
    data = json.loads((mirror / VERIFIED_FILE).read_text("utf-8"))
    info = json.loads((mirror / "MIRROR-INFO.json").read_text("utf-8"))
    extra = set(info.get("extra_requirements") or ())
    return [
        c
        for c in data
        if c.get("platform") == platform_key
        and c.get("python") == python
        and c.get("outcome") == "wheels"
        and (c.get("top") or c.get("requirement") in extra)
    ]


def pip_command(requirement, index_url, target):
    return [
        sys.executable, "-m", "pip", "install",
        "--only-binary", ":all:",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--index-url", index_url,
        "--target", target,
        "--quiet",
        requirement,
    ]


def uv_command(requirement, index_url, target, uv):
    return [
        uv, "pip", "install",
        "--only-binary", ":all:",
        "--no-config",
        # Offline on top of the file:// index: a warm cache must never be able to
        # stand in for a file the mirror does not have.
        "--offline",
        "--index-url", index_url,
        "--target", target,
        "--python", sys.executable,
        "--quiet",
        requirement,
    ]


def run_one(command, env):
    with tempfile.TemporaryDirectory() as cache:
        env = dict(env, UV_CACHE_DIR=cache, UV_NO_PROGRESS="1")
        proc = subprocess.run(command, capture_output=True, universal_newlines=True, env=env)
    if proc.returncode == 0:
        return None
    return (proc.stderr.strip().splitlines() or ["?"])[-1]


def main():
    mirror = Path(sys.argv[1]).resolve()
    platform_key = sys.argv[2]
    wanted = (sys.argv[3] if len(sys.argv) > 3 else "pip,uv").split(",")
    python = "{}.{}".format(*sys.version_info[:2])

    if not (mirror / SUMS_FILE).is_file():
        print("not a mirror: {} has no {}".format(mirror, SUMS_FILE), file=sys.stderr)
        return 2

    installers = {}
    if "pip" in wanted:
        installers["pip"] = None
    if "uv" in wanted:
        uv = shutil.which("uv")
        if uv is None:
            print("uv was requested but is not on PATH", file=sys.stderr)
            return 2
        installers["uv"] = uv

    index_url = (mirror / "simple").as_uri()
    checks = checks_for(mirror, platform_key, python)
    print("python {} on {} ({}): {} checks x {}".format(
        python, platform_key, sysconfig.get_platform(), len(checks), "+".join(installers)
    ))

    if not checks:
        # Passing in silence here would mean "the mirror does not cover this
        # interpreter" counted as success, which is the one thing this job exists
        # to catch.
        print(
            "no recorded checks for python {} on {}: the mirror does not cover this "
            "system. Add the version to targets.python.".format(python, platform_key),
            file=sys.stderr,
        )
        return 1

    failures = []
    for check in checks:
        requirement = check["requirement"]
        for name, uv in installers.items():
            with tempfile.TemporaryDirectory() as target:
                command = (
                    pip_command(requirement, index_url, target)
                    if name == "pip"
                    else uv_command(requirement, index_url, target, uv)
                )
                error = run_one(command, os.environ)
            if error is None:
                print("  ok    {:<4} {}".format(name, requirement))
            else:
                print("  FAIL  {:<4} {}: {}".format(name, requirement, error))
                failures.append("{} via {}".format(requirement, name))

    total = len(checks) * len(installers)
    if failures:
        print("\n{} of {} installs failed from wheels alone".format(len(failures), total), file=sys.stderr)
        return 1
    print("\nall {} installs came from wheels, offline, via {}".format(total, " and ".join(installers)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
