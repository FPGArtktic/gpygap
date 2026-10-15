# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise offline.py on the oldest supported Python (3.9), using only the stdlib.

Run by bin/check-offline-py39 inside a python:3.9 container; pytest is not
available there. Covers export -> apply -> verify and the damaged-delta refusal.
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("offline", sys.argv[1])
assert spec is not None and spec.loader is not None
offline = importlib.util.module_from_spec(spec)
sys.modules["offline"] = offline  # dataclasses need the module registered
spec.loader.exec_module(offline)


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, "utf-8")


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    online, mirror = base / "online", base / "mirror"
    write(online, "packages/a/a-1.whl", "a1")
    write(online, offline.VERSION_FILE, "v1\n")
    write(
        online,
        offline.SUMS_FILE,
        f"{offline.sha256_file(online / 'packages/a/a-1.whl')}  packages/a/a-1.whl\n",
    )
    offline.export_delta(online, base / "d1", base / "snapshot.json")
    assert offline.apply_delta(base / "d1", mirror) == []
    assert (mirror / "packages/a/a-1.whl").read_text("utf-8") == "a1"

    write(online, "packages/b/b-1.whl", "b1")
    write(online, offline.VERSION_FILE, "v2\n")
    offline.export_delta(online, base / "d2", base / "snapshot.json")
    (base / "d2" / offline.PAYLOAD_DIR / "packages/b/b-1.whl").write_text("rot", "utf-8")
    try:
        offline.apply_delta(base / "d2", mirror)
    except RuntimeError as exc:
        assert "damaged" in str(exc)
    else:
        raise AssertionError("damaged delta was applied")

print(f"offline.py OK on Python {sys.version.split()[0]}")
