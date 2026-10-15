# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the Windows wheels a Linux mirror cannot build for itself.

The online machine runs Linux, and no container on it produces a ``win_amd64``
wheel: a Linux kernel does not run the MSVC toolchain, and setuptools has no
cross-build mode. So the mirror publishes what it could not cover, in
``MIRROR-GAPS.json``, and a Windows machine -- a build host on the online
network, or this CI runner -- reads that list, builds exactly those, and hands
them back. ``build.external_dir`` then folds them in on the next run.

    python build_windows_wheels.py MIRROR_DIR OUT_DIR

Nothing here is specific to CI: the same script run on a Windows box with MSVC
Build Tools and a copy of the mirror produces the same wheels.
"""

import json
import subprocess
import sys
from pathlib import Path

GAPS_FILE = "MIRROR-GAPS.json"


def wanted(mirror, python):
    """Sdists this interpreter should try to build, from the published gap list."""
    path = mirror / GAPS_FILE
    if not path.is_file():
        print("{} has no {}: nothing to build".format(mirror, GAPS_FILE))
        return []
    gaps = json.loads(path.read_text("utf-8")).get("gaps", [])
    return sorted(
        {g["sdist"] for g in gaps if g.get("platform", "").startswith("win-") and g.get("python") == python}
    )


def main():
    mirror = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    python = "{}.{}".format(*sys.version_info[:2])

    sdists = wanted(mirror, python)
    if not sdists:
        print("no win-* gaps for python {}: the mirror is already covered".format(python))
        return 0

    index_url = (mirror / "simple").as_uri()
    failures = []
    for sdist in sdists:
        source = mirror / sdist
        if not source.is_file():
            print("  skip  {} (not in this copy of the mirror)".format(sdist))
            continue
        command = [
            sys.executable, "-m", "pip", "wheel",
            "--no-deps", "--no-cache-dir", "--disable-pip-version-check",
            "--index-url", index_url,
            "--wheel-dir", str(out),
            str(source),
        ]
        proc = subprocess.run(command, capture_output=True, universal_newlines=True)
        if proc.returncode == 0:
            print("  built {}".format(sdist))
        else:
            # Not fatal: plenty of projects have no Windows support at all, and the
            # mirror already reports them as needing a compiler. Saying so beats
            # failing the job over a package that was never going to build.
            tail = (proc.stderr.strip().splitlines() or ["?"])[-1]
            print("  FAIL  {}: {}".format(sdist, tail))
            failures.append(sdist)

    built = sorted(p.name for p in out.glob("*.whl"))
    print("\n{} wheels built, {} could not be: {}".format(len(built), len(failures), ", ".join(failures) or "-"))
    for name in built:
        print("  {}".format(name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
