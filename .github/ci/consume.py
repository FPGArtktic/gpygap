# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install from a finished mirror the way an air-gapped developer would.

Replays the install checks the online side recorded in ``MIRROR-VERIFIED.json``, but
with this machine's own pip, against the mirror's ``file://`` index, and with
``--only-binary :all:`` so that a compiler is never consulted. Run inside a distro
container with no network, or on a Windows runner; it is what proves the mirror is
usable on a system gpygap never saw.

    python3 consume.py MIRROR_DIR PLATFORM_KEY

Only the standard library, and Python 3.9, so it runs on whatever the distro ships.
"""

import json
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

VERIFIED_FILE = "MIRROR-SHA256SUMS"  # presence check only; see main()


def checks_for(mirror: Path, platform_key: str, python: str):
    """Return the recorded checks this machine should be able to reproduce."""
    data = json.loads((mirror / "MIRROR-VERIFIED.json").read_text("utf-8"))
    return [
        c
        for c in data
        if c.get("platform") == platform_key
        and c.get("python") == python
        and c.get("outcome") == "wheels"
        and c.get("top")
    ]


def main() -> int:
    mirror = Path(sys.argv[1]).resolve()
    platform_key = sys.argv[2]
    python = "{}.{}".format(*sys.version_info[:2])

    if not (mirror / VERIFIED_FILE).is_file():
        print("not a mirror: {} has no {}".format(mirror, VERIFIED_FILE), file=sys.stderr)
        return 2

    index_url = (mirror / "simple").as_uri()
    checks = checks_for(mirror, platform_key, python)
    print("python {} on {} ({}): {} checks".format(python, platform_key, sysconfig.get_platform(), len(checks)))

    if not checks:
        # Silence here would mean "the mirror does not cover this interpreter" passing
        # as success, which is the one failure this job exists to catch.
        print(
            "no recorded checks for python {} on {}: the mirror does not cover this "
            "system. Add the version to targets.python.".format(python, platform_key),
            file=sys.stderr,
        )
        return 1

    failures = []
    for check in checks:
        requirement = check["requirement"]
        with tempfile.TemporaryDirectory() as target:
            command = [
                sys.executable, "-m", "pip", "install",
                "--only-binary", ":all:",
                "--no-cache-dir",
                "--index-url", index_url,
                "--target", target,
                "--quiet", "--disable-pip-version-check",
                requirement,
            ]
            proc = subprocess.run(command, capture_output=True, universal_newlines=True)
        if proc.returncode == 0:
            print("  ok    {}".format(requirement))
        else:
            tail = (proc.stderr.strip().splitlines() or ["?"])[-1]
            print("  FAIL  {}: {}".format(requirement, tail))
            failures.append(requirement)

    if failures:
        print("\n{} of {} installs failed from wheels alone".format(len(failures), len(checks)), file=sys.stderr)
        return 1
    print("\nall {} installs came from wheels, offline".format(len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
